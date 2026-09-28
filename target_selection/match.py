"""Compatibility wrappers for collection matching.

New notebooks should call the corresponding CollectionContext methods directly.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Sequence
import pandas as pd


def visits(targets: pd.DataFrame, *, collection, **kwargs) -> pd.DataFrame:
    """Add exact individual-visit coverage to target rows."""
    return collection.get_visit_coverage(targets, **kwargs)


def coadds(coverage: pd.DataFrame, *, collection, **kwargs) -> pd.DataFrame:
    """Add direct coadd existence and band coverage to a visit table."""
    return collection.get_coadd_coverage(coverage, **kwargs)


def property_maps(
    coverage: pd.DataFrame,
    *,
    collection,
    properties: str | Sequence[str] = ("maglim", "psf_size", "exposure_time", "sky_noise"),
    bands: Sequence[str] = tuple("ugrizy"),
    **kwargs: Any,
) -> pd.DataFrame:
    """Sample coadd property maps at target positions and merge them into coverage."""
    positions = coverage[[c for c in ("target_id", "ra_deg", "dec_deg", "RA", "DEC") if c in coverage]].copy()
    values = collection.get_property_map_values(positions, properties=properties, bands=bands, **kwargs)
    return coverage.drop(columns=[c for c in values.columns if c != "target_id" and c in coverage.columns], errors="ignore").merge(values, on="target_id", how="left")
