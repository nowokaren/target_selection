"""Small public registry for configured target and data providers.

The registry is intentionally lazy: importing :mod:`target_selection` does
not open MOP, TAP or Butler connections. Providers are handles with declared
features and methods that return normalized tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .contracts import normalize_targets, validate_targets


@dataclass
class ProviderHandle:
    name: str
    kind: str
    features: tuple[str, ...] = ()
    _loader: Callable[..., pd.DataFrame] | None = field(default=None, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)

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
        description = {"name": self.name, "kind": self.kind, "features": list(self.features)}
        description.update(self.metadata)
        return description

    def photometry(self, targets: pd.DataFrame, **kwargs) -> pd.DataFrame:
        raise NotImplementedError(f"Provider {self.name!r} does not implement photometry.")

    def epochs(self, targets: pd.DataFrame, **kwargs) -> pd.DataFrame:
        raise NotImplementedError(f"Provider {self.name!r} does not implement epochs.")

    def load_targets(self, *, filters: dict[str, Any] | None = None, **kwargs) -> pd.DataFrame:
        if self._loader is None:
            raise NotImplementedError(f"Provider {self.name!r} does not implement target loading.")
        result = normalize_targets(self._loader(filters=filters, **kwargs), provider=self.name)
        return validate_targets(result)


class MOPHandle(ProviderHandle):
    def __init__(self) -> None:
        super().__init__("mop", "tom", ("targets", "parameters", "photometry", "visibility"))

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

    def photometry(self, targets: pd.DataFrame, *, client=None, cache_dir="outputs/mop_photometry", refresh: bool = False, errors: str = "ignore", verbose: bool = True, **_) -> pd.DataFrame:
        """Return normalized MOP photometry for one or many targets."""
        from target_selection.mop_photometry import load_event_photometry
        from .sources.mop import create_client

        if "target_id" not in targets and "Target" not in targets:
            raise ValueError("MOP photometry requires target_id or Target.")
        if client is None:
            client = create_client()
        rows = []
        for _, source in targets.iterrows():
            target_id = str(source.get("target_id", source.get("Target", "")))
            legacy = source.copy()
            legacy["Target"] = target_id
            if "RA" not in legacy:
                legacy["RA"] = source.get("ra_deg", source.get("RA_deg", float("nan")))
            if "Dec" not in legacy:
                legacy["Dec"] = source.get("dec_deg", source.get("Dec_deg", float("nan")))
            try:
                points = load_event_photometry(legacy, mop=client, cache_dir=cache_dir, refresh=refresh, verbose=verbose)
                if points is not None and not points.empty:
                    points = points.copy()
                    points.insert(0, "target_id", target_id)
                    points["provider"] = "mop"
                    rows.append(points)
            except Exception:
                if errors == "raise":
                    raise
                if verbose:
                    print(f"MOP photometry unavailable for {target_id}.", flush=True)
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()

    def visible_targets(self, *, observatory: str = "El Leoncito", start_date: str, end_date: str | None = None, **kwargs) -> pd.DataFrame:
        return self.load_targets(observatory=observatory, start_date=start_date, end_date=end_date, **kwargs)


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

    def coverage(self, targets: pd.DataFrame, *, image_kinds=("individual", "coadd"), properties=(), bands=tuple("ugrizy"), output_path=None, **kwargs):
        from .collection_access import load_collection_coverage, load_collection_coadds
        result = load_collection_coverage(targets, collection=self.collection.profile, tap_service=self.collection.tap_service, bands=bands, **kwargs)
        if "coadd" in image_kinds:
            result = load_collection_coadds(result, collection=self.collection.profile, butler=self.collection.butler, bands=bands, **kwargs)
        if properties:
            positions = result[["target_id", "ra_deg", "dec_deg"]]
            props = self.collection.get_property_map_values(positions, properties=properties, bands=bands)
            result = result.merge(props, on="target_id", how="left")
        if output_path is not None:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            result.to_csv(output, index=False)
        return result

    def get_image(self, targets, *, kind: str = "coadd", **kwargs):
        return self.collection.get_image(targets, image_type=kind, **kwargs)

    def photometry(self, targets, *, coverage=None, method: str = "dia_forced_catalog", output_path=None, **kwargs):
        from .sources.lsst import compute_release_photometry
        result = compute_release_photometry(
            targets,
            coverage=coverage,
            method=method,
            data_release=self.collection.profile,
            tap_service=self.collection.tap_service,
            butler=self.collection.butler,
            **kwargs,
        )
        if output_path is not None:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            result.to_csv(output, index=False)
        return result


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
    registry.register(ProviderHandle("hsh", "survey", ("targets",), lambda **kwargs: __import__("target_selection.target_io", fromlist=["load"]).load("hsh_events", filters=kwargs.get("filters"))))
    registry.register(MOPHandle())
    registry.register(RubinHandle())
    from .sources.ogle import OGLEAdapter
    from .sources.opm import OPMAdapter
    from .sources.bhtom import BHTOMAdapter
    ogle = OGLEAdapter()
    opm = OPMAdapter()
    bhtom = BHTOMAdapter()
    registry.register(ProviderHandle("ogle", "survey", ogle.features, ogle.load_targets, metadata=ogle.describe()))
    registry.register(ProviderHandle("opm", "tom", opm.features, opm.load_targets, metadata=opm.describe()))
    registry.register(ProviderHandle("bhtom", "tom", bhtom.features, bhtom.load_targets, metadata=bhtom.describe()))
    registry.get("ogle").photometry = ogle.photometry
    registry.get("ogle").epochs = ogle.epochs
    registry.get("opm").ranking = opm.ranking
    registry.get("opm").photometry = opm.photometry
    registry.get("opm").login = opm.login
    registry.get("opm").private_targets = opm.private_targets
    registry.get("bhtom").login = bhtom.login
    registry.get("bhtom").photometry = bhtom.photometry
    return registry


data_source = default_data_sources()
