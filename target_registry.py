"""Persistent local registry for targets and observatory observation epochs."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from observatory_observations import canonical_target_name, load_hsh_astrometry_catalog


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
        records = []
        for row in targets.itertuples(index=False):
            values = row._asdict()
            name = str(values["Target"]).strip()
            if not name:
                continue
            key = canonical_target_name(name)
            ra = pd.to_numeric(values.get("RA_deg"), errors="coerce")
            dec = pd.to_numeric(values.get("Dec_deg"), errors="coerce")
            records.append((key, name, float(ra) if pd.notna(ra) else None, float(dec) if pd.notna(dec) else None, now, now))
        if not records:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO targets(target_key, preferred_name, ra_deg, dec_deg, first_seen_utc, last_seen_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_key) DO UPDATE SET
                    preferred_name=excluded.preferred_name,
                    ra_deg=COALESCE(excluded.ra_deg, targets.ra_deg),
                    dec_deg=COALESCE(excluded.dec_deg, targets.dec_deg),
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
        positions = observations.groupby("target_key", sort=False).agg(
            Target=("source_target", "first"),
            RA_deg=("ra_deg", "median"),
            Dec_deg=("dec_deg", "median"),
        ).reset_index(drop=True)
        self.register_targets(positions, provider="HSH")
        now = self._now()
        records = []
        for index, row in observations.iterrows():
            filename = str(row.get("filename", ""))
            observation_id = f"HSH:{filename or index}"
            metadata = {
                "source_target": str(row["source_target"]),
                "obj_stat": str(row.get("obj_stat", "")),
                "astromet": str(row.get("astromet", "")),
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
                   GROUP_CONCAT(DISTINCT o.provider) AS observatory_providers
            FROM targets t JOIN survey_observations o ON o.target_key=t.target_key
            WHERE o.provider IN ({placeholders})
            GROUP BY t.target_key, t.preferred_name, t.ra_deg, t.dec_deg
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
