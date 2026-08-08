"""Batch forced PSF photometry for known MOP target positions.

The Rubin Science Platform imports are intentionally delayed until a
measurement is requested.  This keeps CSV preparation and unit tests usable
outside the RSP environment.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from data_release_config import DataReleaseConfig


FORCED_PHOTOMETRY_COLUMNS = [
    "Target", "RA_deg", "Dec_deg", "visitId", "detector", "expMidptMJD",
    "band", "dataset_type", "inst_flux", "inst_flux_err", "magnitude",
    "magnitude_err", "measurement_flag", "measurement_status", "message",
    "data_release", "butler_collection", "measurement_method", "diaObjectId",
    "dia_match_sep_arcsec", "direct_flux_njy", "direct_flux_err_njy",
    "direct_flux_flag", "difference_flux_njy", "difference_flux_err_njy",
    "difference_flux_flag", "tract", "patch", "coadd_epoch_mjd",
    "epoch_definition",
]
FORCED_PHOTOMETRY_VERSION = 4


def _empty_forced_photometry() -> pd.DataFrame:
    return pd.DataFrame(columns=FORCED_PHOTOMETRY_COLUMNS)


def prepare_forced_photometry_requests(
    targets: pd.DataFrame,
    coverage_rows: pd.DataFrame,
    target_names: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Join target positions to unique visit/detector measurement requests.

    A request represents one target coordinate on one single-detector
    calibrated exposure.  The caller can group the output by ``visitId`` and
    ``detector`` to load each ``calexp`` only once.
    """
    required_targets = {"Target", "RA_deg", "Dec_deg"}
    required_coverage = {"Target", "visitId", "detector", "expMidptMJD", "band"}
    missing_targets = sorted(required_targets - set(targets.columns))
    missing_coverage = sorted(required_coverage - set(coverage_rows.columns))
    if missing_targets:
        raise ValueError(f"Targets are missing required columns: {missing_targets}")
    if missing_coverage:
        raise ValueError(f"Coverage rows are missing required columns: {missing_coverage}")

    positions = targets[["Target", "RA_deg", "Dec_deg"]].copy()
    positions["Target"] = positions["Target"].astype(str)
    positions["RA_deg"] = pd.to_numeric(positions["RA_deg"], errors="coerce")
    positions["Dec_deg"] = pd.to_numeric(positions["Dec_deg"], errors="coerce")
    positions = positions.dropna(subset=["RA_deg", "Dec_deg"]).drop_duplicates("Target")

    requests = coverage_rows[["Target", "visitId", "detector", "expMidptMJD", "band"]].copy()
    requests["Target"] = requests["Target"].astype(str)
    if target_names is not None:
        selected = {str(name) for name in target_names}
        requests = requests[requests["Target"].isin(selected)]
    requests["visitId"] = pd.to_numeric(requests["visitId"], errors="coerce")
    requests["detector"] = pd.to_numeric(requests["detector"], errors="coerce")
    requests["expMidptMJD"] = pd.to_numeric(requests["expMidptMJD"], errors="coerce")
    requests = requests.dropna(subset=["visitId", "detector"])
    requests["visitId"] = requests["visitId"].astype(np.int64)
    requests["detector"] = requests["detector"].astype(np.int64)
    requests = requests.merge(positions, on="Target", how="inner")
    requests = requests.drop_duplicates(["Target", "visitId", "detector"]).reset_index(drop=True)
    return requests[["Target", "RA_deg", "Dec_deg", "visitId", "detector", "expMidptMJD", "band"]]


