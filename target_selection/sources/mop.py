"""MOP provider helpers shared by adapters and task APIs."""

from __future__ import annotations

import numpy as np
import pandas as pd


def create_client():
    """Create the default MOP client lazily."""
    from mop_api import MOPClient

    return MOPClient()


def apply_authoritative_coordinates(targets: pd.DataFrame) -> pd.DataFrame:
    """Use full-precision target-page coordinates whenever MOP supplied them."""
    data = targets.copy()
    if data.empty:
        return data
    def numeric_column(name: str) -> pd.Series:
        if name not in data:
            return pd.Series(np.nan, index=data.index, dtype=float)
        return pd.to_numeric(data[name], errors="coerce")

    old_ra = numeric_column("RA_deg")
    old_dec = numeric_column("Dec_deg")
    mop_ra = numeric_column("mop_ra_deg")
    mop_dec = numeric_column("mop_dec_deg")
    page_valid = mop_ra.between(0.0, 360.0, inclusive="left") & mop_dec.between(-90.0, 90.0)
    old_valid = old_ra.between(0.0, 360.0, inclusive="left") & old_dec.between(-90.0, 90.0)

    separation = pd.Series(np.nan, index=data.index, dtype=float)
    comparable = page_valid & old_valid
    if comparable.any():
        ra1 = np.deg2rad(old_ra.loc[comparable].to_numpy(dtype=float))
        dec1 = np.deg2rad(old_dec.loc[comparable].to_numpy(dtype=float))
        ra2 = np.deg2rad(mop_ra.loc[comparable].to_numpy(dtype=float))
        dec2 = np.deg2rad(mop_dec.loc[comparable].to_numpy(dtype=float))
        cosine = (
            np.sin(dec1) * np.sin(dec2)
            + np.cos(dec1) * np.cos(dec2) * np.cos(ra1 - ra2)
        )
        separation.loc[comparable] = np.rad2deg(
            np.arccos(np.clip(cosine, -1.0, 1.0))
        ) * 3600.0

    data["coordinate_offset_arcsec"] = separation
    data["coordinate_was_overridden"] = (
        page_valid & old_valid & separation.fillna(0.0).gt(1e-6)
    )
    data.loc[page_valid, "RA_deg"] = mop_ra.loc[page_valid].astype(float)
    data.loc[page_valid, "Dec_deg"] = mop_dec.loc[page_valid].astype(float)
    if "coordinate_source" not in data:
        data["coordinate_source"] = pd.NA
    if "coordinate_priority" not in data:
        data["coordinate_priority"] = 0
    data.loc[page_valid, "coordinate_source"] = "MOP target page"
    data.loc[page_valid, "coordinate_priority"] = 100

    visible = data.get(
        "is_mop_visible_in_run", pd.Series(False, index=data.index)
    )
    visible = pd.Series(visible, index=data.index).fillna(False).astype(bool)
    selection_only = visible & ~page_valid & old_valid
    data.loc[selection_only, "coordinate_source"] = "MOP visibility table"
    data.loc[selection_only, "coordinate_priority"] = 90
    return data

