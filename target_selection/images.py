"""Compatibility wrapper for collection image products.

New notebooks should call CollectionContext.get_image directly.
"""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
import pandas as pd


def cutouts(
    coverage: pd.DataFrame,
    *,
    collection,
    png_dir: str | Path,
    fits_dir: str | Path,
    bands: Sequence[str] = tuple("ugrizy"),
    size_arcsec: float = 20.0,
    category_column: str | None = None,
    add_rgb: bool = False,
    reuse_existing: bool = True,
    **kwargs,
) -> pd.DataFrame:
    """Generate/reuse FITS and PNG cutouts for targets with coadd coverage."""
    return collection.get_coadd_cutouts(
        coverage, output_dir=png_dir, fits_dir=fits_dir, bands=bands,
        size_arcsec=size_arcsec, category_column=category_column,
        add_rgb=add_rgb, reuse_existing=reuse_existing, **kwargs,
    )
