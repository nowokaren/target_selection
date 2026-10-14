"""Generic quality flags for image and coadd products."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd


def apply_coadd_quality_flags(
    frame: pd.DataFrame,
    bands: Sequence[str] = tuple("ugrizy"),
    manifest=None,
) -> pd.DataFrame:
    """Add per-band coadd, metric, pixel and complete-coadd flags."""
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
        result[f"has_coadd_{band}"] = result["coadd_bands"].fillna("").map(
            lambda value: band in {x.strip() for x in str(value).split(",") if x.strip()}
        )
        metric_cols = [
            f"coadd_maglim_{band}",
            f"coadd_psf_size_pixel_{band}",
            f"coadd_exposure_time_s_{band}",
            f"coadd_sky_noise_njy_{band}",
        ]
        metric_cols = [column for column in metric_cols if column in result]
        metrics_ok = (
            result[metric_cols].apply(pd.to_numeric, errors="coerce").notna().all(axis=1)
            if metric_cols else pd.Series(False, index=result.index)
        )
        exposure = f"coadd_exposure_time_s_{band}"
        if exposure in result:
            metrics_ok &= pd.to_numeric(result[exposure], errors="coerce").gt(0)
        result[f"has_valid_metrics_{band}"] = result[f"has_coadd_{band}"] & metrics_ok
        result[f"has_valid_pixels_{band}"] = [pixel_valid[(str(target), band)] for target in result["target_id"]]
        result[f"has_valid_coadd_{band}"] = (
            result[f"has_coadd_{band}"]
            & result[f"has_valid_metrics_{band}"]
            & result[f"has_valid_pixels_{band}"]
        )
        result[f"has_empty_coadd_{band}"] = (
            result[f"has_coadd_{band}"] & ~result[f"has_valid_pixels_{band}"] if checked else False
        )
    result["pixels_checked"] = checked
    result["coadd_n_valid_bands"] = result[[f"has_valid_coadd_{band}" for band in bands]].sum(axis=1)
    result["has_valid_coadd"] = result["coadd_n_valid_bands"].gt(0)
    result["has_valid_coadd_any"] = result["has_valid_coadd"]
    result["has_valid_metrics_any"] = result[[f"has_valid_metrics_{band}" for band in bands]].any(axis=1)
    result["has_valid_pixels_any"] = result[[f"has_valid_pixels_{band}" for band in bands]].any(axis=1)
    result["has_empty_coadd"] = result[[f"has_empty_coadd_{band}" for band in bands]].any(axis=1)
    return result
