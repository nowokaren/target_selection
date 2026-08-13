"""Persistent local registry for targets and observatory observation epochs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from observatory_observations import canonical_target_name, load_hsh_astrometry_catalog


COORDINATE_PRIORITIES = {
    "MOP": 100,
    "USER": 80,
    "ANALYSIS": 0,
    "HSH": 10,
    "JS": 10,
}


def coordinate_priority(provider: str) -> int:
    """Return the precedence assigned to target coordinates from one source."""
    return COORDINATE_PRIORITIES.get(str(provider).upper(), 50)


class TargetRegistry:
    """SQLite-backed registry shared by all target-selection runs.

    Raw provider caches remain in their native per-target CSV/JSON files. This
    database stores the cross-provider identities, observation epochs, and
    refresh state needed to reuse those caches and build monitoring reports.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS targets (
                    target_key TEXT PRIMARY KEY,
                    preferred_name TEXT NOT NULL,
                    ra_deg REAL,
                    dec_deg REAL,
                    coordinate_source TEXT,
                    coordinate_priority INTEGER NOT NULL DEFAULT 0,
                    first_seen_utc TEXT NOT NULL,
                    last_seen_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS target_aliases (
                    alias_key TEXT PRIMARY KEY,
                    target_key TEXT NOT NULL REFERENCES targets(target_key),
                    alias_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS survey_observations (
                    observation_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    target_key TEXT NOT NULL REFERENCES targets(target_key),
                    target_name TEXT NOT NULL,
                    mjd REAL,
                    band TEXT,
                    exptime_s REAL,
                    usable INTEGER NOT NULL,
                    target_in_frame INTEGER,
                    catalogue_match INTEGER,
                    airmass REAL,
                    metadata_json TEXT,
                    updated_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS survey_observations_target_provider_mjd
                    ON survey_observations(target_key, provider, mjd);
                CREATE TABLE IF NOT EXISTS source_state (
                    provider TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    fingerprint TEXT,
                    refreshed_on TEXT,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY(provider, source_id)
                );
                CREATE TABLE IF NOT EXISTS source_target_records (
                    source_name TEXT NOT NULL,
                    source_role TEXT NOT NULL,
                    target_key TEXT NOT NULL REFERENCES targets(target_key),
                    record_json TEXT NOT NULL,
                    source_observed_at TEXT,
                    updated_utc TEXT NOT NULL,
                    PRIMARY KEY(source_name, target_key)
                );
                CREATE INDEX IF NOT EXISTS source_target_records_target
                    ON source_target_records(target_key, source_role);
                CREATE TABLE IF NOT EXISTS photometry_points (
                    point_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_role TEXT NOT NULL,
                    target_key TEXT NOT NULL REFERENCES targets(target_key),
                    target_name TEXT NOT NULL,
                    mjd REAL NOT NULL,
                    band TEXT,
                    magnitude REAL,
                    magnitude_error REAL,
                    flux REAL,
                    flux_error REAL,
                    metadata_json TEXT,
                    updated_utc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS photometry_points_target_source_mjd
                    ON photometry_points(target_key, source_name, mjd);
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    output_path TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(targets)")
            }
            if "coordinate_source" not in columns:
                connection.execute("ALTER TABLE targets ADD COLUMN coordinate_source TEXT")
            if "coordinate_priority" not in columns:
                connection.execute(
                    "ALTER TABLE targets ADD COLUMN coordinate_priority INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                UPDATE targets
                SET coordinate_source=COALESCE(coordinate_source, 'legacy_unknown'),
                    coordinate_priority=COALESCE(coordinate_priority, 0)
                WHERE ra_deg IS NOT NULL OR dec_deg IS NOT NULL
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def register_targets(self, targets: pd.DataFrame, *, provider: str) -> None:
        """Upsert target names and positions without replacing known coordinates with NaN."""
        if targets.empty or "Target" not in targets:
            return
        now = self._now()
        priority = coordinate_priority(provider)
        source = str(provider).upper()
        records = []
        for row in targets.itertuples(index=False):
            values = row._asdict()
            name = str(values["Target"]).strip()
            if not name:
                continue
            key = canonical_target_name(name)
            ra = pd.to_numeric(values.get("RA_deg"), errors="coerce")
            dec = pd.to_numeric(values.get("Dec_deg"), errors="coerce")
            has_position = pd.notna(ra) and pd.notna(dec)
            row_priority = pd.to_numeric(
                values.get("coordinate_priority"), errors="coerce"
            )
            effective_priority = (
                int(row_priority) if pd.notna(row_priority) else priority
            )
            row_source = values.get("coordinate_source")
            effective_source = (
                str(row_source)
                if row_source is not None and pd.notna(row_source)
                else source
            )
            records.append((
                key, name,
                float(ra) if has_position else None,
                float(dec) if has_position else None,
                effective_source if has_position else None,
                effective_priority if has_position else 0,
                now, now,
            ))
        if not records:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO targets(
                    target_key, preferred_name, ra_deg, dec_deg,
                    coordinate_source, coordinate_priority, first_seen_utc, last_seen_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    preferred_name=excluded.preferred_name,
                    ra_deg=CASE
                        WHEN excluded.ra_deg IS NOT NULL AND (
                            targets.ra_deg IS NULL
                            OR excluded.coordinate_priority >= targets.coordinate_priority
                        ) THEN excluded.ra_deg ELSE targets.ra_deg END,
                    dec_deg=CASE
                        WHEN excluded.dec_deg IS NOT NULL AND (
                            targets.dec_deg IS NULL
                            OR excluded.coordinate_priority >= targets.coordinate_priority
                        ) THEN excluded.dec_deg ELSE targets.dec_deg END,
                    coordinate_source=CASE
                        WHEN excluded.ra_deg IS NOT NULL AND excluded.dec_deg IS NOT NULL AND (
                            targets.ra_deg IS NULL OR targets.dec_deg IS NULL
                            OR excluded.coordinate_priority >= targets.coordinate_priority
                        ) THEN excluded.coordinate_source ELSE targets.coordinate_source END,
                    coordinate_priority=CASE
                        WHEN excluded.ra_deg IS NOT NULL AND excluded.dec_deg IS NOT NULL AND (
                            targets.ra_deg IS NULL OR targets.dec_deg IS NULL
                            OR excluded.coordinate_priority >= targets.coordinate_priority
                        ) THEN excluded.coordinate_priority ELSE targets.coordinate_priority END,
                    last_seen_utc=excluded.last_seen_utc
                """,
                records,
            )
            connection.executemany(
                """
                INSERT INTO target_aliases(alias_key, target_key, alias_name, provider, updated_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET
                    target_key=excluded.target_key, alias_name=excluded.alias_name,
                    provider=excluded.provider, updated_utc=excluded.updated_utc
                """,
                [(key, key, name, provider, now) for key, name, *_ in records],
            )

    def source_is_current(self, provider: str, source_id: str, fingerprint: str) -> bool:
        """Return whether an imported source has the same file fingerprint."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM source_state WHERE provider=? AND source_id=?",
                (provider, source_id),
            ).fetchone()
        return row is not None and row["fingerprint"] == fingerprint

    def mark_source(self, provider: str, source_id: str, fingerprint: str | None = None) -> None:
        """Record the latest successful import or provider refresh."""
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_state(provider, source_id, fingerprint, refreshed_on, updated_utc)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider, source_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint, refreshed_on=excluded.refreshed_on,
                    updated_utc=excluded.updated_utc
                """,
                (provider, source_id, fingerprint, date.today().isoformat(), now),
            )

    def needs_daily_refresh(self, provider: str, source_id: str = "targets") -> bool:
        """Return True when a provider has not been refreshed on the current UTC day."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT refreshed_on FROM source_state WHERE provider=? AND source_id=?",
                (provider, source_id),
            ).fetchone()
        return row is None or row["refreshed_on"] != date.today().isoformat()

    def import_hsh_catalog(self, path: str | Path, *, force: bool = False) -> int:
        """Import astrometrically processed HSH science images, incrementally."""
        path = Path(path)
        stat = path.stat()
        source_id = str(path.resolve())
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        if not force and self.source_is_current("HSH", source_id, fingerprint):
            return 0
        observations = load_hsh_astrometry_catalog(path)
        # HSH CRVAL coordinates describe image pointings, not event positions.
        # Register the identity only; an authoritative target provider supplies
        # the canonical coordinates later.
        identities = observations.groupby("target_key", sort=False).agg(
            Target=("source_target", "first"),
        ).reset_index(drop=True)
        self.register_targets(identities, provider="HSH")
        now = self._now()
        records = []
        for index, row in observations.iterrows():
            filename = str(row.get("filename", ""))
            observation_id = f"HSH:{filename or index}"
            metadata = {
                "source_target": str(row["source_target"]),
                "obj_stat": str(row.get("obj_stat", "")),
                "astromet": str(row.get("astromet", "")),
                "pointing_ra_deg": (
                    float(row["pointing_ra_deg"])
                    if pd.notna(row.get("pointing_ra_deg")) else None
                ),
                "pointing_dec_deg": (
                    float(row["pointing_dec_deg"])
                    if pd.notna(row.get("pointing_dec_deg")) else None
                ),
            }
            records.append((
                observation_id, "HSH", row["target_key"], str(row["source_target"]),
                float(row["mjd"]) if pd.notna(row["mjd"]) else None,
                str(row["band"]) if pd.notna(row["band"]) else None,
                float(row["exptime_s"]) if pd.notna(row["exptime_s"]) else None,
                int(bool(row["usable"])), int(bool(row["target_in_frame"])),
                int(bool(row["catalogue_match"])),
                float(row["airmass_value"]) if pd.notna(row["airmass_value"]) else None,
                json.dumps(metadata), now,
            ))
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO survey_observations(
                    observation_id, provider, target_key, target_name, mjd, band, exptime_s,
                    usable, target_in_frame, catalogue_match, airmass, metadata_json, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    target_key=excluded.target_key, target_name=excluded.target_name, mjd=excluded.mjd,
                    band=excluded.band, exptime_s=excluded.exptime_s, usable=excluded.usable,
                    target_in_frame=excluded.target_in_frame, catalogue_match=excluded.catalogue_match,
                    airmass=excluded.airmass, metadata_json=excluded.metadata_json,
                    updated_utc=excluded.updated_utc
                """,
                records,
            )
        self.mark_source("HSH", source_id, fingerprint)
        return len(records)

    def import_survey_observations(
        self,
        provider: str,
        observations: pd.DataFrame,
        *,
        source_id: str = "manual",
    ) -> int:
        """Upsert normalized observations from a local survey such as JS.

        Required columns are ``Target`` and ``mjd``. Optional columns are
        ``band``, ``exptime_s``, ``usable``, ``RA_deg``, ``Dec_deg``, and
        ``observation_id``. This deliberately accepts the same normalized
        form for HSH, JS, and future observatory providers.
        """
        provider = str(provider).upper()
        required = {"Target", "mjd"}
        missing = required - set(observations.columns)
        if missing:
            raise ValueError(f"Survey observations are missing columns: {sorted(missing)}")
        data = observations.copy()
        data["Target"] = data["Target"].astype(str)
        data["target_key"] = data["Target"].map(canonical_target_name)
        data["mjd"] = pd.to_numeric(data["mjd"], errors="coerce")
        data = data.dropna(subset=["mjd"])
        self.register_targets(data, provider=provider)
        now = self._now()
        records = []
        for index, row in data.iterrows():
            observation_id = str(row.get("observation_id", f"{provider}:{source_id}:{index}"))
            records.append((
                observation_id, provider, row["target_key"], row["Target"], float(row["mjd"]),
                str(row.get("band")) if pd.notna(row.get("band")) else None,
                float(row.get("exptime_s")) if pd.notna(row.get("exptime_s")) else None,
                int(bool(row.get("usable", True))),
                int(bool(row.get("target_in_frame", True))), int(bool(row.get("catalogue_match", True))),
                float(row.get("airmass")) if pd.notna(row.get("airmass")) else None,
                json.dumps({"source_id": source_id}), now,
            ))
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO survey_observations(
                    observation_id, provider, target_key, target_name, mjd, band, exptime_s,
                    usable, target_in_frame, catalogue_match, airmass, metadata_json, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(observation_id) DO UPDATE SET
                    target_key=excluded.target_key, target_name=excluded.target_name, mjd=excluded.mjd,
                    band=excluded.band, exptime_s=excluded.exptime_s, usable=excluded.usable,
                    target_in_frame=excluded.target_in_frame, catalogue_match=excluded.catalogue_match,
                    airmass=excluded.airmass, metadata_json=excluded.metadata_json,
                    updated_utc=excluded.updated_utc
                """,
                records,
            )
        self.mark_source(provider, source_id)
        return len(records)

    def observed_targets(
        self, providers: Iterable[str] = ("HSH", "JS"), *, microlensing_only: bool = True,
    ) -> pd.DataFrame:
        """Return previously observed targets from the given providers.

        HSH catalogues can also contain calibration fields and non-microlensing
        programs. By default only established transient-survey name families
        are returned for the target-selection union.
        """
        providers = tuple(str(provider).upper() for provider in providers)
        if not providers:
            return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg", "observatory_providers"])
        placeholders = ",".join("?" for _ in providers)
        query = f"""
            SELECT t.preferred_name AS Target, t.ra_deg AS RA_deg, t.dec_deg AS Dec_deg,
                   t.coordinate_source, t.coordinate_priority,
                   GROUP_CONCAT(DISTINCT o.provider) AS observatory_providers
            FROM targets t JOIN survey_observations o ON o.target_key=t.target_key
            WHERE o.provider IN ({placeholders})
            GROUP BY t.target_key, t.preferred_name, t.ra_deg, t.dec_deg,
                     t.coordinate_source, t.coordinate_priority
            ORDER BY t.preferred_name
        """
        with self._connect() as connection:
            result = pd.read_sql_query(query, connection, params=providers)
        if microlensing_only and not result.empty:
            result = result.loc[
                result["Target"].astype(str).str.match(r"^(OGLE|GAIA|ZTF|ASASSN)", case=False, na=False)
            ].reset_index(drop=True)
        return result

    def observation_epochs(self, targets: pd.DataFrame, providers: Iterable[str] = ("HSH", "JS")) -> pd.DataFrame:
        """Return stored observation epochs for target light-curve shading."""
        if targets.empty or "Target" not in targets:
            return pd.DataFrame(columns=["Target", "provider", "mjd", "band", "exptime_s", "usable"])
        names = targets[["Target"]].drop_duplicates().copy()
        names["target_key"] = names["Target"].map(canonical_target_name)
        providers = tuple(str(provider).upper() for provider in providers)
        if not providers:
            return pd.DataFrame(columns=["Target", "provider", "mjd", "band", "exptime_s", "usable"])
        target_placeholders = ",".join("?" for _ in range(len(names)))
        provider_placeholders = ",".join("?" for _ in providers)
        query = f"""
            SELECT target_key, provider, mjd, band, exptime_s, usable
            FROM survey_observations
            WHERE target_key IN ({target_placeholders}) AND provider IN ({provider_placeholders})
        """
        with self._connect() as connection:
            data = pd.read_sql_query(query, connection, params=[*names.target_key, *providers])
        if data.empty:
            return pd.DataFrame(columns=["Target", "provider", "mjd", "band", "exptime_s", "usable"])
        return names.merge(data, on="target_key", how="inner").drop(columns="target_key")
    def upsert_source_records(
        self,
        records: pd.DataFrame,
        *,
        source_name: str,
        source_role: str,
        observed_at_column: str | None = None,
    ) -> int:
        """Store the latest normalized record supplied by one source per target."""
        if records.empty or "Target" not in records:
            return 0
        self.register_targets(records, provider=source_name)
        now = self._now()
        payloads = []
        for row in records.to_dict(orient="records"):
            name = str(row["Target"]).strip()
            if not name:
                continue
            observed_at = row.get(observed_at_column) if observed_at_column else None
            serializable = {}
            for key, value in row.items():
                missing = pd.isna(value)
                if isinstance(missing, bool) and missing:
                    serializable[key] = None
                else:
                    serializable[key] = value
            payloads.append(
                (
                    str(source_name),
                    str(source_role),
                    canonical_target_name(name),
                    json.dumps(serializable, default=str, sort_keys=True),
                    None
                    if observed_at is None or pd.isna(observed_at)
                    else str(observed_at),
                    now,
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO source_target_records(
                    source_name, source_role, target_key, record_json,
                    source_observed_at, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_name, target_key) DO UPDATE SET
                    source_role=excluded.source_role,
                    record_json=excluded.record_json,
                    source_observed_at=excluded.source_observed_at,
                    updated_utc=excluded.updated_utc
                """,
                payloads,
            )
        return len(payloads)

    def import_photometry(
        self,
        source_name: str,
        photometry: pd.DataFrame,
        *,
        source_role: str,
    ) -> int:
        """Upsert normalized photometry from any provider or survey.

        Required columns are Target and mjd. Optional common columns are band,
        magnitude, magnitude_error, flux, flux_error, and point_id.
        """
        required = {"Target", "mjd"}
        missing = required - set(photometry.columns)
        if missing:
            raise ValueError(f"Photometry is missing columns: {sorted(missing)}")
        data = photometry.copy()
        data["mjd"] = pd.to_numeric(data["mjd"], errors="coerce")
        data = data.dropna(subset=["Target", "mjd"])
        self.register_targets(data, provider=source_name)
        now = self._now()
        rows = []
        for index, row in data.iterrows():
            name = str(row["Target"])
            point_id = str(
                row.get(
                    "point_id",
                    f"{source_name}:{canonical_target_name(name)}:{float(row['mjd']):.8f}:"
                    f"{row.get('band', '')}:{index}",
                )
            )
            known = {
                "Target",
                "mjd",
                "band",
                "magnitude",
                "magnitude_error",
                "flux",
                "flux_error",
                "point_id",
            }
            metadata = {key: value for key, value in row.items() if key not in known}

            def numeric(key: str) -> float | None:
                return float(row[key]) if key in row and pd.notna(row[key]) else None

            rows.append(
                (
                    point_id,
                    str(source_name),
                    str(source_role),
                    canonical_target_name(name),
                    name,
                    float(row["mjd"]),
                    str(row["band"]) if pd.notna(row.get("band")) else None,
                    numeric("magnitude"),
                    numeric("magnitude_error"),
                    numeric("flux"),
                    numeric("flux_error"),
                    json.dumps(metadata, default=str, sort_keys=True),
                    now,
                )
            )
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO photometry_points(
                    point_id, source_name, source_role, target_key, target_name,
                    mjd, band, magnitude, magnitude_error, flux, flux_error,
                    metadata_json, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(point_id) DO UPDATE SET
                    source_name=excluded.source_name, source_role=excluded.source_role,
                    target_key=excluded.target_key, target_name=excluded.target_name,
                    mjd=excluded.mjd, band=excluded.band, magnitude=excluded.magnitude,
                    magnitude_error=excluded.magnitude_error, flux=excluded.flux,
                    flux_error=excluded.flux_error, metadata_json=excluded.metadata_json,
                    updated_utc=excluded.updated_utc
                """,
                rows,
            )
        return len(rows)

    def source_catalog(self) -> pd.DataFrame:
        """Return one compact row per target and source."""
        query = """
            SELECT t.preferred_name AS Target, t.ra_deg AS RA_deg, t.dec_deg AS Dec_deg,
                   t.coordinate_source, t.coordinate_priority,
                   r.source_name, r.source_role, r.source_observed_at,
                   r.record_json, r.updated_utc
            FROM source_target_records r
            JOIN targets t ON t.target_key=r.target_key
            ORDER BY t.preferred_name, r.source_role, r.source_name
        """
        with self._connect() as connection:
            return pd.read_sql_query(query, connection)

    def record_run(
        self,
        run_id: str,
        config: dict,
        output_path: str | Path,
        *,
        status: str,
    ) -> None:
        """Record a reproducible analysis run and its output location."""
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs(
                    run_id, status, output_path, config_json, created_utc, updated_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status, output_path=excluded.output_path,
                    config_json=excluded.config_json, updated_utc=excluded.updated_utc
                """,
                (
                    str(run_id),
                    str(status),
                    str(Path(output_path)),
                    json.dumps(config, default=str, sort_keys=True),
                    now,
                    now,
                ),
            )

    def photometry(
        self,
        targets: pd.DataFrame,
        sources: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        """Return normalized stored photometry for the requested targets."""
        columns = [
            "Target",
            "provider",
            "mjd",
            "band",
            "magnitude",
            "magnitude_error",
            "flux",
            "flux_error",
        ]
        if targets.empty or "Target" not in targets:
            return pd.DataFrame(columns=columns)
        names = targets[["Target"]].drop_duplicates().copy()
        names["target_key"] = names["Target"].map(canonical_target_name)
        target_placeholders = ",".join("?" for _ in range(len(names)))
        parameters: list[str] = list(names["target_key"])
        source_clause = ""
        if sources is not None:
            normalized_sources = tuple(str(source) for source in sources)
            if not normalized_sources:
                return pd.DataFrame(columns=columns)
            source_placeholders = ",".join("?" for _ in normalized_sources)
            source_clause = f" AND source_name IN ({source_placeholders})"
            parameters.extend(normalized_sources)
        query = f"""
            SELECT target_key, source_name AS provider, mjd, band, magnitude,
                   magnitude_error, flux, flux_error
            FROM photometry_points
            WHERE target_key IN ({target_placeholders}){source_clause}
            ORDER BY target_key, source_name, mjd
        """
        with self._connect() as connection:
            data = pd.read_sql_query(query, connection, params=parameters)
        if data.empty:
            return pd.DataFrame(columns=columns)
        return names.merge(data, on="target_key", how="inner").drop(
            columns="target_key"
        )
