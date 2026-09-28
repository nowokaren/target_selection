"""Small public registry for configured target and data providers.

The registry is intentionally lazy: importing :mod:`target_selection` does
not open MOP, TAP or Butler connections. Providers are handles with declared
features and methods that return normalized tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from .contracts import normalize_targets, validate_targets


@dataclass
class ProviderHandle:
    name: str
    kind: str
    features: tuple[str, ...] = ()
    _loader: Callable[..., pd.DataFrame] | None = field(default=None, repr=False)

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Compatibility alias; public documentation prefers ``features``."""
        return self.features

    def supports(self, feature: str) -> bool:
        return str(feature).strip().lower() in {item.lower() for item in self.features}

    def __call__(self, **kwargs):
        """Return this handle for APIs that accept optional provider settings."""
        if kwargs:
            raise TypeError(f"Provider {self.name!r} does not accept settings: {sorted(kwargs)}")
        return self

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "features": list(self.features)}

    def load_targets(self, *, filters: dict[str, Any] | None = None, **kwargs) -> pd.DataFrame:
        if self._loader is None:
            raise NotImplementedError(f"Provider {self.name!r} does not implement target loading.")
        result = normalize_targets(self._loader(filters=filters, **kwargs), provider=self.name)
        return validate_targets(result)


class MOPHandle(ProviderHandle):
    def __init__(self) -> None:
        super().__init__("mop", "tom", ("targets", "parameters", "visibility"))

    def load_targets(self, *, observatory: str = "El Leoncito", start_date: str | None = None, end_date: str | None = None, filters: dict[str, Any] | None = None, **kwargs) -> pd.DataFrame:
        if start_date is None:
            raise ValueError("MOP target loading requires start_date for its visibility query.")
        from .tasks.targets import collect_mop_targets
        result = collect_mop_targets(observatory=observatory, start_date=start_date, end_date=end_date, **kwargs)
        result = normalize_targets(result, provider=self.name)
        if filters:
            from .sources.catalog_adapters import _apply_filters
            result = _apply_filters(result, filters, category_column="category", magnitude_column="magnitude_last", source_name=self.name)
        return validate_targets(result)

    def parameters(self, targets: pd.DataFrame, *, fields: tuple[str, ...] | None = None, **kwargs) -> pd.DataFrame:
        from .sources.mop import load_event_data
        return load_event_data(targets, fields=fields, **kwargs)


class RubinHandle(ProviderHandle):
    def __init__(self, collection: str = "DP2") -> None:
        super().__init__("lsst", "survey", ("coverage", "images", "property_maps", "photometry"))
        self.collection_name = collection
        self._collection = None

    @property
    def collection(self):
        if self._collection is None:
            from .collection_access import open_collection
            self._collection = open_collection(self.collection_name)
        return self._collection

    def __call__(self, *, collection: str | None = None, **kwargs):
        if kwargs:
            raise TypeError(f"Unknown LSST provider settings: {sorted(kwargs)}")
        return self if collection is None or str(collection) == self.collection_name else RubinHandle(str(collection))

    def coverage(self, targets: pd.DataFrame, *, image_kinds=("individual", "coadd"), properties=(), bands=tuple("ugrizy"), **kwargs):
        from .collection_access import load_collection_coverage, load_collection_coadds
        result = load_collection_coverage(targets, collection=self.collection.profile, tap_service=self.collection.tap_service, bands=bands, **kwargs)
        if "coadd" in image_kinds:
            result = load_collection_coadds(result, collection=self.collection.profile, butler=self.collection.butler, bands=bands, **kwargs)
        if properties:
            positions = result[["target_id", "ra_deg", "dec_deg"]]
            props = self.collection.get_property_map_values(positions, properties=properties, bands=bands)
            result = result.merge(props, on="target_id", how="left")
        return result

    def get_image(self, targets, *, kind: str = "coadd", **kwargs):
        return self.collection.get_image(targets, image_type=kind, **kwargs)


class DataSourceRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderHandle] = {}

    def register(self, provider: ProviderHandle) -> ProviderHandle:
        self._providers[provider.name] = provider
        return provider

    def get(self, name: str) -> ProviderHandle:
        try:
            return self._providers[str(name).lower()]
        except KeyError as exc:
            raise KeyError(f"Unknown provider {name!r}; available: {sorted(self._providers)}") from exc

    def list(self, *, kind: str | None = None) -> list[str]:
        values = self._providers.values()
        if kind is not None:
            values = [item for item in values if item.kind == kind]
        return sorted(item.name for item in values)

    def describe(self) -> pd.DataFrame:
        return pd.DataFrame([item.describe() for item in self._providers.values()]).sort_values("name").reset_index(drop=True)

    def __getattr__(self, name: str) -> ProviderHandle:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.get(name)


def default_data_sources() -> DataSourceRegistry:
    registry = DataSourceRegistry()
    registry.register(ProviderHandle("hsh", "survey", ("targets", "epochs"), lambda **kwargs: __import__("target_selection.target_io", fromlist=["load"]).load("hsh_events", filters=kwargs.get("filters"))))
    registry.register(MOPHandle())
    registry.register(RubinHandle())
    return registry


data_source = default_data_sources()
