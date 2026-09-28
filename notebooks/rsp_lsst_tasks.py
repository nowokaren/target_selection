"""Reusable RSP/LSST DP2 coverage utilities.

The DP2 implementation queries CcdVisit in batches and performs the
visit-center/target association locally to avoid one TAP query per target.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import astropy.units as u
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from lsst.rsp import get_tap_service


RELEASES = {
    "DP2": {
        "tap_table": "dp02_dc2_catalogs.CcdVisit",
        "tap_ra_column": "ra",
        "tap_dec_column": "decl",
        "default_collection": "2.2i/runs/DP0.2",
        "visit_radius_deg": 11.0 / 60.0,
    }
}
BANDS = tuple("ugrizy")


def open_butler(*, release: str = "DP2", collection: str | None = None):
    """Open the Butler for a supported release and collection."""
    if release not in RELEASES:
        raise ValueError(f"Unsupported release {release!r}; available: {sorted(RELEASES)}")
    from lsst.daf.butler import Butler
    selected_collection = collection or RELEASES[release]["default_collection"]
    return Butler("dp02", collections=selected_collection)


def query_visit_coverage(
    targets: pd.DataFrame,
    *,
    release: str = "DP2",
    collection: str | None = None,
    target_id_column: str = "target_id",
    ra_column: str = "ra",
    dec_column: str = "dec",
    batch_size: int = 100,
    visit_radius_deg: float | None = None,
    tap_service=None,
    progress: bool = True,
) -> pd.DataFrame:
    """Return individual CcdVisit counts per target and band.

    Queries are batched by target position. ``collection`` is accepted as a
    common RSP interface parameter; DP2 CcdVisit is exposed through its DP2
    TAP table, so the release configuration determines the table.
    Counts are detector-visit rows, matching the CcdVisit representation.
    """
    if release not in RELEASES:
        raise ValueError(f"Unsupported release {release!r}; available: {sorted(RELEASES)}")
    cfg = RELEASES[release]
    if collection is None:
        collection = cfg["default_collection"]
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    radius_deg = cfg["visit_radius_deg"] if visit_radius_deg is None else float(visit_radius_deg)
    required = {target_id_column, ra_column, dec_column}
    missing = required - set(targets.columns)
    if missing:
        raise ValueError(f"Missing target columns: {sorted(missing)}")

    work = targets[[target_id_column, ra_column, dec_column]].copy()
    work[ra_column] = pd.to_numeric(work[ra_column], errors="coerce")
    work[dec_column] = pd.to_numeric(work[dec_column], errors="coerce")
    work = work.dropna(subset=[ra_column, dec_column]).reset_index(drop=True)
    if work.empty:
        return _empty_coverage(target_id_column)

    service = tap_service or get_tap_service("tap")
    all_counts = []
    total_batches = (len(work) + batch_size - 1) // batch_size

    for batch_number, start in enumerate(range(0, len(work), batch_size), start=1):
        batch = work.iloc[start:start + batch_size]
        clauses = [
            f"CONTAINS(POINT('ICRS', {cfg['tap_ra_column']}, {cfg['tap_dec_column']}), "
            f"CIRCLE('ICRS', {float(ra):.15g}, {float(dec):.15g}, {radius_deg:.15g})) = 1"
            for ra, dec in zip(batch[ra_column], batch[dec_column])
        ]
        query = (
            f"SELECT visitId, detector, band, {cfg['tap_ra_column']} AS visit_ra, "
            f"{cfg['tap_dec_column']} AS visit_dec FROM {cfg['tap_table']} "
            f"WHERE {' OR '.join(clauses)}"
        )
        job = service.submit_job(query)
        job.run()
        job.wait(phases=["COMPLETED", "ERROR"])
        if job.phase == "ERROR":
            job.raise_if_error()
        visits = job.fetch_result().to_table().to_pandas()
        counts = _empty_counts(batch[target_id_column].tolist(), target_id_column)
        if not visits.empty:
            target_coords = SkyCoord(batch[ra_column].to_numpy() * u.deg, batch[dec_column].to_numpy() * u.deg)
            visit_coords = SkyCoord(visits["visit_ra"].to_numpy() * u.deg, visits["visit_dec"].to_numpy() * u.deg)
            idx_visit, idx_target, _, _ = target_coords.search_around_sky(visit_coords, radius_deg * u.deg)
            matched = visits.iloc[np.asarray(idx_visit, dtype=int)].copy()
            matched["_target_row"] = np.asarray(idx_target, dtype=int)
            for target_row, group in matched.groupby("_target_row"):
                target_id = batch.iloc[int(target_row)][target_id_column]
                counts.loc[counts[target_id_column].eq(target_id), "n_images_total"] = len(group)
                for band, band_group in group.groupby(group["band"].astype(str)):
                    if band in BANDS:
                        counts.loc[counts[target_id_column].eq(target_id), f"n_{band}"] = len(band_group)
        all_counts.append(counts)
        if progress:
            print(f"DP2 coverage batch {batch_number}/{total_batches}")

    result = work.merge(pd.concat(all_counts, ignore_index=True), on=target_id_column, how="left")
    result["in_dp2"] = result["n_images_total"].fillna(0).astype(int).gt(0)
    return result


def _empty_counts(target_ids: Iterable, target_id_column: str) -> pd.DataFrame:
    rows = []
    for target_id in target_ids:
        row = {target_id_column: target_id, **{f"n_{band}": 0 for band in BANDS}, "n_images_total": 0}
        rows.append(row)
    return pd.DataFrame(rows)


def _empty_coverage(target_id_column: str) -> pd.DataFrame:
    return _empty_counts([], target_id_column).assign(in_dp2=pd.Series(dtype=bool))


def plot_coverage_map(
    coverage: pd.DataFrame,
    *,
    ra_column: str = "ra",
    dec_column: str = "dec",
    value_column: str = "n_images_total",
    title: str = "DP2 individual-visit coverage",
    ax=None,
    show: bool = True,
    save_path: str | Path | None = None,
):
    """Plot target positions colored by a coverage column."""
    required = {ra_column, dec_column, value_column}
    missing = required - set(coverage.columns)
    if missing:
        raise ValueError(f"Missing coverage columns: {sorted(missing)}")
    if ax is None:
        _, ax = plt.subplots(figsize=(12, 6))
    values = coverage[value_column].fillna(0).to_numpy()
    scatter = ax.scatter(coverage[ra_column], coverage[dec_column], c=values, s=18, cmap="viridis")
    ax.set_xlabel("RA [deg]")
    ax.set_ylabel("Dec [deg]")
    ax.set_title(title)
    ax.invert_xaxis()
    plt.colorbar(scatter, ax=ax, label=value_column)
    if save_path is not None:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    return ax
