"""Large-catalog enrichment with Rubin visit and deep-coadd information."""

from __future__ import annotations

import gc
import hashlib
import json
import math
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from data_release_config import DataReleaseConfig, get_data_release

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


COADD_PROPERTY_MAPS: Mapping[str, tuple[str, str]] = {
    "maglim": (
        "deepCoadd_psf_maglim_consolidated_map_weighted_mean",
        "coadd_maglim",
    ),
    "psf_size": (
        "deepCoadd_psf_size_consolidated_map_weighted_mean",
        "coadd_psf_size_pixel",
    ),
    "exposure_time": (
        "deepCoadd_exposure_time_consolidated_map_sum",
        "coadd_exposure_time_s",
    ),
    "sky_noise": (
        "deepCoadd_sky_noise_consolidated_map_weighted_mean",
        "coadd_sky_noise_njy",
    ),
    "sky_background": (
        "deepCoadd_sky_background_consolidated_map_weighted_mean",
        "coadd_sky_background_njy",
    ),
    "epoch_min": ("deepCoadd_epoch_consolidated_map_min", "coadd_first_mjd"),
    "epoch_max": ("deepCoadd_epoch_consolidated_map_max", "coadd_last_mjd"),
}

DEFAULT_QUALITY_WEIGHTS = {
    "depth": 0.50,
    "seeing": 0.35,
    "band_coverage": 0.15,
}


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_") or "catalog"


@dataclass(frozen=True)
class ReferenceCatalogConfig:
    """Configuration for enriching a large coordinate catalog."""

    input_path: str
    name: str = "reference_catalog"
    output_dir: str = "outputs/catalog_enrichment"
    cache_dir: str = "outputs/_cache/reference_catalog"
    data_release: str = "DP2"
    target_id_column: str = "object_id"
    ra_column: str = "ra"
    dec_column: str = "dec"
    extra_info_column: str | None = "extra_info"
    bands: tuple[str, ...] = ("u", "g", "r", "i", "z", "y")
    property_maps: tuple[str, ...] = (
        "maglim",
        "psf_size",
        "exposure_time",
        "sky_noise",
    )
    coadd_skymap: str | None = None
    coadd_pixel_scale_arcsec: float = 0.2
    partial_property_map_reads: bool = True
    visit_query_scope: str = "coadd"
    tap_tile_nside: int = 64
    tap_max_workers: int = 2
    tap_timeout_seconds: int = 7200
    detector_search_radius_deg: float = 0.35
    target_limit: int | None = None
    reuse_cache: bool = True
    generate_cutouts: bool = True
    max_cutouts: int = 100
    cutout_size_arcsec: float = 20.0
    cutout_grades: tuple[str, ...] = ("A", "B")
    cutout_bands: tuple[str, ...] = ("u", "g", "r", "i", "z", "y")
    quality_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_QUALITY_WEIGHTS)
    )
    verbose: bool = True

    def validate(self) -> "ReferenceCatalogConfig":
        errors: list[str] = []
        if not self.input_path:
            errors.append("input_path is required")
        if not self.bands:
            errors.append("bands cannot be empty")
        unknown_maps = sorted(set(self.property_maps) - set(COADD_PROPERTY_MAPS))
        if unknown_maps:
            errors.append(f"Unknown property_maps: {unknown_maps}")
        if self.visit_query_scope not in {"coadd", "all"}:
            errors.append("visit_query_scope must be 'coadd' or 'all'")
        if self.tap_tile_nside < 1 or self.tap_tile_nside & (self.tap_tile_nside - 1):
            errors.append("tap_tile_nside must be a positive power of two")
        if self.tap_max_workers < 1:
            errors.append("tap_max_workers must be positive")
        if self.tap_timeout_seconds < 1:
            errors.append("tap_timeout_seconds must be positive")
        if self.detector_search_radius_deg <= 0:
            errors.append("detector_search_radius_deg must be positive")
        if self.target_limit is not None and self.target_limit < 1:
            errors.append("target_limit must be positive or omitted")
        if self.max_cutouts < 0:
            errors.append("max_cutouts cannot be negative")
        if self.cutout_size_arcsec <= 0:
            errors.append("cutout_size_arcsec must be positive")
        if self.coadd_pixel_scale_arcsec <= 0:
            errors.append("coadd_pixel_scale_arcsec must be positive")
        missing_weights = set(DEFAULT_QUALITY_WEIGHTS) - set(self.quality_weights)
        if missing_weights:
            errors.append(f"quality_weights is missing: {sorted(missing_weights)}")
        weights = [float(self.quality_weights[key]) for key in DEFAULT_QUALITY_WEIGHTS]
        if any(weight < 0 for weight in weights) or not sum(weights) > 0:
            errors.append("quality_weights must be non-negative and have a positive sum")
        if errors:
            raise ValueError("Invalid reference catalog configuration:\n- " + "\n- ".join(errors))
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ReferenceCatalogConfig":
        settings = dict(values.get("catalog", values))
        for key in ("bands", "property_maps", "cutout_grades", "cutout_bands"):
            if key in settings:
                settings[key] = _tuple(settings[key])
        if "quality_weights" in values:
            settings["quality_weights"] = dict(values["quality_weights"])
        return cls(**settings).validate()


@dataclass
class ReferenceCatalogResult:
    """Products from one large-catalog enrichment run."""

    config: ReferenceCatalogConfig
    catalog: pd.DataFrame
    output_dir: Path
    catalog_path: Path
    cutout_plan_path: Path
    manifest_path: Path
    summary: Mapping[str, Any]


def load_reference_catalog_config(path: str | Path) -> ReferenceCatalogConfig:
    """Load a large-catalog TOML or JSON configuration."""
    path = Path(path)
    if path.suffix.lower() == ".toml":
        with path.open("rb") as stream:
            values = tomllib.load(stream)
    elif path.suffix.lower() == ".json":
        values = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Reference catalog configuration must use .toml or .json")
    return ReferenceCatalogConfig.from_mapping(values)


def _extra_value(series: pd.Series, key: str) -> pd.Series:
    pattern = rf"(?:^|,){re.escape(key)}=([^,]*)"
    return series.fillna("").astype(str).str.extract(pattern, expand=False)


