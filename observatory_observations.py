"""Summaries of astrometrically processed observatory images by microlensing event."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


HSH_REQUIRED_COLUMNS = {
    "object", "imagetyp", "astromet", "obj_stat", "clmatch", "mjd-obs", "filter", "exptime", "airmass",
}
HSH_SUMMARY_COLUMNS = [
    "Target", "hsh_n_astrometric", "hsh_n_target_in_frame", "hsh_n_clmatch",
    "hsh_n_usable", "hsh_exptime_s", "hsh_target_in_frame_fraction",
    "hsh_clmatch_fraction", "hsh_usable_fraction", "hsh_airmass_median", "hsh_airmass_p90",
    "hsh_quality_status", "hsh_quality_scope", "hsh_pre_baseline_n",
    "hsh_rise_n", "hsh_peak_n", "hsh_fall_n", "hsh_post_baseline_n",
    "hsh_unknown_phase_n",
]


def canonical_target_name(value: object) -> str:
    """Normalize common event-name spelling variants for catalogue matching."""
    name = str(value).strip().upper().replace("_", "-")
    name = re.sub(r"\s+", "", name)
    # OGLE transient alerts appear as both GD and DG in upstream catalogues.
    name = re.sub(r"(?<=-)(DG|GD)(?=-)", "GD", name)
    return re.sub(r"[^A-Z0-9]", "", name)


def _numeric_parameter(values: pd.Series) -> pd.Series:
    """Extract the nominal numerical value from MOP ``value∓uncertainty`` fields."""
    return pd.to_numeric(
        values.astype(str).str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", expand=False),
        errors="coerce",
    )


def _short_filter(values: pd.Series) -> pd.Series:
    """Convert HSH filter labels such as ``(5) I`` to compact band names."""
    return values.astype(str).str.extract(r"([A-Za-z])\s*$", expand=False).str.upper()


def load_hsh_astrometry_catalog(path: str | Path) -> pd.DataFrame:
    """Load one row per HSH science image with a successful WCS solution.

    The collection contains entries from several processing stages. Selecting
    ``imagetyp=object`` and ``astromet=yes`` retains the astrometric products
    and avoids counting the corresponding raw/intermediate image repeatedly.
    """
    path = Path(path)
    available = pd.read_csv(path, nrows=0).columns
    missing = HSH_REQUIRED_COLUMNS - set(available)
    if missing:
        raise ValueError(f"HSH catalogue is missing columns: {sorted(missing)}")
    optional = [name for name in ("filename", "objname", "crval1", "crval2") if name in available]
    columns = sorted(HSH_REQUIRED_COLUMNS | set(optional))
    data = pd.read_csv(path, usecols=columns, low_memory=False)
    science = data[
        data["imagetyp"].astype(str).str.strip().str.lower().eq("object")
        & data["astromet"].astype(str).str.strip().str.lower().eq("yes")
    ].copy()
    science["source_target"] = science["object"].where(
        science["object"].notna(), science.get("objname")
    ).astype(str)
    science = science[~science["source_target"].str.upper().isin({"", "NAN", "NO_OBJECT"})]
    science["target_key"] = science["source_target"].map(canonical_target_name)
    science["mjd"] = pd.to_numeric(science["mjd-obs"], errors="coerce")
    science["exptime_s"] = pd.to_numeric(science["exptime"], errors="coerce")
    science["airmass_value"] = pd.to_numeric(science["airmass"], errors="coerce")
    science["band"] = _short_filter(science["filter"])
    science["ra_deg"] = pd.to_numeric(science.get("crval1"), errors="coerce")
    science["dec_deg"] = pd.to_numeric(science.get("crval2"), errors="coerce")
    science["target_in_frame"] = science["obj_stat"].astype(str).str.upper().eq("OK")
    science["catalogue_match"] = science["clmatch"].fillna(False).astype(bool)
    science["usable"] = science["target_in_frame"] & science["catalogue_match"]
    return science.reset_index(drop=True)


def assign_microlensing_stage(
    observations: pd.DataFrame,
    parameters: pd.DataFrame,
    *,
    peak_half_width_t_e: float = 0.3,
    event_half_width_t_e: float = 2.0,
) -> pd.DataFrame:
    """Classify observations as baseline, rise, peak, or fall.

    The default boundaries are expressed in Einstein times: peak is
    ``|t-t0|/tE <= 0.3`` and the event window extends to ``2 tE`` on either
    side. Observations before and after that window are kept as separate
    baseline columns. Invalid or absent MOP parameters yield ``unknown``.
    """
    if peak_half_width_t_e <= 0 or event_half_width_t_e <= peak_half_width_t_e:
        raise ValueError("Require 0 < peak_half_width_t_e < event_half_width_t_e.")
    required = {"Target", "mop_t_0_hjd", "mop_t_e_days"}
    missing = required - set(parameters.columns)
    if missing:
        raise ValueError(f"Microlensing parameters are missing columns: {sorted(missing)}")
    params = parameters[["Target", "mop_t_0_hjd", "mop_t_e_days"]].copy()
    params["target_key"] = params["Target"].map(canonical_target_name)
    params["t0_hjd"] = _numeric_parameter(params["mop_t_0_hjd"])
    params["t_e_days"] = _numeric_parameter(params["mop_t_e_days"])
    params = params.drop_duplicates("target_key", keep="last")
    result = observations.merge(params[["target_key", "t0_hjd", "t_e_days"]], on="target_key", how="left")
    tau = (result["mjd"] + 2400000.5 - result["t0_hjd"]) / result["t_e_days"]
    valid = np.isfinite(tau) & np.isfinite(result["t_e_days"]) & (result["t_e_days"] > 0)
    result["microlensing_stage"] = "unknown"
    result.loc[valid & (tau < -event_half_width_t_e), "microlensing_stage"] = "pre_baseline"
    result.loc[valid & (tau >= -event_half_width_t_e) & (tau < -peak_half_width_t_e), "microlensing_stage"] = "rise"
    result.loc[valid & (tau.abs() <= peak_half_width_t_e), "microlensing_stage"] = "peak"
    result.loc[valid & (tau > peak_half_width_t_e) & (tau <= event_half_width_t_e), "microlensing_stage"] = "fall"
    result.loc[valid & (tau > event_half_width_t_e), "microlensing_stage"] = "post_baseline"
    return result


def summarize_hsh_observations(
    targets: pd.DataFrame,
    image_catalog_path: str | Path,
    *,
    peak_half_width_t_e: float = 0.3,
    event_half_width_t_e: float = 2.0,
) -> pd.DataFrame:
    """Summarize HSH astrometry products for selected microlensing targets.

    ``hsh_quality_scope`` deliberately states that this is not a photometric
    quality score: the input has image/WCS metadata but no measured target
    flux uncertainties or FWHM values.
    """
    if "Target" not in targets:
        raise ValueError("Targets are missing the 'Target' column.")
    observations = assign_microlensing_stage(
        load_hsh_astrometry_catalog(image_catalog_path), targets,
        peak_half_width_t_e=peak_half_width_t_e,
        event_half_width_t_e=event_half_width_t_e,
    )
    target_index = targets[["Target"]].drop_duplicates().copy()
    target_index["target_key"] = target_index["Target"].map(canonical_target_name)
    selected = observations.merge(target_index[["Target", "target_key"]], on="target_key", how="inner")
    rows: list[dict] = []
    stages = ["pre_baseline", "rise", "peak", "fall", "post_baseline", "unknown"]
    for target, group in selected.groupby("Target", sort=False):
        usable = group[group["usable"]]
        astrometric = len(group)
        usable_count = len(usable)
        record = {
            "Target": target,
            "hsh_n_astrometric": astrometric,
            "hsh_n_target_in_frame": int(group["target_in_frame"].sum()),
            "hsh_n_clmatch": int(group["catalogue_match"].sum()),
            "hsh_n_usable": usable_count,
            "hsh_exptime_s": float(usable["exptime_s"].sum(min_count=1)) if usable_count else 0.0,
            "hsh_target_in_frame_fraction": float(group["target_in_frame"].mean()) if astrometric else np.nan,
            "hsh_clmatch_fraction": float(group["catalogue_match"].mean()) if astrometric else np.nan,
            "hsh_usable_fraction": float(usable_count / astrometric) if astrometric else np.nan,
            "hsh_airmass_median": usable["airmass_value"].median(),
            "hsh_airmass_p90": usable["airmass_value"].quantile(.9),
            "hsh_quality_status": (
                "astrometry_usable" if usable_count else "astrometry_target_not_usable"
            ),
            "hsh_quality_scope": "astrometry_and_geometry_only",
        }
        for stage in stages:
            record[f"hsh_{stage}_n"] = int((usable["microlensing_stage"] == stage).sum())
        for band, count in usable["band"].value_counts().items():
            if pd.notna(band):
                record[f"hsh_n_{band}"] = int(count)
                record[f"hsh_exptime_{band}_s"] = float(usable.loc[usable["band"] == band, "exptime_s"].sum(min_count=1))
        rows.append(record)
    summary = pd.DataFrame(rows)
    if summary.empty:
        summary = pd.DataFrame(columns=HSH_SUMMARY_COLUMNS)
    summary = target_index[["Target"]].merge(summary, on="Target", how="left")
    integer_columns = [name for name in summary if name.startswith("hsh_n_") or name.endswith("_n")]
    for column in integer_columns:
        summary[column] = pd.to_numeric(summary[column], errors="coerce").fillna(0).astype(int)
    for column in ("hsh_exptime_s", "hsh_target_in_frame_fraction", "hsh_clmatch_fraction", "hsh_usable_fraction"):
        if column in summary:
            summary[column] = pd.to_numeric(summary[column], errors="coerce").fillna(0.0)
    if "hsh_quality_status" in summary:
        summary["hsh_quality_status"] = summary["hsh_quality_status"].fillna("not_observed")
        summary["hsh_quality_scope"] = summary["hsh_quality_scope"].fillna("no_hsh_data")
    return summary


def save_hsh_observation_summary(summary: pd.DataFrame, path: str | Path) -> Path:
    """Write the HSH observation summary CSV and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(path, index=False)
    return path