def _make_forced_measurement_task():
    """Create the minimal Rubin task and schema required for PSF fluxes."""
    import lsst.afw.table as afw_table
    from lsst.meas.base import ForcedMeasurementTask

    schema = afw_table.SourceTable.makeMinimalSchema()
    aliases = schema.getAliasMap()
    x_key = schema.addField("centroid_x", type="D")
    y_key = schema.addField("centroid_y", type="D")
    aliases.set("slot_Centroid", "centroid")
    schema.addField("shape_xx", type="D")
    schema.addField("shape_yy", type="D")
    schema.addField("shape_xy", type="D")
    aliases.set("slot_Shape", "shape")
    type_key = schema.addField("type_flag", type="F")

    config = ForcedMeasurementTask.ConfigClass()
    config.copyColumns = {}
    config.plugins.names = [
        "base_TransformedCentroid",
        "base_PsfFlux",
        "base_TransformedShape",
    ]
    config.doReplaceWithNoise = False
    return ForcedMeasurementTask(schema, config=config), schema, x_key, y_key, type_key


def _base_record(request: pd.Series, dataset_type: str, **values) -> dict:
    record = {column: np.nan for column in FORCED_PHOTOMETRY_COLUMNS}
    record.update({
        "Target": str(request["Target"]),
        "RA_deg": float(request["RA_deg"]),
        "Dec_deg": float(request["Dec_deg"]),
        "visitId": (int(request["visitId"]) if pd.notna(request.get("visitId")) else np.nan),
        "detector": (int(request["detector"]) if pd.notna(request.get("detector")) else np.nan),
        "expMidptMJD": request.get("expMidptMJD", np.nan),
        "band": request["band"],
        "dataset_type": dataset_type,
    })
    record.update(values)
    return record


def _get_calexp(butler, release: "DataReleaseConfig", visit_id: int, detector: int):
    """Fetch a calibrated exposure using release-configured dataset names."""
    data_id = release.calexp_data_id(visit_id, detector)
    errors = []
    for dataset_type in release.calexp_dataset_types:
        try:
            return butler.get(dataset_type, dataId=data_id), dataset_type
        except Exception as exc:  # RSP exception classes differ by deployment.
            errors.append(f"{dataset_type}: {exc}")
    message = "; ".join(errors)
    raise RuntimeError(
        f"No calibrated exposure for visit={visit_id}, detector={detector}. {message}"
    )


def _as_forced_measurement_exposure(image):
    """Return an afw ``Exposure`` suitable for ``ForcedMeasurementTask``.

    DP2 ``deep_coadd`` datasets are modern ``CellCoadd`` objects. They hold
    the image, WCS, effective PSF, and calibration, but do not implement the
    legacy afw exposure API that measurement tasks require. Their supported
    ``to_legacy`` conversion preserves exactly those components.
    """
    if hasattr(image, "to_legacy") and not hasattr(image, "getWcs"):
        return image.to_legacy()
    return image


