"""LSST/Rubin access helpers used by task-level APIs.

This module is the single import location for Rubin-specific services in the
public task API.  The heavy RSP imports are intentionally delayed so the package
can still be imported outside the Rubin Science Platform.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

import pandas as pd

from data_release_config import DataReleaseConfig, get_data_release


SUPPORTED_PHOTOMETRY_METHODS = {"coadd_forced", "dia_forced_catalog", "calexp_forced"}


def resolve_data_release(data_release: str | DataReleaseConfig = "DP2", *, photometry_method: str | None = None) -> DataReleaseConfig:
    """Return a Rubin data-release configuration, optionally overriding photometry."""
    release = get_data_release(data_release)
    if photometry_method is None:
        return release
    method = str(photometry_method).strip().lower()
    if method not in SUPPORTED_PHOTOMETRY_METHODS:
        raise ValueError(
            f"photometry_method must be one of {sorted(SUPPORTED_PHOTOMETRY_METHODS)}"
        )
    return replace(release, photometry_method=method)


def create_tap_service(data_release: str | DataReleaseConfig = "DP2"):
    """Create the Rubin RSP TAP client for one data release."""
    release = get_data_release(data_release)
    from lsst.rsp import RSPDiscovery

    return RSPDiscovery(release.rsp_instance).get_tap_client()


def create_butler(data_release: str | DataReleaseConfig = "DP2"):
    """Create a Butler configured for one Rubin data release/collection."""
    release = get_data_release(data_release)
    from lsst.daf.butler import Butler

    options = {}
    if release.butler_collections is not None:
        options["collections"] = release.butler_collections
    return Butler(release.butler_repo, **options)


def query_visit_coverage(
    targets: pd.DataFrame,
    *,
    tap_service=None,
    data_release: str | DataReleaseConfig = "DP2",
    search_radius: float = 11 / 60,
    max_workers: int = 4,
) -> pd.DataFrame:
    """Query Rubin visit/detector coverage around each target position."""
    from target_selection_pipeline import query_release_coverage

    release = get_data_release(data_release)
    tap_service = tap_service or create_tap_service(release)
    return query_release_coverage(
        targets,
        tap_service=tap_service,
        search_radius=search_radius,
        max_workers=max_workers,
        data_release=release,
    )


def query_visit_centers(*, tap_service=None, data_release: str | DataReleaseConfig = "DP2") -> pd.DataFrame:
    """Query approximate Rubin visit centers for coarse coverage maps."""
    from target_selection_pipeline import query_release_visit_centers

    release = get_data_release(data_release)
    tap_service = tap_service or create_tap_service(release)
    return query_release_visit_centers(tap_service=tap_service, data_release=release)


def compute_release_photometry(
    targets: pd.DataFrame,
    coverage: pd.DataFrame | None = None,
    *,
    method: str = "dia_forced_catalog",
    tap_service=None,
    butler=None,
    data_release: str | DataReleaseConfig = "DP2",
    max_workers: int = 4,
    verbose: bool = True,
) -> pd.DataFrame:
    """Compute or retrieve Rubin photometry for target positions.

    Parameters
    ----------
    targets:
        DataFrame with ``Target``, ``RA_deg`` and ``Dec_deg``.
    coverage:
        Visit/detector coverage table. Required for ``calexp_forced`` and useful
        for coadd epoch metadata in ``coadd_forced``.
    method:
        ``dia_forced_catalog``, ``coadd_forced`` or ``calexp_forced``.
    """
    release = resolve_data_release(data_release, photometry_method=method)
    method = release.photometry_method
    if method == "dia_forced_catalog":
        from release_photometry import query_dia_forced_photometry

        tap_service = tap_service or create_tap_service(release)
        return query_dia_forced_photometry(
            targets,
            tap_service=tap_service,
            data_release=release,
            max_workers=max_workers,
            verbose=verbose,
        )
    if coverage is None:
        raise ValueError(f"coverage is required for {method!r} photometry")
    butler = butler or create_butler(release)
    if method == "coadd_forced":
        from release_photometry import compute_coadd_forced_photometry

        return compute_coadd_forced_photometry(
            targets,
            coverage,
            butler=butler,
            data_release=release,
            max_workers=max_workers,
            verbose=verbose,
        )
    if method == "calexp_forced":
        from release_photometry import compute_release_forced_photometry

        return compute_release_forced_photometry(
            targets,
            coverage,
            butler=butler,
            data_release=release,
            max_workers=max_workers,
        )
    raise ValueError(f"Unsupported photometry method: {method!r}")


def save_release_photometry(photometry: pd.DataFrame, path: str | Path) -> Path:
    """Save one normalized Rubin photometry table."""
    from release_photometry import save_release_forced_photometry

    path = Path(path)
    save_release_forced_photometry(photometry, path)
    return path


def load_release_photometry(path: str | Path, target_name: str | None = None) -> pd.DataFrame:
    """Load Rubin photometry saved by :func:`save_release_photometry`."""
    from release_photometry import load_release_forced_photometry

    return load_release_forced_photometry(path, target_name=target_name)


def save_target_release_photometry(photometry: pd.DataFrame, directory: str | Path) -> int:
    """Upsert Rubin photometry into one persistent CSV per target."""
    from release_photometry import save_target_release_photometry as _save

    before = len(list(Path(directory).glob("*.csv"))) if Path(directory).exists() else 0
    _save(photometry, directory)
    after = len(list(Path(directory).glob("*.csv"))) if Path(directory).exists() else 0
    return after - before


def find_coadds_for_target(
    target,
    *,
    butler=None,
    data_release: str | DataReleaseConfig = "DP2",
    bands: Iterable[str] = ("u", "g", "r", "i", "z", "y"),
) -> dict[str, object]:
    """Return coadd objects that contain one target position, keyed by band."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord

    release = get_data_release(data_release)
    butler = butler or create_butler(release)
    coord = SkyCoord(float(target["RA_deg"]) * u.deg, float(target["Dec_deg"]) * u.deg)
    found: dict[str, object] = {}
    for band in bands:
        for dataset_type in release.coadd_dataset_types:
            try:
                refs = list(
                    butler.query_datasets(
                        dataset_type,
                        where=release.coadd_spatial_where,
                        bind={"band": band, "ra": float(target["RA_deg"]), "dec": float(target["Dec_deg"])},
                    )
                )
            except Exception:
                refs = []
            for ref in refs:
                coadd = butler.get(ref)
                x, y = coadd.fits_wcs.world_to_pixel(coord)
                ny, nx = coadd.image.array.shape
                if 0 <= x < nx and 0 <= y < ny:
                    found[str(band)] = coadd
                    break
            if str(band) in found:
                break
    return found
