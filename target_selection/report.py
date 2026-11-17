"""Report products for collection matching analyses."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from .reporting import plot_collection_distributions, write_collection_summaries
from .collection_access import build_collection_report_pdf


def _normalize_observatory_photometry(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    """Adapt normalized provider observations to the monitoring-report schema."""
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    rename = {
        "target_id": "Target",
        "observation_time": "Timestamp",
        "magnitude": "Magnitude",
        "magnitude_err": "Error",
        "magnitude_error": "Error",
        "band": "Filter",
        "telescope": "Telescope",
    }
    for source, destination in rename.items():
        if source not in result:
            continue
        if destination not in result:
            result[destination] = result[source]
        else:
            result[destination] = result[destination].combine_first(result[source])
    timestamp = pd.to_datetime(result.get("Timestamp", pd.Series(pd.NaT, index=result.index)), errors="coerce", utc=True).dt.tz_localize(None)
    if "Timestamp" not in result and "mjd" in result:
        timestamp = pd.to_datetime(
            pd.to_numeric(result["mjd"], errors="coerce"),
            unit="D", origin="1858-11-17", errors="coerce",
        )
    numeric_time = pd.to_numeric(result.get("observation_time", result.get("Timestamp", pd.Series(np.nan, index=result.index))), errors="coerce")
    is_mjd = numeric_time.between(30000, 100000)
    mjd_timestamp = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns]")
    mjd_timestamp.loc[is_mjd] = pd.to_datetime(
        numeric_time.loc[is_mjd], unit="D", origin="1858-11-17", errors="coerce"
    )
    timestamp = timestamp.where(~is_mjd, mjd_timestamp)
    if "epoch_hjd" in result:
        hjd = pd.to_numeric(result["epoch_hjd"], errors="coerce")
        hjd_timestamp = pd.to_datetime(hjd - 2400000.5, unit="D", origin="1858-11-17", errors="coerce")
        # Numeric observation_time values are often MJD. pandas treats them as
        # nanoseconds unless converted explicitly, which puts valid points near 1970.
        valid_hjd = hjd.ge(2450000)
        timestamp = timestamp.where(~valid_hjd, hjd_timestamp)
    result["Timestamp"] = timestamp
    if "Error" not in result:
        result["Error"] = np.nan
    if "Filter" not in result:
        result["Filter"] = ""
    if "provider" in result:
        result["provider"] = result["provider"].astype(str).str.upper()
    return result


def create_lightcurve_plots(targets: pd.DataFrame, **kwargs):
    """Create per-target light-curve PNGs from the public report API.

    The report task historically uses the legacy ``Target`` column while the
    current selection notebooks use ``target_id``.  Normalize that boundary
    here so callers can pass either table convention.
    """
    from .tasks.reports import create_lightcurve_plots as _create_lightcurve_plots

    data = targets.copy()
    rename = {
        "target_id": "Target",
        "ra_deg": "RA_deg",
        "dec_deg": "Dec_deg",
        "magnitude_hsh": "mag_now",
    }
    for source, destination in rename.items():
        if source in data and destination not in data:
            data[destination] = data[source]
    if "observatory_photometry" in kwargs:
        kwargs["observatory_photometry"] = _normalize_observatory_photometry(kwargs["observatory_photometry"])
    return _create_lightcurve_plots(data, **kwargs)


def selection_summary_pdf(
    visibility_images: Sequence[str | Path],
    lightcurve_images: Sequence[str | Path],
    output_path: str | Path,
) -> Path:
    """Combine selected-target visibility and light-curve PNGs into one PDF."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image_paths = [Path(path) for path in (*visibility_images, *lightcurve_images)]
    with PdfPages(output) as pdf:
        for path in image_paths:
            if not path.exists():
                continue
            image = plt.imread(path)
            height, width = image.shape[:2]
            figsize = (11.0, max(4.0, 11.0 * height / width))
            figure, axis = plt.subplots(figsize=figsize)
            axis.imshow(image)
            axis.axis("off")
            pdf.savefig(figure, bbox_inches="tight", pad_inches=0.05)
            plt.close(figure)
    return output


def match_summary(coverage: pd.DataFrame, output_dir: str | Path, *, bands: Sequence[str] = tuple("ugrizy"), category_column: str = "category", category_label: str = "Category", name: str = "collection") -> pd.DataFrame:
    return write_collection_summaries(coverage, output_dir, bands=bands, category_column=category_column, category_label=category_label, name=name)


def property_distributions(coverage: pd.DataFrame, output_dir: str | Path, *, bands: Sequence[str] = tuple("ugrizy"), category_column: str = "category", category_label: str = "Category", name: str = "collection") -> None:
    return plot_collection_distributions(coverage, output_dir, bands=bands, category_column=category_column, category_label=category_label, name=name)


def main(coverage: pd.DataFrame, *, fits_dir: str | Path, asset_dir: str | Path, output_path: str | Path, category_name: str | None = None, bands: Sequence[str] = tuple("ugrizy"), verbose: bool = True) -> Path:
    return build_collection_report_pdf(coverage, fits_dir=fits_dir, asset_dir=asset_dir, output_path=output_path, catalog_name=category_name, bands=bands, manifest_path=manifest_path, verbose=verbose)



def sky_maps(coverage: pd.DataFrame, output_dir: str | Path, *, bands: Sequence[str] = tuple("ugrizy"), name: str = "collection") -> list[Path]:
    """Save total and per-band individual-visit sky maps."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ra = pd.to_numeric(coverage.get("ra_deg", coverage.get("RA")), errors="coerce")
    dec = pd.to_numeric(coverage.get("dec_deg", coverage.get("DEC")), errors="coerce")
    paths = []
    columns = [("total", "n_images_total")] + [(band, f"n_images_{band}") for band in bands]
    for label, column in columns:
        if column not in coverage:
            continue
        values = pd.to_numeric(coverage[column], errors="coerce").fillna(0)
        fig, axis = plt.subplots(figsize=(10, 5), dpi=180)
        scatter = axis.scatter(ra % 360, dec, c=values, s=8, cmap="viridis", linewidths=0)
        axis.invert_xaxis()
        axis.grid(alpha=0.25)
        axis.set(xlabel="RA [deg]", ylabel="DEC [deg]", title=f"{name} — individual visits ({label})")
        fig.colorbar(scatter, ax=axis, label="Number of visits")
        fig.tight_layout()
        path = out / f"sky_map_visits_{label}.png"
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths
