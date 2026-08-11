"""Configuration-driven analysis orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from observatory_observations import canonical_target_name
from target_registry import TargetRegistry
from target_selection.config import AnalysisConfig
from target_selection.products import write_product_index
from target_selection.sources import (
    AdapterRegistry,
    SourceContext,
    default_adapter_registry,
)


@dataclass
class ReferenceRunResult:
    """Products returned for one reference survey."""

    reference_survey: str
    targets: pd.DataFrame
    paths: Mapping[str, Path]


@dataclass
class AnalysisResult:
    """All reference-survey runs produced by one analysis configuration."""

    config: AnalysisConfig
    runs: dict[str, ReferenceRunResult] = field(default_factory=dict)
    source_updates: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def targets(self) -> pd.DataFrame:
        if not self.runs:
            return pd.DataFrame()
        if len(self.runs) == 1:
            return next(iter(self.runs.values())).targets
        frames = []
        for name, result in self.runs.items():
            frames.append(result.targets.assign(reference_survey=name))
        return pd.concat(frames, ignore_index=True, sort=False)


class _EmptyTargetClient:
    """MOP-compatible empty provider used for follow-up-only analyses."""

    @staticmethod
    def _empty() -> pd.DataFrame:
        return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg", "observation_date"])

    def visible_targets(self, **kwargs) -> pd.DataFrame:
        return self._empty()

    def visibility_summary(self, **kwargs) -> pd.DataFrame:
        return self._empty()

    def enrich_microlensing_parameters(self, targets, **kwargs) -> pd.DataFrame:
        return targets.copy()


def _merge_targets(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame.copy() for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg"])
    merged = pd.concat(usable, ignore_index=True, sort=False)
    merged["target_key"] = merged["Target"].map(canonical_target_name)
    source_columns = [
        name for name in ("target_source", "observatory_providers") if name in merged
    ]
    sources = (
        merged.assign(
            _source=merged[source_columns].fillna("").astype(str).agg(",".join, axis=1)
        )
        .groupby("target_key")["_source"]
        .agg(
            lambda values: ",".join(
                sorted({item for value in values for item in value.split(",") if item})
            )
        )
    )
    merged = merged.drop_duplicates("target_key", keep="first")
    merged["input_sources"] = merged["target_key"].map(sources)
    return merged.drop(columns="target_key").reset_index(drop=True)


def _monitoring_layers(
    config: AnalysisConfig, followup_names: tuple[str, ...]
) -> tuple[str, ...]:
    translated: list[str] = []
    for layer in config.products.monitoring_layers:
        if layer == "target_provider_photometry":
            translated.append("mop_photometry")
        elif layer == "reference_epochs":
            translated.append("release_epochs")
        elif layer == "followup_surveys":
            translated.extend(name.lower() for name in followup_names)
        else:
            translated.append(layer)
    return tuple(dict.fromkeys(translated))


def _import_reference_photometry(
    database: TargetRegistry,
    source_name: str,
    path: Path,
) -> int:
    """Import a run's calibrated reference photometry into the shared registry."""
    if not path.exists():
        return 0
    data = pd.read_csv(path)
    if data.empty or "Target" not in data:
        return 0
    normalized = pd.DataFrame({"Target": data["Target"]})
    epoch_columns = [
        column for column in ("coadd_epoch_mjd", "expMidptMJD", "mjd") if column in data
    ]
    if not epoch_columns:
        return 0
    normalized["mjd"] = (
        data[epoch_columns]
        .apply(pd.to_numeric, errors="coerce")
        .bfill(axis=1)
        .iloc[:, 0]
    )
    normalized["band"] = data["band"] if "band" in data else None
    normalized["magnitude"] = pd.to_numeric(data.get("magnitude"), errors="coerce")
    normalized["magnitude_error"] = pd.to_numeric(
        data.get("magnitude_err"), errors="coerce"
    )
    flux_columns = [
        column
        for column in ("inst_flux", "direct_flux_njy", "difference_flux_njy")
        if column in data
    ]
    error_columns = [
        column
        for column in (
            "inst_flux_err",
            "direct_flux_err_njy",
            "difference_flux_err_njy",
        )
        if column in data
    ]
    normalized["flux"] = (
        data[flux_columns]
        .apply(pd.to_numeric, errors="coerce")
        .bfill(axis=1)
        .iloc[:, 0]
        if flux_columns
        else None
    )
    normalized["flux_error"] = (
        data[error_columns]
        .apply(pd.to_numeric, errors="coerce")
        .bfill(axis=1)
        .iloc[:, 0]
        if error_columns
        else None
    )
    has_measurement = normalized[["magnitude", "flux"]].notna().any(axis=1)
    normalized = normalized.loc[has_measurement & normalized["mjd"].notna()].copy()
    return database.import_photometry(
        source_name,
        normalized,
        source_role="reference_survey",
    )