def _measure_group(exposure, requests: pd.DataFrame, dataset_type: str, task_bundle) -> list[dict]:
    """Measure all requested target positions on one loaded image or coadd."""
    import astropy.units as u
    import lsst.afw.table as afw_table
    import lsst.geom as geom

    exposure = _as_forced_measurement_exposure(exposure)
    task, schema, x_key, y_key, type_key = task_bundle
    coordinates = requests[["RA_deg", "Dec_deg"]].to_numpy(dtype=float)
    overlaps = np.asarray(
        exposure.containsSkyCoords(coordinates[:, 0] * u.deg, coordinates[:, 1] * u.deg),
        dtype=bool,
    )
    records: list[dict] = []
    valid_requests = requests.loc[overlaps].reset_index(drop=True)
    for _, request in requests.loc[~overlaps].iterrows():
        records.append(_base_record(
            request, dataset_type, measurement_status="outside_calexp",
            message="Target coordinate is outside this calibrated exposure.",
        ))
    if valid_requests.empty:
        return records

    source_catalog = afw_table.SourceCatalog(schema)
    for _, request in valid_requests.iterrows():
        source = source_catalog.addNew()
        coord = geom.SpherePoint(
            float(request["RA_deg"]) * geom.degrees,
            float(request["Dec_deg"]) * geom.degrees,
        )
        source.setCoord(coord)
        pixel = exposure.getWcs().skyToPixel(coord)
        source[x_key] = pixel.getX()
        source[y_key] = pixel.getY()
        source[type_key] = 0

    measured_catalog = task.generateMeasCat(exposure, source_catalog, exposure.getWcs())
    task.run(measured_catalog, exposure, source_catalog, exposure.getWcs())
    measured = measured_catalog.asAstropy()
    photo_calib = exposure.getPhotoCalib()
    for index, (_, request) in enumerate(valid_requests.iterrows()):
        flux = float(measured["base_PsfFlux_instFlux"][index])
        flux_err = float(measured["base_PsfFlux_instFluxErr"][index])
        flag = bool(measured["base_PsfFlux_flag"][index])
        magnitude = magnitude_err = np.nan
        message = ""
        if np.isfinite(flux) and flux > 0 and np.isfinite(flux_err) and flux_err >= 0:
            try:
                calibrated = photo_calib.instFluxToMagnitude(flux, flux_err)
                magnitude, magnitude_err = float(calibrated.value), float(calibrated.error)
            except Exception as exc:
                message = f"Magnitude calibration failed: {exc}"
        elif not np.isfinite(flux) or flux <= 0:
            message = "Non-positive or invalid PSF flux; magnitude is undefined."
        else:
            message = "Invalid PSF flux uncertainty; magnitude is undefined."
        records.append(_base_record(
            request, dataset_type, inst_flux=flux, inst_flux_err=flux_err,
            magnitude=magnitude, magnitude_err=magnitude_err,
            measurement_flag=flag, measurement_status="measured", message=message,
        ))
    return records


