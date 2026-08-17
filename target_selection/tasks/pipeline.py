"""Small task-oriented orchestration helpers.

This module is intentionally thinner than the legacy ``run_target_selection``
backend.  It shows the preferred workflow: call explicit task functions with
Python variables, persist the few tables/products requested, and keep each step
replaceable in notebooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

from target_selection.tasks.lsst import compute_lsst_photometry, query_lsst_coverage
from target_selection.tasks.reports import create_lightcurves_report, create_target_reports
from target_selection.tasks.targets import collect_mop_targets, load_target_list, merge_targets, restrict_targets
from target_selection.tasks.surveys import import_hsh_inventory, observed_targets
from target_selection.tasks.visibility import evaluate_visibility, make_visibility_sequence, save_visibility_plots
from target_selection_pipeline import create_run_structure


@dataclass
class TaskRunResult:
    """Outputs from a task-oriented target-selection run."""

    targets: pd.DataFrame = field(default_factory=pd.DataFrame)
    visibility: pd.DataFrame = field(default_factory=pd.DataFrame)
    coverage: pd.DataFrame = field(default_factory=pd.DataFrame)
    lsst_photometry: pd.DataFrame = field(default_factory=pd.DataFrame)
    paths: dict[str, Path] = field(default_factory=dict)
    products: dict[str, object] = field(default_factory=dict)


def run_target_selection_tasks(
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    output_dir: str | Path = "outputs",
    run_name: str = "",
    data_release="DP2",
    observing_windows=None,
    target_names: Iterable[str] | None = None,
    include_mop: bool = True,
    mop=None,
    target_csv: str | Path | None = None,
    target_csv_column_map: dict[str, str] | None = None,
    hsh_inventory: str | Path | None = None,
    include_observed_targets: bool = False,
    observed_target_providers: tuple[str, ...] = ("HSH",),
    refresh_observation_inventory: bool = False,
    database_path: str | Path = "outputs/target_database/target_selection.sqlite",
    compute_visibility: bool = True,
    query_coverage: bool = True,
    compute_photometry: bool = False,
    photometry_method: str = "dia_forced_catalog",
    make_visibility_plots: bool = True,
    make_visibility_pdf: bool = False,
    make_lightcurves_pdf: bool = False,
    make_target_reports: bool = False,
    max_workers: int = 4,
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    overwrite_products: bool = False,
    verbose: bool = True,
) -> TaskRunResult:
    """Run a notebook-friendly task workflow using explicit Python variables.

    This is the preferred high-level function for interactive use.  Each block
    below can also be called independently through the public task functions.
    """
    end_date = end_date or start_date
    paths = create_run_structure(
        output_dir,
        start_date,
        end_date,
        data_release=data_release,
        observing_windows=observing_windows,
        run_name=run_name,
    )
    result = TaskRunResult(paths=paths)
    target_tables: list[pd.DataFrame] = []

    if include_mop:
        if verbose:
            print("[targets] MOP visible targets", flush=True)
        target_tables.append(
            collect_mop_targets(
                start_date=start_date,
                end_date=end_date,
                observatory=observatory,
                mop=mop,
                cache_dir=output_dir,
                max_workers=max_workers,
            )
        )
    if target_csv is not None:
        if verbose:
            print(f"[targets] user CSV: {target_csv}", flush=True)
        target_tables.append(load_target_list(target_csv, column_map=target_csv_column_map))
    if hsh_inventory is not None:
        if verbose:
            print(f"[surveys] import HSH inventory: {hsh_inventory}", flush=True)
        import_hsh_inventory(
            hsh_inventory,
            database_path=database_path,
            force=refresh_observation_inventory,
        )
    if include_observed_targets:
        if verbose:
            print(f"[targets] observed targets: {', '.join(observed_target_providers)}", flush=True)
        target_tables.append(
            observed_targets(
                providers=observed_target_providers,
                database_path=database_path,
            )
        )

    targets = merge_targets(*target_tables)
    targets = restrict_targets(targets, target_names)
    result.targets = targets
    paths["tables"].mkdir(parents=True, exist_ok=True)
    targets.to_csv(paths["tables"] / "targets.csv", index=False)

    if compute_visibility and not targets.empty:
        if verbose:
            print("[visibility] evaluating", flush=True)
        visibility = evaluate_visibility(
            targets,
            start_date=start_date,
            end_date=end_date,
            observatory=observatory,
            minimum_altitude_deg=minimum_altitude_deg,
            minimum_observable_minutes=minimum_observable_minutes,
            time_step_minutes=time_step_minutes,
            observing_windows=observing_windows,
            output_path=paths["visibility_plots"] / "visibility_selection.csv",
        )
        result.visibility = visibility
        if make_visibility_plots:
            result.products["visibility_plots"] = save_visibility_plots(
                targets,
                start_date=start_date,
                end_date=end_date,
                output_dir=paths["visibility_plots"],
                observatory=observatory,
                minimum_altitude_deg=minimum_altitude_deg,
                minimum_observable_minutes=minimum_observable_minutes,
                time_step_minutes=time_step_minutes,
                observing_windows=observing_windows,
                selection=visibility,
                target_scope="all_queried",
                overwrite=overwrite_products,
                verbose=verbose,
            )
        if make_visibility_pdf:
            selected = visibility.loc[visibility["selected_for_visibility"].astype(bool)].copy()
            result.products["visibility_sequence"] = make_visibility_sequence(
                selected,
                paths["visibility_plots"] / "visibility_sequence.pdf",
                observatory=observatory,
                minimum_altitude_deg=minimum_altitude_deg,
                minimum_observable_minutes=minimum_observable_minutes,
                time_step_minutes=time_step_minutes,
                observing_windows=observing_windows,
            )

    if query_coverage and not targets.empty:
        if verbose:
            print(f"[coverage] {data_release}", flush=True)
        coverage = query_lsst_coverage(
            targets,
            data_release=data_release,
            max_workers=max_workers,
            output_path=paths["tables"] / "lsst_coverage.csv",
        )
        result.coverage = coverage

    if compute_photometry and not targets.empty:
        if verbose:
            print(f"[photometry] {data_release} {photometry_method}", flush=True)
        photometry = compute_lsst_photometry(
            targets,
            result.coverage,
            data_release=data_release,
            method=photometry_method,
            max_workers=max_workers,
            output_path=paths["tables"] / "lsst_photometry.csv",
            persistent_target_dir=Path(output_dir) / "rubin_photometry",
            verbose=verbose,
        )
        result.lsst_photometry = photometry

    if make_lightcurves_pdf and not targets.empty:
        result.products["lightcurves_pdf"] = create_lightcurves_report(
            targets,
            output_path=paths["monitoring_reports"] / "lightcurves.pdf",
            mop_photometry_dir=Path(output_dir) / "mop_photometry",
            release_photometry=result.lsst_photometry,
            coverage=result.coverage,
            data_release=str(data_release),
        )

    if make_target_reports and not targets.empty:
        result.products["target_reports"] = create_target_reports(
            targets,
            coverage=result.coverage,
            release_photometry=result.lsst_photometry,
            output_dir=paths["target_reports"],
            data_release=data_release,
            overwrite=overwrite_products,
        )

    return result
