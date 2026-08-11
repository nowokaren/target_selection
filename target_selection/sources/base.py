"""Interfaces shared by target providers and observing surveys."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import pandas as pd

from target_selection.config import AnalysisConfig, SourceSpec


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