def compute_release_forced_photometry(
    targets: pd.DataFrame,
    coverage_rows: pd.DataFrame,
    *,
    butler,
    data_release: "DataReleaseConfig",
    target_names: Iterable[str] | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Run batched forced PSF measurements for covered target coordinates.

    Each calibrated exposure is retrieved once and all target coordinates that
    TAP associated with its visit/detector are measured in one task call.  A
    row is retained for unavailable exposures and off-image coordinates so the
    output is an auditable record of every requested epoch.
    """
    requests = prepare_forced_photometry_requests(targets, coverage_rows, target_names)
    if requests.empty:
        return _empty_forced_photometry()

    task_bundle = _make_forced_measurement_task()
    results: list[dict] = []
    groups = requests.groupby(["visitId", "detector"], sort=False)
    iterator = groups
    if verbose:
        try:
            from tqdm.auto import tqdm
            iterator = tqdm(groups, total=groups.ngroups, desc="Forced photometry", unit="calexp", dynamic_ncols=True)
        except ImportError:
            pass
    for (visit_id, detector), group in iterator:
        try:
            exposure, dataset_type = _get_calexp(butler, data_release, int(visit_id), int(detector))
            results.extend(_measure_group(exposure, group, dataset_type, task_bundle))
        except Exception as exc:
            for _, request in group.iterrows():
                results.append(_base_record(
                    request, ",".join(data_release.calexp_dataset_types),
                    measurement_status="calexp_unavailable", message=str(exc),
                ))
    result = pd.DataFrame(results, columns=FORCED_PHOTOMETRY_COLUMNS)
    result["data_release"] = data_release.name
    result["butler_collection"] = str(data_release.butler_collections)
    result["measurement_method"] = "calexp_forced"
    return result.sort_values(["Target", "expMidptMJD", "visitId", "detector"], na_position="last").reset_index(drop=True)


def compute_coadd_forced_photometry(
    targets: pd.DataFrame,
    coverage_rows: pd.DataFrame,
    *,
    butler,
    data_release: "DataReleaseConfig",
    target_names: Iterable[str] | None = None,
    verbose: bool = True,
    on_target_result=None,
) -> pd.DataFrame:
    """Measure a PSF flux at each target coordinate on every available deep coadd.

    Each result is one coadd measurement per target and band.  ``expMidptMJD``
    is the median epoch of the TAP coverage rows in that band; it is only a
    representative plotting position, not the epoch of the stacked coadd.
    """
    required = {"Target", "RA_deg", "Dec_deg"}
    if missing := required - set(targets.columns):
        raise ValueError(f"Targets are missing required columns: {sorted(missing)}")
    selected = targets[["Target", "RA_deg", "Dec_deg"]].dropna().drop_duplicates("Target").copy()
    if target_names is not None:
        selected = selected[selected["Target"].astype(str).isin({str(name) for name in target_names})]
    task_bundle = _make_forced_measurement_task()
    all_records: list[dict] = []
    iterator = list(selected.itertuples(index=False))
    if verbose:
        try:
            from tqdm.auto import tqdm
            iterator = tqdm(iterator, desc="Coadd forced photometry", unit="target", dynamic_ncols=True)
        except ImportError:
            pass
    for target in iterator:
        target_records: list[dict] = []
        target_name, ra, dec = str(target.Target), float(target.RA_deg), float(target.Dec_deg)
        try:
            refs = []
            for dataset_type in data_release.coadd_dataset_types:
                refs = list(butler.query_datasets(
                    dataset_type, where=data_release.coadd_spatial_where,
                    bind={"ra": ra, "dec": dec},
                ))
                if refs:
                    break
            if not refs:
                request = pd.Series({"Target": target_name, "RA_deg": ra, "Dec_deg": dec,
                                     "band": np.nan, "visitId": np.nan, "detector": np.nan,
                                     "expMidptMJD": np.nan})
                target_records.append(_base_record(
                    request, ",".join(data_release.coadd_dataset_types),
                    measurement_status="no_coadd", message="No deep coadd contains the target coordinate.",
                ))
            seen_bands: set[str] = set()
            for ref in refs:
                band = str(ref.dataId["band"])
                if band in seen_bands:
                    continue
                seen_bands.add(band)
                band_rows = coverage_rows.loc[
                    (coverage_rows["Target"].astype(str) == target_name)
                    & (coverage_rows["band"].astype(str) == band)
                ] if {"Target", "band"}.issubset(coverage_rows.columns) else pd.DataFrame()
                epoch = pd.to_numeric(band_rows.get("expMidptMJD"), errors="coerce").median()
                request = pd.Series({"Target": target_name, "RA_deg": ra, "Dec_deg": dec,
                                     "band": band, "visitId": np.nan, "detector": np.nan,
                                     "expMidptMJD": epoch})
                try:
                    coadd = butler.get(ref)
                    records = _measure_group(coadd, pd.DataFrame([request]), dataset_type, task_bundle)
                    for record in records:
                        record["tract"] = ref.dataId.get("tract", np.nan)
                        record["patch"] = str(ref.dataId.get("patch", ""))
                        record["coadd_epoch_mjd"] = epoch
                        record["epoch_definition"] = "median_coverage_mjd"
                    target_records.extend(records)
                except Exception as exc:
                    target_records.append(_base_record(
                        request, dataset_type, measurement_status="coadd_measurement_error",
                        message=str(exc), tract=ref.dataId.get("tract", np.nan),
                        patch=str(ref.dataId.get("patch", "")), coadd_epoch_mjd=epoch,
                        epoch_definition="median_coverage_mjd",
                    ))
        except Exception as exc:
            request = pd.Series({"Target": target_name, "RA_deg": ra, "Dec_deg": dec,
                                 "band": np.nan, "visitId": np.nan, "detector": np.nan,
                                 "expMidptMJD": np.nan})
            target_records.append(_base_record(
                request, ",".join(data_release.coadd_dataset_types),
                measurement_status="coadd_query_error", message=str(exc),
            ))
        frame = pd.DataFrame(target_records, columns=FORCED_PHOTOMETRY_COLUMNS)
        frame["data_release"] = data_release.name
        frame["butler_collection"] = str(data_release.butler_collections)
        frame["measurement_method"] = "coadd_forced"
        if on_target_result is not None:
            on_target_result(frame)
        all_records.extend(frame.to_dict("records"))
    result = pd.DataFrame(all_records, columns=FORCED_PHOTOMETRY_COLUMNS)
    if result.empty:
        return _empty_forced_photometry()
    return result.sort_values(["Target", "band", "expMidptMJD"], na_position="last").reset_index(drop=True)


def save_release_forced_photometry(photometry: pd.DataFrame, path: str | Path) -> None:
    """Persist the long forced-photometry table as a CSV file."""
    frame = photometry.reindex(columns=FORCED_PHOTOMETRY_COLUMNS)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def load_release_forced_photometry(path: str | Path, target_name: str | None = None) -> pd.DataFrame:
    """Load a cached release-photometry CSV, optionally for one target."""
    path = Path(path)
    if not path.exists():
        return _empty_forced_photometry()
    try:
        data = pd.read_csv(path)
    except (OSError, ValueError):
        return _empty_forced_photometry()
    if target_name is not None and "Target" in data:
        data = data[data["Target"].astype(str) == str(target_name)]
    return data.reindex(columns=[column for column in FORCED_PHOTOMETRY_COLUMNS if column in data.columns])


def summarize_release_forced_photometry(photometry: pd.DataFrame) -> pd.DataFrame:
    """Return per-target counts of requested and usable forced measurements."""
    columns = ["Target", "release_forced_n_requested", "release_forced_n_measured", "release_forced_n_magnitudes"]
    if photometry.empty or "Target" not in photometry:
        return pd.DataFrame(columns=columns)
    grouped = photometry.groupby("Target", sort=False)
    result = grouped.size().rename("release_forced_n_requested").to_frame()
    if "measurement_status" in photometry:
        result["release_forced_n_measured"] = grouped["measurement_status"].apply(
            lambda values: int((values == "measured").sum())
        )
    else:
        result["release_forced_n_measured"] = 0
    if "magnitude" in photometry:
        result["release_forced_n_magnitudes"] = grouped["magnitude"].apply(
            lambda values: int(pd.to_numeric(values, errors="coerce").notna().sum())
        )
    else:
        result["release_forced_n_magnitudes"] = 0
    return result.reset_index()


def prepare_release_lightcurve_data(photometry: pd.DataFrame, band: str) -> pd.DataFrame:
    """Prepare finite calibrated magnitudes from one Rubin band for plotting."""
    required = {"band", "expMidptMJD", "magnitude"}
    if photometry.empty or not required.issubset(photometry.columns):
        return pd.DataFrame(columns=["Timestamp", "Magnitude", "Error"])
    data = photometry.loc[photometry["band"].astype(str) == str(band)].copy()
    data["Timestamp"] = pd.to_datetime(
        pd.to_numeric(data["expMidptMJD"], errors="coerce"), unit="D",
        origin=pd.Timestamp("1858-11-17"), errors="coerce",
    )
    data["Magnitude"] = pd.to_numeric(data["magnitude"], errors="coerce")
    data["Error"] = pd.to_numeric(data.get("magnitude_err"), errors="coerce")
    return data.dropna(subset=["Timestamp", "Magnitude"]).sort_values("Timestamp")


def _tap_to_frame(tap_service, query: str) -> pd.DataFrame:
    """Run one TAP query and return its result as a pandas table."""
    job = tap_service.submit_job(query)
    job.run()
    job.wait(phases=["COMPLETED", "ERROR"])
    if job.phase == "ERROR":
        job.raise_if_error()
    return job.fetch_result().to_table().to_pandas()


def _nanojansky_to_magnitude(flux, flux_error):
    """Convert positive AB flux density in nJy to magnitude and uncertainty."""
    flux = pd.to_numeric(pd.Series(flux), errors="coerce").to_numpy(dtype=float)
    flux_error = pd.to_numeric(pd.Series(flux_error), errors="coerce").to_numpy(dtype=float)
    magnitude = np.full(len(flux), np.nan)
    magnitude_error = np.full(len(flux), np.nan)
    valid = np.isfinite(flux) & (flux > 0)
    magnitude[valid] = 31.4 - 2.5 * np.log10(flux[valid])
    valid_error = valid & np.isfinite(flux_error) & (flux_error >= 0)
    magnitude_error[valid_error] = 2.5 / np.log(10) * flux_error[valid_error] / flux[valid_error]
    return magnitude, magnitude_error


def query_dia_forced_photometry(
    targets: pd.DataFrame,
    *,
    tap_service,
    data_release: "DataReleaseConfig",
    match_radius_arcsec: float = 1.0,
    max_workers: int = 4,
    verbose: bool = True,
    on_target_result=None,
) -> pd.DataFrame:
    """Fetch published DIA forced photometry for targets matched to DiaObjects.

    This is the appropriate DP2 path while individual visit images are not
    available. A target without an unambiguous nearby DiaObject is retained as
    a status row rather than silently dropped.
    """
    from concurrent.futures import ThreadPoolExecutor
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    if data_release.tap_dia_object_table is None or data_release.tap_dia_forced_source_table is None:
        raise ValueError(f"{data_release.name} has no configured DIA forced-source tables.")
    radius_deg = float(match_radius_arcsec) / 3600.0

    def fetch_one(row: pd.Series) -> pd.DataFrame:
        target = str(row["Target"])
        ra, dec = float(row["RA_deg"]), float(row["Dec_deg"])
        provenance = {
            "Target": target, "RA_deg": ra, "Dec_deg": dec,
            "data_release": data_release.name,
            "butler_collection": str(data_release.butler_collections),
            "measurement_method": "dia_forced_catalog",
            "dataset_type": "ForcedSourceOnDiaObject",
        }
        try:
            objects = _tap_to_frame(tap_service, f"""
                SELECT diaObjectId, ra, dec
                FROM {data_release.tap_dia_object_table}
                WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra}, {dec}, {radius_deg})) = 1
            """)
            if objects.empty:
                return pd.DataFrame([{**provenance, "measurement_status": "no_dia_object",
                                      "message": f"No DiaObject within {match_radius_arcsec:g} arcsec."}])
            target_coord = SkyCoord(ra * u.deg, dec * u.deg)
            object_coord = SkyCoord(
                pd.to_numeric(objects["ra"], errors="coerce").to_numpy() * u.deg,
                pd.to_numeric(objects["dec"], errors="coerce").to_numpy() * u.deg,
            )
            separation = target_coord.separation(object_coord).arcsec
            selected = objects.iloc[int(np.nanargmin(separation))]
            dia_object_id = int(selected["diaObjectId"])
            forced = _tap_to_frame(tap_service, f"""
                SELECT fs.diaObjectId AS diaObjectId, fs.visit AS visitId, fs.detector AS detector,
                       fs.band AS band, fs.psfFlux AS direct_flux_njy,
                       fs.psfFluxErr AS direct_flux_err_njy, fs.psfFlux_flag AS direct_flux_flag,
                       fs.psfDiffFlux AS difference_flux_njy,
                       fs.psfDiffFluxErr AS difference_flux_err_njy,
                       fs.psfDiffFlux_flag AS difference_flux_flag,
                       vd.expMidptMJD AS expMidptMJD
                FROM {data_release.tap_dia_forced_source_table} AS fs,
                     {data_release.tap_visit_table} AS vd
                WHERE fs.visit = vd.{data_release.visit_columns['visitId']}
                  AND fs.detector = vd.{data_release.visit_columns['detector']}
                  AND fs.diaObjectId = {dia_object_id}
            """)
            if forced.empty:
                return pd.DataFrame([{**provenance, "diaObjectId": dia_object_id,
                                      "dia_match_sep_arcsec": float(np.nanmin(separation)),
                                      "measurement_status": "dia_object_without_forced_sources",
                                      "message": "Matched DiaObject has no forced-source rows."}])
            forced["magnitude"], forced["magnitude_err"] = _nanojansky_to_magnitude(
                forced["direct_flux_njy"], forced["direct_flux_err_njy"],
            )
            forced["inst_flux"] = forced["direct_flux_njy"]
            forced["inst_flux_err"] = forced["direct_flux_err_njy"]
            forced["measurement_flag"] = forced["direct_flux_flag"]
            forced["measurement_status"] = "measured"
            forced["message"] = ""
            for key, value in provenance.items():
                forced[key] = value
            forced["dia_match_sep_arcsec"] = float(np.nanmin(separation))
            return forced
        except Exception as exc:
            return pd.DataFrame([{**provenance, "measurement_status": "tap_query_error", "message": str(exc)}])

    rows = [row for _, row in targets[["Target", "RA_deg", "Dec_deg"]].dropna().iterrows()]
    workers = max(1, min(int(max_workers), len(rows))) if rows else 1
    if on_target_result is not None:
        # Persist each target immediately; this makes an interrupted long run reusable.
        iterator = rows
        if verbose:
            try:
                from tqdm.auto import tqdm
                iterator = tqdm(rows, desc="DIA forced photometry", unit="target", dynamic_ncols=True)
            except ImportError:
                pass
        frames = []
        for row in iterator:
            frame = fetch_one(row)
            on_target_result(frame)
            frames.append(frame)
    elif workers == 1:
        frames = [fetch_one(row) for row in rows]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            frames = list(executor.map(fetch_one, rows))
    return pd.concat(frames, ignore_index=True) if frames else _empty_forced_photometry()


def target_release_photometry_path(cache_dir: str | Path, target_name: str) -> Path:
    """Return the persistent Rubin-photometry CSV path for one target."""
    import re
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(target_name)).strip("._") or "unknown_target"
    return Path(cache_dir) / f"{safe_name}.csv"


def save_target_release_photometry(
    data: pd.DataFrame,
    cache_dir: str | Path,
    *,
    replace_measurement_scope: bool = False,
) -> None:
    """Upsert newly retrieved Rubin rows into one CSV per target.

    With ``replace_measurement_scope=True``, all cached rows from the same
    release, Butler collection, and measurement method are replaced for that
    target. This is appropriate for coadd measurements, where one refresh
    re-queries every available band and stale patch-boundary errors must not
    remain in the cache.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    identity = [
        "data_release", "butler_collection", "measurement_method", "dataset_type",
        "band", "diaObjectId", "visitId", "detector", "tract", "patch",
    ]
    scope = ["data_release", "butler_collection", "measurement_method"]
    for target, fresh in data.groupby("Target", sort=False):
        path = target_release_photometry_path(cache_dir, target)
        try:
            old = pd.read_csv(path) if path.exists() else pd.DataFrame()
        except (OSError, ValueError):
            old = pd.DataFrame()
        if replace_measurement_scope and not old.empty and set(scope).issubset(old.columns):
            fresh_scope = fresh[scope].drop_duplicates()
            matching_scope = old.merge(fresh_scope, on=scope, how="left", indicator=True)["_merge"].eq("both")
            old = old.loc[~matching_scope.to_numpy()].copy()
        combined = pd.concat([old, fresh], ignore_index=True, sort=False)
        existing_identity = [column for column in identity if column in combined]
        if existing_identity:
            combined = combined.drop_duplicates(existing_identity, keep="last")
        sort_columns = [
            column for column in ("expMidptMJD", "visitId", "detector")
            if column in combined
        ]
        if sort_columns:
            combined.sort_values(sort_columns, inplace=True, na_position="last")
        combined.to_csv(path, index=False)


def load_target_release_photometry(cache_dir: str | Path, target_name: str) -> pd.DataFrame:
    """Load persistent Rubin photometry for a single target."""
    path = target_release_photometry_path(cache_dir, target_name)
    try:
        return pd.read_csv(path) if path.exists() else pd.DataFrame()
    except (OSError, ValueError):
        return pd.DataFrame()