def _normalized_grade(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    matches = re.findall(r"(?<![A-Z])([A-E])(?:[+-])?(?![A-Z])", str(value).upper())
    return matches[0] if len(set(matches)) == 1 else None


def load_target_catalog(config: ReferenceCatalogConfig) -> pd.DataFrame:
    """Load, validate, and normalize an input coordinate catalog."""
    path = Path(config.input_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Input catalog does not exist: {path}")
    data = pd.read_csv(path)
    required = {config.target_id_column, config.ra_column, config.dec_column}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"Input catalog is missing columns: {sorted(missing)}")
    data = data.copy()
    data["target_id"] = data[config.target_id_column].astype(str).str.strip()
    data["ra_deg"] = pd.to_numeric(data[config.ra_column], errors="coerce")
    data["dec_deg"] = pd.to_numeric(data[config.dec_column], errors="coerce")
    invalid = (
        data["target_id"].eq("")
        | data["ra_deg"].isna()
        | data["dec_deg"].isna()
        | ~data["ra_deg"].between(0, 360, inclusive="left")
        | ~data["dec_deg"].between(-90, 90, inclusive="both")
    )
    if invalid.any():
        examples = data.loc[invalid, [config.target_id_column, config.ra_column, config.dec_column]].head(5)
        raise ValueError(
            f"Input catalog has {int(invalid.sum())} invalid identifier/coordinate row(s):\n"
            + examples.to_string(index=False)
        )
    if data["target_id"].duplicated().any():
        examples = data.loc[data["target_id"].duplicated(False), "target_id"].head(5).tolist()
        raise ValueError(f"target_id values must be unique; duplicates include {examples}")

    if config.extra_info_column and config.extra_info_column in data:
        extra = data[config.extra_info_column]
        data["vetting_grade_raw"] = _extra_value(extra, "vetting_grade")
        data["catalog_grade_raw"] = _extra_value(extra, "Grade")
        vetting = data["vetting_grade_raw"].map(_normalized_grade)
        catalog = data["catalog_grade_raw"].map(_normalized_grade)
        data["selection_grade"] = vetting.fillna(catalog)
        data["selection_grade_source"] = np.select(
            [vetting.notna(), catalog.notna()],
            ["vetting_grade", "Grade"],
            default="unavailable",
        )
    else:
        data["vetting_grade_raw"] = pd.NA
        data["catalog_grade_raw"] = pd.NA
        data["selection_grade"] = pd.NA
        data["selection_grade_source"] = "unavailable"
    if config.target_limit is not None:
        data = data.head(config.target_limit).copy()
    return data.reset_index(drop=True)


def _catalog_fingerprint(targets: pd.DataFrame, release: DataReleaseConfig) -> str:
    payload = targets[["target_id", "ra_deg", "dec_deg"]].sort_values("target_id")
    digest = hashlib.sha256()
    digest.update(release.name.encode())
    digest.update(pd.util.hash_pandas_object(payload, index=False).values.tobytes())
    return digest.hexdigest()[:20]


def _tap_result_frame(job: Any) -> pd.DataFrame:
    table = job.fetch_result().to_table()
    frame = table.to_pandas() if hasattr(table, "to_pandas") else pd.DataFrame(table)
    frame.columns = [str(column).lower() for column in frame.columns]
    return frame


def _angular_separation_deg(
    ra1: np.ndarray | float,
    dec1: np.ndarray | float,
    ra2: np.ndarray | float,
    dec2: np.ndarray | float,
) -> np.ndarray:
    """Return great-circle separation in degrees."""
    ra1_rad, dec1_rad, ra2_rad, dec2_rad = [
        np.deg2rad(np.asarray(value, dtype=float))
        for value in np.broadcast_arrays(ra1, dec1, ra2, dec2)
    ]
    cosine = (
        np.sin(dec1_rad) * np.sin(dec2_rad)
        + np.cos(dec1_rad) * np.cos(dec2_rad) * np.cos(ra1_rad - ra2_rad)
    )
    return np.rad2deg(np.arccos(np.clip(cosine, -1.0, 1.0)))


def _match_targets_to_detector_rows(
    tile_targets: pd.DataFrame,
    detector_rows: pd.DataFrame,
    search_radius_deg: float,
) -> pd.DataFrame:
    """Match target points to exact detector quadrilaterals locally."""
    if detector_rows.empty:
        return pd.DataFrame()
    outputs: list[pd.DataFrame] = []
    corner_ra = detector_rows[["llcra", "ulcra", "urcra", "lrcra"]].to_numpy(float)
    corner_dec = detector_rows[["llcdec", "ulcdec", "urcdec", "lrcdec"]].to_numpy(float)
    detector_ra = pd.to_numeric(detector_rows["detector_ra"], errors="coerce").to_numpy()
    detector_dec = pd.to_numeric(detector_rows["detector_dec"], errors="coerce").to_numpy()
    for row in tile_targets.itertuples(index=False):
        separation = _angular_separation_deg(
            detector_ra, detector_dec, float(row.ra_deg), float(row.dec_deg)
        )
        nearby = np.isfinite(separation) & (separation <= search_radius_deg)
        if not nearby.any():
            continue
        indices = np.flatnonzero(nearby)
        dra = (corner_ra[indices] - float(row.ra_deg) + 180.0) % 360.0 - 180.0
        x = dra * np.cos(np.deg2rad(float(row.dec_deg)))
        y = corner_dec[indices] - float(row.dec_deg)
        cross = x * np.roll(y, -1, axis=1) - y * np.roll(x, -1, axis=1)
        inside = np.isfinite(cross).all(axis=1) & (
            (cross >= -1e-12).all(axis=1) | (cross <= 1e-12).all(axis=1)
        )
        if inside.any():
            matched = detector_rows.iloc[indices[inside]].copy()
            matched.insert(0, "target_id", str(row.target_id))
            outputs.append(matched)
    return pd.concat(outputs, ignore_index=True, sort=False) if outputs else pd.DataFrame()


def _aggregate_detector_matches(matches: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "target_id", "band", "n_images", "visit_seeing_mean_arcsec",
        "visit_seeing_best_arcsec", "visit_maglim_mean", "visit_maglim_best",
        "visit_exptime_mean_s", "visit_exptime_total_s", "visit_efftime_mean_s",
        "visit_sky_noise_mean", "visit_zero_point_mean", "visit_n_psf_star_mean",
        "visit_first_mjd", "visit_last_mjd",
    ]
    if matches.empty:
        return pd.DataFrame(columns=columns)
    grouped = matches.groupby(["target_id", "band"], dropna=False, sort=False)
    output = grouped.agg(
        n_images=("visit_id", "size"),
        visit_seeing_mean_arcsec=("seeing", "mean"),
        visit_seeing_best_arcsec=("seeing", "min"),
        visit_maglim_mean=("mag_lim", "mean"),
        visit_maglim_best=("mag_lim", "max"),
        visit_exptime_mean_s=("exp_time", "mean"),
        visit_exptime_total_s=("exp_time", "sum"),
        visit_efftime_mean_s=("eff_time", "mean"),
        visit_sky_noise_mean=("sky_noise", "mean"),
        visit_zero_point_mean=("zero_point", "mean"),
        visit_n_psf_star_mean=("n_psf_star", "mean"),
        visit_first_mjd=("mjd", "min"),
        visit_last_mjd=("mjd", "max"),
    ).reset_index()
    return output[columns]


def query_visit_summary_by_band(
    targets: pd.DataFrame,
    *,
    tap_service: Any,
    data_release: str | DataReleaseConfig = "DP2",
    tile_nside: int = 64,
    max_workers: int = 2,
    timeout_seconds: int = 7200,
    search_radius_deg: float = 0.35,
    cache_dir: str | Path | None = None,
    reuse_cache: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Return exact image counts using spatially pruned TAP tiles.

    Only detector rows around occupied HEALPix tiles are transferred. Matching
    to the four detector corners is then performed locally. This avoids both a
    35,000-job query loop and an unindexed upload cross-join against all five
    million DP2 detector rows. Each tile is independently cached.
    """
    import hpgeom

    release = get_data_release(data_release)
    if tile_nside < 1 or tile_nside & (tile_nside - 1):
        raise ValueError("tile_nside must be a positive power of two")
    cache_path = Path(cache_dir) if cache_dir is not None else None
    if cache_path is not None:
        cache_path.mkdir(parents=True, exist_ok=True)
    work = targets[["target_id", "ra_deg", "dec_deg"]].copy()
    work["tile"] = hpgeom.angle_to_pixel(
        tile_nside,
        work["ra_deg"].to_numpy(float),
        work["dec_deg"].to_numpy(float),
        nest=True,
        lonlat=True,
    )
    grouped = [(int(tile), frame.copy()) for tile, frame in work.groupby("tile", sort=True)]
    c = release.visit_columns
    raw_columns = [
        "band", "visit_id", "detector", "seeing", "mag_lim", "exp_time",
        "eff_time", "sky_noise", "zero_point", "n_psf_star", "mjd",
        "detector_ra", "detector_dec", "llcra", "llcdec", "ulcra", "ulcdec",
        "urcra", "urcdec", "lrcra", "lrcdec",
    ]

    def query_tile(item: tuple[int, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
        tile, tile_targets = item
        center_ra, center_dec = hpgeom.pixel_to_angle(
            tile_nside, tile, nest=True, lonlat=True
        )
        target_separations = _angular_separation_deg(
            tile_targets["ra_deg"].to_numpy(float),
            tile_targets["dec_deg"].to_numpy(float),
            float(center_ra),
            float(center_dec),
        )
        query_radius = float(np.nanmax(target_separations)) + float(search_radius_deg)
        cache_payload = json.dumps(
            [release.name, tile_nside, tile, search_radius_deg], separators=(",", ":")
        )
        cache_key = hashlib.sha256(cache_payload.encode()).hexdigest()[:16]
        cache_file = cache_path / f"detectors_{tile}_{cache_key}.csv" if cache_path else None
        state = "queried"
        error = ""
        try:
            if reuse_cache and cache_file is not None and cache_file.exists():
                detector_rows = pd.read_csv(cache_file)
                state = "cache"
            else:
                query = f"""
                    SELECT vd.{c['band']} AS band,
                        vd.{c['visitId']} AS visit_id,
                        vd.{c['detector']} AS detector,
                        vd.{c['seeing']} AS seeing,
                        vd.{c['magLim']} AS mag_lim,
                        vd.expTime AS exp_time,
                        vd.effTime AS eff_time,
                        vd.skyNoise AS sky_noise,
                        vd.zeroPoint AS zero_point,
                        vd.nPsfStar AS n_psf_star,
                        vd.{c['expMidptMJD']} AS mjd,
                        vd.{release.tap_ra} AS detector_ra,
                        vd.{release.tap_dec} AS detector_dec,
                        vd.llcra, vd.llcdec, vd.ulcra, vd.ulcdec,
                        vd.urcra, vd.urcdec, vd.lrcra, vd.lrcdec
                    FROM {release.tap_visit_table} AS vd
                    WHERE CONTAINS(
                        POINT('ICRS', vd.{release.tap_ra}, vd.{release.tap_dec}),
                        CIRCLE('ICRS', {float(center_ra):.15g}, {float(center_dec):.15g},
                            {query_radius:.15g})
                    ) = 1
                """
                job = tap_service.submit_job(query)
                job.run()
                job.wait(phases=["COMPLETED", "ERROR"], timeout=timeout_seconds)
                if job.phase == "ERROR":
                    job.raise_if_error()
                detector_rows = _tap_result_frame(job)
                for column in raw_columns:
                    if column not in detector_rows:
                        detector_rows[column] = pd.NA
                detector_rows = detector_rows[raw_columns]
                if cache_file is not None:
                    detector_rows.to_csv(cache_file, index=False)
            matches = _match_targets_to_detector_rows(
                tile_targets, detector_rows, search_radius_deg
            )
        except Exception as caught:
            matches = pd.DataFrame()
            state = "error"
            error = f"{type(caught).__name__}: {caught}"
        statuses = tile_targets[["target_id"]].copy()
        statuses["visit_query_status"] = state
        statuses["visit_query_error"] = error
        return matches, statuses

    workers = max(1, min(int(max_workers), len(grouped))) if grouped else 1
    results: list[tuple[pd.DataFrame, pd.DataFrame]] = []
    if workers == 1:
        iterator: Any = map(query_tile, grouped)
        if verbose and grouped:
            try:
                from tqdm import tqdm

                iterator = tqdm(iterator, total=len(grouped), desc="Visit tiles", unit="tile")
            except ImportError:  # pragma: no cover
                pass
        results = list(iterator)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(query_tile, item) for item in grouped]
            iterator = as_completed(futures)
            if verbose and grouped:
                try:
                    from tqdm import tqdm

                    iterator = tqdm(iterator, total=len(grouped), desc="Visit tiles", unit="tile")
                except ImportError:  # pragma: no cover
                    pass
            results = [future.result() for future in iterator]
    matches = [result[0] for result in results if not result[0].empty]
    summary = _aggregate_detector_matches(
        pd.concat(matches, ignore_index=True, sort=False) if matches else pd.DataFrame()
    )
    statuses = (
        pd.concat([result[1] for result in results], ignore_index=True)
        if results
        else pd.DataFrame(columns=["target_id", "visit_query_status", "visit_query_error"])
    )
    summary.attrs["target_status"] = statuses
    errors = statuses.loc[statuses["visit_query_status"].eq("error")]
    if len(errors):
        warnings.warn(
            f"{len(errors)} target(s) belong to failed VisitDetector tiles; inspect visit_query_error.",
            RuntimeWarning,
            stacklevel=2,
        )
    if verbose:
        print(
            f"Visit coverage: {len(grouped)} spatial tiles, "
            f"{len(targets)} targets, {len(summary)} target-band rows",
            flush=True,
        )
    return summary


def pivot_visit_summary(
    visit_summary: pd.DataFrame,
    target_ids: Sequence[str],
    bands: Sequence[str],
    *,
    queried_target_ids: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Convert target-band visit statistics to one row per target."""
    output = pd.DataFrame({"target_id": list(target_ids)}).set_index("target_id")
    metrics = [column for column in visit_summary.columns if column not in {"target_id", "band"}]
    if not visit_summary.empty:
        for metric in metrics:
            wide = visit_summary.pivot(index="target_id", columns="band", values=metric)
            wide = wide.rename(columns={band: f"{metric}_{band}" for band in wide.columns})
            output = output.join(wide, how="left")
    queried = output.index.isin(
        list(target_ids) if queried_target_ids is None else list(queried_target_ids)
    )
    image_columns = []
    for band in bands:
        column = f"n_images_{band}"
        image_columns.append(column)
        if column not in output:
            output[column] = pd.NA
        values = pd.to_numeric(output[column], errors="coerce")
        values.loc[queried & values.isna()] = 0
        output[column] = values.astype("Int64")
    output["n_images_total"] = output[image_columns].sum(axis=1, min_count=1).astype("Int64")

    def present_bands(row: pd.Series) -> Any:
        if row.isna().all():
            return pd.NA
        return ",".join(
            band for band, value in zip(bands, row, strict=True) if pd.notna(value) and int(value) > 0
        )

    output["visit_bands"] = output[image_columns].apply(present_bands, axis=1)
    return output.reset_index()


def _sample_map_values(property_map: Any, ra: np.ndarray, dec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    try:
        valid = np.asarray(
            property_map.get_values_pos(ra, dec, lonlat=True, valid_mask=True),
            dtype=bool,
        )
    except TypeError:
        valid = np.ones(len(ra), dtype=bool)
    try:
        values = np.asarray(property_map.get_values_pos(ra, dec, lonlat=True), dtype=float)
    except TypeError:
        values = np.asarray(property_map.get_values_pos(ra, dec), dtype=float)
    valid &= np.isfinite(values)
    return values, valid


def _partial_property_map(
    butler: Any,
    dataset_type: str,
    band: str,
    skymap: str | None,
    ra: np.ndarray,
    dec: np.ndarray,
) -> Any:
    """Read only HealSparse coverage pixels needed by the target catalog."""
    import healsparse
    import healsparse.io_map as io_map
    import hpgeom

    refs = list(
        butler.query_datasets(
            dataset_type,
            where="band.name=:band",
            bind={"band": str(band)},
        )
    )
    if skymap is not None:
        refs = [ref for ref in refs if str(_data_id_value(ref, "skymap", skymap)) == str(skymap)]
    if not refs:
        raise LookupError(f"No {dataset_type} map for band {band}")
    uri = str(butler.getURI(refs[0]))
    # HealSparse has used different optional FITS-reader flags across
    # releases. Treat missing flags as unsupported instead of making the
    # partial-read path fail and falling back to a full Butler.get.
    previous_rustfits = getattr(io_map, "use_rustfits", None)
    previous_fitsio = getattr(io_map, "use_fitsio", None)
    try:
        # fitsio/CFITSIO supports HTTP range reads; rustfits currently attempts
        # to load the entire remote artifact (about 750 MB for a DP2 map).
        if previous_rustfits is not None:
            io_map.use_rustfits = False
        if previous_fitsio is not None:
            io_map.use_fitsio = True
        coverage = healsparse.HealSparseCoverage.read(uri)
        coverage_pixels = np.unique(
            hpgeom.angle_to_pixel(
                coverage.nside_coverage,
                ra,
                dec,
                nest=True,
                lonlat=True,
            )
        )
        return healsparse.HealSparseMap.read(uri, pixels=coverage_pixels.tolist())
    finally:
        if previous_rustfits is not None:
            io_map.use_rustfits = previous_rustfits
        if previous_fitsio is not None:
            io_map.use_fitsio = previous_fitsio


def sample_coadd_properties(
    targets: pd.DataFrame,
    *,
    butler: Any,
    bands: Sequence[str],
    property_maps: Sequence[str],
    skymap: str | None = None,
    pixel_scale_arcsec: float = 0.2,
    partial_reads: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Sample consolidated coadd property maps at all target coordinates."""
    output = pd.DataFrame({"target_id": targets["target_id"].astype(str)}).set_index("target_id")
    ra = targets["ra_deg"].astype(float).to_numpy()
    dec = targets["dec_deg"].astype(float).to_numpy()
    valid_by_band: dict[str, np.ndarray] = {
        str(band): np.zeros(len(targets), dtype=bool) for band in bands
    }
    for map_key in property_maps:
        dataset_type, stem = COADD_PROPERTY_MAPS[map_key]
        for band in bands:
            kwargs: dict[str, Any] = {"band": str(band)}
            if skymap:
                kwargs["skymap"] = skymap
            try:
                if partial_reads:
                    try:
                        property_map = _partial_property_map(
                            butler, dataset_type, str(band), skymap, ra, dec
                        )
                    except Exception as partial_error:
                        warnings.warn(
                            f"Partial map read failed for {dataset_type} {band}; "
                            f"falling back to Butler.get: {partial_error}",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                        property_map = butler.get(dataset_type, **kwargs)
                else:
                    property_map = butler.get(dataset_type, **kwargs)
                values, valid = _sample_map_values(property_map, ra, dec)
                del property_map
                gc.collect()
            except Exception as error:  # Butler types differ between RSP releases.
                warnings.warn(
                    f"Could not sample {dataset_type} for band {band}: {error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                values = np.full(len(targets), np.nan)
                valid = np.zeros(len(targets), dtype=bool)
            values = np.where(valid, values, np.nan)
            output[f"{stem}_{band}"] = values
            if map_key == "exposure_time":
                valid_by_band[str(band)] = valid & (values > 0)
            else:
                valid_by_band[str(band)] |= valid
            if verbose:
                print(
                    f"Coadd map: {map_key} {band} ({int(valid.sum())}/{len(targets)} positions)",
                    flush=True,
                )
    for band in bands:
        output[f"has_coadd_{band}"] = valid_by_band[str(band)]
    has_columns = [f"has_coadd_{band}" for band in bands]
    output["coadd_n_bands"] = output[has_columns].sum(axis=1).astype("int64")
    output["has_coadd"] = output["coadd_n_bands"].gt(0)
    output["coadd_bands"] = output[has_columns].apply(
        lambda row: ",".join(band for band, present in zip(bands, row, strict=True) if present),
        axis=1,
    )

    maglim_columns = [f"coadd_maglim_{band}" for band in bands if f"coadd_maglim_{band}" in output]
    psf_columns = [f"coadd_psf_size_pixel_{band}" for band in bands if f"coadd_psf_size_pixel_{band}" in output]
    exposure_columns = [f"coadd_exposure_time_s_{band}" for band in bands if f"coadd_exposure_time_s_{band}" in output]
    sky_columns = [f"coadd_sky_noise_njy_{band}" for band in bands if f"coadd_sky_noise_njy_{band}" in output]
    output["coadd_maglim_median"] = output[maglim_columns].median(axis=1, skipna=True) if maglim_columns else np.nan
    if psf_columns:
        for column in psf_columns:
            band = column.rsplit("_", 1)[-1]
            output[f"coadd_psf_fwhm_arcsec_{band}"] = (
                pd.to_numeric(output[column], errors="coerce") * 2.354820045 * pixel_scale_arcsec
            )
        fwhm_columns = [f"coadd_psf_fwhm_arcsec_{band}" for band in bands if f"coadd_psf_fwhm_arcsec_{band}" in output]
        output["coadd_psf_fwhm_median_arcsec"] = output[fwhm_columns].median(axis=1, skipna=True)
    else:
        output["coadd_psf_fwhm_median_arcsec"] = np.nan
    output["coadd_exposure_time_total_s"] = output[exposure_columns].sum(axis=1, min_count=1) if exposure_columns else np.nan
    output["coadd_sky_noise_median_njy"] = output[sky_columns].median(axis=1, skipna=True) if sky_columns else np.nan
    return output.reset_index()


def rank_cutout_candidates(
    catalog: pd.DataFrame,
    *,
    bands: Sequence[str],
    grades: Sequence[str] = ("A", "B"),
    max_cutouts: int = 100,
    quality_weights: Mapping[str, float] = DEFAULT_QUALITY_WEIGHTS,
) -> pd.DataFrame:
    """Assign transparent quality scores and lexicographic cutout priorities."""
    output = catalog.copy()
    depth = pd.to_numeric(output.get("coadd_maglim_median"), errors="coerce")
    seeing = pd.to_numeric(output.get("coadd_psf_fwhm_median_arcsec"), errors="coerce")
    band_count = pd.to_numeric(output.get("coadd_n_bands"), errors="coerce").fillna(0)
    output["quality_depth_percentile"] = depth.rank(pct=True).fillna(0)
    output["quality_seeing_percentile"] = (-seeing).rank(pct=True).fillna(0)
    output["quality_band_coverage_fraction"] = band_count / max(1, len(tuple(bands)))
    weight_sum = sum(float(quality_weights[key]) for key in DEFAULT_QUALITY_WEIGHTS)
    output["coadd_quality_score"] = 100 * (
        float(quality_weights["depth"]) * output["quality_depth_percentile"]
        + float(quality_weights["seeing"]) * output["quality_seeing_percentile"]
        + float(quality_weights["band_coverage"]) * output["quality_band_coverage_fraction"]
    ) / weight_sum
    normalized_grades = tuple(str(grade).upper() for grade in grades)
    grade_rank = {grade: index for index, grade in enumerate(normalized_grades)}
    output["cutout_grade_rank"] = output["selection_grade"].astype(str).str.upper().map(grade_rank)
    output["cutout_eligible"] = output["has_coadd"].fillna(False).astype(bool) & output["cutout_grade_rank"].notna()
    candidates = output.loc[output["cutout_eligible"]].sort_values(
        ["cutout_grade_rank", "coadd_quality_score", "coadd_n_bands", "target_id"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    rank = pd.Series(pd.array(range(1, len(candidates) + 1), dtype="Int64"), index=candidates.index)
    output["cutout_priority_rank"] = pd.Series(pd.NA, index=output.index, dtype="Int64")
    output.loc[candidates.index, "cutout_priority_rank"] = rank
    output["cutout_selected"] = output["cutout_priority_rank"].notna() & output["cutout_priority_rank"].le(max_cutouts)
    output["cutout_status"] = np.select(
        [
            ~output["has_coadd"].fillna(False).astype(bool),
            output["cutout_grade_rank"].isna(),
            output["cutout_selected"],
        ],
        ["no_coadd", "grade_not_selected", "planned"],
        default="outside_cutout_budget",
    )
    output["cutout_path"] = ""
    output["cutout_n_bands"] = 0
    return output


def _data_id_value(ref: Any, key: str, default: Any = None) -> Any:
    try:
        return ref.dataId[key]
    except (KeyError, TypeError, AttributeError):
        return default


def save_coadd_cutout_grid(
    row: pd.Series,
    *,
    butler: Any,
    data_release: str | DataReleaseConfig,
    output_path: str | Path,
    bands: Sequence[str],
    size_arcsec: float = 20.0,
) -> tuple[Path | None, int]:
    """Save a multi-band square cutout grid for one target."""
    import astropy.units as u
    import matplotlib.pyplot as plt
    from astropy.coordinates import SkyCoord
    from astropy.visualization import AsinhStretch, ImageNormalize

    release = get_data_release(data_release)
    coordinate = SkyCoord(float(row["ra_deg"]) * u.deg, float(row["dec_deg"]) * u.deg)
    refs: list[Any] = []
    for dataset_type in release.coadd_dataset_types:
        try:
            refs = list(
                butler.query_datasets(
                    dataset_type,
                    where=release.coadd_spatial_where,
                    bind={"ra": float(row["ra_deg"]), "dec": float(row["dec_deg"])},
                )
            )
        except Exception:
            refs = []
        if refs:
            break
    by_band: dict[str, list[Any]] = {str(band): [] for band in bands}
    for ref in refs:
        band = str(_data_id_value(ref, "band", ""))
        if band in by_band:
            by_band[band].append(ref)

    n_columns = min(3, max(1, len(bands)))
    n_rows = math.ceil(len(bands) / n_columns)
    figure, axes = plt.subplots(n_rows, n_columns, figsize=(3.35 * n_columns, 3.1 * n_rows), squeeze=False)
    plotted = 0
    for axis, band in zip(axes.flat, bands, strict=False):
        selected = None
        for ref in by_band[str(band)]:
            coadd = butler.get(ref)
            x, y = coadd.fits_wcs.world_to_pixel(coordinate)
            ny, nx = coadd.image.array.shape
            if 0 <= x < nx and 0 <= y < ny:
                selected = (coadd, float(x), float(y))
                break
        if selected is None:
            axis.axis("off")
            axis.text(0.5, 0.5, f"No {band}-band coadd", ha="center", va="center", transform=axis.transAxes)
            continue
        coadd, x, y = selected
        pixel_scale = float(np.mean([scale.to_value(u.arcsec) for scale in coadd.fits_wcs.proj_plane_pixel_scales()]))
        half_pixels = max(2, int(math.ceil(size_arcsec / (2 * pixel_scale))))
        x0, x1 = max(0, int(math.floor(x)) - half_pixels), min(nx, int(math.floor(x)) + half_pixels + 1)
        y0, y1 = max(0, int(math.floor(y)) - half_pixels), min(ny, int(math.floor(y)) + half_pixels + 1)
        cutout = np.asarray(coadd.image.array[y0:y1, x0:x1], dtype=float)
        finite = cutout[np.isfinite(cutout)]
        if not len(finite):
            axis.axis("off")
            axis.text(0.5, 0.5, f"Empty {band}-band coadd", ha="center", va="center", transform=axis.transAxes)
            continue
        extent = (
            (x0 - x) * pixel_scale,
            (x1 - x) * pixel_scale,
            (y0 - y) * pixel_scale,
            (y1 - y) * pixel_scale,
        )
        norm = ImageNormalize(
            vmin=np.nanpercentile(finite, 5),
            vmax=np.nanpercentile(finite, 99.8),
            stretch=AsinhStretch(0.08),
            clip=True,
        )
        axis.imshow(cutout, origin="lower", cmap="gray", norm=norm, extent=extent)
        axis.add_patch(plt.Circle((0, 0), 0.8, edgecolor="red", facecolor="none", lw=1.2))
        subtitle = []
        for column, label, fmt in (
            (f"n_images_{band}", "N", ".0f"),
            (f"coadd_maglim_{band}", "depth", ".2f"),
            (f"coadd_psf_fwhm_arcsec_{band}", "FWHM", ".2f"),
        ):
            value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
            if pd.notna(value):
                subtitle.append(f"{label}={value:{fmt}}")
        axis.set_title(f"{band}  " + "  ".join(subtitle), fontsize=8)
        axis.set_xlabel("x offset [arcsec]", fontsize=7)
        axis.set_ylabel("y offset [arcsec]", fontsize=7)
        axis.tick_params(labelsize=7)
        plotted += 1
    for axis in axes.flat[len(bands) :]:
        axis.axis("off")
    figure.suptitle(
        f"{row['target_id']} — grade {row.get('selection_grade', '—')} — "
        f"quality {float(row.get('coadd_quality_score', 0)):.1f}",
        fontsize=11,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.95))
    if plotted == 0:
        plt.close(figure)
        return None, 0
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=170, bbox_inches="tight")
    plt.close(figure)
    return output_path, plotted


def generate_prioritized_cutouts(
    catalog: pd.DataFrame,
    *,
    butler: Any,
    data_release: str | DataReleaseConfig,
    output_dir: str | Path,
    bands: Sequence[str],
    size_arcsec: float = 20.0,
    reuse_existing: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Generate cutouts only for rows selected by the ranking budget."""
    output = catalog.copy()
    selected = output.loc[output["cutout_selected"]].sort_values("cutout_priority_rank")
    iterator: Any = selected.iterrows()
    if verbose and len(selected):
        try:
            from tqdm import tqdm

            iterator = tqdm(iterator, total=len(selected), desc="Coadd cutouts", unit="target")
        except ImportError:  # pragma: no cover - tqdm is a project dependency.
            pass
    for index, row in iterator:
        filename = f"{int(row['cutout_priority_rank']):04d}_{_safe_name(row['target_id'])}_coadds.png"
        existing_path = Path(output_dir) / filename
        if reuse_existing and existing_path.exists():
            output.at[index, "cutout_status"] = "reused"
            output.at[index, "cutout_path"] = str(existing_path)
            output.at[index, "cutout_n_bands"] = int(row.get("coadd_n_bands", 0))
            continue
        try:
            path, plotted = save_coadd_cutout_grid(
                row,
                butler=butler,
                data_release=data_release,
                output_path=Path(output_dir) / filename,
                bands=bands,
                size_arcsec=size_arcsec,
            )
            output.at[index, "cutout_status"] = "generated" if path else "no_coadd_at_position"
            output.at[index, "cutout_path"] = str(path) if path else ""
            output.at[index, "cutout_n_bands"] = int(plotted)
        except Exception as error:
            output.at[index, "cutout_status"] = f"error: {type(error).__name__}: {error}"
    return output



def run_reference_coverage_catalog(
    config: ReferenceCatalogConfig | str | Path,
    *,
    tap_service: Any = None,
) -> pd.DataFrame:
    """Build and cache the fast TAP-only coverage catalog.

    This stage does not initialize Butler or read coadd maps. It returns one
    row per input target with per-band image counts and VisitDetector quality
    summaries, making it suitable as the first pass over a very large catalog.
    """
    if not isinstance(config, ReferenceCatalogConfig):
        config = load_reference_catalog_config(config)
    config.validate()
    release = get_data_release(config.data_release)
    tap_service, _ = _default_clients(release, tap_service, butler=object())
    targets = load_target_catalog(config)
    fingerprint = _catalog_fingerprint(targets, release)
    cache_dir = Path(config.cache_dir) / _safe_name(release.name) / fingerprint
    output_dir = Path(config.output_dir) / _safe_name(config.name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    visits_long = query_visit_summary_by_band(
        targets, tap_service=tap_service, data_release=release,
        tile_nside=config.tap_tile_nside, max_workers=config.tap_max_workers,
        timeout_seconds=config.tap_timeout_seconds,
        search_radius_deg=config.detector_search_radius_deg,
        cache_dir=cache_dir / "visit_tiles", reuse_cache=config.reuse_cache,
        verbose=config.verbose,
    )
    visit_status = visits_long.attrs.get(
        "target_status",
        pd.DataFrame(columns=["target_id", "visit_query_status", "visit_query_error"]),
    )
    completed_ids = visit_status.loc[
        ~visit_status["visit_query_status"].eq("error"), "target_id"
    ].astype(str)
    visits = pivot_visit_summary(
        visits_long, targets["target_id"], config.bands,
        queried_target_ids=completed_ids,
    )
    catalog = targets.merge(visits, on="target_id", how="left").merge(
        visit_status, on="target_id", how="left",
    )
    catalog["visit_query_status"] = catalog["visit_query_status"].fillna("skipped_no_coverage")
    catalog["visit_query_error"] = catalog["visit_query_error"].fillna("")
    catalog["visit_coverage_queried"] = catalog["visit_query_status"].isin({"queried", "cache"})
    catalog_path = output_dir / "coverage_catalog.csv"
    catalog.to_csv(catalog_path, index=False)
    if config.verbose:
        print(f"Coverage catalog: {catalog_path}", flush=True)
    return catalog


def enrich_reference_catalog_coadds(
    config: ReferenceCatalogConfig | str | Path,
    coverage_catalog: pd.DataFrame,
    *,
    butler: Any = None,
) -> pd.DataFrame:
    """Add coadd-map properties to a previously generated coverage catalog."""
    if not isinstance(config, ReferenceCatalogConfig):
        config = load_reference_catalog_config(config)
    config.validate()
    release = get_data_release(config.data_release)
    if butler is None:
        _, butler = _default_clients(release, tap_service=object(), butler=None)
    targets = coverage_catalog.copy()
    covered = pd.to_numeric(targets.get("n_images_total"), errors="coerce").fillna(0).gt(0)
    map_targets = targets.loc[covered, ["target_id", "ra_deg", "dec_deg"]].copy()
    fingerprint = _catalog_fingerprint(targets[["target_id", "ra_deg", "dec_deg"]], release)
    cache_dir = Path(config.cache_dir) / _safe_name(release.name) / fingerprint
    cache_dir.mkdir(parents=True, exist_ok=True)
    map_signature = hashlib.sha256(json.dumps(
        [config.bands, config.property_maps, config.coadd_skymap, config.coadd_pixel_scale_arcsec],
        default=list, separators=(",", ":"),
    ).encode()).hexdigest()[:12]
    cache_file = cache_dir / f"coadd_properties_{map_signature}.csv"
    if config.reuse_cache and cache_file.exists():
        coadd = pd.read_csv(cache_file)
    elif len(map_targets):
        coadd = sample_coadd_properties(
            map_targets, butler=butler, bands=config.bands,
            property_maps=config.property_maps, skymap=config.coadd_skymap,
            pixel_scale_arcsec=config.coadd_pixel_scale_arcsec,
            partial_reads=config.partial_property_map_reads, verbose=config.verbose,
        )
        coadd.to_csv(cache_file, index=False)
    else:
        coadd = pd.DataFrame({"target_id": pd.Series(dtype=str), "has_coadd": pd.Series(dtype=bool), "coadd_n_bands": pd.Series(dtype=int)})
    result = targets.merge(coadd, on="target_id", how="left")
    result["has_coadd"] = result["has_coadd"].fillna(False).astype(bool)
    result["coadd_n_bands"] = pd.to_numeric(result["coadd_n_bands"], errors="coerce").fillna(0).astype(int)
    result = rank_cutout_candidates(
        result, bands=config.bands, grades=config.cutout_grades,
        max_cutouts=config.max_cutouts, quality_weights=config.quality_weights,
    )
    output_dir = Path(config.output_dir) / _safe_name(config.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_dir / "reference_catalog.csv", index=False)
    result.loc[result["cutout_eligible"]].sort_values("cutout_priority_rank").to_csv(output_dir / "cutout_plan.csv", index=False)
    return result


def generate_reference_catalog_cutouts(
    config: ReferenceCatalogConfig | str | Path,
    catalog: pd.DataFrame,
    *,
    butler: Any = None,
) -> pd.DataFrame:
    """Generate prioritized coadd cutouts from an enriched reference catalog."""
    if not isinstance(config, ReferenceCatalogConfig):
        config = load_reference_catalog_config(config)
    config.validate()
    release = get_data_release(config.data_release)
    if butler is None:
        _, butler = _default_clients(release, tap_service=object(), butler=None)
    output_dir = Path(config.output_dir) / _safe_name(config.name)
    result = generate_prioritized_cutouts(
        catalog, butler=butler, data_release=release,
        output_dir=output_dir / "cutouts", bands=config.cutout_bands,
        size_arcsec=config.cutout_size_arcsec, reuse_existing=config.reuse_cache,
        verbose=config.verbose,
    )
    result.to_csv(output_dir / "reference_catalog.csv", index=False)
    return result

def run_reference_catalog_preview(
    config: ReferenceCatalogConfig | str | Path,
    *,
    n_targets: int = 200,
    max_cutouts: int | None = 12,
    tap_service: Any = None,
    butler: Any = None,
) -> ReferenceCatalogResult:
    """Run a bounded preview without changing the full-run configuration."""
    from dataclasses import replace

    if n_targets < 1:
        raise ValueError("n_targets must be positive")
    if not isinstance(config, ReferenceCatalogConfig):
        config = load_reference_catalog_config(config)
    preview = replace(
        config,
        name=f"{config.name}_preview_{int(n_targets)}",
        target_limit=int(n_targets),
        max_cutouts=config.max_cutouts if max_cutouts is None else int(max_cutouts),
    )
    return run_reference_catalog(preview, tap_service=tap_service, butler=butler)


def _default_clients(release: DataReleaseConfig, tap_service: Any, butler: Any) -> tuple[Any, Any]:
    if tap_service is None:
        from lsst.rsp import get_tap_service

        tap_service = get_tap_service("tap")
    if butler is None:
        from lsst.daf.butler import Butler

        butler = Butler(release.butler_repo, collections=release.butler_collections)
    return tap_service, butler


def run_reference_catalog(
    config: ReferenceCatalogConfig | str | Path,
    *,
    tap_service: Any = None,
    butler: Any = None,
) -> ReferenceCatalogResult:
    """Build the enriched dataset and optional prioritized coadd cutouts."""
    if not isinstance(config, ReferenceCatalogConfig):
        config = load_reference_catalog_config(config)
    config.validate()
    release = get_data_release(config.data_release)
    tap_service, butler = _default_clients(release, tap_service, butler)
    targets = load_target_catalog(config)
    fingerprint = _catalog_fingerprint(targets, release)
    output_dir = Path(config.output_dir) / _safe_name(config.name)
    output_dir.mkdir(parents=True, exist_ok=True)
    cutout_dir = output_dir / "cutouts"
    cache_dir = Path(config.cache_dir) / _safe_name(release.name) / fingerprint

    if config.verbose:
        print(f"Catalog targets: {len(targets)}", flush=True)
        print(f"Data release: {release.name}", flush=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Coverage-first workflow: TAP provides the per-target/per-band image
    # counts and medians before any coadd-property maps are read. This avoids
    # touching maps for targets with no individual-image coverage.
    visits_long = query_visit_summary_by_band(
        targets,
        tap_service=tap_service,
        data_release=release,
        tile_nside=config.tap_tile_nside,
        max_workers=config.tap_max_workers,
        timeout_seconds=config.tap_timeout_seconds,
        search_radius_deg=config.detector_search_radius_deg,
        cache_dir=cache_dir / "visit_tiles",
        reuse_cache=config.reuse_cache,
        verbose=config.verbose,
    )
    visit_status = visits_long.attrs.get(
        "target_status",
        pd.DataFrame(columns=["target_id", "visit_query_status", "visit_query_error"]),
    )
    completed_ids = visit_status.loc[
        ~visit_status["visit_query_status"].eq("error"), "target_id"
    ].astype(str)
    visits = pivot_visit_summary(
        visits_long,
        targets["target_id"],
        config.bands,
        queried_target_ids=completed_ids,
    )
    covered_ids = set(
        visits.loc[pd.to_numeric(visits["n_images_total"], errors="coerce").fillna(0).gt(0), "target_id"].astype(str)
    )
    map_targets = targets.loc[targets["target_id"].astype(str).isin(covered_ids)].copy()

    map_signature = hashlib.sha256(
        json.dumps(
            [config.bands, config.property_maps, config.coadd_skymap,
             config.coadd_pixel_scale_arcsec],
            default=list,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:12]
    coadd_cache_file = cache_dir / f"coadd_properties_{map_signature}.csv"
    if config.reuse_cache and coadd_cache_file.exists():
        cached_coadd = pd.read_csv(coadd_cache_file)
        missing_ids = set(map_targets["target_id"].astype(str)) - set(cached_coadd["target_id"].astype(str))
        if missing_ids:
            new_coadd = sample_coadd_properties(
                map_targets.loc[map_targets["target_id"].astype(str).isin(missing_ids)],
                butler=butler, bands=config.bands, property_maps=config.property_maps,
                skymap=config.coadd_skymap, pixel_scale_arcsec=config.coadd_pixel_scale_arcsec,
                partial_reads=config.partial_property_map_reads, verbose=config.verbose,
            )
            coadd = pd.concat([cached_coadd, new_coadd], ignore_index=True, sort=False).drop_duplicates("target_id", keep="last")
            coadd.to_csv(coadd_cache_file, index=False)
        else:
            coadd = cached_coadd
        if config.verbose:
            print(f"Coadd properties: cache ({coadd_cache_file})", flush=True)
    elif len(map_targets):
        coadd = sample_coadd_properties(
            map_targets, butler=butler, bands=config.bands,
            property_maps=config.property_maps, skymap=config.coadd_skymap,
            pixel_scale_arcsec=config.coadd_pixel_scale_arcsec,
            partial_reads=config.partial_property_map_reads, verbose=config.verbose,
        )
        coadd.to_csv(coadd_cache_file, index=False)
    else:
        coadd = pd.DataFrame({
            "target_id": pd.Series(dtype=str),
            "coadd_n_bands": pd.Series(dtype="int64"),
            "has_coadd": pd.Series(dtype=bool),
            "coadd_bands": pd.Series(dtype=str),
        })

    catalog = (
        targets.merge(coadd, on="target_id", how="left")
        .merge(visits, on="target_id", how="left")
        .merge(visit_status, on="target_id", how="left")
    )
    catalog["has_coadd"] = catalog["has_coadd"].fillna(False).astype(bool)
    catalog["coadd_n_bands"] = pd.to_numeric(catalog["coadd_n_bands"], errors="coerce").fillna(0).astype(int)
    skipped = catalog["visit_query_status"].isna()
    catalog.loc[skipped, "visit_query_status"] = "skipped_no_coverage"
    catalog.loc[skipped, "visit_query_error"] = ""
    catalog["visit_coverage_queried"] = catalog["visit_query_status"].isin(
        ["queried", "cache"]
    )
    catalog = rank_cutout_candidates(
        catalog,
        bands=config.bands,
        grades=config.cutout_grades,
        max_cutouts=config.max_cutouts,
        quality_weights=config.quality_weights,
    )
    if config.generate_cutouts and config.max_cutouts:
        catalog = generate_prioritized_cutouts(
            catalog,
            butler=butler,
            data_release=release,
            output_dir=cutout_dir,
            bands=config.cutout_bands,
            size_arcsec=config.cutout_size_arcsec,
            reuse_existing=config.reuse_cache,
            verbose=config.verbose,
        )

    catalog_path = output_dir / "reference_catalog.csv"
    cutout_plan_path = output_dir / "cutout_plan.csv"
    catalog.to_csv(catalog_path, index=False)
    catalog.loc[catalog["cutout_eligible"]].sort_values("cutout_priority_rank").to_csv(
        cutout_plan_path, index=False
    )
    summary = {
        "catalog_name": config.name,
        "data_release": release.name,
        "input_path": str(Path(config.input_path).expanduser()),
        "catalog_fingerprint": fingerprint,
        "n_targets": int(len(catalog)),
        "n_with_individual_images": int(catalog["n_images_total"].fillna(0).gt(0).sum()),
        "n_visit_coverage_queried": int(catalog["visit_coverage_queried"].sum()),
        "n_visit_query_errors": int(catalog["visit_query_status"].eq("error").sum()),
        "n_visit_queries_skipped": int(catalog["visit_query_status"].eq("skipped_no_coadd").sum()),
        "n_with_coadd": int(catalog["has_coadd"].fillna(False).sum()),
        "n_cutout_eligible": int(catalog["cutout_eligible"].sum()),
        "n_cutout_selected": int(catalog["cutout_selected"].sum()),
        "n_cutouts_generated": int(catalog["cutout_status"].eq("generated").sum()),
        "n_cutouts_reused": int(catalog["cutout_status"].eq("reused").sum()),
        "n_cutouts_available": int(catalog["cutout_status"].isin(["generated", "reused"]).sum()),
        "cutout_size_arcsec": float(config.cutout_size_arcsec),
        "grade_counts_with_coadd": {
            str(key): int(value)
            for key, value in catalog.loc[catalog["has_coadd"], "selection_grade"]
            .fillna("unavailable")
            .value_counts()
            .items()
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps({"config": asdict(config), "summary": summary}, indent=2, default=str),
        encoding="utf-8",
    )
    if config.verbose:
        print(
            "Coverage summary: "
            f"{summary['n_with_individual_images']} with individual images; "
            f"{summary['n_with_coadd']} with coadds; "
            f"{summary['n_cutout_eligible']} cutout candidates; "
            f"{summary['n_cutouts_generated']} generated.",
            flush=True,
        )
        print(f"Dataset: {catalog_path.resolve()}", flush=True)
    return ReferenceCatalogResult(
        config=config,
        catalog=catalog,
        output_dir=output_dir,
        catalog_path=catalog_path,
        cutout_plan_path=cutout_plan_path,
        manifest_path=manifest_path,
        summary=summary,
    )
