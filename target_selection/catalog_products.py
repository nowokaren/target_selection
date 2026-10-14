"""Rubin/DP2 catalog-product definitions and matching entry points."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .catalog_matching import add_match_flag, cached_crossmatch, open_hats_catalog


DP2_HATS_PRODUCTS = {
    "object": {
        "path": "/rubin/lsdb_data/object_collection",
        "ra_column": "coord_ra",
        "dec_column": "coord_dec",
        "id_columns": ("objectId",),
    },
    "dia_object": {
        "path": "/rubin/lsdb_data/dia_object_collection",
        "ra_column": "ra",
        "dec_column": "dec",
        "id_columns": ("diaObjectId", "nDiaSources"),
    },
    "dia_source": {
        "path": "/rubin/lsdb_data/dia_source_collection",
        "ra_column": "ra",
        "dec_column": "dec",
        "id_columns": ("diaSourceId", "diaObjectId"),
    },
}


ASSOCIATED_DP2_PRODUCTS = {
    "object_forced_source": {
        "parent": "object",
        "relation": "objectId",
        "description": "forced measurements associated with Object",
    },
    "dia_object_forced_source": {
        "parent": "dia_object",
        "relation": "diaObjectId",
        "description": "forced measurements associated with DiaObject",
    },
}


def describe_dp2_products() -> dict[str, dict[str, Any]]:
    """Return spatial and associated DP2 product definitions."""
    return {**DP2_HATS_PRODUCTS, **ASSOCIATED_DP2_PRODUCTS}


def open_dp2_hats_catalog(
    product: str = "object",
    *,
    columns: list[str] | None = None,
    paths: dict[str, str | Path] | None = None,
    show_statistics: bool = False,
):
    """Open a spatial DP2 HATS product by semantic product name."""
    key = str(product).strip().lower()
    if key not in DP2_HATS_PRODUCTS:
        if key in ASSOCIATED_DP2_PRODUCTS:
            raise ValueError(
                f"{key!r} is an associated product, not an independent spatial "
                "catalog. Match its parent first, then retrieve it by parent ID."
            )
        raise ValueError(f"Unknown spatial DP2 product {product!r}: {sorted(DP2_HATS_PRODUCTS)}")
    spec = DP2_HATS_PRODUCTS[key]
    path = (paths or {}).get(key, spec["path"])
    requested = columns or [spec["ra_column"], spec["dec_column"], *spec["id_columns"]]
    return open_hats_catalog(path, columns=requested, show_statistics=show_statistics)


def match_dp2_catalog(
    targets: pd.DataFrame,
    *,
    product: str = "object",
    output_path: str | Path | None = None,
    reuse_existing: bool = True,
    radius_arcsec: float = 1.0,
    target_ra_column: str = "ra_deg",
    target_dec_column: str = "dec_deg",
    paths: dict[str, str | Path] | None = None,
    columns: list[str] | None = None,
    **crossmatch_kwargs: Any,
) -> pd.DataFrame:
    """Match targets to a selected spatial DP2 product.

    Valid ``product`` values include ``object``, ``dia_object`` and
    ``dia_source``. Forced-source products are retrieved by parent ID after
    this spatial match and are intentionally rejected here.
    """
    key = str(product).strip().lower()
    spec = DP2_HATS_PRODUCTS.get(key)
    if spec is None:
        if key in ASSOCIATED_DP2_PRODUCTS:
            raise ValueError(
                f"Use product={ASSOCIATED_DP2_PRODUCTS[key]['parent']!r} for the spatial "
                f"match, then retrieve {key!r} by {ASSOCIATED_DP2_PRODUCTS[key]['relation']}."
            )
        raise ValueError(f"Unknown DP2 product {product!r}")
    catalog = open_dp2_hats_catalog(key, columns=columns, paths=paths)
    kwargs = dict(
        target_ra_column=target_ra_column,
        target_dec_column=target_dec_column,
        catalog_name=f"targets_{key}",
        radius_arcsec=radius_arcsec,
        **crossmatch_kwargs,
    )
    if output_path is None:
        from .catalog_matching import crossmatch_lsdb
        result = crossmatch_lsdb(targets, catalog, **kwargs)
    else:
        result = cached_crossmatch(targets, catalog, output_path, reuse_existing=reuse_existing, **kwargs)
    return add_match_flag(result, radius_arcsec=radius_arcsec, flag_column=f"{key}_match")
