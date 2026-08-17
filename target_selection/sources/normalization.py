"""Column normalization shared by configurable tabular source adapters."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


TARGET_ALIASES = {
    "target": "Target", "name": "Target", "object": "Target",
    "target_name": "Target", "ra": "RA_deg", "ra_deg": "RA_deg",
    "ra_icrs": "RA_deg", "dec": "Dec_deg", "dec_deg": "Dec_deg",
    "dec_icrs": "Dec_deg",
}
OBSERVATION_ALIASES = {
    "target": "Target", "name": "Target", "object": "Target",
    "target_name": "Target", "mjd": "mjd", "mjd_obs": "mjd",
    "filter": "band", "filter_name": "band", "exptime": "exptime_s",
    "exposure": "exptime_s", "exposure_s": "exptime_s", "usable": "usable",
    "ra": "RA_deg", "ra_deg": "RA_deg", "dec": "Dec_deg", "dec_deg": "Dec_deg",
}
PHOTOMETRY_ALIASES = {
    "target": "Target", "name": "Target", "object": "Target",
    "target_name": "Target", "mjd": "mjd", "filter": "band",
    "filter_name": "band", "magnitude": "magnitude", "mag": "magnitude",
    "error": "magnitude_error", "mag_error": "magnitude_error",
    "magnitude_error": "magnitude_error", "flux": "flux",
    "flux_error": "flux_error", "timestamp": "Timestamp", "date": "Timestamp",
    "point_id": "point_id",
}


def normalize_columns(
    data: pd.DataFrame,
    *,
    aliases: Mapping[str, str],
    column_map: Mapping[str, str] | None = None,
    source_name: str,
    data_kind: str,
) -> pd.DataFrame:
    """Translate a source table into the internal canonical column names.

    ``column_map`` maps an exact input-column name to one of the canonical
    names accepted for the relevant data kind. Explicit mappings take
    precedence over built-in aliases and ambiguous mappings fail early.
    """
    normalized = data.copy()
    canonical = set(aliases.values())
    raw_map = dict(column_map or {})
    canonical_by_lower = {str(value).casefold(): value for value in canonical}
    explicit: dict[str, str] = {}
    for key, value in raw_map.items():
        key, value = str(key), str(value)
        # Accept both documented forms: input -> canonical, and the
        # convenient canonical -> input form used in interactive notebooks.
        if value.casefold() in canonical_by_lower and key in normalized.columns:
            explicit[key] = canonical_by_lower[value.casefold()]
        elif key.casefold() in canonical_by_lower and value in normalized.columns:
            explicit[value] = canonical_by_lower[key.casefold()]
        else:
            explicit[key] = value
    invalid = sorted({str(value) for value in explicit.values()} - canonical)
    if invalid:
        raise ValueError(
            f"{data_kind} source {source_name!r} maps to unsupported canonical "
            f"column(s): {invalid}. Allowed: {sorted(canonical)}"
        )
    missing = sorted(str(column) for column in explicit if column not in normalized)
    if missing:
        raise ValueError(
            f"{data_kind} source {source_name!r} column_map references missing "
            f"column(s): {missing}"
        )
    rename: dict[str, str] = {}
    occupied = set(normalized.columns)
    for source, destination in explicit.items():
        source, destination = str(source), str(destination)
        if source != destination and destination in occupied:
            raise ValueError(
                f"{data_kind} source {source_name!r} cannot map {source!r} to "
                f"{destination!r}: that canonical column already exists"
            )
        rename[source] = destination
        occupied.discard(source)
        occupied.add(destination)
    normalized = normalized.rename(columns=rename)

    occupied = set(normalized.columns)
    alias_rename: dict[str, str] = {}
    for column in normalized.columns:
        destination = aliases.get(str(column).strip().lower())
        if destination and column != destination and destination not in occupied:
            alias_rename[str(column)] = destination
            occupied.add(destination)
    return normalized.rename(columns=alias_rename)
