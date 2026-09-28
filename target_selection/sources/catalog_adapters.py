"""Adapters for catalog-like target sources.

Each adapter owns the source-specific reading and normalization logic and
returns the small canonical target schema used by the rest of the package.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
import warnings

import pandas as pd


class CatalogAdapter(Protocol):
    capabilities: tuple[str, ...]

    def load(self, definition: Mapping[str, Any], *, filters: Mapping[str, Any] | None = None, verbose: bool = True) -> pd.DataFrame: ...


def _read_table(path: Path, delimiter: str = ",") -> pd.DataFrame:
    if path.suffix.lower() in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    if path.suffix.lower() in {".fits", ".fit", ".fz"}:
        from astropy.table import Table
        return Table.read(path).to_pandas()
    return pd.read_csv(path, sep=delimiter)


def _parse_category(value: Any, parser: str | Callable[[Any], Any] | None) -> Any:
    if parser in (None, "identity"):
        return value
    if callable(parser):
        return parser(value)
    if parser == "first_extra_value":
        text = "" if pd.isna(value) else str(value).strip()
        first = text.split(",", 1)[0]
        return first.split("=", 1)[1].strip() if "=" in first else first
    raise ValueError(f"Unknown category parser: {parser!r}")


def _apply_filters(frame: pd.DataFrame, filters: Mapping[str, Any] | None, *, category_column: str | None, magnitude_column: str | None, source_name: str) -> pd.DataFrame:
    if not filters:
        return frame
    result = frame.copy()
    category = filters.get("category", filters.get("categories"))
    if category is not None:
        if "category" not in result:
            warnings.warn(f"Category filter was not applied: source {source_name!r} has no category.", stacklevel=2)
        else:
            values = {str(value) for value in (category if isinstance(category, (list, tuple, set)) else [category])}
            result = result.loc[result["category"].astype(str).isin(values)].copy()
    magnitude = filters.get("magnitude")
    if magnitude is not None:
        spec = {"column": magnitude_column, **magnitude} if isinstance(magnitude, Mapping) else {"column": magnitude_column, "maximum": magnitude}
        column = spec.get("column")
        if not column or column not in result:
            warnings.warn(f"Magnitude filter was not applied: source {source_name!r} has no configured magnitude column. Use target_io.add_data first.", stacklevel=2)
        else:
            values = pd.to_numeric(result[column], errors="coerce")
            if spec.get("minimum") is not None:
                result = result.loc[values.ge(float(spec["minimum"]))].copy()
            if spec.get("maximum") is not None:
                maximum = float(spec["maximum"])
                maximum_mask = values.lt(maximum) if spec.get("maximum_inclusive", True) is False else values.le(maximum)
                result = result.loc[maximum_mask].copy()
    function = filters.get("callable")
    if function is not None:
        result = result.loc[function(result)].copy()
    return result


class CsvCatalogAdapter:
    """Adapter for CSV, FITS and Parquet target tables."""

    capabilities = ("targets", "coordinates", "source_columns")

    def load(self, definition: Mapping[str, Any], *, filters: Mapping[str, Any] | None = None, verbose: bool = True) -> pd.DataFrame:
        path = Path(str(definition["path"])).expanduser()
        if not path.is_absolute() and definition.get("base_dir"):
            path = Path(str(definition["base_dir"])) / path
        if not path.exists():
            raise FileNotFoundError(f"Catalog source does not exist: {path}")
        raw = _read_table(path, delimiter=str(definition.get("delimiter", ",")))
        return self._normalize(raw, definition, filters=filters, verbose=verbose)

    def _normalize(self, raw: pd.DataFrame, definition: Mapping[str, Any], *, filters: Mapping[str, Any] | None, verbose: bool) -> pd.DataFrame:
        columns = dict(definition.get("columns", {}))
        source_name = str(definition.get("name", "catalog"))
        id_column, ra_column, dec_column = columns.get("id", "target_id"), columns.get("ra", "ra_deg"), columns.get("dec", "dec_deg")
        required = {id_column, ra_column, dec_column}
        missing = sorted(required - set(raw.columns))
        if missing:
            raise ValueError(f"Source {source_name!r} is missing columns: {missing}")
        result = raw.rename(columns={id_column: "target_id", ra_column: "ra_deg", dec_column: "dec_deg"}).copy()
        result["target_id"] = result["target_id"].astype(str)
        result["ra_deg"] = pd.to_numeric(result["ra_deg"], errors="coerce")
        result["dec_deg"] = pd.to_numeric(result["dec_deg"], errors="coerce")
        result = result.loc[result["ra_deg"].notna() & result["dec_deg"].notna()].copy()
        category = dict(definition.get("category", {}))
        category_column = category.get("column")
        if category_column:
            if category_column not in result:
                raise ValueError(f"Category column {category_column!r} is not present in source {source_name!r}")
            result["category"] = result[category_column].map(lambda value: _parse_category(value, category.get("parser", "identity")))
        result["source"] = source_name
        result = result.drop_duplicates("target_id").reset_index(drop=True)
        result = _apply_filters(result, filters, category_column=category_column, magnitude_column=definition.get("magnitude_column"), source_name=source_name)
        result.attrs["source_definition"] = dict(definition)
        result.attrs["source_capabilities"] = tuple(definition.get("capabilities", self.capabilities))
        if verbose:
            print(f"Loaded {len(result):,} targets from {source_name}.")
        return result


class CatalogAdapterRegistry:
    """Registry used to add new catalog APIs without changing target_io."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], CatalogAdapter]] = {}
        self._capabilities: dict[str, tuple[str, ...]] = {}

    def register(self, name: str, factory: Callable[[], CatalogAdapter], *, capabilities: tuple[str, ...] = ()) -> None:
        key = str(name).strip().lower()
        self._factories[key] = factory
        self._capabilities[key] = tuple(capabilities)

    def create(self, name: str) -> CatalogAdapter:
        key = str(name).strip().lower()
        if key not in self._factories:
            raise ValueError(f"Unknown catalog adapter {name!r}. Available: {', '.join(self.available())}")
        return self._factories[key]()

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def describe(self, name: str | None = None) -> dict[str, tuple[str, ...]] | tuple[str, ...]:
        if name is None:
            return {key: self._capabilities[key] for key in self.available()}
        return self._capabilities.get(str(name).lower(), ())


def default_catalog_registry() -> CatalogAdapterRegistry:
    registry = CatalogAdapterRegistry()
    registry.register("csv", CsvCatalogAdapter, capabilities=CsvCatalogAdapter.capabilities)
    registry.register("fits", CsvCatalogAdapter, capabilities=CsvCatalogAdapter.capabilities)
    registry.register("parquet", CsvCatalogAdapter, capabilities=CsvCatalogAdapter.capabilities)
    return registry
