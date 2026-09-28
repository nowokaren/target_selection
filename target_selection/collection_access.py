"""High-level access helpers for Rubin collections used by target selection.

The notebook-facing API in this module keeps collection-specific Butler/TAP
setup and repeated coverage/cutout orchestration out of notebooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
import re
import json

import numpy as np
import pandas as pd

from data_release_config import DataReleaseConfig, get_data_release


@dataclass
class CollectionContext:
    """Open clients and the release profile for one Rubin collection."""

    profile: DataReleaseConfig
    tap_service: Any
    butler: Any

    @property
    def name(self) -> str:
        return self.profile.name

    def list_tables(self):
        from .rsp_access import list_tables
        return list_tables(self.profile)

    def list_property_maps(self):
        from .rsp_access import list_property_maps
        return list_property_maps(self.profile)

    def list_images(self):
        from .rsp_access import list_images
        return list_images(self.profile)

    def describe(self):
        from .rsp_access import describe
        return describe(self.profile)

    def get_table(self, table_name: str, **kwargs):
        from .rsp_access import get_table
        return get_table(table_name, collection=self.profile, tap_service=self.tap_service, **kwargs)

    def get_property_map_values(self, positions, **kwargs):
        from .rsp_access import get_property_map_values
        return get_property_map_values(positions, collection=self.profile, butler=self.butler, **kwargs)

    def get_visit_coverage(self, positions, **kwargs):
        from .rsp_access import get_visit_coverage
        return get_visit_coverage(positions, collection=self.profile, tap_service=self.tap_service, **kwargs)

    def get_coadd_coverage(self, coverage, **kwargs):
        from .rsp_access import get_coadd_coverage
        return get_coadd_coverage(coverage, collection=self.profile, butler=self.butler, **kwargs)

    def get_coadd_cutouts(self, coverage, **kwargs):
        from .rsp_access import get_coadd_cutouts
        return get_coadd_cutouts(coverage, collection=self.profile, butler=self.butler, **kwargs)

    def get_image(self, targets, image_type: str = "coadd", **kwargs):
        from .rsp_access import get_image
        return get_image(targets, image_type=image_type, collection=self.profile, butler=self.butler, **kwargs)


def open_collection(name: str | DataReleaseConfig = "DP2") -> CollectionContext:
    """Open the TAP and Butler clients described by a collection profile."""
    from lsst.daf.butler import Butler
    from lsst.rsp import get_tap_service

    profile = get_data_release(name)
    return CollectionContext(profile, get_tap_service("tap"), Butler(profile.butler_repo, collections=profile.butler_collections))


def load_collection_coverage(targets: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2", tap_service: Any = None, tile_nside: int = 64, max_workers: int = 2, timeout_seconds: int = 7200, search_radius_deg: float = 0.35, cache_dir: str | Path | None = None, reuse_cache: bool = True, bands: Sequence[str] = tuple("ugrizy"), verbose: bool = True) -> pd.DataFrame:
    """Return one target row with individual-visit coverage by band."""
    from .reference_catalog import pivot_visit_summary, query_visit_summary_by_band

    profile = get_data_release(collection)
    work = targets.copy()
    if {"ra_deg", "dec_deg"} - set(work.columns):
        work = work.rename(columns={"RA": "ra_deg", "DEC": "dec_deg"})
    required = {"target_id", "ra_deg", "dec_deg"}
    missing = required - set(work.columns)
    if missing:
        raise ValueError(f"Targets are missing columns: {sorted(missing)}")
    visits = query_visit_summary_by_band(work[["target_id", "ra_deg", "dec_deg"]], tap_service=tap_service, data_release=profile, tile_nside=tile_nside, max_workers=max_workers, timeout_seconds=timeout_seconds, search_radius_deg=search_radius_deg, cache_dir=cache_dir, reuse_cache=reuse_cache, verbose=verbose)
    status = visits.attrs.get("target_status", pd.DataFrame())
    completed = status.loc[~status["visit_query_status"].eq("error"), "target_id"] if not status.empty else pd.Series(dtype=str)
    wide = pivot_visit_summary(visits, work["target_id"], bands, queried_target_ids=completed)
    result = work.merge(wide, on="target_id", how="left")
    if not status.empty:
        result = result.merge(status, on="target_id", how="left")
    result["n_images_total"] = pd.to_numeric(result.get("n_images_total", 0), errors="coerce").fillna(0).astype(int)
    result["has_visit_coverage"] = result["n_images_total"].gt(0)
    result.attrs["detector_matches"] = visits.attrs.get("detector_matches", pd.DataFrame())
    result.attrs["target_status"] = status
    return result


def load_collection_coadds(targets: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2", butler: Any, cache_path: str | Path | None = None, reuse_cache: bool = True, bands: Sequence[str] = tuple("ugrizy"), verbose: bool = True) -> pd.DataFrame:
    """Add direct coadd existence and available-band columns to target rows."""
    from .reference_catalog import query_direct_coadd_coverage

    profile = get_data_release(collection)
    work = targets.copy()
    if {"ra_deg", "dec_deg"} - set(work.columns):
        work = work.rename(columns={"RA": "ra_deg", "DEC": "dec_deg"})
    required = {"target_id", "ra_deg", "dec_deg"}
    missing = required - set(work.columns)
    if missing:
        raise ValueError(f"Targets are missing columns: {sorted(missing)}")
    covered = work.loc[work.get("has_visit_coverage", True).astype(bool), ["target_id", "ra_deg", "dec_deg"]]
    direct = query_direct_coadd_coverage(covered, butler=butler, data_release=profile, cache_path=cache_path, reuse_cache=reuse_cache, verbose=verbose)
    result = work.merge(direct, on="target_id", how="left")
    if "has_coadd" in result:
        result["has_coadd"] = result["has_coadd"].astype("boolean").fillna(False).astype(bool)
    else:
        result["has_coadd"] = False
    result["coadd_n_bands"] = pd.to_numeric(result.get("coadd_n_bands", 0), errors="coerce").fillna(0).astype(int)
    result["coadd_bands"] = result.get("coadd_bands", "").fillna("").astype(str)
    for band in bands:
        result[f"has_coadd_{band}"] = result["coadd_bands"].map(lambda value, b=band: b in {item.strip() for item in value.split(",") if item.strip()})
    return result


def generate_collection_cutouts(coverage: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2", butler: Any, output_dir: str | Path, fits_dir: str | Path, bands: Sequence[str] = tuple("ugrizy"), size_arcsec: float = 20.0, catalog_name: str | None = None, filename_prefix_column: str | None = None, add_rgb: bool = False, reuse_existing: bool = True, manifest_path: str | Path | None = None, verbose: bool = True) -> pd.DataFrame:
    """Generate and verify coadd cutouts for every target with a found coadd.

    A manifest row is ``reused`` only when the PNG and every expected FITS
    band exist. Only bands already registered by the coverage table are
    requested from Butler. A target is ``generated`` or ``partial`` only when
    files were actually written. Zero-band records and exceptions are recorded
    explicitly instead of being marked as generated.
    """
    import json
    import traceback
    from .reference_catalog import save_coadd_cutout_grid
    from tqdm.auto import tqdm

    release = get_data_release(collection)
    output_dir, fits_dir = Path(output_dir), Path(fits_dir)
    manifest_path = Path(manifest_path) if manifest_path is not None else None
    output_dir.mkdir(parents=True, exist_ok=True)
    fits_dir.mkdir(parents=True, exist_ok=True)
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.unlink(missing_ok=True)
    rows = coverage.loc[coverage["has_coadd"].fillna(False).astype(bool)].copy()
    if catalog_name is not None and "catalog" in rows:
        rows = rows.loc[rows["catalog"].eq(catalog_name)]
    iterator = rows.iterrows()
    if verbose:
        iterator = tqdm(iterator, total=len(rows), desc=f"Cutouts {catalog_name or release.name}")
    records = []

    def add_record(record: dict[str, Any]) -> None:
        records.append(record)
        if manifest_path is not None:
            pd.DataFrame([record]).to_csv(manifest_path, mode="a", header=not manifest_path.exists(), index=False)

    for _, row in iterator:
        target_name = str(row.get("DB_JNAME", row["target_id"]))
        safe_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(row["target_id"]))
        category = str(row.get(filename_prefix_column, "")).strip() if filename_prefix_column else ""
        category_prefix = re.sub(r"[^A-Za-z0-9_.+-]+", "_", category).strip("_")
        prefix = f"{category_prefix}_" if category_prefix else (f"{catalog_name}_" if catalog_name else "")
        png_path = output_dir / f"{prefix}{safe_name}_coadds.png"
        expected_bands = [str(band) for band in bands if bool(row.get(f"has_coadd_{band}", False))]
        if not expected_bands and str(row.get("coadd_bands", "")).strip():
            expected_bands = [band.strip() for band in str(row["coadd_bands"]).split(",") if band.strip() in {str(value) for value in bands}]
        target_file_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row["target_id"])).strip("_")
        expected_fits = [fits_dir / f"{prefix}{target_file_name}_{band}.fits" for band in expected_bands]
        target_keys = {_fits_key(target_file_name), _fits_key(str(row["target_id"]).split(":", 1)[-1])}

        def matching_fit_paths():
            matches = []
            for band in expected_bands:
                exact = fits_dir / f"{prefix}{target_file_name}_{band}.fits"
                candidates = [exact] if exact.exists() else [path for path in fits_dir.glob(f"*_{band}.fits") if any(_fits_key(path.stem.rsplit("_", 1)[0]).endswith(key) for key in target_keys)]
                if candidates:
                    matches.append(sorted(candidates)[0])
            return matches

        def matching_png_path():
            if png_path.exists():
                return png_path
            candidates = [path for path in output_dir.glob("*_coadds.png") if any(_fits_key(path.stem.removesuffix("_coadds")).endswith(key) for key in target_keys)]
            return sorted(candidates)[0] if candidates else None

        existing_fits = matching_fit_paths()
        existing_png = matching_png_path()
        base = {"target_id": row["target_id"], "DB_JNAME": target_name, "png": str(existing_png) if existing_png else "", "requested_bands": ",".join(expected_bands), "expected_bands": ",".join(expected_bands), "written_bands": ",".join(path.name.rsplit("_", 1)[-1].removesuffix(".fits") for path in existing_fits), "n_bands": len(existing_fits), "status": "", "error_type": "", "error_message": "", "diagnostics_json": "[]", "error_traceback": "", "cutout_verified": False}
        if not expected_bands:
            base.update({"status": "coverage_inconsistent", "error_type": "no_coadd_registered", "error_message": "The target was selected as has_coadd=True but no coadd band was registered in coverage."})
            add_record(base)
            continue
        if reuse_existing and expected_fits and existing_png is not None and len(existing_fits) == len(expected_fits):
            base.update({"status": "reused", "cutout_verified": True})
            add_record(base)
            continue
        try:
            cutout_row = row.copy()
            rename_coordinates = {}
            if "ra_deg" not in cutout_row.index and "RA" in cutout_row.index:
                rename_coordinates["RA"] = "ra_deg"
            if "dec_deg" not in cutout_row.index and "DEC" in cutout_row.index:
                rename_coordinates["DEC"] = "dec_deg"
            if rename_coordinates:
                cutout_row = cutout_row.rename(rename_coordinates)
            band_diagnostics = []
            returned_path, n_bands = save_coadd_cutout_grid(cutout_row, butler=butler, data_release=release, output_path=png_path, bands=expected_bands, size_arcsec=size_arcsec, save_fits=True, fits_output_dir=fits_dir, fits_name_prefix=category_prefix, add_rgb=add_rgb, diagnostics=band_diagnostics)
            diagnostic_tracebacks = [str(item.get("traceback", "")) for item in band_diagnostics if item.get("traceback")]
            base.update({"diagnostics_json": json.dumps(band_diagnostics, default=str, ensure_ascii=False), "error_traceback": "\n\n".join(diagnostic_tracebacks)})
            existing_fits = matching_fit_paths()
            existing_png = matching_png_path()
            png_exists = existing_png is not None and bool(returned_path is not None or existing_png.exists())
            n_bands = len(existing_fits)
            base.update({"png": str(existing_png) if png_exists else "", "written_bands": ",".join(path.name.rsplit("_", 1)[-1].removesuffix(".fits") for path in existing_fits), "n_bands": n_bands, "cutout_verified": bool(png_exists and n_bands == len(expected_bands))})
            missing_bands = [band for band in expected_bands if band not in set(base["written_bands"].split(","))]
            if base["cutout_verified"]:
                base["status"] = "generated" if n_bands == len(expected_fits) else "partial"
                if missing_bands:
                    base["error_type"] = "missing_bands"
                    base["error_message"] = f"No cutout file was written for registered coadd bands: {','.join(missing_bands)}."
            elif n_bands:
                base["status"] = "partial"
                if png_exists:
                    base["error_type"] = "missing_bands"
                    base["error_message"] = f"PNG created, but no FITS file was written for registered coadd bands: {','.join(missing_bands)}."
                else:
                    base["error_type"] = "missing_png"
                    base["error_message"] = "FITS cutouts were written, but the PNG grid was not created."
            else:
                base["status"] = "no_valid_cutout"
                diagnostic_statuses = {str(item.get("status", "")) for item in band_diagnostics}
                if "empty_cutout" in diagnostic_statuses:
                    base["error_type"] = "empty_cutout"
                    base["error_message"] = "The registered coadd exists and was loaded, but the target cutout contains no finite pixels."
                elif "outside_image" in diagnostic_statuses:
                    base["error_type"] = "outside_image"
                    base["error_message"] = "The registered coadd was queried, but the target lies outside the usable image footprint."
                elif "no_reference" in diagnostic_statuses:
                    base["error_type"] = "no_reference_for_band"
                    base["error_message"] = "Coverage registered a coadd, but the Butler query returned no reference for the requested band."
                else:
                    base["error_type"] = "no_rendered_band"
                    base["error_message"] = "The registered coadd bands produced no renderable cutout."
        except Exception as error:
            existing_fits = matching_fit_paths()
            existing_png = matching_png_path()
            base.update({"png": str(existing_png) if existing_png else "", "written_bands": ",".join(path.name.rsplit("_", 1)[-1].removesuffix(".fits") for path in existing_fits), "n_bands": len(existing_fits), "status": "partial_error" if existing_fits else "error", "error_type": type(error).__name__, "error_message": str(error), "error_traceback": traceback.format_exc(), "cutout_verified": bool(existing_png is not None and len(existing_fits) == len(expected_bands))})
        add_record(base)
    return pd.DataFrame(records)

def _fits_key(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value).strip()).upper()


def build_cutout_pdf(coverage: pd.DataFrame, *, fits_dir: str | Path, output_path: str | Path, catalog_name: str | None = None, bands: Sequence[str] = tuple("ugrizy"), verbose: bool = True) -> Path:
    """Create one PDF page per target, showing all available FITS bands."""
    import json
    from astropy.io import fits
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from tqdm.auto import tqdm

    fits_dir = Path(fits_dir); output_path = Path(output_path)
    prefix = f"{catalog_name}_" if catalog_name else ""
    rows = coverage.copy()
    if catalog_name is not None and "catalog" in rows:
        rows = rows.loc[rows["catalog"].eq(catalog_name)]
    if "has_coadd" in rows:
        rows = rows.loc[rows["has_coadd"].fillna(False).astype(bool)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    iterator = rows.itertuples(index=False)
    if verbose:
        iterator = tqdm(iterator, total=len(rows), desc=f"PDF {catalog_name or 'cutouts'}")
    with PdfPages(output_path) as pdf:
        for row in iterator:
            name = getattr(row, "DB_JNAME", getattr(row, "target_id", "target"))
            target_file_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(row, "target_id", name))).strip("_")
            files = {}
            for band in bands:
                candidates = sorted(fits_dir.glob(f"*_{target_file_name}_{band}.fits"))
                if candidates:
                    files[str(band)] = candidates[0]
            fig, axes = plt.subplots(2, 3, figsize=(11, 7.5), squeeze=False)
            ra = getattr(row, "RA", getattr(row, "ra_deg", np.nan)); dec = getattr(row, "DEC", getattr(row, "dec_deg", np.nan))
            fig.suptitle(f"{name}\nRA={ra:.6f}  DEC={dec:.6f}", fontsize=13)
            for axis, band in zip(axes.flat, bands):
                path = files.get(str(band))
                if path is None:
                    axis.axis("off"); axis.text(0.5, 0.5, f"{band}: FITS no disponible", ha="center", va="center")
                    continue
                try:
                    data = np.squeeze(np.asarray(fits.getdata(path), dtype=float)); finite = data[np.isfinite(data)]
                    lo, hi = np.nanpercentile(finite, [1, 99]) if finite.size else (0, 1)
                    axis.imshow(data, origin="lower", cmap="gray", vmin=lo, vmax=hi); axis.set_title(str(band)); axis.set_xticks([]); axis.set_yticks([])
                except Exception as error:
                    axis.axis("off"); axis.text(0.5, 0.5, f"{band}: {type(error).__name__}", ha="center", va="center")
            fig.tight_layout(rect=[0, 0, 1, 0.94]); pdf.savefig(fig); plt.close(fig)
    return output_path


def build_collection_report_pdf(coverage: pd.DataFrame, *, fits_dir: str | Path, asset_dir: str | Path, output_path: str | Path, catalog_name: str | None = None, bands: Sequence[str] = tuple("ugrizy"), manifest_path: str | Path | None = None, verbose: bool = True) -> Path:
    """Create an English report PDF: maps, distributions, tables, then target cutouts."""
    from astropy.io import fits
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from tqdm.auto import tqdm

    fits_dir, asset_dir, output_path = Path(fits_dir), Path(asset_dir), Path(output_path)
    rows = coverage.copy()
    if catalog_name is not None and "catalog" in rows:
        rows = rows.loc[rows["catalog"].eq(catalog_name)].copy()
    rows = rows.loc[rows.get("has_coadd", True).fillna(False).astype(bool)].copy()
    # FITS may have been generated with a catalog prefix or migrated later to
    # filenames beginning with a category/status prefix. Discover all FITS and
    # match them to targets by normalized DB_JNAME/target_id below.
    grouped: dict[str, dict[str, Path]] = {}
    for path in sorted(fits_dir.glob("*.fits")):
        stem = path.stem
        band = stem.rsplit("_", 1)[-1].lower()
        if band not in {str(value).lower() for value in bands}:
            continue
        target_part = stem.rsplit("_", 1)[0]
        grouped.setdefault(_fits_key(target_part), {})[band] = path
    assets = sorted(asset_dir.glob("*.png"))
    assets = [path for path in assets if path.exists()]
    diagnostic_status: dict[tuple[str, str], str] = {}
    diagnostic_error: dict[tuple[str, str], str] = {}
    if manifest_path is not None and Path(manifest_path).exists():
        manifest = pd.read_csv(manifest_path, low_memory=False)
        for record in manifest.to_dict("records"):
            target = str(record.get("target_id", ""))
            try:
                diagnostics = json.loads(record.get("diagnostics_json", "[]") or "[]")
            except (TypeError, json.JSONDecodeError):
                diagnostics = []
            for diagnostic in diagnostics:
                band = str(diagnostic.get("band", ""))
                if band in {str(value) for value in bands}:
                    diagnostic_status[(target, band)] = str(diagnostic.get("status", ""))
                    if diagnostic.get("error_message"):
                        diagnostic_error[(target, band)] = str(diagnostic["error_message"])
            error_type = str(record.get("error_type", ""))
            if error_type == "empty_cutout":
                for band in str(record.get("expected_bands", "")).split(","):
                    if band in {str(value) for value in bands}:
                        diagnostic_status.setdefault((target, band), "empty_cutout")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def asset_caption(path):
        stem = path.stem.lower()
        if stem.startswith("sky_map_visits"):
            return "Each plotted point represents one target. The map shows the sky positions of targets with at least one individual LSST visit in the indicated band or in the total sample."
        if stem == "summary_by_band":
            return "All entries are numbers of targets. Columns report targets with visits, targets with a coadd found, targets with coadd metrics, and targets whose coadd image has no valid pixels, by band and in total."
        if stem.startswith("summary_by_"):
            return "All entries are numbers of targets. Rows classify targets by the catalog category and by visit/coadd status; columns give the total and the corresponding band counts."
        if stem == "distributions":
            return "Histogram y-axes show numbers of targets. The first panel uses total individual LSST visits per target; coverage panels count targets, while the property panels show numbers of targets as a function of coadd or visit image properties."
        if stem.startswith("distributions_by_"):
            return "Histogram y-axes show numbers of targets. The same visit, band-coverage, coadd-band, and image-property distributions are separated by catalog category."
        if stem.startswith("individual_visits_by_"):
            return "Each histogram y-axis shows numbers of targets in one catalog category, and each x-axis value is the number of individual visits in that band for one target. Targets with zero visits in that band are excluded."
        return "Diagnostic plot: plotted counts are numbers of targets unless an axis explicitly identifies visits, bands, images, or an image property."

    def add_asset(pdf, path):
        image = plt.imread(path)
        fig, axis = plt.subplots(figsize=(11, 8.5), dpi=180)
        axis.imshow(image, interpolation="none"); axis.axis("off")
        fig.suptitle(path.stem.replace("_", " ").title(), fontsize=14, y=0.985)
        fig.text(0.5, 0.935, asset_caption(path), ha="center", va="top", fontsize=10, wrap=True)
        fig.tight_layout(rect=[0, 0.02, 1, 0.88]); pdf.savefig(fig); plt.close(fig)

    def number(row, column):
        value = pd.to_numeric(pd.Series([getattr(row, column, np.nan)]), errors="coerce").iloc[0]
        return "—" if pd.isna(value) else f"{value:.2f}"

    def files_for_row(row):
        candidates = []
        for column in ("DB_JNAME", "target_id", "Target"):
            value = getattr(row, column, None)
            if value is not None and str(value).strip() and str(value) != "nan":
                candidates.append(_fits_key(value))
        for key, files in grouped.items():
            if any(key == candidate or key.endswith(candidate) or candidate.endswith(key) for candidate in candidates):
                return files
        return {}

    # The report population is defined by direct coadd discovery. Do not
    # silently drop a coadd target because an earlier cutout attempt failed;
    # show a diagnostic page and let the manifest classify the failure.
    target_rows = list(rows.itertuples(index=False))
    missing_fits = sum(not files_for_row(row) for row in target_rows)
    if verbose:
        print(f"Report target pages: {len(target_rows):,} coadd targets; {missing_fits:,} currently without verified FITS.")
    iterator = target_rows
    if verbose:
        iterator = tqdm(iterator, total=len(target_rows), desc="Report target pages")
    with PdfPages(output_path) as pdf:
        for asset in assets:
            add_asset(pdf, asset)
        for row in iterator:
            name = str(getattr(row, "DB_JNAME", getattr(row, "target_id", "target")))
            files = files_for_row(row)
            fig, axes = plt.subplots(2, 3, figsize=(11, 8.5), dpi=160, squeeze=False)
            found_bands = str(getattr(row, "coadd_bands", "") or "—")
            valid_bands = ",".join(str(b) for b in bands if bool(getattr(row, f"has_valid_coadd_{b}", False))) or "—"
            fig.suptitle(name, fontsize=12, y=0.97)
            for axis, band in zip(axes.flat, bands):
                path = files.get(str(band))
                if path is None:
                    target_id = str(getattr(row, "target_id", ""))
                    status = diagnostic_status.get((target_id, str(band)), "")
                    if status == "empty_cutout":
                        message = f"{band}: coadd exists but has no valid pixels"
                    elif status == "outside_image":
                        message = f"{band}: target outside usable coadd image"
                    elif status == "no_reference":
                        message = f"{band}: coverage registered, but no Butler reference"
                    elif status in {"butler_error", "wcs_or_image_error", "render_or_write_error"}:
                        message = f"{band}: cutout error ({status})"
                    elif bool(getattr(row, f"has_coadd_{band}", False)):
                        message = f"{band}: coadd registered, cutout unavailable"
                    else:
                        message = f"{band}: no coadd registered"
                    axis.axis("off"); axis.text(0.5, 0.5, message, ha="center", va="center", wrap=True); continue
                try:
                    with fits.open(path, memmap=True) as hdul:
                        data = np.squeeze(hdul[0].data)
                        if data.ndim > 2: data = data.reshape(data.shape[-2], data.shape[-1])
                        step = max(1, max(data.shape, default=0) // 256)
                        data = np.asarray(data[::step, ::step], dtype=float)
                    finite = data[np.isfinite(data)]
                    if not finite.size: raise ValueError("no finite pixels")
                    lo, hi = np.nanpercentile(finite, [1, 99]); axis.imshow(data, origin="lower", cmap="gray", vmin=lo, vmax=hi, rasterized=True)
                    is_valid = bool(getattr(row, f"has_valid_coadd_{band}", False))
                    metric = f"depth={number(row, f'coadd_maglim_{band}')}  FWHM={number(row, f'coadd_psf_fwhm_arcsec_{band}')}"
                    axis.set_title(f"{band}  {'valid metrics' if is_valid else 'metrics unavailable'}\n{metric}", fontsize=8)
                    axis.set_xticks([]); axis.set_yticks([])
                except Exception as error:
                    axis.axis("off"); axis.text(0.5, 0.5, f"{band}: {type(error).__name__}", ha="center", va="center")
            fig.subplots_adjust(left=0.04, right=0.98, bottom=0.08, top=0.88, wspace=0.14, hspace=0.42)
            pdf.savefig(fig); plt.close(fig)
    return output_path
