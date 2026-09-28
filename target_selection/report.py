"""Report products for collection matching analyses."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
import matplotlib.pyplot as plt
import pandas as pd

from .reporting import plot_collection_distributions, write_collection_summaries
from .collection_access import build_collection_report_pdf


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
