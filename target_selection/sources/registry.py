"""Adapter registration without provider-specific pipeline conditionals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from target_selection.config import SourceSpec


class AdapterRegistry:
    """Factories grouped by semantic source role."""

    ROLES = ("target_provider", "followup_survey", "reference_survey")

    def __init__(self) -> None:
        self._factories: dict[str, dict[str, Callable[[SourceSpec], Any]]] = {
            role: {} for role in self.ROLES
        }

    def register(
        self, role: str, name: str, factory: Callable[[SourceSpec], Any]
    ) -> None:
        if role not in self._factories:
            raise ValueError(
                f"Unknown source role {role!r}; expected one of {self.ROLES}"
            )
        self._factories[role][str(name).lower()] = factory

    def create(self, role: str, spec: SourceSpec) -> Any:
        try:
            factory = self._factories[role][spec.adapter.lower()]
        except KeyError as exc:
            available = ", ".join(sorted(self._factories.get(role, {}))) or "none"
            raise ValueError(
                f"No {role} adapter named {spec.adapter!r}. Available adapters: {available}"
            ) from exc
        return factory(spec)

    def available(
        self, role: str | None = None
    ) -> dict[str, tuple[str, ...]] | tuple[str, ...]:
        if role is not None:
            if role not in self._factories:
                raise ValueError(f"Unknown source role {role!r}")
            return tuple(sorted(self._factories[role]))
        return {
            name: tuple(sorted(factories))
            for name, factories in self._factories.items()
        }


def default_adapter_registry() -> AdapterRegistry:
    """Return the built-in adapter set; callers may register more adapters."""
    from target_selection.sources.adapters import (
        CsvFollowupSurvey,
        CsvTargetProvider,
        HshFollowupSurvey,
        MopTargetProvider,
        RubinReferenceSurvey,
    )

    registry = AdapterRegistry()
    registry.register("target_provider", "mop", MopTargetProvider)
    registry.register("target_provider", "csv", CsvTargetProvider)
    registry.register("followup_survey", "hsh", HshFollowupSurvey)
    registry.register("followup_survey", "csv", CsvFollowupSurvey)
    registry.register("reference_survey", "rubin", RubinReferenceSurvey)
    return registry
