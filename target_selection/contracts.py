"""Normalized table contracts for target-selection analyses.

The functions in this module are deliberately small. Providers use them at
their public boundary so the rest of the package can rely on the same column
names, units and row grains.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd


class ContractError(ValueError):
    """Raised when a provider table cannot satisfy a normalized contract."""


def _rename_first(frame: pd.DataFrame, target: str, aliases: Iterable[str]) -> None:
    if target in frame:
        return
    source = next((name for name in aliases if name in frame), None)
    if source is not None:
        frame.rename(columns={source: target}, inplace=True)


def normalize_targets(table: pd.DataFrame, *, provider: str | None = None) -> pd.DataFrame:
    """Return a canonical target table without dropping provider columns.

    ``category`` is the single target-level classification field. Existing
    provider fields such as ``status`` or ``grade`` are preserved and are used
    as fallbacks only when ``category`` is absent.
    """
    if not isinstance(table, pd.DataFrame):
        raise TypeError("Target contract requires a pandas DataFrame.")
    result = table.copy()
    _rename_first(result, "target_id", ("Target", "object_id", "Object", "id"))
    _rename_first(result, "ra_deg", ("RA_deg", "RA", "ra"))
    _rename_first(result, "dec_deg", ("Dec_deg", "DEC", "dec"))
    _rename_first(result, "provider", ("source", "target_source", "data_source"))
    if "category" not in result:
        _rename_first(result, "category", ("Category", "status", "Status", "grade", "Grade"))
    if provider is not None and "provider" not in result:
        result["provider"] = str(provider)
    if "target_id" in result:
        result["target_id"] = result["target_id"].astype(str)
    for column in ("ra_deg", "dec_deg"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    return result


def validate_targets(table: pd.DataFrame, *, require_provider: bool = True) -> pd.DataFrame:
    """Validate and return a target table with one row per target."""
    result = normalize_targets(table)
    required = {"target_id", "ra_deg", "dec_deg"}
    if require_provider:
        required.add("provider")
    missing = sorted(required - set(result.columns))
    if missing:
        raise ContractError(f"Target table is missing required columns: {missing}")
    if result["target_id"].duplicated().any():
        duplicate_ids = result.loc[result["target_id"].duplicated(), "target_id"].head(5).tolist()
        raise ContractError(f"Target table must have one row per target; duplicates include {duplicate_ids}")
    if result[["ra_deg", "dec_deg"]].isna().any().any():
        raise ContractError("Target coordinates cannot be null.")
    if not result["ra_deg"].between(0, 360, inclusive="left").all():
        raise ContractError("ra_deg must be in [0, 360).")
    if not result["dec_deg"].between(-90, 90, inclusive="both").all():
        raise ContractError("dec_deg must be in [-90, 90].")
    return result


def normalize_observations(table: pd.DataFrame, *, provider: str | None = None) -> pd.DataFrame:
    """Normalize epoch and photometry aliases into ``observations.csv`` fields."""
    if not isinstance(table, pd.DataFrame):
        raise TypeError("Observation contract requires a pandas DataFrame.")
    result = table.copy()
    _rename_first(result, "target_id", ("Target", "object_id", "Object", "id"))
    _rename_first(result, "provider", ("source", "data_source"))
    _rename_first(result, "epoch_hjd", ("hjd", "HJD", "epoch", "time_hjd"))
    _rename_first(result, "band", ("filter", "Filter", "passband"))
    _rename_first(result, "magnitude_err", ("magnitude_error", "mag_err", "mag_error"))
    _rename_first(result, "flux_err", ("flux_error",))
    if provider is not None and "provider" not in result:
        result["provider"] = str(provider)
    for column in ("epoch_hjd", "magnitude", "magnitude_err", "flux", "flux_err", "exposure_time_s"):
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="coerce")
    if "target_id" in result:
        result["target_id"] = result["target_id"].astype(str)
    return result


def validate_observations(table: pd.DataFrame) -> pd.DataFrame:
    """Validate one row per target/provider/epoch/band observation record."""
    result = normalize_observations(table)
    required = {"target_id", "provider", "epoch_hjd", "band"}
    missing = sorted(required - set(result.columns))
    if missing:
        raise ContractError(f"Observation table is missing required columns: {missing}")
    if result[["target_id", "provider", "epoch_hjd", "band"]].isna().any().any():
        raise ContractError("target_id, provider, epoch_hjd and band cannot be null.")
    if "is_usable" in result:
        result["is_usable"] = result["is_usable"].astype("boolean")
    return result


def validate_visibility(table: pd.DataFrame) -> pd.DataFrame:
    """Validate one row per target and observing night."""
    required = {"target_id", "night", "observatory", "is_visible"}
    missing = sorted(required - set(table.columns))
    if missing:
        raise ContractError(f"Visibility table is missing required columns: {missing}")
    result = table.copy()
    if result.duplicated(["target_id", "night"]).any():
        raise ContractError("visibility_nightly must have one row per target_id and night.")
    return result


def validate_coverage(table: pd.DataFrame) -> pd.DataFrame:
    """Validate one row per target/provider/collection/image kind/band."""
    required = {"target_id", "provider", "collection", "image_kind", "band"}
    missing = sorted(required - set(table.columns))
    if missing:
        raise ContractError(f"Coverage table is missing required columns: {missing}")
    result = table.copy()
    key = ["target_id", "provider", "collection", "image_kind", "band"]
    if result.duplicated(key).any():
        raise ContractError("coverage must have one row per target/provider/collection/image kind/band.")
    return result


def validate_contract(name: str, table: pd.DataFrame) -> pd.DataFrame:
    """Dispatch validation by normalized contract name."""
    validators = {
        "targets": validate_targets,
        "observations": validate_observations,
        "visibility": validate_visibility,
        "coverage": validate_coverage,
    }
    try:
        validator = validators[str(name).strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unknown contract {name!r}; choose {sorted(validators)}") from exc
    return validator(table)

