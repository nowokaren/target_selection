"""Load, cache, and prepare MOP photometry."""

from pathlib import Path
import re

import numpy as np
import pandas as pd


def _safe_target_name(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")


def normalize_photometry_provenance(data: pd.DataFrame, *, provider: str = "MOP") -> pd.DataFrame:
    """Normalize provenance while retaining survey and provider information."""
    if data is None:
        return pd.DataFrame()
    result = data.copy()
    for column in ("Telescope", "Source", "Provider", "Collection"):
        if column not in result:
            result[column] = ""
    telescope = result["Telescope"].fillna("").astype(str).str.strip()
    source = result["Source"].fillna("").astype(str).str.strip()
    result.loc[telescope.eq(""), "Telescope"] = source.loc[telescope.eq("")]
    result["Provider"] = result["Provider"].fillna("").astype(str).str.strip()
    result.loc[result["Provider"].eq(""), "Provider"] = str(provider)
    return result


def _valid_photometry(data: pd.DataFrame) -> bool:
    if data is None or data.empty or not {"Timestamp", "Magnitude"}.issubset(data.columns):
        return False
    return bool(pd.to_datetime(data["Timestamp"], errors="coerce").notna().any() and pd.to_numeric(data["Magnitude"], errors="coerce").notna().any())


def load_event_photometry(target, *, mop, cache_dir, refresh=False, legacy_cache_dir=None):
    """Fetch MOP photometry by name, with a positional fallback and CSV cache.

    ``legacy_cache_dir`` lets prior runs using the old ``photometry`` directory
    be migrated lazily into the clearer ``mop_photometry`` cache.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    is_row = hasattr(target, "get")
    target_name = target.get("Target") if is_row else str(target)
    path = cache_dir / f"{_safe_target_name(target_name)}.csv"
    if path.exists() and not refresh:
        try:
            data = normalize_photometry_provenance(pd.read_csv(path, parse_dates=["Timestamp"]))
        except (OSError, ValueError) as exc:
            print(f"Photometry skipped for {target_name}: invalid cache ({exc})")
            path.unlink(missing_ok=True)
            data = pd.DataFrame()
        if _valid_photometry(data):
            data.to_csv(path, index=False)
            return data
        print(f"Photometry skipped for {target_name}: cached file has no usable measurements")
        path.unlink(missing_ok=True)
    if legacy_cache_dir is not None and not refresh:
        legacy_path = Path(legacy_cache_dir) / path.name
        if legacy_path.exists():
            data = normalize_photometry_provenance(pd.read_csv(legacy_path, parse_dates=["Timestamp"]))
            if _valid_photometry(data):
                data.to_csv(path, index=False)
                return data
            print(f"Photometry skipped for {target_name}: legacy file has no usable measurements")
    try:
        data = normalize_photometry_provenance(mop.photometry(target_name), provider="MOP")
        if _valid_photometry(data):
            data.to_csv(path, index=False)
            return data
        raise ValueError("MOP returned no usable photometric measurements")
    except Exception as name_error:
        if is_row and target.get("RA") is not None and target.get("Dec") is not None:
            try:
                data = normalize_photometry_provenance(
                    mop.photometry(ra=target["RA"], dec=target["Dec"]), provider="MOP"
                )
                if _valid_photometry(data):
                    data.to_csv(path, index=False)
                    return data
                raise ValueError("MOP returned no usable photometric measurements at the target coordinates")
            except Exception as position_error:
                name_error = position_error
        result = pd.DataFrame()
        path.unlink(missing_ok=True)
        result.attrs["error"] = str(name_error)
        print(f"Photometry skipped for {target_name}: {name_error}")

        return result


def _as_bool(value: object) -> bool:
    """Interpret nullable boolean values after CSV round-trips."""
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def annotate_mop_event_data(targets, *, photometry_dir):
    """Annotate MOP-visible targets with cached parameter/photometry availability."""
    data = targets.copy()
    if data.empty:
        data["has_mop_parameters"] = pd.Series(dtype=bool)
        data["mop_photometry_points"] = pd.Series(dtype=int)
        data["has_mop_event_data"] = pd.Series(dtype=bool)
        data["excluded_missing_mop_data"] = pd.Series(dtype=bool)
        data["mop_data_exclusion_reason"] = pd.Series(dtype=str)
        return data

    status = data.get("mop_parameters_status", pd.Series("", index=data.index))
    parameter_metadata = {
        "mop_link", "mop_parameters_status", "mop_parameters_error",
        "mop_ra", "mop_dec", "mop_ra_deg", "mop_dec_deg",
        "mop_coordinate_source",
    }
    parameter_columns = [
        column for column in data.columns
        if column.startswith("mop_") and column not in parameter_metadata
    ]
    if parameter_columns:
        parameter_values = data[parameter_columns]
        has_parameter_value = (
            parameter_values.notna()
            & parameter_values.astype(str).apply(
                lambda column: column.str.strip().ne("")
            )
        ).any(axis=1)
    else:
        has_parameter_value = pd.Series(False, index=data.index)
    data["has_mop_parameters"] = (
        status.astype(str).str.strip().str.casefold().eq("available")
        & has_parameter_value
    )
    cache_dir = Path(photometry_dir)

    def point_count(target_name: object) -> int:
        path = cache_dir / f"{_safe_target_name(target_name)}.csv"
        if not path.exists():
            return 0
        try:
            return len(pd.read_csv(path, usecols=["Timestamp"]))
        except (OSError, ValueError):
            return 0

    data["mop_photometry_points"] = data["Target"].map(point_count).astype(int)
    data["has_mop_event_data"] = (
        data["has_mop_parameters"] | data["mop_photometry_points"].gt(0)
    )
    visible = data.get("is_mop_visible_in_run", pd.Series(False, index=data.index))
    data["excluded_missing_mop_data"] = visible.map(_as_bool) & ~data["has_mop_event_data"]
    data["mop_data_exclusion_reason"] = np.where(
        data["excluded_missing_mop_data"],
        "MOP candidate has neither event parameters nor photometry",
        "",
    )
    return data


def split_mop_candidates_without_event_data(targets, *, photometry_dir):
    """Return analysis targets and excluded MOP-visible candidates with no data."""
    annotated = annotate_mop_event_data(targets, photometry_dir=photometry_dir)
    excluded = annotated.loc[annotated["excluded_missing_mop_data"]].copy()
    included = annotated.loc[~annotated["excluded_missing_mop_data"]].copy()
    return included.reset_index(drop=True), excluded.reset_index(drop=True)


def select_lightcurve_filters(photometry):
    """Prefer OGLE_I and G; otherwise select the filter with the most points."""
    if photometry.empty or "Filter" not in photometry:
        return []
    labels = {str(value).casefold(): value for value in photometry["Filter"].dropna().unique()}
    selected = [labels[key] for key in ("ogle_i", "g") if key in labels]
    if selected:
        return selected
    counts = photometry["Filter"].value_counts()
    return [counts.index[0]] if not counts.empty else []


def prepare_lightcurve_data(photometry, filter_name):
    """Normalize dates and numeric columns in a light curve."""
    data = photometry.loc[photometry["Filter"] == filter_name].copy()
    data["Timestamp"] = pd.to_datetime(data["Timestamp"], errors="coerce", utc=True).dt.tz_convert(None)
    data["Magnitude"] = pd.to_numeric(data["Magnitude"], errors="coerce")
    if "Error" in data:
        data["Error"] = pd.to_numeric(data["Error"], errors="coerce")
    return data.dropna(subset=["Timestamp", "Magnitude"]).sort_values("Timestamp")
