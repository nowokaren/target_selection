"""Evidence-based microlensing follow-up assessment and ordering."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from typing import Any
from pathlib import Path
import re

import numpy as np
import pandas as pd

from .observatory_observations import canonical_target_name


CATEGORY_ORDER = {
    "NUEVO-RISE": 0,
    "NUEVO-PEAK": 1,
    "CONTINUIDAD": 2,
    "DIAGNÓSTICO": 3,
    "OPORTUNISTA": 4,
    "REVISAR": 5,
    "DESCARTAR": 6,
}
SCHEDULABLE_CATEGORIES = frozenset({
    "NUEVO-RISE", "NUEVO-PEAK", "CONTINUIDAD", "DIAGNÓSTICO", "OPORTUNISTA",
})


def select_schedulable_targets(candidates: pd.DataFrame, priority: pd.DataFrame) -> pd.DataFrame:
    """Use current-run visibility for inclusion and priority rank only for sorting."""
    current = priority[["target_id", "priority_category", "n_visible_nights", "priority_rank"]]
    feasible = candidates.drop(columns=["priority_category", "priority_rank", "n_visible_nights"], errors="ignore").merge(
        current, on="target_id", how="left",
    )
    selected = feasible.loc[
        pd.to_numeric(feasible["n_visible_nights"], errors="coerce").fillna(0).gt(0)
    ]
    return selected.sort_values("priority_rank").drop(columns=["priority_rank", "n_visible_nights"])


PRIORITY_TABLE_STAGES = (
    ("pre_baseline", "Pre-baseline"),
    ("rise", "Subida"),
    ("peak", "Peak"),
    ("fall", "Bajada"),
    ("post_baseline", "Post-baseline"),
)

# Approximate field boxes used only when an event name has no region code.
LMC_FIELD = (65.0, 100.0, -78.0, -60.0)
SMC_FIELD = (0.0, 30.0, -80.0, -63.0)


def _sky_region(row: pd.Series) -> str:
    """Use catalog region tags, then approximate sky fields for untagged names."""
    name = str(row.get("target_id", "")).upper()
    tagged = re.search(r"(?:^|[-_])(BLG|DG|GD|LMC|SMC)(?:[-_]|$)", name)
    if tagged:
        return tagged.group(1)

    ra = _numeric(pd.DataFrame([row]), ("ra_deg", "RA_deg", "RA", "ra")).iloc[0] % 360
    dec = _numeric(pd.DataFrame([row]), ("dec_deg", "Dec_deg", "DEC_deg", "Dec", "DEC", "dec")).iloc[0]
    if np.isfinite(ra) and np.isfinite(dec):
        if LMC_FIELD[0] <= ra <= LMC_FIELD[1] and LMC_FIELD[2] <= dec <= LMC_FIELD[3]:
            return "LMC"
        if SMC_FIELD[0] <= ra <= SMC_FIELD[1] and SMC_FIELD[2] <= dec <= SMC_FIELD[3]:
            return "SMC"

    longitude = _numeric(pd.DataFrame([row]), ("galactic_lng", "galactic_l", "l")).iloc[0]
    latitude = _numeric(pd.DataFrame([row]), ("galactic_lat", "galactic_b", "b")).iloc[0]
    if not (np.isfinite(longitude) and np.isfinite(latitude)) and np.isfinite(ra) and np.isfinite(dec):
        # ICRS to Galactic rotation; sufficient for broad field labels.
        alpha, delta = np.deg2rad(ra), np.deg2rad(dec)
        xyz = np.array([np.cos(delta) * np.cos(alpha), np.cos(delta) * np.sin(alpha), np.sin(delta)])
        rotation = np.array([
            [-0.0548755604, -0.8734370902, -0.4838350155],
            [ 0.4941094279, -0.4448296300,  0.7469822445],
            [-0.8676661490, -0.1980763734,  0.4559837762],
        ])
        galactic = rotation @ xyz
        longitude = np.rad2deg(np.arctan2(galactic[1], galactic[0])) % 360
        latitude = np.rad2deg(np.arcsin(galactic[2]))
    if np.isfinite(longitude) and np.isfinite(latitude):
        if abs(latitude) <= 10:
            return "BLG" if min(longitude, 360 - longitude) <= 15 else "GD"
    return "OTHER"


def build_priority_table(
    priority: pd.DataFrame,
    observations: pd.DataFrame,
    survey_epochs: pd.DataFrame,
    visibility_by_night: pd.DataFrame,
    *,
    as_of: str | date | datetime | pd.Timestamp,
    config: PriorityConfig | None = None,
) -> pd.DataFrame:
    """Build the ordered, one-row-per-photometric-target priority table.

    Each stage is reported as point/image counts by data provider. Visibility
    minutes sum time above 40 degrees altitude.
    """
    config = config or PriorityConfig()
    if priority.empty:
        return pd.DataFrame(columns=[
            "tom_source", "target_id", "event_subtype", "event_survey", "photometry_sources",
            "last_magnitude", "last_survey",
            "last_filter", "stage", "days_since_t0", "tE_days", "u0",
            "visible_minutes_above_40", "minimum_airmass",
        ])

    table = priority.copy()
    if "target_id" not in table and "Target" in table:
        table = table.rename(columns={"Target": "target_id"})
    table["target_id"] = table["target_id"].astype(str)
    table = table.drop_duplicates("target_id").copy()
    ids = table["target_id"]
    table["_target_key"] = ids.map(canonical_target_name)
    now_hjd = _as_hjd(as_of)
    t0 = _to_hjd(_numeric(table, ("t0_hjd", "mop_t_0_hjd", "mop_t0_hjd", "t_0_HJD", "t_0_hjd", "event_peak_hjd", "Tmax (HJD)")))
    te = _numeric(table, ("tE_days", "mop_t_e_days", "mop_te_days", "t_E_days", "t_e_days", "event_duration_days", "tau"))
    te = te.where(te.gt(0))
    classifiable = t0.notna() & te.gt(0)
    table["t0_hjd"] = t0
    table["days_since_t0"] = now_hjd - t0
    table["tE_days"] = te
    table["u0"] = _numeric(table, ("u0", "mop_u_0", "mop_u0", "u_0", "Umin"))
    table["current_event_stage"] = [
        classify_event_stage(t0_value, te_value, now_hjd, config)[0]
        for t0_value, te_value in zip(t0, te)
    ]
    latest_stage_records: list[pd.DataFrame] = []

    names = ids.str.upper()
    table["event_subtype"] = names.str.extract(r"(?:^|[-_])(BLG|DG|GD|LMC|SMC)(?:[-_]|$)", expand=False).fillna("")
    table["event_subtype"] = table.apply(_sky_region, axis=1)
    table["event_survey"] = names.str.extract(r"^(OGLE|ZTF|GAIA|ASASSN|ASAS|ATLAS|MOA)", expand=False).fillna("")
    table["event_survey"] = table["event_survey"].replace({"ASASSN": "ASAS-SN", "ASAS": "ASAS-SN"})
    if "tom_source" not in table:
        table["tom_source"] = table.get("source_providers", pd.Series("", index=table.index))

    points = observations.copy() if observations is not None else pd.DataFrame()
    if not points.empty:
        point_id = "target_id" if "target_id" in points else "Target"
        points["_target_key"] = points[point_id].astype(str).map(canonical_target_name)
        points["_epoch_hjd"] = _epoch_hjd(points)
        points["_magnitude"] = _coalesced_numeric(points, ("magnitude", "Magnitude", "mag"))
        points["_band"] = _coalesced_text(points, ("band", "Filter", "filter"))
        points["_survey"] = _coalesced_text(points, ("survey", "Source", "provider", "Provider"))
        points["_photometry_source"] = _coalesced_text(
            points, ("provider", "Provider", "survey", "Source"), default="unknown"
        ).str.strip().str.upper()
        points = points.loc[points["_epoch_hjd"].notna() & points["_magnitude"].notna()].copy()
        if not points.empty:
            points["_survey_key"] = points["_survey"].str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            points["_band_key"] = points["_band"].str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            points["_epoch_key"] = points["_epoch_hjd"].round(6)
            source_points = points.copy()
            source_counts = points.drop_duplicates(
                ["_target_key", "_photometry_source", "_survey_key", "_band_key", "_epoch_key"]
            ).groupby(["_target_key", "_photometry_source"]).size()
            source_labels = {
                key: "; ".join(f"{source} ({int(count)})" for (_, source), count in rows.items())
                for key, rows in source_counts.groupby(level=0)
            }
            table["photometry_sources"] = table["_target_key"].map(source_labels).fillna("")
            points = points.sort_values("_epoch_hjd").drop_duplicates(
                ["_target_key", "_survey_key", "_band_key", "_epoch_key"], keep="first",
            )
            latest = points.sort_values("_epoch_hjd").drop_duplicates("_target_key", keep="last").set_index("_target_key")
            i_band = points["_band"].str.contains(r"(?<![a-z])i(?![a-z])", case=False, regex=True)
            latest_i = points.loc[i_band].sort_values("_epoch_hjd").drop_duplicates(
                "_target_key", keep="last",
            ).set_index("_target_key")
            latest.update(latest_i)
            latest_values = latest[["_magnitude", "_survey", "_band"]]
            table["last_magnitude"] = table["_target_key"].map(latest_values["_magnitude"])
            table["last_survey"] = table["_target_key"].map(latest_values["_survey"])
            table["last_filter"] = table["_target_key"].map(latest_values["_band"])

            event_params = table.set_index("_target_key")[["t0_hjd", "tE_days"]].copy()
            stage_points = source_points.drop_duplicates(
                ["_target_key", "_photometry_source", "_survey_key", "_band_key", "_epoch_key"]
            ).drop(columns=["t0_hjd", "tE_days"], errors="ignore").join(event_params, on="_target_key")
            stage_points["_stage"] = _classify_observation_stages(
                stage_points["_epoch_hjd"], stage_points["t0_hjd"], stage_points["tE_days"], config,
            )
            latest_stage_records.append(stage_points.loc[
                stage_points["_stage"].ne("unknown"), ["_target_key", "_epoch_hjd", "_stage"]
            ])
            is_casleo = _casleo_mask(stage_points)
            external = stage_points.loc[~is_casleo & stage_points["_stage"].ne("unknown")]
            external_counts = external.groupby(["_target_key", "_photometry_source", "_stage"]).size()
            for source in sorted(external["_photometry_source"].unique()):
                source_name = re.sub(r"[^a-z0-9]+", "_", str(source).casefold()).strip("_") or "unknown"
                for stage, _ in PRIORITY_TABLE_STAGES:
                    table[f"{source_name}_{stage}_points"] = [
                        int(external_counts.get((key, source, stage), 0)) if classifiable.iloc[index] else np.nan
                        for index, key in enumerate(table["_target_key"])
                    ]
    if "photometry_sources" not in table:
        table["photometry_sources"] = ""
    epochs = survey_epochs.copy() if survey_epochs is not None else pd.DataFrame()
    if not epochs.empty:
        name_column = "Target" if "Target" in epochs else "target_id"
        epochs["_target_key"] = epochs[name_column].astype(str).map(canonical_target_name)
        epochs["_provider"] = _coalesced_text(epochs, ("provider",)).str.upper()
        epochs["_epoch_hjd"] = _epoch_hjd(epochs)
        epochs["_exptime_s"] = _coalesced_numeric(epochs, ("exptime_s", "exposure_s")).fillna(0).clip(lower=0)
        params = table.set_index("_target_key")[["t0_hjd", "tE_days"]]
        epochs = epochs.join(params, on="_target_key")
        epochs["_stage"] = _classify_observation_stages(
            epochs["_epoch_hjd"], epochs["t0_hjd"], epochs["tE_days"], config,
        )
        latest_stage_records.append(epochs.loc[
            epochs["_stage"].ne("unknown"), ["_target_key", "_epoch_hjd", "_stage"]
        ])
        survey_counts = epochs.loc[epochs["_stage"].ne("unknown")].groupby(
            ["_target_key", "_provider", "_stage"]
        ).size()
        for source in sorted(epochs["_provider"].dropna().astype(str).str.casefold().unique()):
            source_name = re.sub(r"[^a-z0-9]+", "_", source).strip("_") or "unknown"
            for stage, _ in PRIORITY_TABLE_STAGES:
                table[f"{source_name}_{stage}_points"] = [
                    int(survey_counts.get((key, source.upper(), stage), 0)) if classifiable.iloc[index] else np.nan
                    for index, key in enumerate(table["_target_key"])
                ]

    source_columns = sorted({
        column for column in table.columns
        if re.fullmatch(r"[a-z0-9]+_(?:pre_baseline|rise|peak|fall|post_baseline)_points", column)
    })
    for source_column in source_columns:
        table[source_column] = table[source_column].where(classifiable, np.nan)
    table["stage"] = table["current_event_stage"].replace({"decline": "fall"})
    if "event_stage" in table:
        stage_aliases = {
            "pre-baseline": "pre_baseline", "pre_baseline": "pre_baseline",
            "rise": "rise", "subida": "rise", "peak": "peak",
            "decline": "fall", "fall": "fall", "bajada": "fall",
            "post-baseline": "post_baseline", "post_baseline": "post_baseline",
        }
        reported_stage = table["event_stage"].fillna("").astype(str).str.casefold().map(stage_aliases)
        use_reported = table["stage"].eq("unknown") & reported_stage.notna()
        table.loc[use_reported, "stage"] = reported_stage.loc[use_reported]
    if latest_stage_records:
        latest_stage = pd.concat(latest_stage_records, ignore_index=True).sort_values("_epoch_hjd").drop_duplicates(
            "_target_key", keep="last",
        ).set_index("_target_key")["_stage"]
        unknown_current = table["stage"].eq("unknown")
        table.loc[unknown_current, "stage"] = table.loc[unknown_current, "_target_key"].map(latest_stage).fillna("unknown")

    visibility = visibility_by_night.copy() if visibility_by_night is not None else pd.DataFrame()
    if not visibility.empty:
        if "target_id" not in visibility and "Target" in visibility:
            visibility = visibility.rename(columns={"Target": "target_id"})
        visibility["_target_key"] = visibility["target_id"].astype(str).map(canonical_target_name)
        visibility["_minutes"] = pd.to_numeric(visibility.get("observable_minutes"), errors="coerce").fillna(0)
        visibility["_altitude"] = pd.to_numeric(visibility.get("peak_altitude_deg"), errors="coerce")
        visibility["_airmass"] = 1 / np.sin(np.deg2rad(visibility["_altitude"].where(visibility["_altitude"].ge(40))))
        grouped = visibility.groupby("_target_key").agg(
            visible_minutes_above_40=("_minutes", "sum"), minimum_airmass=("_airmass", "min"),
        )
        table["visible_minutes_above_40"] = table["_target_key"].map(grouped["visible_minutes_above_40"])
        table["minimum_airmass"] = table["_target_key"].map(grouped["minimum_airmass"])
    else:
        table["visible_minutes_above_40"] = np.nan
        table["minimum_airmass"] = np.nan

    if "last_magnitude" not in table:
        table["last_magnitude"] = np.nan
        table["last_survey"] = ""
        table["last_filter"] = ""
    for column, default in (("priority_rank", np.nan),
                            ("visible_minutes_above_40", np.nan), ("minimum_airmass", np.nan)):
        if column not in table:
            table[column] = default
    columns = [
        "tom_source", "target_id", "event_subtype", "event_survey", "photometry_sources",
        "last_magnitude", "last_survey", "last_filter", "stage",
        "days_since_t0", "tE_days", "u0",
        "visible_minutes_above_40", "minimum_airmass",
    ]
    columns.extend(
        column for stage, _ in PRIORITY_TABLE_STAGES for column in source_columns
        if column.endswith(f"_{stage}_points")
    )
    if "priority_rank" in table:
        table = table.sort_values("priority_rank", kind="stable")
    return table.reindex(columns=columns).reset_index(drop=True)


@dataclass(frozen=True)
class PriorityConfig:
    """Explicit, reviewable cutoffs for the first rule-based implementation."""

    rise_significance_sigma: float = 3.0
    minimum_rise_points: int = 3
    rise_lookback_days: float = 90.0
    stale_cadence_factor: float = 2.0
    stale_minimum_days: float = 30.0
    planned_visit_interval_days: float = 7.0
    peak_half_width_t_e: float = 0.3
    event_half_width_t_e: float = 2.0
    magnitude_limits: tuple[tuple[str, float], ...] = (("HSH", 16.5), ("JS", 18.0))


def empty_coverage(events: pd.DataFrame) -> pd.DataFrame:
    """Create an empty LSST coverage table for a disabled query branch."""
    columns = [name for name in ("target_id", "ra_deg", "dec_deg", "survey") if name in events]
    result = events[columns].copy()
    result["has_visit_coverage"] = False
    result["has_coadd"] = False
    return result


def _numeric(frame: pd.DataFrame, names: tuple[str, ...], default: float = np.nan) -> pd.Series:
    value = pd.Series(default, index=frame.index, dtype=float)
    for name in names:
        if name in frame:
            direct = pd.to_numeric(frame[name], errors="coerce")
            parsed = pd.to_numeric(
                frame[name].astype(str).str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", expand=False),
                errors="coerce",
            )
            value = value.fillna(direct).fillna(parsed)
    return value


def _epoch_hjd(frame: pd.DataFrame) -> pd.Series:
    epoch = _numeric(frame, ("epoch_hjd", "hjd"))
    epoch = epoch.fillna(_numeric(frame, ("epoch_mjd", "mjd")) + 2400000.5)
    for column in ("observation_time", "Timestamp", "timestamp"):
        if column in frame:
            stamp = pd.to_datetime(frame[column], errors="coerce", utc=True)
            jd = stamp.map(lambda x: x.to_julian_date() if pd.notna(x) else np.nan)
            epoch = epoch.fillna(jd)
    return epoch


def _as_hjd(value: str | date | datetime | pd.Timestamp | None) -> float:
    stamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return float(stamp.tz_convert("UTC").to_julian_date())


def _classify_observation_stages(
    epochs: pd.Series,
    t0: pd.Series,
    te: pd.Series,
    config: PriorityConfig,
) -> pd.Series:
    """Assign phase labels to observation epochs when event parameters exist."""
    tau = (pd.to_numeric(epochs, errors="coerce") - pd.to_numeric(t0, errors="coerce")) / pd.to_numeric(te, errors="coerce")
    stages = pd.Series("unknown", index=epochs.index, dtype=object)
    stages.loc[tau.lt(-config.event_half_width_t_e)] = "pre_baseline"
    stages.loc[tau.ge(-config.event_half_width_t_e) & tau.lt(-config.peak_half_width_t_e)] = "rise"
    stages.loc[tau.abs().le(config.peak_half_width_t_e)] = "peak"
    stages.loc[tau.gt(config.peak_half_width_t_e) & tau.le(config.event_half_width_t_e)] = "fall"
    stages.loc[tau.gt(config.event_half_width_t_e)] = "post_baseline"
    return stages.where(np.isfinite(tau), "unknown")


def casleo_phase_coverage(
    targets: pd.DataFrame, survey_epochs: pd.DataFrame, *, config: PriorityConfig | None = None,
) -> pd.DataFrame:
    """Count usable HSH/JS image epochs by model phase, without calling them photometry."""
    config = config or PriorityConfig()
    if targets.empty or survey_epochs.empty:
        return pd.DataFrame(columns=["target_id", "hsh_n_images", "js_n_images"])
    names = "target_id" if "target_id" in survey_epochs else "Target"
    params = targets.copy()
    params["_key"] = params["target_id"].map(canonical_target_name)
    params["_t0"] = _to_hjd(_numeric(params, ("t0_hjd", "mop_t_0_hjd", "event_peak_hjd", "Tmax (HJD)")))
    params["_te"] = _numeric(params, ("tE_days", "mop_t_e_days", "event_duration_days", "tau"))
    params = params.drop_duplicates("_key").set_index("_key")
    epochs = survey_epochs.copy()
    epochs["_key"] = epochs[names].map(canonical_target_name)
    epochs["_provider"] = epochs["provider"].fillna("").astype(str).str.upper()
    epochs = epochs.loc[epochs["_provider"].isin(("HSH", "JS")) & epochs["_key"].isin(params.index)]
    if "usable" in epochs:
        epochs = epochs.loc[epochs["usable"].astype(str).str.casefold().isin(("true", "1", "yes"))]
    epochs["_epoch"] = _epoch_hjd(epochs)
    epochs["_t0"] = epochs["_key"].map(params["_t0"])
    epochs["_te"] = epochs["_key"].map(params["_te"])
    epochs["_phase"] = _classify_observation_stages(epochs["_epoch"], epochs["_t0"], epochs["_te"], config)
    rows = []
    for key, group in epochs.groupby("_key"):
        row = {"target_id": params.loc[key, "target_id"]}
        for provider in ("HSH", "JS"):
            subset = group.loc[group["_provider"].eq(provider)]
            row[f"{provider.lower()}_n_images"] = len(subset)
            for phase, _ in PRIORITY_TABLE_STAGES:
                row[f"{provider.lower()}_{phase}_n"] = int(subset["_phase"].eq(phase).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def _to_hjd(values: pd.Series) -> pd.Series:
    """Normalize JD/HJD and MJD-like peak epochs to the JD scale."""
    result = pd.to_numeric(values, errors="coerce")
    mjd = result.between(30_000, 100_000, inclusive="both")
    result = result.where(~mjd, result + 2_400_000.5)
    return result.where(result.between(2_000_000, 3_000_000, inclusive="both"))


def _first_text(row: pd.Series, names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if pd.notna(value) and str(value).strip().casefold() not in {"", "nan", "none", "<na>"}:
            return str(value).strip()
    return ""


def _coalesced_text(frame: pd.DataFrame, names: tuple[str, ...], default: str = "") -> pd.Series:
    """Choose the first non-empty value per row, even when columns are sparse."""
    value = pd.Series("", index=frame.index, dtype=object)
    for name in names:
        if name not in frame:
            continue
        candidate = frame[name]
        valid = candidate.notna() & candidate.astype(str).str.strip().ne("")
        missing = value.fillna("").astype(str).str.strip().eq("")
        value.loc[missing & valid] = candidate.loc[missing & valid]
    return value.fillna(default).astype(str)


def _coalesced_numeric(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    """Choose the first numeric value per row across sparse aliases."""
    value = pd.Series(np.nan, index=frame.index, dtype=float)
    for name in names:
        if name in frame:
            value = value.fillna(pd.to_numeric(frame[name], errors="coerce"))
    return value


def _joined_text(row: pd.Series, names: tuple[str, ...]) -> str:
    values = []
    for name in names:
        value = row.get(name)
        if pd.notna(value) and str(value).strip().casefold() not in {"", "nan", "none", "<na>"}:
            values.append(str(value).strip())
    return " ".join(values)


def _lens_magnification(u: float) -> float:
    if not np.isfinite(u) or u <= 0:
        return np.nan
    return (u * u + 2) / (u * np.sqrt(u * u + 4))


def _model_delta_mag(t0: float, te: float, u0: float, now: float, interval: float) -> float:
    if not (np.isfinite(t0) and np.isfinite(te) and te > 0 and np.isfinite(u0) and u0 > 0):
        return np.nan
    u_now = np.sqrt(u0**2 + ((now - t0) / te) ** 2)
    u_next = np.sqrt(u0**2 + ((now + interval - t0) / te) ** 2)
    a_now, a_next = _lens_magnification(u_now), _lens_magnification(u_next)
    return float(abs(2.5 * np.log10(a_next / a_now)))


def _rise_evidence(group: pd.DataFrame, now: float, config: PriorityConfig) -> tuple[float, int]:
    """Return strongest brightening slope significance and its point count."""
    if group.empty:
        return np.nan, 0
    data = group.copy()
    data["epoch"] = _epoch_hjd(data)
    data["mag"] = _coalesced_numeric(data, ("magnitude", "Magnitude", "mag"))
    data = data.loc[data["epoch"].between(now - config.rise_lookback_days, now) & data["mag"].notna()].copy()
    if data.empty:
        return np.nan, 0
    data["band_key"] = _coalesced_text(data, ("band", "Filter", "filter")).str.upper()
    data["survey_key"] = _coalesced_text(data, ("survey", "Source", "provider"), "unknown").str.upper()
    strongest, strongest_n = np.nan, 0
    for _, curve in data.groupby(["survey_key", "band_key"], dropna=False):
        curve = curve.sort_values("epoch")
        if len(curve) < config.minimum_rise_points or curve["epoch"].nunique() < config.minimum_rise_points:
            continue
        x = curve["epoch"].to_numpy(dtype=float)
        y = curve["mag"].to_numpy(dtype=float)
        x = x - x.mean()
        err = _coalesced_numeric(curve, ("magnitude_err", "Magnitude Error", "magnitude_error", "Error")).to_numpy(dtype=float)
        valid_err = np.isfinite(err) & (err > 0)
        weights = np.where(valid_err, 1.0 / np.square(err), 1.0)
        design = np.column_stack((np.ones(len(x)), x))
        try:
            covariance = np.linalg.inv(design.T @ (weights[:, None] * design))
            beta = covariance @ (design.T @ (weights * y))
            residual = y - design @ beta
            dof = max(len(y) - 2, 1)
            scale = max(float(np.sum(weights * residual**2) / dof), 1.0 if valid_err.any() else 0.0)
            slope_error = np.sqrt(max(covariance[1, 1] * scale, 0.0))
            significance = float(-beta[1] / slope_error) if slope_error > 0 else np.nan
        except np.linalg.LinAlgError:
            continue
        if np.isfinite(significance) and (not np.isfinite(strongest) or significance > strongest):
            strongest, strongest_n = significance, len(curve)
    return strongest, strongest_n


def classify_event_stage(t0_hjd: float, tE_days: float, now_hjd: float, config: PriorityConfig) -> tuple[str, float]:
    """Return event phase and tau=(now_hjd-t0_hjd)/tE_days using configured boundaries."""
    if not (np.isfinite(t0_hjd) and np.isfinite(tE_days) and tE_days > 0):
        return "unknown", np.nan
    tau = (now_hjd - t0_hjd) / tE_days
    if tau < -config.event_half_width_t_e:
        stage = "pre_baseline"
    elif tau < -config.peak_half_width_t_e:
        stage = "rise"
    elif tau <= config.peak_half_width_t_e:
        stage = "peak"
    elif tau <= config.event_half_width_t_e:
        stage = "decline"
    else:
        stage = "post_baseline"
    return stage, float(tau)


def _casleo_mask(observations: pd.DataFrame) -> pd.Series:
    labels = pd.Series("", index=observations.index, dtype=str)
    for column in ("survey", "provider", "access_provider", "source", "Source"):
        if column in observations:
            labels = labels + " " + observations[column].fillna("").astype(str)
    return labels.str.contains(r"\b(?:HSH|JS|CASLEO)\b", case=False, regex=True)


def _is_ended_event(row: pd.Series) -> tuple[bool, str]:
    status = _joined_text(row, ("event_state", "event_status", "status")).casefold()
    if any(word in status for word in ("finished", "terminated", "completed", "ended", "event over")):
        return True, f"estado reportado: {status}"
    return False, ""


def criterion_ended_event(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Discard events explicitly reported as completed."""
    ended, reason = _is_ended_event(row)
    return _finished_event_decision(metrics, reason) if ended else None


def _finished_event_decision(metrics: dict[str, Any], reason: str) -> tuple[str, str]:
    """An image epoch does not establish a measured CASLEO baseline."""
    if metrics["n_casleo_observations"] <= 0:
        return "DESCARTAR", reason
    measured = set(metrics.get("casleo_photometry_phases", ()))
    if measured & {"pre_baseline", "post_baseline"}:
        return "DESCARTAR", f"{reason}; baseline CASLEO medida"
    images = set(metrics.get("casleo_image_phases", ()))
    if images & {"pre_baseline", "post_baseline"}:
        return "REVISAR", f"{reason}; hay imágenes CASLEO en baseline, falta verificar su fotometría"
    return "OPORTUNISTA", f"{reason}; seguimiento CASLEO sin baseline medida"


def criterion_supported_event(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Require a microlensing label, model, or configured significant rise."""
    label = _joined_text(row, ("event_type", "classification", "object_class", "class", "survey", "target_id", "event_class")).casefold()
    labeled = any(term in label for term in ("microlens", "microlensing", "ogle-20", "blg-"))
    has_model = np.isfinite(metrics["t0_hjd"]) and np.isfinite(metrics["tE_days"]) and metrics["tE_days"] > 0
    rising = (metrics["rise_significance_sigma"] >= config.rise_significance_sigma
              and metrics["rise_points"] >= config.minimum_rise_points)
    if not (labeled or has_model or rising):
        return "REVISAR", "faltan etiqueta o datos suficientes para confirmar microlensing"
    return None


def criterion_post_baseline(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Handle events after baseline, unless an observing question remains."""
    if metrics["event_stage"] != "post_baseline":
        return None
    question = _first_text(row, ("planning_question", "observation_question", "priority_question"))
    if question:
        return "OPORTUNISTA", f"pregunta explícita sobre baseline: {question}"
    if metrics["n_casleo_observations"] > 0:
        return _finished_event_decision(metrics, "modelo ubica el evento en baseline posterior")
    if metrics["stale_external_data"]:
        return "DIAGNÓSTICO", "modelo predice baseline posterior, pero falta fotometría externa reciente"
    return "DESCARTAR", "modelo ubica el evento en baseline posterior"


def criterion_casleo_continuity(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Prioritize new phases or a modeled change for an already observed event."""
    stage = metrics["event_stage"]
    if metrics["n_casleo_observations"] <= 0 or stage not in {"rise", "peak", "decline"}:
        return None
    detectable = (np.isfinite(metrics["expected_delta_mag"])
                  and np.isfinite(metrics["detectable_delta_mag"])
                  and metrics["expected_delta_mag"] >= metrics["detectable_delta_mag"])
    if detectable or stage not in metrics["casleo_phases"]:
        return "CONTINUIDAD", "CASLEO tiene datos y la próxima visita mide cambio o añade fase"
    return None


def criterion_casleo_stale_photometry(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Flag CASLEO-followed events when external photometry is stale."""
    if metrics["n_casleo_observations"] > 0 and metrics["stale_external_data"]:
        return "DIAGNÓSTICO", "evento plausible con brecha en fotometría externa reciente"
    return None


def criterion_casleo_missing_uncertainty(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Request review when CASLEO has an active event but no error estimate."""
    active = metrics["event_stage"] in {"rise", "peak", "decline"}
    if metrics["n_casleo_observations"] > 0 and active and not np.isfinite(metrics["detectable_delta_mag"]):
        return "REVISAR", "hay cobertura CASLEO, pero falta una incertidumbre fotométrica para probar cambio medible"
    return None


def criterion_casleo_observing_question(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Keep a CASLEO target when an explicit observing question remains."""
    question = _first_text(row, ("planning_question", "observation_question", "priority_question"))
    if metrics["n_casleo_observations"] > 0 and question:
        return "OPORTUNISTA", f"pregunta explícita: {question}"
    return None


def criterion_casleo_no_added_information(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Discard an already observed event when no new phase or change is expected."""
    if metrics["n_casleo_observations"] > 0:
        return "DESCARTAR", "el seguimiento no añade cambio medible ni fase nueva"
    return None


def criterion_rising_event(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Assess rising events using configured significance and point thresholds."""
    if metrics["n_casleo_observations"] > 0 or metrics["event_stage"] != "rise":
        return None
    significant = (metrics["rise_significance_sigma"] >= config.rise_significance_sigma
                   and metrics["rise_points"] >= config.minimum_rise_points)
    if significant:
        return "NUEVO-RISE", "subida reciente significativa y etapa de ascenso"
    if metrics["stale_external_data"]:
        return "DIAGNÓSTICO", "señal previa plausible, pero faltan datos recientes"
    return "REVISAR", "etapa de ascenso sin significancia suficiente en datos recientes"


def criterion_peak_event(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Identify a plausible event inside the configured peak window."""
    if metrics["n_casleo_observations"] == 0 and metrics["event_stage"] == "peak":
        return "NUEVO-PEAK", "evento plausible cerca del máximo"
    return None


def criterion_stale_photometry(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Diagnose stale non-CASLEO events outside rise and peak."""
    stage = metrics["event_stage"]
    if metrics["n_casleo_observations"] == 0 and stage not in {"rise", "peak"} and metrics["stale_external_data"]:
        return "DIAGNÓSTICO", "evento plausible con fotometría externa desactualizada"
    return None


def criterion_declining_event(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Keep declining events as lower-priority opportunities."""
    if metrics["n_casleo_observations"] > 0 or metrics["event_stage"] != "decline":
        return None
    question = _first_text(row, ("planning_question", "observation_question", "priority_question"))
    reason = f"pregunta explícita: {question}" if question else "evento plausible en descenso; prioridad menor que subida o máximo"
    return "OPORTUNISTA", reason


def criterion_unknown_event_stage(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str] | None:
    """Request review when the event phase cannot be located from t0/tE."""
    if metrics["n_casleo_observations"] == 0 and metrics["event_stage"] == "unknown":
        return "REVISAR", "faltan parámetros t0/tE para ubicar la etapa del evento"
    return None


def criterion_fallback(metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str]:
    """Assign review when no enabled or applicable criterion defines a category."""
    return "REVISAR", "fase actual fuera de las ventanas priorizadas"


def _classify(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str]:
    """Apply named criteria in order; the first matching rule sets the category."""
    checks = (
        lambda: criterion_ended_event(row, metrics, config),
        lambda: criterion_supported_event(row, metrics, config),
        lambda: criterion_post_baseline(row, metrics, config),
    )
    for check in checks:
        result = check()
        if result is not None:
            return result

    if metrics["n_casleo_observations"] > 0:
        checks = (
            lambda: criterion_casleo_continuity(metrics, config),
            lambda: criterion_casleo_stale_photometry(metrics, config),
            lambda: criterion_casleo_missing_uncertainty(metrics, config),
            lambda: criterion_casleo_observing_question(row, metrics, config),
            lambda: criterion_casleo_no_added_information(metrics, config),
        )
        for check in checks:
            result = check()
            if result is not None:
                return result

    for criterion in (criterion_rising_event, criterion_peak_event, criterion_stale_photometry):
        result = criterion(metrics, config)
        if result is not None:
            return result
    result = criterion_declining_event(row, metrics, config)
    if result is not None:
        return result
    result = criterion_unknown_event_stage(metrics, config)
    return result if result is not None else criterion_fallback(metrics, config)


def build_priority_evidence(
    targets: pd.DataFrame,
    observations: pd.DataFrame,
    visibility_summary: pd.DataFrame | None = None,
    *,
    casleo_coverage: pd.DataFrame | None = None,
    as_of: str | date | datetime | pd.Timestamp | None = None,
    config: PriorityConfig | None = None,
) -> pd.DataFrame:
    """Calculate per-target evidence without applying priority criteria or ranking.

    All input targets are returned, including those with no usable photometry;
    ``evaluate_priority`` applies the no-photometry exclusion afterward.
    """
    config = config or PriorityConfig()
    as_of_stamp = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if as_of_stamp.tzinfo is None:
        as_of_stamp = as_of_stamp.tz_localize("UTC")
    as_of_utc = as_of_stamp.tz_convert("UTC")
    if targets is None or targets.empty:
        return pd.DataFrame(columns=["target_id"])
    if "target_id" not in targets:
        if "Target" not in targets:
            raise ValueError("Targets must contain target_id or Target.")
        targets = targets.rename(columns={"Target": "target_id"})
    result = targets.copy()
    result["target_id"] = result["target_id"].astype(str)
    if result["target_id"].duplicated().any():
        raise ValueError("Targets must be deduplicated to one row per target_id before priority assessment.")

    now = _as_hjd(as_of_utc)
    obs = observations.copy() if observations is not None else pd.DataFrame()
    if not obs.empty:
        if "target_id" not in obs:
            raise ValueError("Observations must contain target_id.")
        obs["target_id"] = obs["target_id"].astype(str)
        obs["_epoch_hjd"] = _epoch_hjd(obs)
        obs["_magnitude"] = _coalesced_numeric(obs, ("magnitude", "Magnitude", "mag"))
        obs = obs.loc[obs["_epoch_hjd"].notna() & obs["_magnitude"].notna()].copy()
        if not obs.empty:
            survey = _coalesced_text(obs, ("survey", "Source", "provider"), "unknown")
            band = _coalesced_text(obs, ("band", "Filter", "filter"))
            obs["_survey_key"] = survey.fillna("unknown").astype(str).str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            obs["_band_key"] = band.fillna("").astype(str).str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            obs["_epoch_key"] = obs["_epoch_hjd"].round(6)
            method = obs.get("measurement_method", pd.Series("", index=obs.index)).astype(str)
            obs["_method_rank"] = method.map({"dia_forced_catalog": 0, "dia_source_catalog": 1}).fillna(0)
            obs = obs.sort_values("_method_rank").drop_duplicates(
                ["target_id", "_survey_key", "_band_key", "_epoch_key"], keep="first"
            ).drop(columns=["_survey_key", "_band_key", "_epoch_key", "_method_rank"])
    else:
        obs = pd.DataFrame(columns=["target_id", "_epoch_hjd", "_magnitude"])

    if visibility_summary is not None and not visibility_summary.empty:
        vis = visibility_summary.copy()
        vis["target_id"] = vis["target_id"].astype(str)
        overlap = (set(result.columns) & set(vis.columns)) - {"target_id"}
        # Current-run visibility supersedes provider metadata for other windows.
        result = result.drop(columns=list(overlap)).merge(vis, on="target_id", how="left")
    coverage_by_id: dict[str, pd.Series] = {}
    if casleo_coverage is not None and not casleo_coverage.empty:
        coverage = casleo_coverage.copy()
        if "target_id" not in coverage and "Target" in coverage:
            coverage = coverage.rename(columns={"Target": "target_id"})
        if "target_id" in coverage:
            coverage["target_id"] = coverage["target_id"].astype(str)
            coverage_by_id = {str(row["target_id"]): row for _, row in coverage.drop_duplicates("target_id").iterrows()}

    rows: list[dict[str, Any]] = []
    groups = {key: frame for key, frame in obs.groupby("target_id", sort=False)} if not obs.empty else {}
    limit_map = dict(config.magnitude_limits)
    for _, target in result.iterrows():
        target_id = str(target["target_id"])
        curve = groups.get(target_id, obs.iloc[0:0]).copy()
        t0 = float(_numeric(pd.DataFrame([target]), (
            "t0_hjd", "mop_t_0_hjd", "mop_t0_hjd", "t_0_HJD", "t_0_hjd",
            "event_peak_hjd", "peak_hjd", "Tmax (HJD)",
        )).iloc[0])
        te = float(_numeric(pd.DataFrame([target]), (
            "tE_days", "mop_t_e_days", "mop_te_days", "t_E_days", "t_e_days",
            "event_duration_days", "tau",
        )).iloc[0])
        u0 = float(_numeric(pd.DataFrame([target]), ("u0", "mop_u_0", "mop_u0", "u_0", "Umin")).iloc[0])
        stage, tau = classify_event_stage(t0, te, now, config)
        rise_sigma, rise_points = _rise_evidence(curve, now, config)
        casleo = _casleo_mask(curve) if not curve.empty else pd.Series(False, index=curve.index)
        casleo_curve = curve.loc[casleo]
        external = curve.loc[~casleo].copy()
        latest_external = float(external["_epoch_hjd"].max()) if not external.empty else np.nan
        days_since_external = now - latest_external if np.isfinite(latest_external) else np.nan
        external["_survey_key"] = _coalesced_text(external, ("survey", "Source", "provider"), "unknown")
        latest_external_survey = ""
        cadence = np.nan
        external_survey_names: list[str] = []
        if not external.empty:
            survey_latest = external.groupby("_survey_key")["_epoch_hjd"].max().sort_values()
            external_survey_names = [str(name) for name in survey_latest.index]
            latest_external_survey = str(survey_latest.index[-1])
            latest_curve = external.loc[external["_survey_key"].eq(latest_external_survey)]
            external_times = np.sort(latest_curve["_epoch_hjd"].dropna().unique())
            cadence = float(np.median(np.diff(external_times))) if len(external_times) >= 3 else np.nan
        cadence_threshold = config.stale_cadence_factor * cadence if np.isfinite(cadence) and cadence > 0 else 0.0
        stale_threshold = max(config.stale_minimum_days, cadence_threshold)
        stale = bool(not np.isfinite(latest_external) or days_since_external > stale_threshold)
        phases: set[str] = set()
        photometry_phases: set[str] = set()
        image_phases: set[str] = set()
        if np.isfinite(t0) and np.isfinite(te) and te > 0 and not casleo_curve.empty:
            photometry_phases.update({"fall" if phase == "decline" else phase for phase in
                                      (classify_event_stage(t0, te, float(epoch), config)[0] for epoch in casleo_curve["_epoch_hjd"])})
            phases.update(photometry_phases)
        coverage_row = coverage_by_id.get(target_id)
        coverage_count = 0
        if coverage_row is not None:
            for phase in ("pre_baseline", "rise", "peak", "fall", "post_baseline"):
                phase_count = sum(max([
                    int(pd.to_numeric(value, errors="coerce") or 0)
                    for value in (coverage_row.get(f"{survey}_{phase}_n"), coverage_row.get(f"{survey}_{phase}_points"))
                    if pd.notna(value)
                ] or [0]) for survey in ("hsh", "js"))
                if phase_count > 0:
                    phases.add(phase)
                    image_phases.add(phase)
                    coverage_count += phase_count
            if not coverage_count:
                coverage_count = sum(int(pd.to_numeric(coverage_row.get(f"{survey}_n_images", 0), errors="coerce") or 0)
                                     for survey in ("hsh", "js"))
        casleo_count = max(len(casleo_curve), coverage_count)
        expected_delta = _model_delta_mag(t0, te, u0, now, config.planned_visit_interval_days)
        errors = _coalesced_numeric(casleo_curve, ("magnitude_err", "Magnitude Error", "magnitude_error", "Error")).dropna()
        detectable_delta = float(np.sqrt(2) * errors.median()) if not errors.empty else np.nan
        model_mag = np.nan
        target_mag = _numeric(pd.DataFrame([target]), ("magnitude_hsh", "magnitude_last", "magnitude", "mag_now")).iloc[0]
        if np.isfinite(target_mag):
            model_mag = float(target_mag)
        elif not curve.empty:
            latest = curve.sort_values("_epoch_hjd").iloc[-1]
            model_mag = float(latest["_magnitude"])
        metric = {
            "t0_hjd": t0, "tE_days": te, "u0": u0, "event_stage": stage,
            "event_model_parameters_available": bool(np.isfinite(t0) and np.isfinite(te) and te > 0),
            "tau_tE": tau, "days_to_peak": t0 - now if np.isfinite(t0) else np.nan,
            "expected_delta_mag": expected_delta, "detectable_delta_mag": detectable_delta,
            "rise_significance_sigma": rise_sigma, "rise_points": rise_points,
            "n_photometry": len(curve), "n_casleo_observations": casleo_count,
            "n_casleo_photometry": len(casleo_curve),
            "n_casleo_coverage_points": coverage_count,
            "casleo_phases": sorted(phases), "days_since_external_photometry": days_since_external,
            "casleo_photometry_phases": sorted(photometry_phases),
            "casleo_image_phases": sorted(image_phases),
            "last_external_survey": latest_external_survey,
            "external_median_cadence_days": cadence,
            "external_stale_threshold_days": stale_threshold,
            "external_surveys": external_survey_names,
            "n_external_surveys": len(external_survey_names),
            "stale_external_data": stale,
            "magnitude_assessment": model_mag,
        }
        if np.isfinite(t0) and np.isfinite(te) and te > 0 and not curve.empty:
            tau_obs = (curve["_epoch_hjd"] - t0) / te
            obs_stage = pd.Series("unknown", index=curve.index, dtype=object)
            obs_stage.loc[tau_obs.lt(-config.event_half_width_t_e)] = "pre_baseline"
            obs_stage.loc[tau_obs.ge(-config.event_half_width_t_e) & tau_obs.lt(-config.peak_half_width_t_e)] = "rise"
            obs_stage.loc[tau_obs.ge(-config.peak_half_width_t_e) & tau_obs.le(config.peak_half_width_t_e)] = "peak"
            obs_stage.loc[tau_obs.gt(config.peak_half_width_t_e) & tau_obs.le(config.event_half_width_t_e)] = "fall"
            obs_stage.loc[tau_obs.gt(config.event_half_width_t_e)] = "post_baseline"
            survey_labels = _coalesced_text(curve, ("survey", "Source", "provider"), "unknown")
            survey_keys = survey_labels.str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")
            counts = pd.DataFrame({"survey": survey_keys, "phase": obs_stage}).groupby(["survey", "phase"]).size()
            for (survey_key, phase), count in counts.items():
                if phase != "unknown":
                    metric[f"{survey_key}_{phase}_points"] = int(count)
        metric["target_id"] = target_id
        metric["maximum_magnification"] = _lens_magnification(u0)
        for instrument, limit in limit_map.items():
            metric[f"passes_{instrument.lower()}_magnitude_limit"] = bool(model_mag <= limit) if np.isfinite(model_mag) else pd.NA
        rows.append(metric)

    assessment = pd.DataFrame(rows)
    overlap = (set(result.columns) & set(assessment.columns)) - {"target_id"}
    result = result.drop(columns=list(overlap))
    return result.merge(assessment, on="target_id", how="left")


def evaluate_priority(
    targets: pd.DataFrame,
    observations: pd.DataFrame,
    visibility_summary: pd.DataFrame | None = None,
    *,
    casleo_coverage: pd.DataFrame | None = None,
    as_of: str | date | datetime | pd.Timestamp | None = None,
    config: PriorityConfig | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Apply ordered priority criteria to prepared photometric evidence."""
    config = config or PriorityConfig()
    as_of_stamp = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if as_of_stamp.tzinfo is None:
        as_of_stamp = as_of_stamp.tz_localize("UTC")
    as_of_utc = as_of_stamp.tz_convert("UTC")
    if targets is None or targets.empty:
        empty_columns = [
            "target_id", "priority_category", "priority_rank", "category_rank",
            "assessment_status", "priority_reason", "event_stage",
            "event_model_parameters_available", "t0_hjd", "tE_days",
            "rise_significance_sigma", "expected_delta_mag", "detectable_delta_mag",
            "n_photometry", "n_casleo_observations", "n_casleo_photometry",
            "n_casleo_coverage_points", "casleo_phases", "days_since_external_photometry",
            "last_external_survey", "external_median_cadence_days",
            "external_stale_threshold_days", "stale_external_data",
            "magnitude_assessment", "n_visible_nights", "best_observable_minutes",
        ]
        result = pd.DataFrame(columns=empty_columns)
        if output_dir is not None:
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            result.to_csv(output / "priority_assessment.csv", index=False)
            run_config = {"as_of_utc": as_of_utc.isoformat(), "priority_config": asdict(config), "category_order": CATEGORY_ORDER}
            (output / "priority_config.json").write_text(json.dumps(run_config, indent=2) + "\n")
        return result

    evidence = build_priority_evidence(
        targets, observations, visibility_summary,
        casleo_coverage=casleo_coverage, as_of=as_of_utc, config=config,
    )
    no_photometry = pd.to_numeric(evidence["n_photometry"], errors="coerce").fillna(0).eq(0)
    if no_photometry.any():
        discarded_ids = set(evidence.loc[no_photometry, "target_id"].astype(str))
        if output_dir is not None:
            discarded = evidence.loc[no_photometry, [column for column in targets.columns if column in evidence]].copy()
            discarded["discard_reason"] = "No se encontró fotometría utilizable en las fuentes consultadas."
            if "photometry_requested" in discarded:
                skipped = ~discarded["photometry_requested"].fillna(False).astype(bool)
                discarded.loc[skipped, "discard_reason"] = "Fuera del alcance de fotometría solicitado; no se consultaron curvas."
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            discarded.to_csv(Path(output_dir) / "priority_discarded_no_photometry.csv", index=False)
        evidence = evidence.loc[~evidence["target_id"].astype(str).isin(discarded_ids)].copy()
    elif output_dir is not None:
        discarded_path = Path(output_dir) / "priority_discarded_no_photometry.csv"
        discarded_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=[*targets.columns, "discard_reason"]).to_csv(discarded_path, index=False)

    rows = []
    for _, target in evidence.iterrows():
        metrics = target.to_dict()
        category, reason = _classify(target, metrics, config)
        metrics.update({"priority_category": category, "priority_reason": reason})
        metrics["category_order"] = CATEGORY_ORDER[category]
        tau = metrics.get("tau_tE", np.nan)
        rise_sigma = metrics.get("rise_significance_sigma", np.nan)
        expected_delta = metrics.get("expected_delta_mag", np.nan)
        detectable_delta = metrics.get("detectable_delta_mag", np.nan)
        days_since_external = metrics.get("days_since_external_photometry", np.nan)
        model_mag = metrics.get("magnitude_assessment", np.nan)
        metrics["_rank_primary"] = {
            "NUEVO-RISE": -rise_sigma if np.isfinite(rise_sigma) else np.inf,
            "NUEVO-PEAK": abs(tau) if np.isfinite(tau) else np.inf,
            "CONTINUIDAD": -(expected_delta / detectable_delta) if np.isfinite(expected_delta) and np.isfinite(detectable_delta) and detectable_delta > 0 else np.inf,
            "DIAGNÓSTICO": -days_since_external if np.isfinite(days_since_external) else np.inf,
            "OPORTUNISTA": model_mag if np.isfinite(model_mag) else np.inf,
            "REVISAR": -rise_sigma if np.isfinite(rise_sigma) else np.inf,
            "DESCARTAR": 0,
        }[category]
        rows.append(metrics)

    assessment = pd.DataFrame(rows)
    if assessment.empty:
        assessment = evidence.iloc[0:0].copy()
        assessment["priority_category"] = pd.Series(dtype=object)
        assessment["priority_reason"] = pd.Series(dtype=object)
        assessment["category_order"] = pd.Series(dtype=float)
        assessment["_rank_primary"] = pd.Series(dtype=float)
    assessment = assessment.sort_values(["category_order", "_rank_primary", "target_id"], kind="stable").reset_index(drop=True)
    assessment["category_rank"] = assessment.groupby("priority_category", sort=False).cumcount() + 1
    assessment["priority_rank"] = np.arange(1, len(assessment) + 1)
    assessment["assessment_status"] = np.where(assessment["priority_category"].eq("REVISAR"), "review", "classified")
    assessment = assessment.drop(columns=["_rank_primary"])
    result = assessment.sort_values("priority_rank", kind="stable").reset_index(drop=True)
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        result.to_csv(output / "priority_assessment.csv", index=False)
        run_config = {"as_of_utc": as_of_utc.isoformat(), "priority_config": asdict(config), "category_order": CATEGORY_ORDER}
        (output / "priority_config.json").write_text(json.dumps(run_config, indent=2) + "\n")
    return result
