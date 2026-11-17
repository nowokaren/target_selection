"""Local survey inventory tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from target_selection.target_registry import TargetRegistry
from target_selection.observatory_observations import canonical_target_name
from target_selection.cache_paths import CACHE_ROOT, OUTPUT_ROOT, PROJECT_ROOT


DEFAULT_HSH_INVENTORY = PROJECT_ROOT / "data" / "hsh_data" / "image_collection_astro.csv"
DEFAULT_HSH_TARGETS = PROJECT_ROOT / "data" / "hsh_data" / "hsh_objects.csv"
DEFAULT_JS_INVENTORY = PROJECT_ROOT / "data" / "js_data" / "js_inventory.csv"


def import_hsh_inventory(
    inventory_path: str | Path,
    *,
    database_path: str | Path = OUTPUT_ROOT / "target_database" / "target_selection.sqlite",
    force: bool = False,
    verbose: bool = True,
) -> int:
    """Import the HSH astrometric image inventory into the registry."""
    inventory_path = Path(inventory_path)
    if not inventory_path.exists() and not inventory_path.is_absolute():
        # Notebooks are often launched from ``notebooks/`` rather than the
        # repository root. Resolve the same user-facing relative path there.
        candidates = [Path.cwd().parent / inventory_path, Path(__file__).resolve().parents[2] / inventory_path]
        inventory_path = next((candidate for candidate in candidates if candidate.exists()), inventory_path)
    if not inventory_path.exists():
        raise FileNotFoundError(
            f"HSH inventory not found: {inventory_path}. "
            "Use an absolute path or run from the repository root."
        )
    if verbose:
        print(f"[surveys] Importing HSH inventory: {inventory_path}", flush=True)
    database = TargetRegistry(database_path)
    count = database.import_hsh_catalog(inventory_path, force=force)
    if verbose:
        print(f"[surveys] HSH records imported/updated: {count}", flush=True)
    return count


def observed_targets(
    *,
    providers: tuple[str, ...] = ("HSH",),
    database_path: str | Path = OUTPUT_ROOT / "target_database" / "target_selection.sqlite",
    verbose: bool = True,
) -> pd.DataFrame:
    """Return targets observed by local surveys already imported in the registry."""
    database = TargetRegistry(database_path)
    result = database.observed_targets(tuple(provider.upper() for provider in providers))
    if verbose:
        print(f"[surveys] Previously observed targets: {len(result)}", flush=True)
    return result


def _import_js_inventory(
    inventory_path: str | Path,
    database: TargetRegistry,
    *,
    force: bool = False,
) -> int:
    """Import confirmed JS science observations; plans are not observations."""
    path = Path(inventory_path)
    if not path.exists() and not path.is_absolute():
        path = PROJECT_ROOT / path
    stat = path.stat()
    source_id = str(path.resolve())
    fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
    if not force and database.source_is_current("JS", source_id, fingerprint):
        return 0

    inventory = pd.read_csv(path)
    status = inventory.get("product_status", pd.Series("", index=inventory.index))
    data = inventory.loc[status.fillna("").astype(str).str.strip().str.casefold().eq("observed")].copy()
    data["Target"] = data.get("target", pd.Series("", index=data.index)).fillna("").astype(str).str.strip()
    # JS records observing nights without exact epochs; midnight UTC lets the
    # report shade the recorded calendar day without implying a precise time.
    data["observation_date"] = pd.to_datetime(data.get("date"), errors="coerce", utc=True)
    data = data.loc[data["Target"].ne("") & data["observation_date"].notna()].copy()
    data["mjd"] = data["observation_date"].map(
        lambda value: value.to_julian_date() - 2400000.5
    )
    data["band"] = data.get("filter_or_grating", pd.Series("", index=data.index)).fillna("").astype(str)
    data["exptime_s"] = pd.to_numeric(data.get("exposure_s"), errors="coerce")
    data["usable"] = True
    data["observation_id"] = data.apply(
        lambda row: "JS:{}:{}:{}:{}".format(
            canonical_target_name(row["Target"]),
            row["observation_date"].strftime("%Y-%m-%d"),
            row["band"], row.name,
        ), axis=1,
    )
    count = database.import_survey_observations("JS", data, source_id=source_id)
    database.mark_source("JS", source_id, fingerprint)
    return count


def load_observed_survey_targets(
    *,
    providers: tuple[str, ...] = ("HSH", "JS"),
    database_path: str | Path = OUTPUT_ROOT / "target_database" / "target_selection.sqlite",
    hsh_inventory_path: str | Path = DEFAULT_HSH_INVENTORY,
    hsh_target_catalog_path: str | Path = DEFAULT_HSH_TARGETS,
    js_inventory_path: str | Path = DEFAULT_JS_INVENTORY,
    force: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """Load targets with recorded HSH/JS observations and their provenance.

    HSH image epochs and confirmed JS inventory rows are imported into the
    shared registry before returning one row per previously observed event.
    Survey coordinates are used only when the registry lacks a preferred
    position; HSH image pointings are never treated as target coordinates.
    """
    selected = tuple(dict.fromkeys(str(name).upper() for name in providers))
    unknown = set(selected) - {"HSH", "JS"}
    if unknown:
        raise ValueError(f"Unsupported local survey providers: {sorted(unknown)}")
    database = TargetRegistry(database_path)
    if "HSH" in selected:
        import_hsh_inventory(hsh_inventory_path, database_path=database_path, force=force, verbose=verbose)
    if "JS" in selected:
        if verbose:
            print(f"[surveys] Importing JS inventory: {js_inventory_path}", flush=True)
        imported = _import_js_inventory(js_inventory_path, database, force=force)
        if verbose:
            print(f"[surveys] JS observations imported/updated: {imported}", flush=True)

    result = database.observed_targets(selected)
    if "HSH" in selected and Path(hsh_target_catalog_path).exists() and not result.empty:
        coordinates = pd.read_csv(hsh_target_catalog_path)
        coordinates["target_key"] = coordinates["object"].map(canonical_target_name)
        coordinates = coordinates.drop_duplicates("target_key").set_index("target_key")
        result["target_key"] = result["Target"].map(canonical_target_name)
        for output, source in (("RA_deg", "ra_deg"), ("Dec_deg", "dec_deg")):
            if source in coordinates:
                fill = result["target_key"].map(coordinates[source])
                missing = pd.to_numeric(result[output], errors="coerce").isna()
                result.loc[missing, output] = fill.loc[missing]
        missing_coordinates = result["coordinate_source"].isna() & result[["RA_deg", "Dec_deg"]].notna().all(axis=1)
        result.loc[missing_coordinates, "coordinate_source"] = "HSH event coordinate catalog"
        result.loc[missing_coordinates, "coordinate_priority"] = 10
        result = result.drop(columns="target_key")

    result = result.rename(columns={"Target": "target_id", "RA_deg": "ra_deg", "Dec_deg": "dec_deg"})
    result["provider"] = result.get("observatory_providers", pd.Series("", index=result.index)).fillna("").str.lower()
    result["source_providers"] = result["provider"]
    result["input_sources"] = result["source_providers"]
    result["is_previously_observed"] = True
    if verbose:
        print(f"[surveys] Previously observed event targets loaded: {len(result)}", flush=True)
    return result.reset_index(drop=True)



def update_observed_survey_target_catalog(
    observed_targets_frame: pd.DataFrame,
    tom_targets: pd.DataFrame,
    *,
    catalog_path: str | Path = CACHE_ROOT / "observed_survey_targets.csv",
) -> pd.DataFrame:
    """Accumulate valid HSH/JS targets and refresh matching rows from daily TOM catalogs.

    Existing records are retained; current survey rows update observation
    provenance, and current TOM rows update event fields and ``tom_source``.
    Rows without valid ICRS coordinates are excluded from the persistent catalog.
    """
    path = Path(catalog_path)
    old = pd.read_csv(path, low_memory=False) if path.exists() else pd.DataFrame()

    def is_missing(value: object) -> bool:
        return value is None or (
            pd.api.types.is_scalar(value) and bool(pd.isna(value))
        ) or (isinstance(value, str) and value.strip().casefold() in {"", "nan", "none", "<na>"})

    def prepare(frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg"])
        result = frame.copy()
        if "target_id" not in result and "Target" in result:
            result = result.rename(columns={"Target": "target_id"})
        if "target_id" not in result:
            return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg"])
        for canonical, aliases in (("ra_deg", ("RA_deg", "RA", "ra")), ("dec_deg", ("Dec_deg", "DEC_deg", "Dec", "DEC", "dec"))):
            result[canonical] = pd.to_numeric(result.get(canonical), errors="coerce")
            for alias in aliases:
                if alias in result:
                    result[canonical] = result[canonical].fillna(pd.to_numeric(result[alias], errors="coerce"))
        if not {"ra_deg", "dec_deg"}.issubset(result.columns):
            return result.iloc[0:0].copy()
        valid = (
            pd.to_numeric(result["ra_deg"], errors="coerce").between(0, 360, inclusive="left")
            & pd.to_numeric(result["dec_deg"], errors="coerce").between(-90, 90, inclusive="both")
        )
        result = result.loc[valid].copy()
        result["target_id"] = result["target_id"].astype(str).str.strip()
        result = result.loc[result["target_id"].ne("")]
        result["_target_key"] = result["target_id"].map(canonical_target_name)
        return result

    previous, current_observed, current_tom = map(prepare, (old, observed_targets_frame, tom_targets))
    for frame in (previous, current_observed, current_tom):
        if "_target_key" not in frame:
            frame["_target_key"] = pd.Series(dtype=str)
    observed_keys = set(current_observed["_target_key"]) | set(previous["_target_key"])
    current_tom = current_tom.loc[current_tom.get("_target_key", pd.Series(dtype=str)).isin(observed_keys)]
    today = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []

    def source_tokens(row: pd.Series, columns: tuple[str, ...]) -> set[str]:
        values = set()
        for column in columns:
            value = row.get(column, "")
            if is_missing(value):
                continue
            values.update(part.strip().lower() for part in str(value).replace(";", ",").split(",") if part.strip())
        return values

    for key in dict.fromkeys([
        *previous.get("_target_key", pd.Series(dtype=str)).tolist(),
        *current_observed.get("_target_key", pd.Series(dtype=str)).tolist(),
    ]):
        records = []
        for frame in (previous, current_observed, current_tom):
            records.extend(row for _, row in frame.loc[frame["_target_key"].eq(key)].iterrows())
        if not records:
            continue
        merged = {}
        for record in records:
            for column, value in record.items():
                if column.startswith("_") or is_missing(value):
                    continue
                merged[column] = value
        observed_sources = set()
        for record in records:
            observed_sources |= source_tokens(record, ("observed_by",))
        for _, record in current_observed.loc[current_observed["_target_key"].eq(key)].iterrows():
            observed_sources |= source_tokens(record, ("source_providers", "provider", "observatory_providers")) & {"hsh", "js"}
        tom_sources = set()
        for record in records:
            tom_sources |= source_tokens(record, ("tom_source",))
        for _, record in current_tom.loc[current_tom["_target_key"].eq(key)].iterrows():
            tom_sources |= source_tokens(record, ("source_providers", "input_sources", "provider", "target_source", "tom_source")) - {"hsh", "js"}
        all_sources = set()
        for record in records:
            all_sources |= source_tokens(record, ("source_providers", "input_sources", "provider", "observatory_providers"))
        merged.update({
            "target_id": merged.get("target_id", key),
            "observed_by": ",".join(sorted(observed_sources)),
            "tom_source": ",".join(sorted(tom_sources)),
            "source_providers": ",".join(sorted(all_sources | observed_sources | tom_sources)),
            "is_previously_observed": True,
        })
        if current_observed["_target_key"].eq(key).any():
            merged["survey_catalog_updated_utc"] = today
        if current_tom["_target_key"].eq(key).any():
            merged["tom_data_updated_utc"] = today
        merged.pop("_target_key", None)
        rows.append(merged)

    if rows:
        catalog = pd.DataFrame(rows)
    else:
        columns = list(dict.fromkeys(
            column for frame in (previous, current_observed, current_tom)
            for column in frame.columns if not column.startswith("_")
        ))
        catalog = pd.DataFrame(columns=[*columns, "observed_by", "tom_source", "source_providers"])
    path.parent.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(path, index=False)
    return catalog.reset_index(drop=True)


def observed_survey_epochs(
    targets: pd.DataFrame,
    *,
    providers: tuple[str, ...] = ("HSH", "JS"),
    database_path: str | Path = OUTPUT_ROOT / "target_database" / "target_selection.sqlite",
) -> pd.DataFrame:
    """Return HSH/JS observation dates for candidate light-curve shading."""
    if "Target" not in targets:
        if "target_id" not in targets:
            raise ValueError("Targets must contain target_id or Target.")
        targets = targets.rename(columns={"target_id": "Target"})
    return TargetRegistry(database_path).observation_epochs(targets, providers)


__all__ = [
    "DEFAULT_HSH_INVENTORY", "DEFAULT_HSH_TARGETS", "DEFAULT_JS_INVENTORY",
    "import_hsh_inventory", "observed_targets", "load_observed_survey_targets",
    "observed_survey_epochs", "update_observed_survey_target_catalog",
]
