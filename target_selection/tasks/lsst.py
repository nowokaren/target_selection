"""Rubin/LSST task wrappers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from target_selection.sources import lsst as lsst_source


def query_lsst_coverage(
    targets: pd.DataFrame,
    *,
    data_release="DP2",
    tap_service=None,
    search_radius: float = 11 / 60,
    max_workers: int = 4,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Task: query LSST/Rubin visit coverage for targets."""
    coverage = lsst_source.query_visit_coverage(
        targets,
        tap_service=tap_service,
        data_release=data_release,
        search_radius=search_radius,
        max_workers=max_workers,
    )
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        coverage.to_csv(path, index=False)
    return coverage


def compute_lsst_photometry(
    targets: pd.DataFrame,
    coverage: pd.DataFrame | None = None,
    *,
    data_release="DP2",
    method: str = "dia_forced_catalog",
    tap_service=None,
    butler=None,
    max_workers: int = 4,
    output_path: str | Path | None = None,
    persistent_target_dir: str | Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Task: get LSST/Rubin photometry by DIA catalog or forced measurement."""
    photometry = lsst_source.compute_release_photometry(
        targets,
        coverage,
        method=method,
        tap_service=tap_service,
        butler=butler,
        data_release=data_release,
        max_workers=max_workers,
        verbose=verbose,
    )
    if output_path is not None:
        save_lsst_photometry(photometry, output_path)
    if persistent_target_dir is not None and not photometry.empty:
        lsst_source.save_target_release_photometry(photometry, persistent_target_dir)
    return photometry


def save_lsst_photometry(photometry: pd.DataFrame, path: str | Path) -> Path:
    """Task: save normalized LSST/Rubin photometry."""
    return lsst_source.save_release_photometry(photometry, path)
