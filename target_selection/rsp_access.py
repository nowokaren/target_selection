"""Small, collection-aware access API for Rubin RSP data products.

This module is the notebook-facing layer for recurring RSP operations. It keeps
collection resolution in one place and delegates the detailed matching and
partial-map logic to the existing reference-catalog implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from data_release_config import DataReleaseConfig, get_data_release


def _normalise_positions(positions: pd.DataFrame) -> pd.DataFrame:
    """Return positions with the canonical target_id/ra_deg/dec_deg columns."""
    if not isinstance(positions, pd.DataFrame):
        raise TypeError("positions must be a pandas DataFrame")
    output = positions.copy()
    renames = {}
    if "ra_deg" not in output and "RA" in output:
        renames["RA"] = "ra_deg"
    if "dec_deg" not in output and "DEC" in output:
        renames["DEC"] = "dec_deg"
    if renames:
        output = output.rename(columns=renames)
    required = {"target_id", "ra_deg", "dec_deg"}
    missing = required - set(output.columns)
    if missing:
        raise ValueError(f"positions is missing required columns: {sorted(missing)}")
    output = output[["target_id", "ra_deg", "dec_deg"]].copy()
    output["target_id"] = output["target_id"].astype(str)
    output["ra_deg"] = pd.to_numeric(output["ra_deg"], errors="coerce")
    output["dec_deg"] = pd.to_numeric(output["dec_deg"], errors="coerce")
    invalid = output["ra_deg"].isna() | output["dec_deg"].isna()
    if invalid.any():
        raise ValueError(f"positions contains {int(invalid.sum())} invalid coordinate row(s)")
    return output.drop_duplicates("target_id").reset_index(drop=True)


def get_property_map_values(
    positions: pd.DataFrame,
    *,
    collection: str | DataReleaseConfig = "DP2",
    properties: str | Sequence[str] = ("maglim", "psf_size", "exposure_time", "sky_noise"),
    bands: Sequence[str] = tuple("ugrizy"),
    butler: Any = None,
    skymap: str | None = None,
    partial_reads: bool = True,
    cache_path: str | Path | None = None,
    reuse_cache: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Sample coadd property maps at target positions for one RSP collection.

    ``positions`` must contain ``target_id``, ``ra_deg`` and ``dec_deg`` (or
    ``RA`` and ``DEC``). Values are sampled at the target position; they are
    not computed from a cutout. ``properties`` accepts map aliases such as
    ``maglim``, ``psf_size``, ``exposure_time`` and ``sky_noise``.
    """
    release = get_data_release(collection)
    if butler is None:
        from .collection_access import open_collection
        butler = open_collection(release).butler
    requested = (properties,) if isinstance(properties, str) else tuple(properties)
    from .reference_catalog import COADD_PROPERTY_MAPS, sample_coadd_properties
    unknown = sorted(set(requested) - set(COADD_PROPERTY_MAPS))
    if unknown:
        raise ValueError(f"Unknown property map(s): {unknown}. Options: {sorted(COADD_PROPERTY_MAPS)}")
    targets = _normalise_positions(positions)
    if cache_path is not None and reuse_cache:
        cache_file = Path(cache_path)
        if cache_file.exists():
            cached = pd.read_csv(cache_file)
            if set(targets["target_id"]) <= set(cached["target_id"].astype(str)):
                return cached.loc[cached["target_id"].astype(str).isin(set(targets["target_id"]))].copy()
    result = sample_coadd_properties(
        targets, butler=butler, bands=bands, property_maps=requested,
        skymap=skymap, partial_reads=partial_reads, verbose=verbose,
    )
    if cache_path is not None:
        cache_file = Path(cache_path)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(cache_file, index=False)
    return result


def get_visit_coverage(
    positions: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2",
    tap_service: Any = None, tile_nside: int = 64, max_workers: int = 2,
    timeout_seconds: int = 7200, search_radius_deg: float = 0.35,
    cache_dir: str | Path | None = None, reuse_cache: bool = True,
    bands: Sequence[str] = tuple("ugrizy"), verbose: bool = True,
) -> pd.DataFrame:
    """Return exact individual-visit coverage for target positions."""
    from .collection_access import load_collection_coverage
    return load_collection_coverage(
        _normalise_positions(positions), collection=collection, tap_service=tap_service,
        tile_nside=tile_nside, max_workers=max_workers, timeout_seconds=timeout_seconds,
        search_radius_deg=search_radius_deg, cache_dir=cache_dir, reuse_cache=reuse_cache,
        bands=bands, verbose=verbose,
    )


