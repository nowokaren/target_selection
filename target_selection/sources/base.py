"""Source interfaces shared by task and config workflows.

The preferred model is capability-oriented: a source has a kind, an access
mode, and one or more capabilities.  The older role-specific protocols remain
available for compatibility with the config-file workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

import pandas as pd

from target_selection.config import AnalysisConfig, SourceSpec

SourceKind = Literal["survey", "event_aggregator", "user_target_list", "local_inventory", "catalog"]
AccessMode = Literal["python_api", "http_api", "tap_butler", "local_csv", "local_files", "database"]
Capability = Literal[
    "targets",
    "event_parameters",
    "coverage",
    "epochs",
    "photometry",
    "objects",
    "images",
    "coadds",
    "cutouts",
    "astrometry",
]


@dataclass(frozen=True)
class DataSourceDefinition:
    """Normalized source description independent from run usage."""

    name: str
    kind: SourceKind
    access: AccessMode
    capabilities: tuple[Capability | str, ...] = ()
    label: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class DataSourceAdapter(Protocol):
    """Capability-oriented adapter base for new task modules."""

    definition: DataSourceDefinition

    def has_capability(self, capability: str) -> bool: ...



@dataclass
class SourceContext:
    """Services and storage shared by adapters during one analysis."""

    config: AnalysisConfig
    database: Any
    cache_dir: Path
    clients: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class TargetProviderAdapter(Protocol):
    """A service that supplies transient or microlensing targets."""

    spec: SourceSpec

    def client(self, context: SourceContext) -> Any: ...

    def collect_targets(self, context: SourceContext) -> pd.DataFrame: ...


@runtime_checkable
class FollowupSurveyAdapter(Protocol):
    """A telescope survey with historical or planned follow-up observations."""

    spec: SourceSpec

    def import_observations(self, context: SourceContext) -> int: ...

    def observed_targets(self, context: SourceContext) -> pd.DataFrame: ...


@runtime_checkable
class ReferenceSurveyAdapter(Protocol):
    """A survey or collection used to enrich and assess candidate targets."""

    spec: SourceSpec

    def data_release(self) -> Any: ...
