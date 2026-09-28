"""Source adapter interfaces and the default adapter registry."""

from .base import (
    AccessMode,
    Capability,
    DataSourceAdapter,
    DataSourceDefinition,
    FollowupSurveyAdapter,
    ReferenceSurveyAdapter,
    SourceContext,
    SourceKind,
    TargetProviderAdapter,
)
from .registry import AdapterRegistry, default_adapter_registry
from .catalog_adapters import CatalogAdapterRegistry, CsvCatalogAdapter, default_catalog_registry

__all__ = [
    "SourceKind",
    "DataSourceDefinition",
    "DataSourceAdapter",
    "Capability",
    "AccessMode",
    "AdapterRegistry",
    "FollowupSurveyAdapter",
    "ReferenceSurveyAdapter",
    "SourceContext",
    "TargetProviderAdapter",
    "default_adapter_registry",
    "CatalogAdapterRegistry",
    "CsvCatalogAdapter",
    "default_catalog_registry",
]
