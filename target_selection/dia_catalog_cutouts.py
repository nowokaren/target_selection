"""Visual diagnostics for matching targets to Rubin DIA catalogue objects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import numpy as np

if TYPE_CHECKING:
    from data_release_config import DataReleaseConfig


def save_dia_match_cutout(
    *,
    butler,
    data_release: "DataReleaseConfig",
    target_name: str,
    target_ra_deg: float,
    target_dec_deg: float,
    dia_object_id: int,
    dia_ra_deg: float,
    dia_dec_deg: float,
    output_path: str | Path,
    preferred_bands: Iterable[str] = ("i", "r", "g", "z", "y", "u"),
    cutout_arcsec: float = 20.0,
    marker_radius_arcsec: float = 0.6,
):
    """Save one coadd cutout marking requested and matched DIA positions.

    The requested target coordinate is shown in red and the selected
    ``DiaObject`` position in cyan.  The closest available preferred band is
    used.  ``None`` is returned if no coadd covers the requested coordinate.
    """
    import astropy.units as u
    import matplotlib.pyplot as plt
    from astropy.coordinates import SkyCoord
    from astropy.visualization import AsinhStretch, ImageNormalize
    from astropy.visualization.wcsaxes import SphericalCircle
    from lsst.daf.butler import EmptyQueryResultError

    target_coord = SkyCoord(float(target_ra_deg) * u.deg, float(target_dec_deg) * u.deg)
    dia_coord = SkyCoord(float(dia_ra_deg) * u.deg, float(dia_dec_deg) * u.deg)
    preferred = {str(band): index for index, band in enumerate(preferred_bands)}
    refs = []
    for dataset_type in data_release.coadd_dataset_types:
        try:
            refs = list(
                butler.query_datasets(
                    dataset_type,
                    where=data_release.coadd_spatial_where,
                    bind={"ra": float(target_ra_deg), "dec": float(target_dec_deg)},
                )
            )
        except (EmptyQueryResultError, LookupError):
            refs = []
        if refs:
            break
    refs.sort(key=lambda ref: preferred.get(str(ref.dataId.get("band", "")), len(preferred)))

    for ref in refs:
        coadd = butler.get(ref)
        array = np.asarray(coadd.image.array, dtype=float)
        x, y = coadd.fits_wcs.world_to_pixel(target_coord)
        ny, nx = array.shape
        if not (0 <= x < nx and 0 <= y < ny):
            continue
        finite = array[np.isfinite(array)]
        if not len(finite):
            continue
        pixel_scale = np.mean(
            [scale.to_value(u.arcsec) for scale in coadd.fits_wcs.proj_plane_pixel_scales()]
        )
        radius_pixels = float(cutout_arcsec) / pixel_scale
        norm = ImageNormalize(
            vmin=np.nanpercentile(finite, 5),
            vmax=np.nanpercentile(finite, 99.8),
            stretch=AsinhStretch(0.08),
            clip=True,
        )
        figure = plt.figure(figsize=(5.5, 5.1))
        axis = figure.add_subplot(projection=coadd.fits_wcs)
        axis.imshow(array, origin="lower", cmap="gray", norm=norm)
        axis.set(xlim=(x - radius_pixels, x + radius_pixels), ylim=(y - radius_pixels, y + radius_pixels))
        axis.add_patch(
            SphericalCircle(
                target_coord, marker_radius_arcsec * u.arcsec,
                transform=axis.get_transform("icrs"), edgecolor="red", facecolor="none", lw=1.8,
                label="Queried target",
            )
        )
        axis.add_patch(
            SphericalCircle(
                dia_coord, marker_radius_arcsec * u.arcsec,
                transform=axis.get_transform("icrs"), edgecolor="cyan", facecolor="none", lw=1.8,
                label="Matched DiaObject",
            )
        )
        axis.legend(loc="upper right", fontsize=7, frameon=True)
        axis.set_title(f"{target_name} — DiaObject {int(dia_object_id)} — {ref.dataId['band']} coadd", fontsize=9)
        axis.coords[0].set_axislabel("RA [deg]")
        axis.coords[1].set_axislabel("Dec [deg]")
        axis.coords[0].set_ticklabel(size=7)
        axis.coords[1].set_ticklabel(size=7)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(output_path, dpi=180, bbox_inches="tight")
        plt.close(figure)
        return output_path
    return None
