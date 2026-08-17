"""Normalized configuration for target providers and observing surveys."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


def _tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class SourceSpec:
    """Common source configuration resolved through an adapter registry."""

    name: str
    adapter: str
    enabled: bool = True
    label: str | None = None
    capabilities: tuple[str, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, values: Mapping[str, Any]) -> "SourceSpec":
        known = {"adapter", "enabled", "label", "capabilities", "options"}
        options = dict(values.get("options", {}))
        options.update(
            {key: value for key, value in values.items() if key not in known}
        )
        return cls(
            name=str(name),
            adapter=str(values.get("adapter", name)),
            enabled=bool(values.get("enabled", True)),
            label=values.get("label"),
            capabilities=_tuple(values.get("capabilities")),
            options=options,
        )


@dataclass(frozen=True)
class SelectionSettings:
    """Frequently changed scientific and feasibility cuts."""

    target_data_scope: str = "with_data"
    target_names: tuple[str, ...] | None = None
    maximum_current_magnitude: float | None = None
    minimum_altitude_deg: float = 40.0
    minimum_observable_minutes: float = 90.0
    time_step_minutes: int = 1
    observing_windows: Any = None
    visibility_target_scope: str = "all_queried"


@dataclass(frozen=True)
class ProductSettings:
    """Products enabled for an analysis run."""

    observing_selection_summary: bool = True
    visibility_plots: bool = True
    sky_maps: bool = True
    monitoring_report: bool = True
    target_reports: bool = False
    target_report_scope: str = "visibility_selected"
    reference_photometry: bool = False
    reference_photometry_targets: tuple[str, ...] | None = None
    coverage_background: bool = False
    marker_encoding: str = "split_color"
    monitoring_layers: tuple[str, ...] = (
        "target_provider_photometry",
        "reference_epochs",
        "followup_surveys",
    )


@dataclass(frozen=True)
class CacheSettings:
    """Persistent cache and refresh policy."""

    reuse: bool = True
    database_path: str = "target_database/target_selection.sqlite"
    refresh_target_providers: bool = False
    refresh_followup_surveys: bool = False
    refresh_reference_surveys: bool = False


@dataclass(frozen=True)
class RuntimeSettings:
    """Less frequently changed execution details."""

    max_workers: int = 4
    verbose: bool = True
    overwrite_visibility_plots: bool = False
    overwrite_target_reports: bool = False
    overwrite_reference_photometry: bool = False
    monitoring_plots_per_page: int = 3
    coverage_resolution: int = 19


@dataclass(frozen=True)
class AnalysisConfig:
    """Complete declarative configuration for one planning analysis."""

    start_date: str
    end_date: str | None = None
    name: str = ""
    observatory: str = "El Leoncito"
    output_dir: str = "outputs"
    target_providers: tuple[str, ...] = ("mop",)
    followup_surveys: tuple[str, ...] = ()
    reference_surveys: tuple[str, ...] = ("rubin_dp2",)
    provider_specs: Mapping[str, SourceSpec] = field(
        default_factory=lambda: {
            "mop": SourceSpec("mop", "mop", label="MOP"),
        }
    )
    followup_specs: Mapping[str, SourceSpec] = field(
        default_factory=lambda: {
            "casleo_hsh": SourceSpec(
                "casleo_hsh",
                "hsh",
                label="CASLEO/HSH",
                options={
                    "provider_name": "HSH",
                    "inventory_path": "hsh_data/image_collection_astro.csv",
                },
            ),
        }
    )
    reference_specs: Mapping[str, SourceSpec] = field(
        default_factory=lambda: {
            "rubin_dp2": SourceSpec(
                "rubin_dp2",
                "rubin",
                label="Rubin DP2",
                options={"data_release": "DP2"},
            ),
        }
    )
    selection: SelectionSettings = field(default_factory=SelectionSettings)
    products: ProductSettings = field(default_factory=ProductSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)

    @property
    def resolved_end_date(self) -> str:
        return self.end_date or self.start_date

    def validate(self) -> "AnalysisConfig":
        groups = (
            ("target provider", self.target_providers, self.provider_specs),
            ("follow-up survey", self.followup_surveys, self.followup_specs),
            ("reference survey", self.reference_surveys, self.reference_specs),
        )
        errors: list[str] = []
        for label, active, specs in groups:
            duplicates = sorted({name for name in active if active.count(name) > 1})
            if duplicates:
                errors.append(f"Duplicate {label} names: {duplicates}")
            missing = [name for name in active if name not in specs]
            if missing:
                errors.append(f"Undefined {label} names: {missing}")
            disabled = [
                name for name in active if name in specs and not specs[name].enabled
            ]
            if disabled:
                errors.append(f"Disabled {label} names were selected: {disabled}")
        if (
            self.selection.minimum_altitude_deg < 0
            or self.selection.minimum_altitude_deg > 90
        ):
            errors.append("minimum_altitude_deg must be between 0 and 90")
        if self.selection.minimum_observable_minutes < 0:
            errors.append("minimum_observable_minutes must be non-negative")
        if self.selection.time_step_minutes < 1:
            errors.append("time_step_minutes must be positive")
        if self.selection.target_data_scope not in {"with_data", "all"}:
            errors.append('target_data_scope must be "with_data" or "all"')
        if self.selection.visibility_target_scope not in {"all_queried", "mop_daily"}:
            errors.append("visibility_target_scope must be \"all_queried\" or \"mop_daily\"")
        if self.runtime.max_workers < 1:
            errors.append("max_workers must be positive")
        if errors:
            raise ValueError(
                "Invalid analysis configuration:\n- " + "\n- ".join(errors)
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_snapshot(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        return path

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "AnalysisConfig":
        run = values.get("run", values)
        active = values.get("sources", {})
        providers = {
            name: SourceSpec.from_mapping(name, spec)
            for name, spec in values.get("target_providers", {}).items()
        }
        followups = {
            name: SourceSpec.from_mapping(name, spec)
            for name, spec in values.get("followup_surveys", {}).items()
        }
        references = {
            name: SourceSpec.from_mapping(name, spec)
            for name, spec in values.get("reference_surveys", {}).items()
        }
        selection = dict(values.get("selection", {}))
        if "target_names" in selection:
            names = _tuple(selection["target_names"])
            selection["target_names"] = names or None
        products = dict(values.get("products", {}))
        if "monitoring_layers" in products:
            products["monitoring_layers"] = _tuple(products["monitoring_layers"])
        if "reference_photometry_targets" in products:
            products["reference_photometry_targets"] = _tuple(
                products["reference_photometry_targets"]
            )
        config = cls(
            start_date=str(run["start_date"]),
            end_date=str(run["end_date"]) if run.get("end_date") else None,
            name=str(run.get("name", "")).strip(),
            observatory=str(run.get("observatory", "El Leoncito")),
            output_dir=str(run.get("output_dir", "outputs")),
            target_providers=_tuple(active.get("target_providers", tuple(providers))),
            followup_surveys=_tuple(active.get("followup_surveys", tuple(followups))),
            reference_surveys=_tuple(
                active.get("reference_surveys", tuple(references))
            ),
            provider_specs=providers,
            followup_specs=followups,
            reference_specs=references,
            selection=SelectionSettings(**selection),
            products=ProductSettings(**products),
            cache=CacheSettings(**values.get("cache", {})),
            runtime=RuntimeSettings(**values.get("runtime", {})),
        )
        return config.validate()


def load_config(path: str | Path) -> AnalysisConfig:
    """Load a TOML or JSON analysis configuration."""
    path = Path(path)
    suffix = path.suffix.lower()
    with path.open("rb") as stream:
        if suffix == ".toml":
            values = tomllib.load(stream)
        elif suffix == ".json":
            values = json.loads(stream.read().decode("utf-8"))
        else:
            raise ValueError("Configuration files must use .toml or .json")
    return AnalysisConfig.from_mapping(values)