class AnalysisWorkflow:
    """Resolve adapters and execute the validated scientific pipeline."""

    def __init__(
        self,
        config: AnalysisConfig,
        *,
        adapters: AdapterRegistry | None = None,
        clients: Mapping[str, Any] | None = None,
        runner: Callable[..., tuple[pd.DataFrame, Mapping[str, Path]]] | None = None,
    ) -> None:
        self.config = config.validate()
        self.adapters = adapters or default_adapter_registry()
        self.clients = dict(clients or {})
        if runner is None:
            from target_selection_pipeline import run_target_selection

            runner = run_target_selection
        self.runner = runner

    def run(self) -> AnalysisResult:
        root = Path(self.config.output_dir)
        database_path = Path(self.config.cache.database_path)
        if not database_path.is_absolute():
            database_path = root / database_path
        database = TargetRegistry(database_path)
        context = SourceContext(self.config, database, root, self.clients)
        updates: list[dict[str, Any]] = []

        followup_adapters = []
        for name in self.config.followup_surveys:
            adapter = self.adapters.create(
                "followup_survey", self.config.followup_specs[name]
            )
            count = adapter.import_observations(context)
            followup_adapters.append(adapter)
            updates.append(
                {"source": name, "role": "followup_survey", "records_updated": count}
            )

        provider_adapters = [
            self.adapters.create("target_provider", self.config.provider_specs[name])
            for name in self.config.target_providers
        ]
        primary_mop = next(
            (
                adapter
                for adapter in provider_adapters
                if adapter.spec.adapter.lower() == "mop"
            ),
            None,
        )
        mop_client = (
            primary_mop.client(context)
            if primary_mop is not None
            else _EmptyTargetClient()
        )
        additional_frames = []
        for adapter in provider_adapters:
            if adapter is primary_mop:
                continue
            frame = adapter.collect_targets(context)
            additional_frames.append(frame)
            database.register_targets(frame, provider=adapter.spec.name)
            database.upsert_source_records(
                frame, source_name=adapter.spec.name, source_role="target_provider"
            )
            updates.append(
                {
                    "source": adapter.spec.name,
                    "role": "target_provider",
                    "records_updated": int(len(frame)),
                }
            )

        observed_frames = [
            adapter.observed_targets(context) for adapter in followup_adapters
        ]
        for adapter, frame in zip(followup_adapters, observed_frames, strict=True):
            database.upsert_source_records(
                frame,
                source_name=adapter.spec.name,
                source_role="followup_survey",
            )
        additional_targets = _merge_targets([*additional_frames, *observed_frames])
        followup_provider_names = tuple(
            str(getattr(adapter, "provider_name", adapter.spec.name)).upper()
            for adapter in followup_adapters
        )
        hsh_path = next(
            (
                str(adapter.inventory_path)
                for adapter in followup_adapters
                if adapter.spec.adapter.lower() == "hsh"
            ),
            None,
        )

        result = AnalysisResult(
            config=self.config,
            source_updates=pd.DataFrame(updates),
        )
        references = [
            self.adapters.create("reference_survey", self.config.reference_specs[name])
            for name in self.config.reference_surveys
        ]
        if not references:
            return self._run_without_reference(
                result,
                context,
                provider_adapters,
                additional_targets,
                followup_provider_names,
            )

        for reference in references:
            release = reference.data_release()
            combined, paths = self.runner(
                self.config.start_date,
                self.config.resolved_end_date,
                data_release=release,
                root_dir=root,
                observatory=self.config.observatory,
                mop=mop_client,
                target_plotter=None if self.config.products.target_reports else False,
                target_report_scope=self.config.products.target_report_scope,
                visibility_target_scope=self.config.selection.visibility_target_scope,
                max_workers=self.config.runtime.max_workers,
                reuse_cache=self.config.cache.reuse,
                overwrite_target_plots=self.config.runtime.overwrite_target_reports,
                generate_sky_maps=self.config.products.sky_maps,
                sky_marker_encoding=self.config.products.marker_encoding,
                show_coverage_background=self.config.products.coverage_background,
                coverage_resolution=self.config.runtime.coverage_resolution,
                generate_visibility_plots=self.config.products.visibility_plots,
                overwrite_visibility_plots=self.config.runtime.overwrite_visibility_plots,
                visibility_minimum_altitude=self.config.selection.minimum_altitude_deg,
                visibility_minimum_observable_minutes=self.config.selection.minimum_observable_minutes,
                visibility_time_step_minutes=self.config.selection.time_step_minutes,
                visibility_observing_windows=self.config.selection.observing_windows,
                generate_release_photometry=self.config.products.reference_photometry,
                release_photometry_targets=self.config.products.reference_photometry_targets,
                overwrite_release_photometry=self.config.runtime.overwrite_reference_photometry,
                hsh_image_catalog=hsh_path,
                refresh_hsh_data=False,
                max_current_magnitude=self.config.selection.maximum_current_magnitude,
                include_previously_observed=bool(followup_adapters),
                previously_observed_providers=followup_provider_names,
                additional_targets=additional_targets
                if not additional_targets.empty
                else None,
                show_queried_targets=self.config.runtime.verbose,
                target_registry_path=database_path,
                generate_monitoring_report=self.config.products.monitoring_report,
                monitoring_report_plots_per_page=self.config.runtime.monitoring_plots_per_page,
                monitoring_layers=_monitoring_layers(
                    self.config, followup_provider_names
                ),
                verbose=self.config.runtime.verbose,
            )
            reference_photometry_count = _import_reference_photometry(
                database,
                reference.spec.name,
                Path(paths["tables"]) / "release_forced_photometry.csv",
            )
            reference_columns = [
                column
                for column in combined.columns
                if column in {"Target", "RA_deg", "Dec_deg"}
                or column.startswith(("coverage_", "release_"))
            ]
            database.upsert_source_records(
                combined[reference_columns],
                source_name=reference.spec.name,
                source_role="reference_survey",
            )
            if primary_mop is not None:
                mop_rows = combined
                if "is_mop_visible_in_run" in combined:
                    mop_rows = combined.loc[
                        combined["is_mop_visible_in_run"].fillna(False)
                    ]
                database.upsert_source_records(
                    mop_rows,
                    source_name=primary_mop.spec.name,
                    source_role="target_provider",
                )
            updates.append(
                {
                    "source": reference.spec.name,
                    "role": "reference_survey",
                    "records_updated": int(len(combined)),
                    "photometry_updated": int(reference_photometry_count),
                }
            )
            result.source_updates = pd.DataFrame(updates)
            run_path = Path(paths["run"])
            self.config.write_snapshot(run_path / "analysis_config.json")
            result.source_updates.to_csv(
                run_path / "tables" / "source_updates.csv", index=False
            )
            database.source_catalog().to_csv(
                run_path / "tables" / "source_catalog.csv",
                index=False,
            )
            write_product_index(paths, reference_survey=reference.spec.name)
            run_id = f"{reference.spec.name}:{run_path.name}"
            database.record_run(
                run_id, self.config.to_dict(), run_path, status="complete"
            )
            result.runs[reference.spec.name] = ReferenceRunResult(
                reference.spec.name,
                combined,
                paths,
            )
        return result

    def _run_without_reference(
        self,
        result: AnalysisResult,
        context: SourceContext,
        provider_adapters: list[Any],
        additional_targets: pd.DataFrame,
        followup_provider_names: tuple[str, ...],
    ) -> AnalysisResult:
        from target_selection_pipeline import _observing_window_slug
        from visibility_plotter import (
            build_visibility_selection,
            save_nightly_visibility_plots,
            summarize_visibility_selection,
        )

        provider_frames = []
        updates = result.source_updates.to_dict(orient="records")
        for adapter in provider_adapters:
            frame = adapter.collect_targets(context)
            provider_frames.append(frame)
            context.database.upsert_source_records(
                frame,
                source_name=adapter.spec.name,
                source_role="target_provider",
            )
            updates.append(
                {
                    "source": adapter.spec.name,
                    "role": "target_provider",
                    "records_updated": int(len(frame)),
                }
            )
        result.source_updates = pd.DataFrame(updates)
        targets = _merge_targets([*provider_frames, additional_targets])
        if not targets.empty:
            context.database.register_targets(targets, provider="analysis")
        date_label = (
            self.config.start_date
            if self.config.start_date == self.config.resolved_end_date
            else f"{self.config.start_date}_to_{self.config.resolved_end_date}"
        )
        run_path = (
            Path(self.config.output_dir)
            / "planning_only"
            / (
                f"{date_label}__{_observing_window_slug(self.config.selection.observing_windows)}"
            )
        )
        paths = {
            "run": run_path,
            "tables": run_path / "tables",
            "visibility_plots": run_path / "visibility_plots",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        nights = pd.date_range(
            self.config.start_date,
            self.config.resolved_end_date,
            freq="D",
        ).strftime("%Y-%m-%d")
        daily = (
            pd.concat(
                [targets.assign(observation_date=night) for night in nights],
                ignore_index=True,
            )
            if not targets.empty
            else pd.DataFrame()
        )
        selection = build_visibility_selection(
            daily,
            self.config.start_date,
            self.config.resolved_end_date,
            observatory=self.config.observatory,
            minimum_altitude=self.config.selection.minimum_altitude_deg,
            minimum_observable_minutes=self.config.selection.minimum_observable_minutes,
            time_step_minutes=self.config.selection.time_step_minutes,
            observing_windows=self.config.selection.observing_windows,
        )
        summary = summarize_visibility_selection(selection)
        combined = (
            targets.merge(summary, on="Target", how="left")
            if not targets.empty
            else targets
        )
        combined.to_csv(paths["tables"] / "targets.csv", index=False)
        selection.to_csv(paths["tables"] / "visibility.csv", index=False)
        result.source_updates.to_csv(
            paths["tables"] / "source_updates.csv", index=False
        )
        context.database.source_catalog().to_csv(
            paths["tables"] / "source_catalog.csv",
            index=False,
        )
        if self.config.products.visibility_plots and not daily.empty:
            save_nightly_visibility_plots(
                daily,
                self.config.start_date,
                self.config.resolved_end_date,
                paths["visibility_plots"],
                observatory=self.config.observatory,
                minimum_altitude=self.config.selection.minimum_altitude_deg,
                minimum_observable_minutes=self.config.selection.minimum_observable_minutes,
                time_step_minutes=self.config.selection.time_step_minutes,
                observing_windows=self.config.selection.observing_windows,
                selection=selection,
                overwrite=self.config.runtime.overwrite_visibility_plots,
                verbose=self.config.runtime.verbose,
            )
        self.config.write_snapshot(run_path / "analysis_config.json")
        write_product_index(paths, reference_survey="planning_only")
        run_id = f"planning_only:{run_path.name}"
        context.database.record_run(
            run_id, self.config.to_dict(), run_path, status="complete"
        )
        result.runs["planning_only"] = ReferenceRunResult(
            "planning_only", combined, paths
        )
        return result


def run_analysis(
    config: AnalysisConfig | str | Path,
    *,
    adapters: AdapterRegistry | None = None,
    clients: Mapping[str, Any] | None = None,
) -> AnalysisResult:
    """Run an analysis from an object or TOML/JSON configuration file."""
    if not isinstance(config, AnalysisConfig):
        from target_selection.config import load_config

        config = load_config(config)
    return AnalysisWorkflow(config, adapters=adapters, clients=clients).run()
