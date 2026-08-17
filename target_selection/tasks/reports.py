"""Report-generation tasks."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from data_release_config import get_data_release
from mop_photometry import load_event_photometry
from target_selection.sources.lsst import create_butler, create_tap_service


def _row_for_target(targets: pd.DataFrame, target_name: str):
    matches = targets.loc[targets["Target"].astype(str).eq(str(target_name))]
    if matches.empty:
        raise KeyError(f"Target {target_name!r} is not present in the target table")
    return matches.iloc[0]


def create_target_report(
    target,
    *,
    coverage: pd.DataFrame | None = None,
    data_release="DP2",
    release_photometry: pd.DataFrame | None = None,
    mop=None,
    mop_photometry_dir: str | Path = "outputs/mop_photometry",
    output_dir: str | Path = "outputs/target_reports",
    butler=None,
    tap_service=None,
    overwrite: bool = True,
    verbose: bool = True,
) -> Path:
    """Task: create or update one target dashboard PNG."""
    from target_report import plot_target
    from target_selection.run_paths import safe_name

    release = get_data_release(data_release)
    butler = butler or create_butler(release)
    tap_service = tap_service or create_tap_service(release)
    if isinstance(target, pd.DataFrame):
        if len(target) != 1:
            raise ValueError("target DataFrame must contain exactly one row")
        target = target.iloc[0]
    target_name = str(target["Target"])
    if verbose:
        print(f"[reports] Building target report: {target_name}", flush=True)
    out = Path(output_dir) / f"{safe_name(target_name)}_target_report.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not overwrite:
        if verbose:
            print(f"[reports] Reused existing report: {out}", flush=True)
        return out
    target_coverage = coverage if coverage is not None else pd.DataFrame()
    if not target_coverage.empty and "Target" in target_coverage:
        target_coverage = target_coverage.loc[target_coverage["Target"].astype(str).eq(target_name)].copy()
    target_release_photometry = release_photometry if release_photometry is not None else pd.DataFrame()
    if not target_release_photometry.empty and "Target" in target_release_photometry:
        target_release_photometry = target_release_photometry.loc[
            target_release_photometry["Target"].astype(str).eq(target_name)
        ].copy()
    mop_photometry = load_event_photometry(target, mop=mop, cache_dir=mop_photometry_dir)
    figure = plot_target(
        target,
        butler=butler,
        tap_service=tap_service,
        data_release=release,
        calexps=target_coverage,
        photometry=mop_photometry,
        release_photometry=target_release_photometry,
    )
    if figure is None:
        raise RuntimeError(f"No coadd covers target {target_name!r}")
    figure.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(figure)
    if verbose:
        print(f"[reports] Report saved: {out}", flush=True)
    return out


def create_target_reports(
    targets: pd.DataFrame,
    *,
    coverage: pd.DataFrame | None = None,
    release_photometry: pd.DataFrame | None = None,
    output_dir: str | Path = "outputs/target_reports",
    target_names: list[str] | tuple[str, ...] | None = None,
    **kwargs,
) -> list[Path]:
    """Task: create target dashboard PNGs for many targets."""
    if target_names is not None:
        wanted = set(str(name) for name in target_names)
        targets = targets.loc[targets["Target"].astype(str).isin(wanted)].copy()
    paths: list[Path] = []
    print(f"[reports] Creating {len(targets)} target report(s).", flush=True)
    try:
        from tqdm.auto import tqdm
        rows = tqdm(list(targets.iterrows()), desc="Target reports", unit="target")
    except ImportError:
        rows = targets.iterrows()
    for _, row in rows:
        paths.append(
            create_target_report(
                row,
                coverage=coverage,
                release_photometry=release_photometry,
                output_dir=output_dir,
                **kwargs,
            )
        )
    print(f"[reports] Reports generated/reused: {len(paths)}", flush=True)
    return paths


def create_lightcurves_report(
    targets: pd.DataFrame,
    *,
    output_path: str | Path,
    mop_photometry_dir: str | Path = "outputs/mop_photometry",
    release_photometry: pd.DataFrame | None = None,
    coverage: pd.DataFrame | None = None,
    data_release: str = "DP2",
    layers=None,
    plots_per_page: int = 3,
    verbose: bool = True,
) -> Path:
    """Task: create a multi-target light-curves PDF."""
    if verbose:
        print(f"[reports] Creating light-curves report for {len(targets)} targets.", flush=True)
    from monitoring_report import create_monitoring_report

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    create_monitoring_report(
        targets,
        output,
        mop=None,
        mop_photometry_dir=mop_photometry_dir,
        release_photometry=release_photometry if release_photometry is not None else pd.DataFrame(),
        lsst_coverage=coverage if coverage is not None else pd.DataFrame(),
        data_release=data_release,
        layers=layers,
        plots_per_page=plots_per_page,
    )
    if verbose:
        print(f"[reports] Light-curves PDF saved: {output}", flush=True)
    return output
