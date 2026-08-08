"""Load, cache, and prepare MOP photometry."""

from pathlib import Path
import re

import pandas as pd


def _safe_target_name(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")


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
        return pd.read_csv(path, parse_dates=["Timestamp"])
    if legacy_cache_dir is not None and not refresh:
        legacy_path = Path(legacy_cache_dir) / path.name
        if legacy_path.exists():
            data = pd.read_csv(legacy_path, parse_dates=["Timestamp"])
            data.to_csv(path, index=False)
            return data
    try:
        return mop.photometry(target_name, save_path=path)
    except Exception as name_error:
        if is_row and target.get("RA") is not None and target.get("Dec") is not None:
            try:
                return mop.photometry(ra=target["RA"], dec=target["Dec"], save_path=path)
            except Exception as position_error:
                name_error = position_error
        result = pd.DataFrame()
        result.attrs["error"] = str(name_error)
        return result


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
