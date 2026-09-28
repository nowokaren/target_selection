"""Public target-loading API.

The normal user operation is ``load(source, filters=...)``. Source-specific
reading and normalization are implemented by catalog adapters; this module
resolves source configuration and exposes optional ``add_data``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
import copy

import pandas as pd

from .sources.catalog_adapters import CatalogAdapterRegistry, default_catalog_registry

_REGISTRY = default_catalog_registry()
_CONFIG_ROOT = Path(__file__).resolve().parents[1] / "configs" / "sources"


def _read_definition(source: str | Path | pd.DataFrame, overrides: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source, pd.DataFrame):
        definition = {"name": "dataframe", "adapter": "csv", "data": source}
    else:
        candidate = Path(str(source)).expanduser()
        if candidate.exists():
            definition = {"name": candidate.stem, "adapter": candidate.suffix.lower().lstrip(".") or "csv", "path": str(candidate)}
        else:
            config_path = _CONFIG_ROOT / f"{source}.toml"
            if not config_path.exists():
                raise FileNotFoundError(f"Source {source!r} is not a file and has no config at {config_path}")
            try:
                import tomllib
            except ModuleNotFoundError:  # pragma: no cover
                import tomli as tomllib
            with config_path.open("rb") as handle:
                parsed = tomllib.load(handle)
            definition = dict(parsed.pop("source", {}))
            definition.update(parsed)
            definition["config_path"] = str(config_path)
            definition["base_dir"] = str(config_path.parents[2])
    definition = copy.deepcopy(definition)
    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"id_column", "ra_column", "dec_column"}:
            definition.setdefault("columns", {})[{"id_column":"id", "ra_column":"ra", "dec_column":"dec"}[key]] = value
        elif key == "category_column":
            definition.setdefault("category", {})["column"] = value
        elif key == "category_parser":
            definition.setdefault("category", {})["parser"] = value
        elif key == "magnitude_column":
            definition["magnitude_column"] = value
        else:
            definition[key] = value
    return definition


def register_adapter(name: str, factory: Callable[[], Any], *, capabilities: tuple[str, ...] = ()) -> None:
    """Register a catalog adapter such as a future OGLE API adapter."""
    _REGISTRY.register(name, factory, capabilities=capabilities)


def list_sources() -> list[str]:
    """List configured catalog definitions."""
    return sorted(path.stem for path in _CONFIG_ROOT.glob("*.toml"))


def list_adapters() -> tuple[str, ...]:
    """List registered catalog adapters."""
    return _REGISTRY.available()


def describe_adapter(name: str | None = None):
    """Return capabilities for one adapter or for all registered adapters."""
    return _REGISTRY.describe(name)


def describe_source(source: str | None = None) -> dict[str, Any]:
    """Describe configured sources or the registered adapter capabilities."""
    if source is None:
        return {"sources": list_sources(), "adapters": _REGISTRY.describe()}
    definition = _read_definition(source, {})
    return definition


def load(
    source: str | Path | pd.DataFrame,
    *,
    filters: Mapping[str, Any] | None = None,
    source_name: str | None = None,
    id_column: str | None = None,
    ra_column: str | None = None,
    dec_column: str | None = None,
    category_column: str | None = None,
    category_parser: str | Callable[[Any], Any] | None = None,
    magnitude_column: str | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Load and filter a configured source into the canonical target table."""
    definition = _read_definition(source, {"source_name": source_name, "id_column": id_column, "ra_column": ra_column, "dec_column": dec_column, "category_column": category_column, "category_parser": category_parser, "magnitude_column": magnitude_column})
    if source_name is not None:
        definition["name"] = source_name
    adapter_name = str(definition.get("adapter", "csv"))
    if "data" in definition:
        from .sources.catalog_adapters import CsvCatalogAdapter
        return CsvCatalogAdapter()._normalize(definition["data"], definition, filters=filters, verbose=verbose)
    return _REGISTRY.create(adapter_name).load(definition, filters=filters, verbose=verbose)


def add_data(
    catalog: pd.DataFrame,
    data: pd.DataFrame | None = None,
    *,
    provider: str = "local",
    fields: list[str] | tuple[str, ...] | None = None,
    on: str = "target_id",
    loader: Callable[[pd.DataFrame, tuple[str, ...]], pd.DataFrame] | None = None,
    provider_options: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Add data from a table, an explicit loader, or a registered provider.

    ``provider="MOP"`` uses the existing MOP client enrichment API. Other
    providers must currently supply ``data`` or an explicit ``loader`` until
    their adapters are registered.
    """
    result = catalog.copy()
    requested = tuple(fields or ())
    if loader is not None:
        data = loader(result.copy(), requested)
    elif data is None and str(provider).strip().lower() == "mop":
        from .sources.mop import load_event_data
        data = load_event_data(result, fields=requested, **dict(provider_options or {}))
    if data is None:
        raise ValueError("add_data requires data or a provider loader callback")
    if on not in result or on not in data:
        raise ValueError(f"Both catalog and data must contain {on!r}")
    additions = data.copy()
    if requested:
        missing = sorted(set(requested) - set(additions.columns))
        if missing:
            raise ValueError(f"Provider {provider!r} did not return fields: {missing}")
        additions = additions[[on, *requested]]
    additions = additions.drop_duplicates(on)
    overlapping = [column for column in additions.columns if column != on and column in result.columns]
    result = result.drop(columns=overlapping, errors="ignore").merge(additions, on=on, how="left")
    result.attrs.setdefault("data_provenance", []).append({"provider": provider, "fields": list(requested or additions.columns)})
    return result
