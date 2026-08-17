"""Local visibility tasks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from visibility_plotter import (
    build_visibility_selection,
    plot_visibility_sequence,
    save_nightly_visibility_plots,
)


def evaluate_visibility(
    targets: pd.DataFrame,
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    observing_windows=None,
    output_path: str | Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Task: evaluate local visibility for all target-night rows."""
    if verbose:
        print(f"[visibility] Evaluating {len(targets)} targets from {start_date} to {end_date or start_date}.", flush=True)
    end_date = end_date or start_date
    target_rows = targets.copy()
    if "observation_date" not in target_rows:
        nights = pd.date_range(start_date, end_date, freq="D").date
        target_rows = pd.concat(
            [target_rows.assign(observation_date=night.isoformat()) for night in nights],
            ignore_index=True,
            sort=False,
        ) if len(target_rows) else target_rows.assign(observation_date=pd.Series(dtype=str))
    selection = build_visibility_selection(
        target_rows,
        start_date,
        end_date,
        observatory=observatory,
        minimum_altitude=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        time_step_minutes=time_step_minutes,
        observing_windows=observing_windows,
    )
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        selection.to_csv(path, index=False)
    if verbose:
        selected = int(selection.get("selected_for_visibility", pd.Series(dtype=bool)).astype(bool).sum())
        print(f"[visibility] Rows: {len(selection)}; selected: {selected}.", flush=True)
    return selection


def save_visibility_plots(
    targets: pd.DataFrame,
    *,
    start_date: str,
    end_date: str | None = None,
    output_dir: str | Path,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    observing_windows=None,
    selection: pd.DataFrame | None = None,
    target_scope: str = "all_queried",
    max_targets_per_plot: int = 20,
    overwrite: bool = False,
    verbose: bool = True,
) -> list[Path]:
    """Task: save nightly visibility plots from targets or a precomputed selection."""
    end_date = end_date or start_date
    target_rows = targets.copy()
    if "observation_date" not in target_rows:
        nights = pd.date_range(start_date, end_date, freq="D").date
        target_rows = pd.concat(
            [target_rows.assign(observation_date=night.isoformat()) for night in nights],
            ignore_index=True,
            sort=False,
        ) if len(target_rows) else target_rows.assign(observation_date=pd.Series(dtype=str))
    return save_nightly_visibility_plots(
        target_rows,
        start_date,
        end_date,
        output_dir,
        observatory=observatory,
        minimum_altitude=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        time_step_minutes=time_step_minutes,
        observing_windows=observing_windows,
        target_scope=target_scope,
        selection=selection,
        max_targets_per_plot=max_targets_per_plot,
        overwrite=overwrite,
        verbose=verbose,
    )


def make_visibility_sequence(
    visibility_selection: pd.DataFrame,
    output_path: str | Path,
    *,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float | None = None,
    time_step_minutes: int = 1,
    observing_windows=None,
    output_format: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Path:
    """Task: stack selected nightly visibility panels into one PDF/PNG."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return plot_visibility_sequence(
        visibility_selection,
        output,
        observatory=observatory,
        minimum_altitude=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        time_step_minutes=time_step_minutes,
        observing_windows=observing_windows,
        output_format=output_format,
        start_date=start_date,
        end_date=end_date,
    )
