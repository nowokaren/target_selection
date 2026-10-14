"""Generic spatial matching helpers for tabular and HATS catalogs.

The functions in this module do not know whether the left catalog is MOP,
QSO, LaStBeRu, or another provider. They only require canonical coordinate
columns and return the raw LSDB crossmatch table so an analysis can add its
own product-specific flags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _left_lsdb_catalog(
    targets: pd.DataFrame | Any,
    *,
    ra_column: str,
    dec_column: str,
    catalog_name: str,
    margin_threshold: float,
    lowest_order: int = 5,
    highest_order: int = 8,
    partition_rows: int = 500,
):
    """Convert a target DataFrame to an LSDB catalog when needed."""
    if hasattr(targets, "crossmatch"):
        return targets
    import lsdb

    return lsdb.from_dataframe(
        targets,
        ra_column=ra_column,
        dec_column=dec_column,
        catalog_name=catalog_name,
        lowest_order=lowest_order,
        highest_order=highest_order,
        partition_rows=partition_rows,
        margin_threshold=margin_threshold,
    )


def open_hats_catalog(
    path: str | Path,
    *,
    columns: list[str] | None = None,
    show_statistics: bool = False,
):
    """Open a HATS catalog lazily through LSDB."""
    import lsdb

    return lsdb.open_catalog(str(path), columns=columns, show_statistics=show_statistics)


def crossmatch_lsdb(
    targets: pd.DataFrame | Any,
    catalog: Any,
    *,
    target_ra_column: str = "ra_deg",
    target_dec_column: str = "dec_deg",
    catalog_name: str = "targets",
    radius_arcsec: float = 1.0,
    n_neighbors: int = 1,
    how: str = "left",
    require_right_margin: bool = True,
    margin_threshold: float | None = None,
    suffix_method: str = "overlapping_columns",
    log_changes: bool = False,
    compute: bool = True,
    **from_dataframe_kwargs: Any,
) -> pd.DataFrame | Any:
    """Crossmatch a target table against an LSDB/HATS catalog.

    ``catalog`` can be an already opened LSDB catalog or a HATS path. The
    target side can be a DataFrame or an already partitioned LSDB catalog.
    ``compute=False`` returns the lazy LSDB crossmatch object.
    """
    if isinstance(catalog, (str, Path)):
        catalog = open_hats_catalog(catalog)
    margin = float(radius_arcsec if margin_threshold is None else margin_threshold)
    left = _left_lsdb_catalog(
        targets,
        ra_column=target_ra_column,
        dec_column=target_dec_column,
        catalog_name=catalog_name,
        margin_threshold=margin,
        **from_dataframe_kwargs,
    )
    if require_right_margin and getattr(catalog, "margin", None) is None:
        raise RuntimeError(
            "The right-hand HATS catalog has no loaded margin; refusing to "
            "run a boundary-sensitive crossmatch."
        )
    lazy = left.crossmatch(
        catalog,
        n_neighbors=n_neighbors,
        radius_arcsec=radius_arcsec,
        how=how,
        require_right_margin=require_right_margin,
        suffix_method=suffix_method,
        log_changes=log_changes,
    )
    return lazy.compute() if compute else lazy


def add_match_flag(
    matches: pd.DataFrame,
    *,
    radius_arcsec: float,
    distance_column: str = "_dist_arcsec",
    flag_column: str = "match_found",
) -> pd.DataFrame:
    """Add a standardized boolean flag based on angular separation."""
    result = matches.copy()
    result[distance_column] = pd.to_numeric(result.get(distance_column), errors="coerce")
    result[flag_column] = result[distance_column].le(float(radius_arcsec))
    return result


def cached_crossmatch(
    targets: pd.DataFrame | Any,
    catalog: Any,
    output_path: str | Path,
    *,
    reuse_existing: bool = True,
    **kwargs: Any,
) -> pd.DataFrame:
    """Run or reuse a materialized crossmatch table."""
    output = Path(output_path)
    if reuse_existing and output.exists():
        return pd.read_csv(output, low_memory=False)
    result = crossmatch_lsdb(targets, catalog, **kwargs)
    if not isinstance(result, pd.DataFrame):
        result = result.compute()
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    return result
