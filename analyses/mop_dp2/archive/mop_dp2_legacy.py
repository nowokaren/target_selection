"""Reusable MOP x DP2 catalog helpers for notebooks and batch runs."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm, Normalize
import matplotlib.pyplot as plt


def load_mop_targets(root: str | Path, source_path: str | Path | None = None):
    """Load the enriched MOP table or the newest complete raw API export.

    Search order is explicit enriched/snapshot tables, then the newest
    ``raw/mop_targets_*.csv`` export, which is where the API downloader stores
    the complete 6,445-target snapshot.
    """
    root = Path(root)
    candidates = []
    if source_path is not None:
        candidates = [Path(source_path)]
    else:
        candidates = [root / "tables" / "mop_targets_enriched.csv", root / "tables" / "mop_targets_snapshot.csv"]
        candidates += sorted((root / "raw").glob("mop_targets_*.csv"), key=lambda x: x.stat().st_mtime, reverse=True)
    candidates = [p for p in candidates if p.exists() and ".ipynb_checkpoints" not in p.parts]
    if not candidates:
        raise FileNotFoundError(f"No encontré catálogo MOP en {root / 'tables'} ni en {root / 'raw'}")
    path = candidates[0]
    table = pd.read_csv(path).loc[:, lambda x: ~x.columns.duplicated()].copy()
    if "target_id" not in table:
        source = next((c for c in ("Target", "Name", "target_name") if c in table), None)
        if source is None:
            raise ValueError("El catálogo MOP no tiene Target, Name ni target_name")
        table.insert(0, "target_id", table[source].astype(str).str.strip())
    table["target_id"] = table["target_id"].astype(str).str.strip()
    if "Target" not in table:
        table["Target"] = table["target_id"]
    ra = next((c for c in ("RA_deg", "ra_deg", "Right Ascension", "ra") if c in table), None)
    dec = next((c for c in ("Dec_deg", "dec_deg", "Declination", "dec") if c in table), None)
    if ra is None or dec is None:
        raise ValueError(f"No encuentro RA/Dec en {path}: {list(table.columns)}")
    table["ra_deg"] = pd.to_numeric(table[ra], errors="coerce")
    table["dec_deg"] = pd.to_numeric(table[dec], errors="coerce")
    table["RA_deg"] = table["ra_deg"]
    table["Dec_deg"] = table["dec_deg"]
    table["coordinates_valid"] = table[["ra_deg", "dec_deg"]].notna().all(axis=1)
    table = table.drop_duplicates("target_id")
    return table.reset_index(drop=True), path


def apply_coadd_quality_flags(frame: pd.DataFrame, bands: Sequence[str] = tuple("ugrizy"), manifest=None) -> pd.DataFrame:
    """Add dataset, metric, pixel and final coadd-validity flags."""
    bands = tuple(bands)
    result = frame.copy()
    checked = manifest is not None
    if isinstance(manifest, (str, Path)):
        path = Path(manifest)
        manifest = pd.read_csv(path) if path.exists() else pd.DataFrame()
    records = manifest.to_dict("records") if isinstance(manifest, pd.DataFrame) else list(manifest or [])
    pixel_valid = {(str(t), band): False for t in result["target_id"] for band in bands}
    for row in records:
        target_id = str(row.get("target_id", row.get("Target", "")))
        written = {x.strip() for x in str(row.get("written_bands", "")).split(",") if x.strip()}
        if str(row.get("status", "")).lower() == "reused":
            written |= {x.strip() for x in str(row.get("available_bands", "")).split(",") if x.strip()}
        for band in written:
            if band in bands:
                pixel_valid[(target_id, band)] = True
    for band in bands:
        result[f"has_coadd_{band}"] = result["coadd_bands"].fillna("").map(lambda v: band in {x.strip() for x in str(v).split(",") if x.strip()})
        metric_cols = [f"coadd_maglim_{band}", f"coadd_psf_size_pixel_{band}", f"coadd_exposure_time_s_{band}", f"coadd_sky_noise_njy_{band}"]
        metric_cols = [c for c in metric_cols if c in result]
        metrics_ok = result[metric_cols].apply(pd.to_numeric, errors="coerce").notna().all(axis=1) if metric_cols else pd.Series(False, index=result.index)
        exposure = f"coadd_exposure_time_s_{band}"
        if exposure in result:
            metrics_ok &= pd.to_numeric(result[exposure], errors="coerce").gt(0)
        result[f"has_valid_metrics_{band}"] = result[f"has_coadd_{band}"] & metrics_ok
        result[f"has_valid_pixels_{band}"] = [pixel_valid[(str(t), band)] for t in result["target_id"]]
        result[f"has_valid_coadd_{band}"] = result[f"has_coadd_{band}"] & result[f"has_valid_metrics_{band}"] & result[f"has_valid_pixels_{band}"]
        result[f"has_empty_coadd_{band}"] = result[f"has_coadd_{band}"] & ~result[f"has_valid_pixels_{band}"] if checked else False
    result["pixels_checked"] = checked
    result["coadd_n_valid_bands"] = result[[f"has_valid_coadd_{b}" for b in bands]].sum(axis=1)
    result["has_valid_coadd"] = result["coadd_n_valid_bands"].gt(0)
    result["has_valid_coadd_any"] = result["has_valid_coadd"]
    result["has_valid_metrics_any"] = result[[f"has_valid_metrics_{b}" for b in bands]].any(axis=1)
    result["has_valid_pixels_any"] = result[[f"has_valid_pixels_{b}" for b in bands]].any(axis=1)
    result["has_empty_coadd"] = result[[f"has_empty_coadd_{b}" for b in bands]].any(axis=1)
    return result


def query_visit_centers_by_band(tap_service, release):
    """Query one sky position per DP2 visit and band for background skyplots."""
    columns = release.visit_columns
    query = (f"SELECT vd.{columns['visitId']} AS visitId, vd.{columns['band']} AS band, "
             f"AVG(vd.{release.tap_ra}) AS ra, AVG(vd.{release.tap_dec}) AS dec "
             f"FROM {release.tap_visit_table} AS vd GROUP BY vd.{columns['visitId']}, vd.{columns['band']}")
    job = tap_service.submit_job(query)
    job.run()
    job.wait(phases=["COMPLETED", "ERROR"])
    if job.phase == "ERROR":
        job.raise_if_error()
    return job.fetch_result().to_table().to_pandas()


def make_skyplots(catalog: pd.DataFrame, visit_centers: pd.DataFrame, output_dir: str | Path, bands: Sequence[str] = tuple("ugrizy")):
    """Write per-band visits, combined visits and valid-coadd skyplots."""
    bands = tuple(bands); output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    centers = visit_centers.copy(); centers.columns = [str(c).lower() for c in centers.columns]
    centers["band"] = centers["band"].astype(str).str.lower(); centers["ra"] = pd.to_numeric(centers["ra"], errors="coerce"); centers["dec"] = pd.to_numeric(centers["dec"], errors="coerce"); centers = centers.dropna(subset=["ra", "dec"])
    def scatter(ax, background, points, count):
        if not background.empty: ax.scatter(background.ra, background.dec, s=1, c="0.75", alpha=.18, rasterized=True)
        points = points.loc[points[count].fillna(0).gt(0)]
        if points.empty: return None
        values = points[count].clip(lower=1); norm = LogNorm(vmin=1, vmax=max(2, values.max())) if values.max() > 1 else Normalize(0, 1)
        return ax.scatter(points.ra_deg, points.dec_deg, c=values, s=12, cmap="viridis", norm=norm, linewidths=0)
    fig, axes = plt.subplots(2, 3, figsize=(18, 8), sharex=True, sharey=True); last = None
    for ax, band in zip(axes.flat, bands):
        last = scatter(ax, centers.loc[centers.band.eq(band)], catalog, f"n_images_{band}"); ax.set_title(band); ax.set_xlim(360, 0); ax.set_ylim(-90, 20); ax.set_xlabel("RA [deg]"); ax.set_ylabel("Dec [deg]")
    if last is not None: fig.colorbar(last, ax=axes.ravel().tolist(), label="Número de visitas")
    fig.tight_layout(); fig.savefig(output_dir / "visits_by_band.png", dpi=180); plt.close(fig)
    background = centers.drop_duplicates(["visitid", "band"]) if "visitid" in centers else centers
    fig, ax = plt.subplots(figsize=(12, 6)); last = scatter(ax, background, catalog, "n_images_total"); ax.set_title("Targets MOP con visitas DP2"); ax.set_xlim(360, 0); ax.set_ylim(-90, 20); ax.set_xlabel("RA [deg]"); ax.set_ylabel("Dec [deg]")
    if last is not None: fig.colorbar(last, ax=ax, label="Número total de visitas")
    fig.tight_layout(); fig.savefig(output_dir / "visits_all_bands.png", dpi=180); plt.close(fig)
    fig, ax = plt.subplots(figsize=(12, 6)); last = scatter(ax, centers.iloc[0:0], catalog, "coadd_n_valid_bands"); ax.set_title("Targets MOP con coadd válido"); ax.set_xlim(360, 0); ax.set_ylim(-90, 20); ax.set_xlabel("RA [deg]"); ax.set_ylabel("Dec [deg]")
    if last is not None: fig.colorbar(last, ax=ax, label="Bandas con coadd válido")
    fig.tight_layout(); fig.savefig(output_dir / "valid_coadds_all_bands.png", dpi=180); plt.close(fig)