def get_coadd_coverage(
    coverage: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2",
    butler: Any = None, cache_path: str | Path | None = None,
    reuse_cache: bool = True, bands: Sequence[str] = tuple("ugrizy"),
    verbose: bool = True,
) -> pd.DataFrame:
    """Add direct Butler coadd-dataset coverage to a target table."""
    from .collection_access import load_collection_coadds
    if butler is None:
        from .collection_access import open_collection
        butler = open_collection(collection).butler
    return load_collection_coadds(
        coverage, collection=collection, butler=butler, cache_path=cache_path,
        reuse_cache=reuse_cache, bands=bands, verbose=verbose,
    )


def get_coadd_cutouts(
    coverage: pd.DataFrame, *, collection: str | DataReleaseConfig = "DP2",
    butler: Any = None, output_dir: str | Path, fits_dir: str | Path,
    bands: Sequence[str] = tuple("ugrizy"), size_arcsec: float = 20.0,
    category_column: str | None = None, add_rgb: bool = False,
    reuse_existing: bool = True, manifest_path: str | Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Generate square coadd cutouts for targets with coadd coverage."""
    from .collection_access import generate_collection_cutouts
    if butler is None:
        from .collection_access import open_collection
        butler = open_collection(collection).butler
    return generate_collection_cutouts(
        coverage, collection=collection, butler=butler, output_dir=output_dir,
        fits_dir=fits_dir, bands=bands, size_arcsec=size_arcsec,
        filename_prefix_column=category_column, add_rgb=add_rgb,
        reuse_existing=reuse_existing, manifest_path=manifest_path,
        verbose=verbose,
    )


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def get_table(
    table_name: str, *, collection: str | DataReleaseConfig = "DP2",
    columns: str | Sequence[str] = "*", where: str | None = None,
    filters: dict[str, Any] | None = None, limit: int | None = None,
    tap_service: Any = None, timeout_seconds: int = 7200,
) -> pd.DataFrame:
    """Query a TAP table from one RSP collection and return a DataFrame.

    Use ``where`` for spatial or compound ADQL conditions. ``filters`` is a
    convenience for simple equality/``IN`` predicates. Table aliases include
    ``CcdVisit``/``VisitDetector`` and are resolved from the collection profile;
    other names must be qualified, e.g. ``dp2.DiaObject``.
    """
    import re
    release = get_data_release(collection)
    if tap_service is None:
        from .collection_access import open_collection
        tap_service = open_collection(release).tap_service
    aliases = {"ccdvisit": release.tap_visit_table.lower(), "visitdetector": release.tap_visit_table.lower()}
    resolved_table = aliases.get(str(table_name).strip().lower(), str(table_name).strip())
    if not re.fullmatch(r"[A-Za-z0-9_.]+", resolved_table):
        raise ValueError("table_name must be a qualified table name without SQL expressions")
    selected = columns if isinstance(columns, str) else ", ".join(str(column) for column in columns)
    predicates = []
    if where:
        predicates.append(f"({where})")
    for column, value in (filters or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_.]+", str(column)):
            raise ValueError(f"Invalid filter column: {column!r}")
        if isinstance(value, (list, tuple, set, frozenset)):
            values = ", ".join(_sql_literal(item) for item in value)
            predicates.append(f"{column} IN ({values})")
        else:
            predicates.append(f"{column} = {_sql_literal(value)}")
    query = f"SELECT {selected} FROM {resolved_table}"
    if predicates:
        query += " WHERE " + " AND ".join(predicates)
    if limit is not None:
        if int(limit) < 1:
            raise ValueError("limit must be positive")
        query = f"SELECT TOP {int(limit)} {selected} FROM {resolved_table}" + ((" WHERE " + " AND ".join(predicates)) if predicates else "")
    job = tap_service.submit_job(query)
    job.run()
    job.wait(phases=["COMPLETED", "ERROR"], timeout=timeout_seconds)
    if job.phase == "ERROR":
        job.raise_if_error()
    return job.fetch_result().to_table().to_pandas()



def get_image(
    targets: pd.DataFrame, *, image_type: str = "coadd",
    collection: str | DataReleaseConfig = "DP2", butler: Any = None,
    output_dir: str | Path | None = None, fits_dir: str | Path | None = None,
    bands: Sequence[str] = tuple("ugrizy"), size_arcsec: float = 20.0,
    category_column: str | None = None, add_rgb: bool = False,
    reuse_existing: bool = True, verbose: bool = True,
):
    """Retrieve or generate image products using collection-specific dataset names.

    ``image_type`` is one of ``coadd``, ``visit``, ``raw_visit`` or ``diff``.
    Coadd cutouts are currently implemented. Other image families are resolved
    and reported through ``list_images`` but require collection-specific cutout
    extractors that are not yet available in every RSP release.
    """
    image_type = str(image_type).strip().lower()
    valid = {"coadd", "visit", "raw_visit", "diff"}
    if image_type not in valid:
        raise ValueError(f"image_type must be one of {sorted(valid)}")
    if image_type == "coadd":
        if output_dir is None or fits_dir is None:
            raise ValueError("coadd images require output_dir and fits_dir")
        return get_coadd_cutouts(
            targets, collection=collection, butler=butler, output_dir=output_dir,
            fits_dir=fits_dir, bands=bands, size_arcsec=size_arcsec,
            category_column=category_column, add_rgb=add_rgb,
            reuse_existing=reuse_existing, verbose=verbose,
        )
    image_info = list_images(collection)
    selected = image_info.loc[image_info["image_type"].eq(image_type)]
    datasets = selected["datasets"].iloc[0] if len(selected) else "unknown"
    status = selected["status"].iloc[0] if len(selected) else "unknown"
    raise NotImplementedError(
        f"Image cutouts for image_type={image_type!r} are not implemented for "
        f"collection={get_data_release(collection).name!r}. Configured datasets: "
        f"{datasets}. Availability/status: {status}."
    )

def list_property_maps(collection: str | DataReleaseConfig = "DP2") -> pd.DataFrame:
    """List property-map aliases and Butler dataset types for a collection."""
    from .reference_catalog import COADD_PROPERTY_MAPS
    release = get_data_release(collection)
    rows = []
    for alias, (dataset_type, output_stem) in COADD_PROPERTY_MAPS.items():
        rows.append({"collection": release.name, "property": alias, "dataset_type": dataset_type, "output_stem": output_stem})
    return pd.DataFrame(rows)


def list_tables(collection: str | DataReleaseConfig = "DP2") -> pd.DataFrame:
    """List configured TAP tables and convenient aliases for a collection."""
    release = get_data_release(collection)
    rows = [
        {"collection": release.name, "alias": "CcdVisit", "table": release.tap_visit_table, "role": "visit/detector metadata"},
        {"collection": release.name, "alias": "VisitDetector", "table": release.tap_visit_table, "role": "visit/detector metadata"},
    ]
    optional = (
        ("DiaObject", release.tap_dia_object_table, "difference-image objects"),
        ("ForcedSourceOnDiaObject", release.tap_dia_forced_source_table, "forced difference-source photometry"),
        ("DiaSource", release.tap_dia_source_table, "difference-image detections"),
    )
    rows.extend({"collection": release.name, "alias": alias, "table": table, "role": role} for alias, table, role in optional if table)
    return pd.DataFrame(rows)


def list_images(collection: str | DataReleaseConfig = "DP2") -> pd.DataFrame:
    """List image/product families and their current API status."""
    release = get_data_release(collection)
    visit_note = "Coverage/metadata only; no calibrated visit image promised by this profile."
    return pd.DataFrame([
        {"collection": release.name, "image_type": "coadd", "datasets": ", ".join(release.coadd_dataset_types), "api": "get_coadd_cutouts", "status": "supported"},
        {"collection": release.name, "image_type": "visit_coverage", "datasets": release.tap_visit_table, "api": "get_visit_coverage", "status": "supported"},
        {"collection": release.name, "image_type": "calexp", "datasets": ", ".join(release.calexp_dataset_types), "api": "not implemented as a generic cutout API", "status": visit_note if "not" in release.notes.lower() and "calibrated individual" in release.notes.lower() else "verify dataset availability in Butler"},
    ])


def describe(collection: str | DataReleaseConfig = "DP2") -> dict[str, Any]:
    """Return the tables, maps, image products and notes for one collection."""
    release = get_data_release(collection)
    return {
        "collection": release.name,
        "rsp_instance": release.rsp_instance,
        "tables": list_tables(release),
        "property_maps": list_property_maps(release),
        "image_types": list_images(release),
        "bands": tuple("ugrizy"),
        "notes": release.notes,
    }


# Backward-compatible names; new code should use CollectionContext methods or the
# shorter module-level names above.
list_available_tables = list_tables
list_available_property_maps = list_property_maps
list_available_image_types = list_images
describe_collection = describe
