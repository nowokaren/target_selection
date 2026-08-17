"""Configurable MOP + Rubin pipeline for notebooks and scripts.

Rubin modules are not imported at module load time. MOP, TAP, and Butler
connections are created lazily and can also be injected.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter
from pathlib import Path
from typing import Callable, Iterable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm.std import tqdm

from data_release_config import DataReleaseConfig, get_data_release
from target_region import classify_target_region


CACHE_VERSION = 4
TARGET_REPORT_VERSION = 13


def _safe_name(value: object) -> str:
    """Compatibility wrapper for :func:`target_selection.run_paths.safe_name`."""
    from target_selection.run_paths import safe_name

    return safe_name(value)


def _load_additional_targets(targets: pd.DataFrame | str | Path | None) -> pd.DataFrame:
    """Load a user-supplied target list with normalized coordinates.

    A dataframe or CSV path may be supplied. ``Target``, ``RA_deg``, and
    ``Dec_deg`` are required; additional columns are retained as metadata.
    """
    if targets is None:
        return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg"])
    if isinstance(targets, (str, Path)):
        data = pd.read_csv(targets)
    elif isinstance(targets, pd.DataFrame):
        data = targets.copy()
    else:
        raise TypeError("additional_targets must be a pandas DataFrame, CSV path, or None.")
    required = {"Target", "RA_deg", "Dec_deg"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"additional_targets is missing columns: {sorted(missing)}")
    data["Target"] = data["Target"].astype(str).str.strip()
    for column in ("RA_deg", "Dec_deg"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.loc[
        data["Target"].ne("") & data["RA_deg"].notna() & data["Dec_deg"].notna()
    ].drop_duplicates("Target", keep="first").reset_index(drop=True)


def _target_name_mask(targets: pd.DataFrame, target_names: Iterable[str] | None) -> pd.Series:
    """Return rows matching explicit user-selected target names."""
    if target_names is None:
        return pd.Series(True, index=targets.index)
    names = tuple(str(name).strip() for name in target_names if str(name).strip())
    if not names:
        return pd.Series(True, index=targets.index)
    if targets.empty or "Target" not in targets:
        return pd.Series(False, index=targets.index)
    try:
        from observatory_observations import canonical_target_name
        wanted = {canonical_target_name(name) for name in names}
        return targets["Target"].map(canonical_target_name).isin(wanted)
    except Exception:
        wanted = {name.casefold() for name in names}
        return targets["Target"].astype(str).str.strip().str.casefold().isin(wanted)


def _mop_magnitude_pass_mask(
    targets: pd.DataFrame, max_current_magnitude: float | None = None,
) -> pd.Series:
    """Return the MOP magnitude-cut mask; missing magnitudes remain valid."""
    if targets.empty:
        return pd.Series(dtype=bool, index=targets.index)
    columns = [name for name in ("mag_now", "mop_mag_now", "mag_now_x", "mag_now_y") if name in targets]
    if not columns:
        return pd.Series(True, index=targets.index)
    magnitude = (
        targets[columns].apply(pd.to_numeric, errors="coerce")
        .bfill(axis=1).iloc[:, 0]
    )
    invalid = magnitude.notna() & magnitude.le(0)
    passed = ~invalid
    if max_current_magnitude is not None:
        passed &= magnitude.isna() | magnitude.le(float(max_current_magnitude))
    return passed.fillna(True).astype(bool)


def _filter_invalid_mop_magnitudes(
    targets: pd.DataFrame, max_current_magnitude: float | None = None,
) -> tuple[pd.DataFrame, int]:
    """Discard invalid or over-limit MOP magnitudes; retain missing magnitudes."""
    passed = _mop_magnitude_pass_mask(targets, max_current_magnitude)
    return targets.loc[passed].copy().reset_index(drop=True), int((~passed).sum())


def _has_mop_page_coordinates(targets: pd.DataFrame) -> bool:
    """Return whether a cached MOP table contains authoritative page positions."""
    required = {"mop_ra", "mop_dec", "mop_ra_deg", "mop_dec_deg"}
    return required.issubset(targets.columns)


def _apply_authoritative_mop_coordinates(targets: pd.DataFrame) -> pd.DataFrame:
    """Compatibility wrapper for the MOP source helper."""
    from target_selection.sources.mop import apply_authoritative_coordinates

    return apply_authoritative_coordinates(targets)


def _visibility_daily_targets(
    targets: pd.DataFrame, start_date: str, end_date: str, *, scope: str,
    mop_daily: pd.DataFrame,
) -> pd.DataFrame:
    """Return target/night rows used by local visibility calculations."""
    if scope == "mop_daily":
        # The daily visibility response determines membership by night, while
        # the event-page coordinates determine every geometric calculation.
        # Match by canonical name and replace the daily-table position without
        # rounding whenever an enriched MOP page position is available.
        from observatory_observations import canonical_target_name

        daily = mop_daily.copy()
        if daily.empty:
            return daily
        coordinate_columns = ["Target", "RA_deg", "Dec_deg"]
        coordinate_columns.extend(
            column for column in (
                "mag_now", "is_mop_visible_in_run", "is_previously_observed",
                "observatory_providers", "input_sources", "target_source",
            ) if column in targets
        )
        authoritative = targets[coordinate_columns].copy()
        authoritative["_target_key"] = authoritative["Target"].map(canonical_target_name)
        authoritative = authoritative.drop_duplicates("_target_key", keep="first")
        daily["_target_key"] = daily["Target"].map(canonical_target_name)
        daily = daily.merge(
            authoritative.drop(columns="Target"), on="_target_key", how="left",
            suffixes=("", "_authoritative"),
        )
        for column in coordinate_columns:
            if column == "Target":
                continue
            replacement = f"{column}_authoritative"
            if replacement in daily:
                existing = daily[column] if column in daily else pd.Series(
                    pd.NA, index=daily.index
                )
                daily[column] = daily[replacement].combine_first(existing)
                daily = daily.drop(columns=replacement)
        # Every daily MOP row is, by construction, a MOP-visible target even
        # if it has no enriched event-page record.
        daily["is_mop_visible_in_run"] = True
        return daily.drop(columns="_target_key")
    if scope != "all_queried":
        raise ValueError("visibility_target_scope must be 'all_queried' or 'mop_daily'.")
    columns = ["Target", "RA_deg", "Dec_deg"]
    columns.extend(
        column for column in (
            "mag_now", "is_mop_visible_in_run", "is_previously_observed",
            "observatory_providers", "input_sources", "target_source",
        ) if column in targets
    )
    base = targets[columns].dropna(subset=["Target", "RA_deg", "Dec_deg"]).drop_duplicates("Target")
    nights = pd.date_range(start_date, end_date, freq="D").strftime("%Y-%m-%d")
    if base.empty or len(nights) == 0:
        return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg", "observation_date"])
    return pd.concat(
        [base.assign(observation_date=night) for night in nights], ignore_index=True,
    )


def _observing_window_slug(observing_windows=None) -> str:
    """Compatibility wrapper for the package path helper."""
    from target_selection.run_paths import observing_window_slug

    return observing_window_slug(observing_windows)


def create_run_structure(
    root_dir: str | Path, start_date: str, end_date: str,
    data_release: str | DataReleaseConfig = "DP2",
    observing_windows=None,
    run_name: str = "",
) -> dict[str, Path]:
    """Compatibility wrapper for the package run-layout helper."""
    from target_selection.run_paths import create_run_structure as _create

    return _create(
        root_dir, start_date, end_date, data_release=data_release,
        observing_windows=observing_windows, run_name=run_name,
    )


def _coverage_cache_key(targets: pd.DataFrame, release: DataReleaseConfig) -> str:
    """Return a stable key for release coverage at a set of target coordinates."""
    fields = targets[["Target", "RA_deg", "Dec_deg"]].copy()
    fields["Target"] = fields["Target"].astype(str)
    for column in ("RA_deg", "Dec_deg"):
        fields[column] = pd.to_numeric(fields[column], errors="coerce")
    payload = {
        "release": release.name,
        "targets": fields.sort_values("Target").fillna("null").values.tolist(),
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def query_release_coverage(
    targets: pd.DataFrame,
    tap_service,
    search_radius: float = 11 / 60,
    max_workers: int = 4,
    data_release: str | DataReleaseConfig = "DP2",
) -> pd.DataFrame:
    """Compatibility wrapper for the dedicated Rubin source adapter."""
    from target_selection.sources.lsst import query_visit_coverage

    return query_visit_coverage(
        targets, tap_service=tap_service, data_release=data_release,
        search_radius=search_radius, max_workers=max_workers,
    )


def query_release_visit_centers(
    tap_service, data_release: str | DataReleaseConfig = "DP2"
) -> pd.DataFrame:
    """Compatibility wrapper for the dedicated Rubin source adapter."""
    from target_selection.sources.lsst import query_visit_centers

    return query_visit_centers(tap_service=tap_service, data_release=data_release)


def summarize_release_coverage(coverage_rows: pd.DataFrame) -> pd.DataFrame:
    """Reduce detector-level coverage rows to one row per MOP target."""
    columns = [
        "Target", "coverage_n_rows", "coverage_n_visits", "coverage_n_epochs",
        "coverage_n_calexps", "coverage_first_mjd", "coverage_last_mjd", "coverage_bands",
    ]
    if coverage_rows.empty:
        return pd.DataFrame(columns=columns)

    rows = coverage_rows.copy()
    rows["expMidptMJD"] = pd.to_numeric(rows["expMidptMJD"], errors="coerce")
    rows["_epoch_mjd"] = rows["expMidptMJD"].round(5)
    grouped = rows.groupby("Target", sort=False)
    summary = grouped.size().rename("coverage_n_rows").to_frame()
    summary["coverage_n_visits"] = grouped["visitId"].nunique()
    summary["coverage_n_epochs"] = grouped["_epoch_mjd"].nunique()
    summary["coverage_n_calexps"] = grouped[["visitId", "detector"]].apply(
        lambda data: data.drop_duplicates().shape[0]
    )
    summary["coverage_first_mjd"] = grouped["expMidptMJD"].min()
    summary["coverage_last_mjd"] = grouped["expMidptMJD"].max()
    summary["coverage_bands"] = grouped["band"].agg(
        lambda values: ",".join(sorted({str(value) for value in values.dropna()}))
    )
    band_visits = (
        rows.dropna(subset=["band"])
        .groupby(["Target", "band"], sort=False)["visitId"].nunique()
        .unstack(fill_value=0)
    )
    band_visits.columns = [f"coverage_n_visits_{_safe_name(band)}" for band in band_visits.columns]
    return summary.join(band_visits, how="left").reset_index()


def save_target_summary(
    targets: pd.DataFrame,
    photometry_dir: str | Path,
    csv_path: str | Path,
    png_path: str | Path,
    release_name: str,
) -> pd.DataFrame:
    """Save a compact per-target science/coverage table as CSV and PNG."""
    targets = targets.reset_index(drop=True)
    result = pd.DataFrame({"Target": targets["Target"].astype(str)})
    result["target_region"] = (
        targets["target_region"].fillna("").astype(str)
        if "target_region" in targets else targets["Target"].map(classify_target_region)
    )
    missing_region = result["target_region"].eq("")
    result.loc[missing_region, "target_region"] = targets.loc[
        missing_region, "Target"
    ].map(classify_target_region)
    result["priority"] = False
    for column in ("tap_priority", "tap_priority_longte", "mop_tap_priority", "mop_tap_priority_longte"):
        if column in targets:
            values = targets[column].astype(str).str.strip().str.casefold()
            result["priority"] |= ~values.isin({"", "0", "0.0", "false", "nan", "none"})

    direct_columns = {
        "mag_now": "mag_now", "Min airmass": "min_airmass",
        "n_visible_nights": "visible_nights", "coverage_n_visits": "release_n_visits",
        "coverage_n_calexps": "release_n_calexps", "coverage_bands": "release_bands",
        "release_forced_n_magnitudes": "release_forced_photometry_points",
        "mop_t_e_days": "t_E_days", "mop_t_0_hjd": "t_0_HJD",
        "mop_u_0": "u_0", "mop_parameters_status": "mop_status",
    }
    for source, destination in direct_columns.items():
        result[destination] = targets[source] if source in targets else np.nan

    band_columns = sorted(
        column for column in targets.columns if column.startswith("coverage_n_visits_")
    )
    for column in band_columns:
        band = column.removeprefix("coverage_n_visits_")
        result[f"n_visits_{band}"] = pd.to_numeric(targets[column], errors="coerce").fillna(0).astype(int)

    curve_columns = sorted(
        column for column in targets.columns if column.startswith("release_curve_points_")
    )
    for column in curve_columns:
        label = column.removeprefix("release_curve_points_")
        result[f"curve_points_{label}"] = pd.to_numeric(targets[column], errors="coerce").fillna(0).astype(int)

    photometry_dir = Path(photometry_dir)
    def count_photometry(target_name: str) -> int:
        path = photometry_dir / f"{_safe_name(target_name)}.csv"
        if not path.exists():
            return 0
        try:
            return len(pd.read_csv(path, usecols=["Timestamp"]))
        except (OSError, ValueError):
            try:
                return len(pd.read_csv(path))
            except (OSError, ValueError):
                return 0

    result["mop_photometry_points"] = result["Target"].map(count_photometry)
    result["matched_release"] = pd.to_numeric(result["release_n_visits"], errors="coerce").fillna(0).gt(0)
    result["matched_with_photometry"] = result["matched_release"] & result["mop_photometry_points"].gt(0)
    result.to_csv(csv_path, index=False)

    display_columns = [
        "Target", "target_region", "priority", "release_n_visits", *[f"n_visits_{c.removeprefix('coverage_n_visits_')}" for c in band_columns],
        "mop_photometry_points", "release_forced_photometry_points", *[f"curve_points_{c.removeprefix('release_curve_points_')}" for c in curve_columns],
        "t_E_days", "t_0_HJD", "u_0", "mag_now", "min_airmass", "visible_nights",
    ]
    display = result[display_columns].copy()
    integer_columns = {"release_n_visits", "mop_photometry_points", "release_forced_photometry_points", "visible_nights", *[c for c in display if c.startswith("n_visits_")], *[c for c in display if c.startswith("curve_points_")]}
    parameter_columns = {"t_E_days", "t_0_HJD", "u_0"}

    def format_cell(value, column):
        if pd.isna(value):
            return "—"
        if isinstance(value, (bool, np.bool_)):
            return "Yes" if value else "No"
        if column in integer_columns:
            number = pd.to_numeric(value, errors="coerce")
            return str(int(number)) if pd.notna(number) else str(value)
        if column in parameter_columns and isinstance(value, str):
            return re.sub(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", lambda match: f"{float(match.group()):.1f}", value)[:24]
        number = pd.to_numeric(value, errors="coerce")
        if pd.notna(number) and column != "Target":
            return f"{float(number):.1f}"
        return str(value)[:24]

    for column in display.columns:
        display[column] = display[column].map(lambda value, name=column: format_cell(value, name))
    header_labels = {
        "Target": "Target", "target_region": "Region", "priority": "Priority", "release_n_visits": "tot",
        "mop_photometry_points": "MOP\nphot.",
        "release_forced_photometry_points": f"{release_name}\nphot.",
        "t_E_days": "t_E\n[days]", "t_0_HJD": "t_0\n[HJD]", "u_0": "u_0",
        "mag_now": "mag\nnow", "min_airmass": "min\nairmass",
        "visible_nights": "visible\nnights",
    }
    header_labels.update({
        column: column.removeprefix("n_visits_")
        for column in display.columns if column.startswith("n_visits_")
    })
    header_labels.update({
        column: column.removeprefix("curve_points_")
        for column in display.columns if column.startswith("curve_points_")
    })
    column_headers = [header_labels.get(column, column) for column in display.columns]
    group_headers = ["" for _ in display.columns]
    visit_indices = [
        index for index, column in enumerate(display.columns)
        if column == "release_n_visits" or column.startswith("n_visits_")
    ]
    lightcurve_indices = [
        index for index, column in enumerate(display.columns)
        if column in {
            "mop_photometry_points", "release_forced_photometry_points",
            *[c for c in display.columns if c.startswith("curve_points_")],
            "t_E_days", "t_0_HJD", "u_0", "mag_now",
        }
    ]
    if visit_indices:
        group_headers[visit_indices[len(visit_indices) // 2]] = f"{release_name} visits"
    if lightcurve_indices:
        group_headers[lightcurve_indices[len(lightcurve_indices) // 2]] = "LC / uLens\nparams"

    n_visible = len(result)
    n_matched = int(result["matched_release"].sum())
    n_photometry = int(result["mop_photometry_points"].gt(0).sum())
    n_matched_photometry = int(result["matched_with_photometry"].sum())
    text_lengths = [
        min(
            22,
            max(
                4,
                max(len(line) for line in column_headers[index].splitlines()) + 1,
                *(len(str(value)) + 1 for value in display.iloc[:, index]),
            ),
        )
        for index in range(len(display.columns))
    ]
    width_units = np.asarray(text_lengths, dtype=float)
    column_widths = (width_units / width_units.sum()).tolist()
    fig_width = max(8.5, min(16.5, width_units.sum() * .058))
    fig_height = max(5, 1.4 + .285 * (len(display) + 2))
    fig = plt.figure(figsize=(fig_width, fig_height))
    ax = fig.add_axes([.015, .015, .97, .885])
    ax.axis("off")
    fig.suptitle(f"MOP + Rubin {release_name} target summary", fontsize=15, fontweight="bold", y=.995)
    fig.text(
        .5, .955,
        f"Visible: {n_visible}  |  With {release_name} coverage: {n_matched}  |  "
        f"With MOP photometry: {n_photometry}  |  With coverage + photometry: {n_matched_photometry}",
        ha="center", va="top", fontsize=11,
    )
    table = ax.table(
        cellText=[column_headers, *display.values.tolist()], colLabels=group_headers,
        cellLoc="center", colLoc="center", colWidths=column_widths,
        loc="center", bbox=[0, 0, 1, 1],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    for (row, column), cell in table.get_celld().items():
        cell.PAD = .018
        if row == 0:
            if column not in visit_indices and column not in lightcurve_indices:
                cell.set_visible(False)
                continue
            cell.set_facecolor("#c9dfef")
            cell.set_text_props(weight="bold", fontsize=6.4)
            cell.visible_edges = "BT"
        elif row == 1:
            cell.set_facecolor("#e8f2f8")
            cell.set_text_props(weight="bold", fontsize=6.5)
        elif row % 2 == 1:
            cell.set_facecolor("#f5f5f5")
    if visit_indices and lightcurve_indices:
        divider_x = float(sum(column_widths[:lightcurve_indices[0]]))
        ax.plot(
            [divider_x, divider_x], [0, 1], transform=ax.transAxes,
            color="#5f7890", linewidth=1.15, zorder=5,
        )
    fig.savefig(png_path, dpi=180, bbox_inches="tight", pad_inches=.08)
    plt.close(fig)
    return result


def plot_sky_dual_metric(*args, **kwargs):
    """Compatibility wrapper for the package sky product."""
    from target_selection.products import plot_sky_dual_metric as _plot

    return _plot(*args, **kwargs)


def _write_report_versions(path: Path, versions: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(versions, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def save_target_reports(
    targets: pd.DataFrame,
    target_plotter: Callable,
    output_dir: str | Path,
    coverage_rows: pd.DataFrame,
    overwrite: bool = False,
    verbose: bool = True,
    continue_on_error: bool = True,
    version_for_target: Callable[[pd.Series], str] | None = None,
    reuse_reports_from: str | Path | None = None,
) -> None:
    """Generate one flat PNG per target and reuse compatible report versions."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    versions_path = output_dir / "report_versions.json"
    statuses_path = output_dir / "report_status.json"
    try:
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        versions = {}
    try:
        statuses = json.loads(statuses_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        statuses = {}
    source_dir = Path(reuse_reports_from) if reuse_reports_from is not None else None
    source_versions = source_statuses = {}
    if source_dir is not None and source_dir != output_dir:
        try:
            source_versions = json.loads((source_dir / "report_versions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            source_versions = {}
        try:
            source_statuses = json.loads((source_dir / "report_status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            source_statuses = {}
    grouped = {name: data for name, data in coverage_rows.groupby("Target", sort=False)}
    empty_coverage = coverage_rows.iloc[0:0].copy()
    total = len(targets)
    plot_errors = []
    generated = skipped = migrated = no_coadd = cached_no_coadd = 0
    if verbose:
        print(
            "Legend — reused: current PNG; migrated: matching previous PNG; generated: new PNG; "
            "no coadd: newly confirmed missing coadd; cached no coadd: previously confirmed; "
            "errors: isolated failures.",
            flush=True,
        )
    progress = tqdm(
        targets.iterrows(), total=total, desc="Reports", unit="target",
        disable=not verbose, dynamic_ncols=True, mininterval=1.0,
    )

    def update_progress() -> None:
        progress.set_postfix(
            reused=skipped, migrated=migrated, generated=generated, no_coadd=no_coadd,
            cached_no_coadd=cached_no_coadd, errors=len(plot_errors), refresh=False,
        )

    for _, row in progress:
        target_name = str(row["Target"])
        file_key = _safe_name(target_name)
        report_path = output_dir / f"{file_key}_target_report.png"
        expected_version = (
            version_for_target(row) if version_for_target is not None
            else str(TARGET_REPORT_VERSION)
        )
        current_version = str(versions.get(file_key, ""))
        if report_path.exists() and current_version == expected_version and not overwrite:
            skipped += 1
            update_progress()
            continue
        cached_status = statuses.get(file_key, {})
        if (
            not overwrite
            and cached_status.get("version") == expected_version
            and cached_status.get("status") == "no_coadd"
        ):
            cached_no_coadd += 1
            update_progress()
            continue
        if not overwrite and source_dir is not None:
            source_report_path = source_dir / f"{file_key}_target_report.png"
            source_status = source_statuses.get(file_key, {})
            if source_report_path.exists() and str(source_versions.get(file_key, "")) == expected_version:
                try:
                    report_path.hardlink_to(source_report_path)
                except OSError:
                    shutil.copy2(source_report_path, report_path)
                versions[file_key] = expected_version
                _write_report_versions(versions_path, versions)
                migrated += 1
                update_progress()
                continue
            if (
                source_status.get("version") == expected_version
                and source_status.get("status") == "no_coadd"
            ):
                statuses[file_key] = source_status
                _write_report_versions(statuses_path, statuses)
                cached_no_coadd += 1
                update_progress()
                continue
        target_coverage = grouped.get(row["Target"], empty_coverage)
        try:
            figure = target_plotter(row, target_coverage)
        except Exception as exc:
            plt.close()
            if not continue_on_error:
                raise
            plot_errors.append({"Target": row["Target"], "error": str(exc)})
            if verbose:
                message = str(exc)
                short_message = message if len(message) <= 300 else message[:297] + "..."
                print(f"    error for {row['Target']}: {short_message}", flush=True)
            continue
        if figure is None:
            no_coadd += 1
            report_path.unlink(missing_ok=True)
            versions.pop(file_key, None)
            statuses[file_key] = {"version": expected_version, "status": "no_coadd"}
            _write_report_versions(versions_path, versions)
            _write_report_versions(statuses_path, statuses)
            update_progress()
            continue
        figure.savefig(report_path, dpi=150, bbox_inches="tight")
        plt.close(figure)
        versions[file_key] = expected_version
        statuses.pop(file_key, None)
        _write_report_versions(versions_path, versions)
        _write_report_versions(statuses_path, statuses)
        generated += 1
        update_progress()
    progress.close()
    if verbose:
        tqdm.write(
            f"Reports complete: generated={generated}, "
            f"reused={skipped}, no coadd={no_coadd}, errors={len(plot_errors)}"
        )
    pd.DataFrame(plot_errors, columns=["Target", "error"]).to_csv(
        output_dir.parent / "plot_errors.csv", index=False
    )


def _create_mop_client():
    """Create the legacy default MOP client lazily."""
    from mop_api import MOPClient

    return MOPClient()


def _create_tap_service(release: DataReleaseConfig):
    """Compatibility wrapper for :mod:`target_selection.sources.lsst`."""
    from target_selection.sources.lsst import create_tap_service

    return create_tap_service(release)


def _create_butler(release: DataReleaseConfig):
    """Compatibility wrapper for :mod:`target_selection.sources.lsst`."""
    from target_selection.sources.lsst import create_butler

    return create_butler(release)


def _create_default_target_plotter(
    *, mop, tap_service, butler, release: DataReleaseConfig, root_dir: str | Path,
    release_photometry: pd.DataFrame | None = None,
):
    """Create the standard report plotter with already cached light curves."""
    from mop_photometry import load_event_photometry
    from target_report import plot_target

    butler = butler or _create_butler(release)
    photometry_dir = Path(root_dir) / "mop_photometry"
    forced_groups = (
        {name: data for name, data in release_photometry.groupby("Target", sort=False)}
        if release_photometry is not None and not release_photometry.empty and "Target" in release_photometry
        else {}
    )
    empty_forced = (
        release_photometry.iloc[0:0].copy()
        if release_photometry is not None else pd.DataFrame()
    )

    def standard_target_plotter(target, coverage):
        photometry = load_event_photometry(
            target, mop=mop, cache_dir=photometry_dir,
            legacy_cache_dir=Path(root_dir) / "photometry",
        )
        return plot_target(
            target, butler=butler, tap_service=tap_service,
            data_release=release, calexps=coverage, photometry=photometry,
            release_photometry=forced_groups.get(target["Target"], empty_forced),
        )

    return standard_target_plotter


def regenerate_target_report_from_run(
    run_dir: str | Path,
    target_name: str,
    *,
    output_dir: str | Path | None = None,
    data_release: str | DataReleaseConfig = "DP2",
    mop=None,
    tap_service=None,
    butler=None,
    photometry_method: str | None = None,
    refresh_photometry: bool = False,
    overwrite: bool = True,
) -> Path:
    """Regenerate one report from the products and caches of an existing run.

    The report is written to ``outputs/target_reports`` by default, rather than
    inside the date-specific run directory.  The run supplies the canonical
    target coordinates, TAP coverage, and optional DP2 photometry; MOP
    photometry is loaded from the persistent ``mop_photometry`` cache.
    """
    run_path = Path(run_dir)
    tables = run_path / "tables"
    combined_path = tables / "combined_targets.csv"
    if not combined_path.exists():
        raise FileNotFoundError(f"Missing run target table: {combined_path}")
    combined = pd.read_csv(combined_path)
    if "Target" not in combined:
        raise ValueError(f"{combined_path} has no Target column")
    matches = combined.loc[combined["Target"].astype(str).eq(str(target_name))]
    if matches.empty:
        raise KeyError(f"Target {target_name!r} is not present in {combined_path}")
    target = matches.iloc[0]

    coverage_path = tables / "coverage_raw.csv"
    if not coverage_path.exists():
        coverage_path = tables / "coverage.csv"
    coverage = pd.read_csv(coverage_path) if coverage_path.exists() else pd.DataFrame()
    if not coverage.empty and "Target" in coverage:
        coverage = coverage.loc[coverage["Target"].astype(str).eq(str(target_name))].copy()

    forced_path = tables / "release_forced_photometry.csv"
    forced = pd.read_csv(forced_path) if forced_path.exists() else pd.DataFrame()
    if not forced.empty and "Target" in forced:
        forced = forced.loc[forced["Target"].astype(str).eq(str(target_name))].copy()

    release = get_data_release(data_release)
    if photometry_method is not None:
        method = str(photometry_method).strip().lower()
        if method not in {"coadd_forced", "dia_forced_catalog", "calexp_forced"}:
            raise ValueError(
                "photometry_method must be coadd_forced, dia_forced_catalog, or calexp_forced"
            )
        from dataclasses import replace as dataclass_replace
        release = dataclass_replace(release, photometry_method=method)
    root_dir = run_path.parent
    if release.photometry_method == "dia_forced_catalog":
        has_dia_coordinates = (
            not forced.empty
            and "measurement_method" in forced
            and forced["measurement_method"].astype(str).eq("dia_forced_catalog").any()
            and {"dia_object_ra_deg", "dia_object_dec_deg"}.issubset(forced.columns)
            and forced[["dia_object_ra_deg", "dia_object_dec_deg"]].notna().all(axis=1).any()
        )
        if refresh_photometry or not has_dia_coordinates:
            from release_photometry import query_dia_forced_photometry, save_target_release_photometry
            tap_service = tap_service or _create_tap_service(release)
            forced = query_dia_forced_photometry(
                target[["Target", "RA_deg", "Dec_deg"]].to_frame().T,
                tap_service=tap_service, data_release=release, max_workers=1, verbose=True,
            )
            if not forced.empty:
                save_target_release_photometry(forced, root_dir / "rubin_photometry")
    destination = Path(output_dir) if output_dir is not None else root_dir / "target_reports"
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / f"{_safe_name(target_name)}_target_report.png"
    if report_path.exists() and not overwrite:
        return report_path

    plotter = _create_default_target_plotter(
        mop=mop, tap_service=tap_service, butler=butler, release=release,
        root_dir=root_dir, release_photometry=forced,
    )
    figure = plotter(target, coverage)
    if figure is None:
        raise RuntimeError(f"No coadd covers target {target_name!r}")
    figure.savefig(report_path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return report_path


def run_target_selection(
    start_date: str,
    end_date: str | None = None,
    *,
    data_release: str | DataReleaseConfig = "DP2",
    root_dir: str | Path = "outputs",
    run_name: str = "",
    observatory: str = "El Leoncito",
    mop=None,
    tap_service=None,
    butler=None,
    target_plotter: Callable | bool | None = None,
    target_report_scope: str = "all_queried",
    visibility_target_scope: str = "all_queried",
    target_data_scope: str = "with_data",
    target_data_observation_providers: Iterable[str] | None = None,
    target_data_photometry_sources: Iterable[str] = (),
    max_workers: int = 4,
    reuse_cache: bool = True,
    overwrite_target_plots: bool = False,
    generate_sky_maps: bool = True,
    generate_observing_selection_summary: bool = True,
    sky_marker_encoding: str = "split_color",
    show_coverage_background: bool = False,
    coverage_resolution: int = 19,
    generate_visibility_plots: bool = True,
    overwrite_visibility_plots: bool = False,
    visibility_minimum_altitude: float = 40.0,
    visibility_minimum_observable_minutes: float = 90.0,
    visibility_time_step_minutes: int = 1,
    visibility_observing_windows=None,
    generate_release_photometry: bool = False,
    release_photometry_targets: Iterable[str] | None = None,
    overwrite_release_photometry: bool = False,
    hsh_image_catalog: str | Path | None = None,
    refresh_hsh_data: bool = False,
    max_current_magnitude: float | None = None,
    include_mop_visible_targets: bool = True,
    hsh_peak_half_width_t_e: float = 0.3,
    hsh_event_half_width_t_e: float = 2.0,
    include_previously_observed: bool = True,
    previously_observed_providers: Iterable[str] = ("HSH", "JS"),
    additional_targets: pd.DataFrame | str | Path | None = None,
    target_names: Iterable[str] | None = None,
    show_queried_targets: bool = True,
    target_registry_path: str | Path | None = None,
    generate_monitoring_report: bool = False,
    monitoring_report_plots_per_page: int = 3,
    monitoring_layers: Iterable[str] | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, dict[str, Path]]:
    """Select visible MOP targets and build Rubin coverage products.

    MOP, TAP, Butler and the standard target reporter are initialized lazily
    when omitted. Pass ``target_plotter=False`` to skip individual reports, or
    pass a callable accepting ``(target_row, coverage_rows)`` to customize them.
    """
    release = get_data_release(data_release)
    mop = mop or _create_mop_client()
    tap_service = tap_service or _create_tap_service(release)
    use_default_target_plotter = target_plotter is None
    if target_plotter is False:
        report_plotter = None
    elif use_default_target_plotter:
        report_plotter = None
    elif callable(target_plotter):
        report_plotter = target_plotter
    else:
        raise TypeError("target_plotter must be callable, False, or None.")
    if target_report_scope not in {"all_queried", "visibility_selected"}:
        raise ValueError("target_report_scope must be 'all_queried' or 'visibility_selected'.")
    from target_selection.data_availability import normalize_target_data_scope
    target_data_scope = normalize_target_data_scope(target_data_scope)
    if visibility_target_scope not in {"all_queried", "mop_daily"}:
        raise ValueError("visibility_target_scope must be 'all_queried' or 'mop_daily'.")
    previously_observed_providers = tuple(
        str(provider).upper() for provider in previously_observed_providers
    )
    if target_data_observation_providers is None:
        target_data_observation_providers = previously_observed_providers
    target_data_observation_providers = tuple(
        str(provider).upper() for provider in target_data_observation_providers
    )
    target_data_photometry_sources = tuple(
        str(source) for source in target_data_photometry_sources
    )
    if isinstance(target_names, str):
        target_names = (target_names,)
    elif target_names is not None:
        target_names = tuple(str(name) for name in target_names)
    user_targets = _load_additional_targets(additional_targets)
    if target_names is not None:
        user_targets = user_targets.loc[_target_name_mask(user_targets, target_names)].copy()
    end_date = end_date or start_date
    if isinstance(release_photometry_targets, str):
        release_photometry_targets = (release_photometry_targets,)
    elif release_photometry_targets is not None:
        release_photometry_targets = tuple(str(name) for name in release_photometry_targets)
    paths = create_run_structure(
        root_dir, start_date, end_date, release,
        observing_windows=visibility_observing_windows,
        run_name=run_name,
    )
    from target_registry import TargetRegistry
    registry_path = (
        Path(target_registry_path) if target_registry_path is not None
        else Path(root_dir) / "target_database" / "target_selection.sqlite"
    )
    registry = TargetRegistry(registry_path)
    if hsh_image_catalog is not None:
        imported_hsh = registry.import_hsh_catalog(hsh_image_catalog, force=refresh_hsh_data)
        if verbose and imported_hsh:
            print(f"      Imported {imported_hsh} HSH astrometry records", flush=True)
    observed_targets = (
        registry.observed_targets(providers=previously_observed_providers)
        if include_previously_observed else pd.DataFrame()
    )
    if target_names is not None:
        observed_targets = observed_targets.loc[
            _target_name_mask(observed_targets, target_names)
        ].copy()
    # Databases created before the coordinate-provenance migration may contain
    # an HSH image WCS reference point as a target position. Never reuse those
    # legacy coordinates: MOP enrichment must replace them, or the target is
    # omitted from coordinate-dependent work when MOP has no event page.
    if not observed_targets.empty and "coordinate_source" in observed_targets:
        legacy_coordinates = observed_targets["coordinate_source"].eq("legacy_unknown")
        observed_targets.loc[legacy_coordinates, ["RA_deg", "Dec_deg"]] = np.nan
    refresh_mop_data = not reuse_cache or registry.needs_daily_refresh("MOP", "target_data")
    daily_path = paths["tables"] / "visible_targets_daily.csv"
    initial_mop_path = paths["tables"] / "mop_targets_initial.csv"
    summary_path = paths["tables"] / "visible_summary.csv"
    analysis_targets_path = paths["tables"] / "analysis_targets.csv"
    mop_candidates_without_data_path = paths["tables"] / "mop_candidates_without_data.csv"
    targets_without_selected_data_path = paths["tables"] / "targets_without_selected_data.csv"
    queried_targets_path = paths["tables"] / "queried_targets.csv"
    coverage_path = paths["tables"] / "coverage_raw.csv"
    coverage_targets_path = paths["tables"] / "coverage_targets.csv"
    manifest_path = paths["run"] / "manifest.json"
    started = perf_counter()

    daily_cache_available = (
        include_mop_visible_targets
        and reuse_cache and daily_path.exists() and not refresh_mop_data
        and (max_current_magnitude is None or initial_mop_path.exists())
    )
    if verbose:
        label = "Visible MOP targets" if include_mop_visible_targets else "Visible MOP targets (disabled)"
        print("[1/5] " + label + (" (cache)" if daily_cache_available else ""), flush=True)
    if not include_mop_visible_targets:
        daily = pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg", "observation_date"])
    elif daily_cache_available:
        daily = pd.read_csv(daily_path)
    else:
        daily = mop.visible_targets(
            observatory=observatory, start_date=start_date, end_date=end_date,
            sort_by_mag=False,
        )
    if target_names is not None:
        daily = daily.loc[_target_name_mask(daily, target_names)].copy()
    initial_mop = daily.copy()
    initial_mop["passes_magnitude_cut"] = _mop_magnitude_pass_mask(initial_mop, max_current_magnitude)
    initial_mop.to_csv(initial_mop_path, index=False)
    daily, excluded_daily = _filter_invalid_mop_magnitudes(daily, max_current_magnitude)
    if excluded_daily and verbose:
        print(f"      Excluded {excluded_daily} MOP candidate(s) by the magnitude cut", flush=True)
    daily.to_csv(daily_path, index=False)

    # A non-default cut invalidates the old enriched summary cache unless the
    # caller explicitly reruns the MOP enrichment.
    summary_cache_available = (
        include_mop_visible_targets
        and reuse_cache and summary_path.exists() and not refresh_mop_data
        and max_current_magnitude is None
    )
    if summary_cache_available:
        try:
            summary_cache_available = _has_mop_page_coordinates(pd.read_csv(summary_path, nrows=1))
        except (OSError, ValueError):
            summary_cache_available = False
    if verbose:
        print("[2/5] MOP parameters + photometry" + (" (cache)" if summary_cache_available else ""), flush=True)
    if summary_cache_available:
        visible_summary = pd.read_csv(summary_path)
    elif include_mop_visible_targets:
        visible_summary = mop.visibility_summary(
            observatory=observatory, start_date=start_date, end_date=end_date,
            include_microlensing_parameters=True, parameter_errors="ignore",
            daily_targets=daily, parameter_max_workers=max_workers,
            parameter_cache_dir=Path(root_dir) / "mop_event_cache",
            photometry_dir=Path(root_dir) / "mop_photometry",
            refresh_parameters=not reuse_cache,
        )
        visible_summary.to_csv(summary_path, index=False)
    else:
        visible_summary = pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg"])
        visible_summary.to_csv(summary_path, index=False)
    if verbose and include_mop_visible_targets and not visible_summary.empty:
        status_column = "mop_photometry_status"
        error_column = "mop_photometry_error"
        if status_column in visible_summary:
            unavailable = visible_summary.loc[
                visible_summary[status_column].astype(str).str.casefold().isin({"unavailable", "empty"})
            ]
            for row in unavailable.itertuples(index=False):
                reason = getattr(row, error_column, "no usable measurements") if error_column in visible_summary else "no usable measurements"
                print(f"      Photometry skipped for {getattr(row, 'Target', '<unknown>')}: {reason}", flush=True)
    visible_summary, excluded_summary = _filter_invalid_mop_magnitudes(visible_summary, max_current_magnitude)
    if excluded_summary and verbose and not excluded_daily:
        print(f"      Excluded {excluded_summary} MOP summary target(s) with mag_now <= 0", flush=True)
    if excluded_summary:
        visible_summary.to_csv(summary_path, index=False)

    # The analysis union includes MOP-visible targets and all targets already
    # observed by local surveys. Observed-only targets are enriched from MOP
    # once per day and use the stored HSH/JS coordinates as a fallback.
    visible_summary = visible_summary.copy()
    visible_summary["is_mop_visible_in_run"] = True
    visible_summary = _apply_authoritative_mop_coordinates(visible_summary)
    registry.register_targets(visible_summary, provider="MOP")
    from observatory_observations import canonical_target_name
    visible_keys = set(visible_summary["Target"].map(canonical_target_name))
    observed_only = observed_targets.loc[
        ~observed_targets["Target"].map(canonical_target_name).isin(visible_keys)
    ].copy() if not observed_targets.empty else pd.DataFrame()
    excluded_observed = 0
    if not observed_only.empty:
        try:
            observed_only = mop.enrich_microlensing_parameters(
                observed_only, errors="ignore", max_workers=max_workers,
                cache_dir=Path(root_dir) / "mop_event_cache",
                photometry_dir=Path(root_dir) / "mop_photometry",
                refresh=refresh_mop_data,
            )
        except Exception as exc:
            if verbose:
                print(f"      MOP enrichment failed for observed-only targets: {exc}", flush=True)
            observed_only["mop_parameters_status"] = "unavailable"
            observed_only["mop_parameters_error"] = str(exc)
        observed_only["is_mop_visible_in_run"] = False
        observed_only = _apply_authoritative_mop_coordinates(observed_only)
        observed_only, excluded_observed = _filter_invalid_mop_magnitudes(observed_only, max_current_magnitude)
        if excluded_observed and verbose:
            print(f"      Excluded {excluded_observed} previously observed target(s) with MOP mag_now <= 0", flush=True)
    visible_summary["is_user_supplied"] = False
    observed_keys = set(observed_targets["Target"].map(canonical_target_name)) if not observed_targets.empty else set()
    user_keys = set(user_targets["Target"].map(canonical_target_name)) if not user_targets.empty else set()
    if not user_targets.empty:
        user_targets = user_targets.copy()
        user_targets["is_mop_visible_in_run"] = False
        user_targets["is_previously_observed"] = False
        user_targets["is_user_supplied"] = True
        user_targets["coordinate_source"] = "USER"
        user_targets["coordinate_priority"] = 80
        registry.register_targets(user_targets, provider="USER")
    summary = pd.concat([visible_summary, observed_only, user_targets], ignore_index=True, sort=False)
    summary = _apply_authoritative_mop_coordinates(summary)
    magnitude_columns = [
        column for column in ("mag_now", "mop_mag_now", "mag_now_x", "mag_now_y")
        if column in summary
    ]
    if magnitude_columns:
        # Keep one canonical magnitude column for visibility tables and plot
        # labels, including HSH/JS targets enriched from MOP.
        summary["mag_now"] = (
            summary[magnitude_columns].apply(pd.to_numeric, errors="coerce")
            .bfill(axis=1).iloc[:, 0]
        )
    summary["is_previously_observed"] = summary["Target"].map(canonical_target_name).isin(observed_keys)
    from mop_photometry import split_mop_candidates_without_event_data

    summary, mop_candidates_without_data = split_mop_candidates_without_event_data(
        summary, photometry_dir=Path(root_dir) / "mop_photometry",
    )
    from target_selection.data_availability import select_targets_by_data_availability

    summary, targets_without_selected_data = select_targets_by_data_availability(
        summary,
        target_data_scope=target_data_scope,
        mop_photometry_dir=Path(root_dir) / "mop_photometry",
        include_mop_photometry=True,
        database=registry,
        observation_providers=target_data_observation_providers,
        photometry_sources=target_data_photometry_sources,
    )
    targets_without_selected_data.to_csv(
        targets_without_selected_data_path, index=False
    )
    if len(targets_without_selected_data) and verbose:
        print(
            f"      Excluded {len(targets_without_selected_data)} target(s) without data from active sources",
            flush=True,
        )
    mop_catalog = summary.loc[
        summary["is_mop_visible_in_run"].fillna(False).astype(bool)
    ].copy()
    mop_catalog.to_csv(summary_path, index=False)
    mop_candidates_without_data.to_csv(mop_candidates_without_data_path, index=False)
    if len(mop_candidates_without_data) and verbose:
        print(
            f"      Excluded {len(mop_candidates_without_data)} MOP candidate(s) without parameters or photometry",
            flush=True,
        )

    excluded_keys = set(mop_candidates_without_data["Target"].map(canonical_target_name))
    excluded_keys.update(
        targets_without_selected_data["Target"].map(canonical_target_name)
    )
    availability = mop_catalog[["Target", "has_mop_parameters", "mop_photometry_points", "has_mop_event_data"]].copy()
    availability["_target_key"] = availability["Target"].map(canonical_target_name)
    availability = availability.drop_duplicates("_target_key", keep="first").set_index("_target_key")
    initial_mop["_target_key"] = initial_mop["Target"].map(canonical_target_name)
    initial_mop = initial_mop.loc[
        ~initial_mop["_target_key"].isin(excluded_keys)
    ].copy()
    for column in ("has_mop_parameters", "mop_photometry_points", "has_mop_event_data"):
        initial_mop[column] = initial_mop["_target_key"].map(availability[column])
    initial_mop["included_in_analysis"] = initial_mop["_target_key"].map(
        availability["has_mop_event_data"]
    ).eq(True)
    initial_mop = initial_mop.drop(columns="_target_key")
    initial_mop.to_csv(initial_mop_path, index=False)

    if excluded_keys:
        daily = daily.loc[
            ~daily["Target"].map(canonical_target_name).isin(excluded_keys)
        ].copy()
        daily.to_csv(daily_path, index=False)
    summary["is_user_supplied"] = summary["Target"].map(canonical_target_name).isin(user_keys)
    summary = summary.dropna(subset=["RA_deg", "Dec_deg"]).drop_duplicates("Target", keep="first").reset_index(drop=True)
    authoritative_mop = pd.to_numeric(
        summary.get("coordinate_priority"), errors="coerce"
    ).eq(100)
    registry.register_targets(summary.loc[authoritative_mop], provider="MOP")
    registry.mark_source("MOP", "target_data")
    summary.to_csv(analysis_targets_path, index=False)

    needs_visibility = (
        generate_visibility_plots
        or generate_observing_selection_summary
        or generate_sky_maps
        or target_report_scope == "visibility_selected"
    )
    visibility_daily = pd.DataFrame()
    visibility_selection = pd.DataFrame()
    if needs_visibility:
        from visibility_plotter import (
            build_visibility_selection,
            save_nightly_visibility_plots,
            summarize_visibility_selection,
        )
        visibility_daily = _visibility_daily_targets(
            summary, start_date, end_date, scope=visibility_target_scope, mop_daily=daily,
        )
        if verbose:
            print(f"      Visibility selection ({visibility_target_scope}: {len(visibility_daily)} target-night rows)", flush=True)
        visibility_selection = build_visibility_selection(
            visibility_daily, start_date, end_date, observatory=observatory,
            minimum_altitude=visibility_minimum_altitude,
            minimum_observable_minutes=visibility_minimum_observable_minutes,
            time_step_minutes=visibility_time_step_minutes,
            observing_windows=visibility_observing_windows,
        )
        visibility_selection.to_csv(paths["visibility_plots"] / "visibility_selection.csv", index=False)
        visibility_target_summary = summarize_visibility_selection(visibility_selection)
        visibility_target_summary.to_csv(paths["tables"] / "visibility_target_summary.csv", index=False)
        if generate_visibility_plots:
            if verbose:
                print("      Nightly visibility plots", flush=True)
            save_nightly_visibility_plots(
                visibility_daily, start_date, end_date, paths["visibility_plots"],
                observatory=observatory, minimum_altitude=visibility_minimum_altitude,
                minimum_observable_minutes=visibility_minimum_observable_minutes,
                time_step_minutes=visibility_time_step_minutes,
                observing_windows=visibility_observing_windows,
                target_scope=visibility_target_scope,
                selection=visibility_selection,
                overwrite=overwrite_visibility_plots, verbose=verbose,
            )
    else:
        if verbose:
            print("      Local visibility selection skipped", flush=True)
        visibility_target_summary = summary[["Target"]].copy()
        visibility_target_summary["passes_visibility_filter"] = False
        visibility_target_summary["visibility_skipped"] = True

    queried_targets = summary[[
        column for column in [
            "Target", "RA_deg", "Dec_deg", "mop_ra", "mop_dec",
            "mop_ra_deg", "mop_dec_deg", "coordinate_source",
            "coordinate_priority", "coordinate_offset_arcsec",
            "coordinate_was_overridden", "is_mop_visible_in_run",
            "is_previously_observed", "is_user_supplied",
        ] if column in summary
    ]].copy()
    mop_visible = queried_targets["is_mop_visible_in_run"].fillna(False).astype(bool)
    previously_observed = queried_targets["is_previously_observed"].fillna(False).astype(bool)
    user_supplied = queried_targets["is_user_supplied"].fillna(False).astype(bool)
    queried_targets["query_source"] = np.select(
        [mop_visible & previously_observed, mop_visible, previously_observed, user_supplied],
        ["MOP visible + previously observed", "MOP visible", "Previously observed", "User supplied"],
        default="Registered target",
    )
    queried_targets = queried_targets.merge(visibility_target_summary, on="Target", how="left")
    queried_targets["passes_visibility_filter"] = queried_targets["passes_visibility_filter"].astype("boolean").fillna(False).astype(bool)
    queried_targets.to_csv(queried_targets_path, index=False)
    if verbose:
        n_mop_visible = int(mop_visible.sum())
        n_observed_only = int((previously_observed & ~mop_visible).sum())
        n_user_supplied = int(user_supplied.sum())
        print(
            f"      Rubin query targets: {len(queried_targets)} "
            f"({n_mop_visible} MOP-visible, {n_observed_only} previously observed only, "
            f"{n_user_supplied} user supplied)",
            flush=True,
        )
        if show_queried_targets:
            names = queried_targets["Target"].astype(str).tolist()
            for start in range(0, len(names), 5):
                print("        " + ", ".join(names[start:start + 5]), flush=True)

    requested_coordinates = summary[["Target", "RA_deg", "Dec_deg"]].copy()
    requested_coordinates["target_key"] = requested_coordinates["Target"].map(canonical_target_name)
    requested_coordinates = requested_coordinates.sort_values("target_key").reset_index(drop=True)

    def target_cache_matches(path: Path) -> bool:
        try:
            cached_targets = pd.read_csv(path)
            required = {"Target", "RA_deg", "Dec_deg"}
            if not required.issubset(cached_targets.columns):
                return False
            cached_targets = cached_targets[list(required)].copy()
            cached_targets["target_key"] = cached_targets["Target"].map(canonical_target_name)
            cached_targets = cached_targets.sort_values("target_key").reset_index(drop=True)
            if cached_targets["target_key"].tolist() != requested_coordinates["target_key"].tolist():
                return False
            for column in ("RA_deg", "Dec_deg"):
                cached_values = pd.to_numeric(cached_targets[column], errors="coerce").to_numpy()
                requested_values = pd.to_numeric(
                    requested_coordinates[column], errors="coerce"
                ).to_numpy()
                if not np.allclose(
                    cached_values, requested_values, rtol=0.0, atol=1e-12,
                    equal_nan=True,
                ):
                    return False
            return True
        except (OSError, ValueError, KeyError):
            return False

    coverage_cache_matches = (
        reuse_cache and coverage_path.exists() and coverage_targets_path.exists()
        and target_cache_matches(coverage_targets_path)
    )
    coverage_cache_key = _coverage_cache_key(summary, release)
    shared_coverage_dir = Path(root_dir) / "_cache" / "release_coverage" / _safe_name(release.name)
    shared_coverage_path = shared_coverage_dir / f"{coverage_cache_key}.csv"
    shared_targets_path = shared_coverage_dir / f"{coverage_cache_key}_targets.csv"
    shared_cache_matches = (
        reuse_cache and shared_coverage_path.exists() and shared_targets_path.exists()
        and target_cache_matches(shared_targets_path)
    )
    date_label = start_date if start_date == end_date else f"{start_date}_to_{end_date}"
    legacy_run_dir = paths["run"].parent / date_label
    legacy_coverage_path = legacy_run_dir / "tables" / "coverage_raw.csv"
    legacy_targets_path = legacy_run_dir / "tables" / "coverage_targets.csv"
    legacy_cache_matches = (
        reuse_cache and legacy_run_dir != paths["run"]
        and legacy_coverage_path.exists() and legacy_targets_path.exists()
        and target_cache_matches(legacy_targets_path)
    )
    if coverage_cache_matches:
        coverage_source = "run cache"
    elif shared_cache_matches:
        coverage_source = "shared cache"
    elif legacy_cache_matches:
        coverage_source = "date-only cache"
    else:
        coverage_source = None
    if verbose:
        cache_label = f" ({coverage_source})" if coverage_source else f" ({max_workers} workers)"
        print(f"[3/5] {release.name} coverage" + cache_label, flush=True)
    if coverage_cache_matches:
        coverage_rows = pd.read_csv(coverage_path)
    elif shared_cache_matches:
        coverage_rows = pd.read_csv(shared_coverage_path)
    elif legacy_cache_matches:
        coverage_rows = pd.read_csv(legacy_coverage_path)
    else:
        coverage_rows = query_release_coverage(
            summary, tap_service, max_workers=max_workers, data_release=release
        )
    if not coverage_cache_matches:
        coverage_rows.to_csv(coverage_path, index=False)
        summary[["Target", "RA_deg", "Dec_deg"]].to_csv(coverage_targets_path, index=False)
    if not shared_cache_matches:
        shared_coverage_dir.mkdir(parents=True, exist_ok=True)
        coverage_rows.to_csv(shared_coverage_path, index=False)
        summary[["Target", "RA_deg", "Dec_deg"]].to_csv(shared_targets_path, index=False)

    forced_photometry = pd.DataFrame()
    forced_photometry_path = paths["tables"] / "release_forced_photometry.csv"
    if generate_release_photometry:
        from release_photometry import FORCED_PHOTOMETRY_VERSION, save_release_forced_photometry

        forced_metadata_path = paths["tables"] / "release_forced_photometry_metadata.json"
        if release.photometry_method == "coadd_forced":
            from release_photometry import (
                compute_coadd_forced_photometry,
                load_target_release_photometry,
                save_target_release_photometry,
            )

            persistent_cache_dir = Path(root_dir) / "rubin_photometry"
            selected_targets = summary[["Target", "RA_deg", "Dec_deg"]].copy()
            if release_photometry_targets is not None:
                selected_targets = selected_targets[
                    selected_targets["Target"].astype(str).isin(release_photometry_targets)
                ]
            collection_key = str(release.butler_collections)

            def cached_coadd_rows(target_name: str, ra: float, dec: float) -> pd.DataFrame:
                cached = load_target_release_photometry(persistent_cache_dir, target_name)
                required = {
                    "data_release", "butler_collection", "measurement_method",
                    "RA_deg", "Dec_deg", "dia_object_ra_deg", "dia_object_dec_deg",
                }
                if cached.empty or not required.issubset(cached.columns):
                    return cached.iloc[0:0].copy()
                cached_ra = pd.to_numeric(cached.get("RA_deg"), errors="coerce")
                cached_dec = pd.to_numeric(cached.get("Dec_deg"), errors="coerce")
                coordinate_match = np.isclose(cached_ra, float(ra), rtol=0.0, atol=1e-12) & np.isclose(
                    cached_dec, float(dec), rtol=0.0, atol=1e-12
                )
                return cached.loc[
                    coordinate_match
                    & (cached["data_release"].astype(str) == release.name)
                    & (cached["butler_collection"].astype(str) == collection_key)
                    & (cached["measurement_method"].astype(str) == "coadd_forced")
                ].copy()

            cached_by_target = {
                str(row.Target): cached_coadd_rows(str(row.Target), row.RA_deg, row.Dec_deg)
                for row in selected_targets.itertuples(index=False)
            }
            retry_statuses = {"coadd_query_error", "coadd_measurement_error"}
            targets_to_measure = []
            for row in selected_targets.itertuples(index=False):
                cached = cached_by_target[str(row.Target)]
                complete = (
                    not cached.empty
                    and not cached.get("measurement_status", pd.Series(dtype=str)).isin(retry_statuses).any()
                )
                if overwrite_release_photometry or not reuse_cache or not complete:
                    targets_to_measure.append({"Target": row.Target, "RA_deg": row.RA_deg, "Dec_deg": row.Dec_deg})

            if verbose:
                state = "cache" if not targets_to_measure else f"{len(targets_to_measure)} targets"
                print(f"[4/6] {release.name} coadd forced photometry ({state})", flush=True)
            if targets_to_measure:
                butler = butler or _create_butler(release)
                compute_coadd_forced_photometry(
                    pd.DataFrame(targets_to_measure), coverage_rows, butler=butler,
                    data_release=release, verbose=verbose,
                    on_target_result=lambda frame: save_target_release_photometry(
                        frame, persistent_cache_dir, replace_measurement_scope=True,
                    ),
                )
                cached_by_target = {
                    str(row.Target): cached_coadd_rows(str(row.Target), row.RA_deg, row.Dec_deg)
                    for row in selected_targets.itertuples(index=False)
                }
            frames = [frame for frame in cached_by_target.values() if not frame.empty]
            forced_photometry = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
            save_release_forced_photometry(forced_photometry, forced_photometry_path)
            forced_metadata_path.write_text(json.dumps({
                "version": FORCED_PHOTOMETRY_VERSION,
                "data_release": release.name,
                "butler_collection": collection_key,
                "measurement_method": "coadd_forced",
                "persistent_cache_dir": str(persistent_cache_dir),
                "n_targets": int(len(selected_targets)),
                "n_targets_measured": int(len(targets_to_measure)),
            }, indent=2), encoding="utf-8")
        elif release.photometry_method == "dia_forced_catalog":
            from release_photometry import (
                load_target_release_photometry,
                query_dia_forced_photometry,
                save_target_release_photometry,
            )

            persistent_cache_dir = Path(root_dir) / "rubin_photometry"
            selected_targets = summary[["Target", "RA_deg", "Dec_deg"]].copy()
            if release_photometry_targets is not None:
                selected_targets = selected_targets[
                    selected_targets["Target"].astype(str).isin(release_photometry_targets)
                ]
            collection_key = str(release.butler_collections)

            def cached_rows_for_target(target_name: str, ra: float, dec: float) -> pd.DataFrame:
                cached = load_target_release_photometry(persistent_cache_dir, target_name)
                if cached.empty:
                    return cached
                required = {
                    "data_release", "butler_collection", "measurement_method",
                    "RA_deg", "Dec_deg",
                }
                if not required.issubset(cached.columns):
                    return cached.iloc[0:0].copy()
                cached_ra = pd.to_numeric(cached.get("RA_deg"), errors="coerce")
                cached_dec = pd.to_numeric(cached.get("Dec_deg"), errors="coerce")
                coordinate_match = np.isclose(cached_ra, float(ra), rtol=0.0, atol=1e-12) & np.isclose(
                    cached_dec, float(dec), rtol=0.0, atol=1e-12
                )
                return cached.loc[
                    coordinate_match
                    & (cached["data_release"].astype(str) == release.name)
                    & (cached["butler_collection"].astype(str) == collection_key)
                    & (cached["measurement_method"].astype(str) == "dia_forced_catalog")
                ].copy()

            cached_by_target = {
                str(row.Target): cached_rows_for_target(str(row.Target), row.RA_deg, row.Dec_deg)
                for row in selected_targets.itertuples(index=False)
            }
            retry_statuses = {"tap_query_error"}
            targets_to_fetch = []
            for row in selected_targets.itertuples(index=False):
                cached = cached_by_target[str(row.Target)]
                complete = (
                    not cached.empty
                    and not cached.get("measurement_status", pd.Series(dtype=str)).isin(retry_statuses).any()
                )
                if overwrite_release_photometry or not reuse_cache or not complete:
                    targets_to_fetch.append({"Target": row.Target, "RA_deg": row.RA_deg, "Dec_deg": row.Dec_deg})

            if verbose:
                state = "cache" if not targets_to_fetch else f"{len(targets_to_fetch)} targets"
                print(f"[4/6] {release.name} DIA forced photometry ({state})", flush=True)
            if targets_to_fetch:
                query_dia_forced_photometry(
                    pd.DataFrame(targets_to_fetch), tap_service=tap_service,
                    data_release=release, max_workers=max_workers, verbose=verbose,
                    on_target_result=lambda frame: save_target_release_photometry(
                        frame, persistent_cache_dir,
                    ),
                )
                cached_by_target = {
                    str(row.Target): cached_rows_for_target(str(row.Target), row.RA_deg, row.Dec_deg)
                    for row in selected_targets.itertuples(index=False)
                }
            frames = [frame for frame in cached_by_target.values() if not frame.empty]
            forced_photometry = (
                pd.concat(frames, ignore_index=True, sort=False)
                if frames else pd.DataFrame()
            )
            save_release_forced_photometry(forced_photometry, forced_photometry_path)
            forced_metadata_path.write_text(json.dumps({
                "version": FORCED_PHOTOMETRY_VERSION,
                "data_release": release.name,
                "butler_collection": collection_key,
                "measurement_method": "dia_forced_catalog",
                "persistent_cache_dir": str(persistent_cache_dir),
                "n_targets": int(len(selected_targets)),
                "n_targets_queried": int(len(targets_to_fetch)),
            }, indent=2), encoding="utf-8")
        else:
            from release_photometry import (
                compute_release_forced_photometry,
                load_release_forced_photometry,
                prepare_forced_photometry_requests,
            )

            requested = prepare_forced_photometry_requests(
                summary, coverage_rows, target_names=release_photometry_targets,
            )
            cache_is_current = False
            if reuse_cache and forced_photometry_path.exists() and not overwrite_release_photometry:
                try:
                    forced_metadata = json.loads(forced_metadata_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    forced_metadata = {}
                cached = load_release_forced_photometry(forced_photometry_path)
                requested_keys = set(
                    requested[["Target", "visitId", "detector"]].astype(str).agg("|".join, axis=1)
                )
                cached_keys = set(
                    cached[["Target", "visitId", "detector"]].dropna().astype(str).agg("|".join, axis=1)
                ) if {"Target", "visitId", "detector"}.issubset(cached.columns) else set()
                cache_is_current = (
                    requested_keys == cached_keys
                    and forced_metadata.get("version") == FORCED_PHOTOMETRY_VERSION
                    and forced_metadata.get("data_release") == release.name
                )
                if cache_is_current:
                    forced_photometry = cached
                    if verbose:
                        print(f"[4/6] {release.name} forced photometry (cache)", flush=True)
            if forced_photometry.empty and not requested.empty:
                if verbose and not cache_is_current:
                    print(f"[4/6] {release.name} forced photometry", flush=True)
                butler = butler or _create_butler(release)
                forced_photometry = compute_release_forced_photometry(
                    summary, coverage_rows, butler=butler, data_release=release,
                    target_names=release_photometry_targets, verbose=verbose,
                )
                from release_photometry import save_target_release_photometry
                save_target_release_photometry(
                    forced_photometry, Path(root_dir) / "rubin_photometry",
                )
                save_release_forced_photometry(forced_photometry, forced_photometry_path)
                forced_metadata_path.write_text(json.dumps({
                    "version": FORCED_PHOTOMETRY_VERSION,
                    "data_release": release.name,
                    "measurement_method": "calexp_forced",
                    "n_requests": int(len(requested)),
                }, indent=2), encoding="utf-8")
            elif requested.empty:
                from release_photometry import save_target_release_photometry
                save_target_release_photometry(
                    forced_photometry, Path(root_dir) / "rubin_photometry",
                )
                save_release_forced_photometry(forced_photometry, forced_photometry_path)
                forced_metadata_path.write_text(json.dumps({
                    "version": FORCED_PHOTOMETRY_VERSION,
                    "data_release": release.name,
                    "measurement_method": "calexp_forced",
                    "n_requests": 0,
                }, indent=2), encoding="utf-8")
                if verbose:
                    print(f"[4/6] {release.name} forced photometry (no covered targets)", flush=True)

    visit_centers = None
    if show_coverage_background:
        visit_centers_path = paths["tables"] / "release_visit_centers.csv"
        if reuse_cache and visit_centers_path.exists():
            visit_centers = pd.read_csv(visit_centers_path)
        else:
            if verbose:
                print("      Querying release-wide visit centers for the coverage background", flush=True)
            visit_centers = query_release_visit_centers(tap_service, release)
            visit_centers.to_csv(visit_centers_path, index=False)

    if verbose:
        print(f"[{5 if generate_release_photometry else 4}/{6 if generate_release_photometry else 5}] Tables and sky maps", flush=True)
    coverage_summary = summarize_release_coverage(coverage_rows)
    coverage_summary.to_csv(paths["tables"] / "coverage_summary.csv", index=False)
    combined = summary.merge(coverage_summary, on="Target", how="left")
    combined = combined.merge(visibility_target_summary, on="Target", how="left")
    combined["passes_visibility_filter"] = combined["passes_visibility_filter"].astype("boolean").fillna(False).astype(bool)
    combined["target_region"] = combined["Target"].map(classify_target_region)
    if generate_release_photometry:
        from release_photometry import summarize_release_forced_photometry
        forced_summary = summarize_release_forced_photometry(forced_photometry)
        combined = combined.merge(forced_summary, on="Target", how="left")
    if hsh_image_catalog is not None:
        from observatory_observations import (
            save_hsh_observation_summary,
            summarize_hsh_observations,
        )
        if verbose:
            print("      HSH observation summary", flush=True)
        hsh_summary = summarize_hsh_observations(
            combined, hsh_image_catalog,
            peak_half_width_t_e=hsh_peak_half_width_t_e,
            event_half_width_t_e=hsh_event_half_width_t_e,
        )
        save_hsh_observation_summary(
            hsh_summary, paths["tables"] / "hsh_observation_summary.csv",
        )
        combined = combined.merge(hsh_summary, on="Target", how="left")
    if generate_release_photometry:
        valid_release = forced_photometry.copy()
        if not valid_release.empty and {"Target", "band", "magnitude"}.issubset(valid_release.columns):
            valid_release["magnitude"] = pd.to_numeric(valid_release["magnitude"], errors="coerce")
            valid_release = valid_release.loc[valid_release["magnitude"].notna()]
            counts = valid_release.groupby(["Target", "band"], sort=False).size().unstack(fill_value=0)
            for band in counts.columns:
                combined[f"release_curve_points_{_safe_name(band)}"] = (
                    combined["Target"].map(counts[band]).fillna(0).astype(int)
                )
            combined["release_curve_points_total"] = (
                combined["Target"].map(valid_release.groupby("Target").size()).fillna(0).astype(int)
            )
        else:
            combined["release_curve_points_total"] = 0

    if generate_observing_selection_summary:
        from observing_selection_summary import (
            build_observing_selection_summary,
            save_observing_selection_summary,
        )
        if verbose:
            print("      Observing selection summary", flush=True)
        observing_selection_summary = build_observing_selection_summary(
            combined, coverage_rows, Path(root_dir) / "mop_photometry",
            hsh_image_catalog=hsh_image_catalog,
            release_visit_exptime_s=release.default_visit_exptime_s,
            peak_half_width_t_e=hsh_peak_half_width_t_e,
            event_half_width_t_e=hsh_event_half_width_t_e,
        )
        save_observing_selection_summary(
            observing_selection_summary,
            paths["tables"] / "observing_selection_summary.csv",
            paths["tables"] / "observing_selection_summary.png",
            release_name=release.name,
            release_visit_exptime_s=release.default_visit_exptime_s,
            peak_half_width_t_e=hsh_peak_half_width_t_e,
            event_half_width_t_e=hsh_event_half_width_t_e,
        )
    combined.to_csv(paths["tables"] / "combined_targets.csv", index=False)
    save_target_summary(
        combined, photometry_dir=Path(root_dir) / "mop_photometry",
        csv_path=paths["tables"] / "target_summary.csv",
        png_path=paths["tables"] / "target_summary.png",
        release_name=release.name,
    )

    if generate_monitoring_report:
        from monitoring_report import create_monitoring_report
        if verbose:
            print("      Monitoring light-curve PDF", flush=True)
        report_epochs = registry.observation_epochs(combined)
        report_photometry = registry.photometry(combined)
        report_layers = None if monitoring_layers is None else tuple(monitoring_layers)
        if generate_release_photometry:
            report_layers = tuple(dict.fromkeys((report_layers or ()) + ("release_photometry",)))
        create_monitoring_report(
            combined,
            paths["monitoring_reports"] / "lightcurves.pdf",
            mop=mop, mop_photometry_dir=Path(root_dir) / "mop_photometry",
            release_photometry=forced_photometry if generate_release_photometry else None,
            lsst_coverage=coverage_rows, observatory_epochs=report_epochs,
            observatory_photometry=report_photometry, data_release=release.name, layers=report_layers,
            plots_per_page=monitoring_report_plots_per_page,
        )

    sky_targets = combined.loc[combined["passes_visibility_filter"].astype(bool)].copy()
    if generate_sky_maps and {"coverage_n_visits", "mag_now"}.issubset(sky_targets.columns):
        plot_sky_dual_metric(
            sky_targets, "mag_now", "coverage_n_visits",
            paths["sky_plots"] / "sky_by_mag_and_visits.png",
            title=f"Locally selected targets with {release.name} coverage",
            left_label="MOP mag_now", right_label=f"{release.name} visits",
            marker_encoding=sky_marker_encoding,
            coverage_background=visit_centers,
            coverage_resolution=coverage_resolution,
            coverage_label=f"{release.name} visits",
        )
        plot_sky_dual_metric(
            sky_targets, "mag_now", "coverage_n_visits",
            paths["sky_plots"] / "sky_bulge_zoom_mag_and_visits.png",
            title=f"Galactic bulge zoom — selected targets + {release.name}",
            left_label="MOP mag_now", right_label=f"{release.name} visits",
            bulge_zoom=(20, 12),
            marker_encoding=sky_marker_encoding,
            coverage_background=visit_centers,
            coverage_resolution=coverage_resolution,
            coverage_label=f"{release.name} visits",
        )
    if use_default_target_plotter:
        report_plotter = _create_default_target_plotter(
            mop=mop, tap_service=tap_service, butler=butler,
            release=release, root_dir=root_dir, release_photometry=forced_photometry,
        )
    def report_version_for_target(target: pd.Series) -> str:
        forced_count = pd.to_numeric(
            target.get("release_forced_n_requested", np.nan), errors="coerce",
        )
        mode = "forced" if pd.notna(forced_count) else "coverage_only"
        return (
            f"{TARGET_REPORT_VERSION}:{mode}:"
            f"{float(target['RA_deg'])!r}:{float(target['Dec_deg'])!r}"
        )

    report_targets = combined if target_report_scope == "all_queried" else combined.loc[
        combined["passes_visibility_filter"].astype(bool)
    ].copy()
    report_output_dir = paths["targets"] if target_report_scope == "all_queried" else paths["targets_visibility_selected"]
    if report_plotter is not None:
        if verbose:
            print(
                f"[{6 if generate_release_photometry else 5}/{6 if generate_release_photometry else 5}] "
                f"Per-target reports ({target_report_scope}: {len(report_targets)} targets)",
                flush=True,
            )
        save_target_reports(
            report_targets, report_plotter, report_output_dir, coverage_rows,
            overwrite=overwrite_target_plots or overwrite_release_photometry,
            verbose=verbose, version_for_target=report_version_for_target,
            reuse_reports_from=legacy_run_dir / "targets",
        )

    forced_photometry_version = None
    if generate_release_photometry:
        from release_photometry import FORCED_PHOTOMETRY_VERSION
        forced_photometry_version = FORCED_PHOTOMETRY_VERSION

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_release": release.name,
        "observatory": observatory, "start_date": start_date, "end_date": end_date,
        "n_daily_rows": int(len(daily)), "n_targets": int(len(combined)),
        "n_mop_invalid_magnitude_excluded": int(excluded_daily + excluded_summary + excluded_observed),
        "max_current_magnitude": None if max_current_magnitude is None else float(max_current_magnitude),
        "include_mop_visible_targets": bool(include_mop_visible_targets),
        "max_workers": int(max_workers), "reuse_cache": bool(reuse_cache),
        "cache_version": CACHE_VERSION,
        "target_report_version": TARGET_REPORT_VERSION,
        "overwrite_target_plots": bool(overwrite_target_plots),
        "target_report_scope": target_report_scope,
        "target_names": None if target_names is None else list(target_names),
        "n_target_reports_requested": int(len(report_targets)),
        "generate_sky_maps": bool(generate_sky_maps),
        "sky_marker_encoding": sky_marker_encoding,
        "show_coverage_background": bool(show_coverage_background),
        "coverage_resolution": int(coverage_resolution),
        "generate_visibility_plots": bool(generate_visibility_plots),
        "visibility_evaluated": bool(needs_visibility),
        "overwrite_visibility_plots": bool(overwrite_visibility_plots),
        "visibility_minimum_altitude": float(visibility_minimum_altitude),
        "visibility_minimum_observable_minutes": float(visibility_minimum_observable_minutes),
        "visibility_time_step_minutes": int(visibility_time_step_minutes),
        "visibility_observing_windows": visibility_observing_windows,
        "visibility_target_scope": visibility_target_scope,
        "target_data_scope": target_data_scope,
        "target_data_observation_providers": list(target_data_observation_providers),
        "target_data_photometry_sources": list(target_data_photometry_sources),
        "n_targets_without_selected_data": int(len(targets_without_selected_data)),
        "previously_observed_providers": list(previously_observed_providers),
        "additional_targets": (
            None if additional_targets is None
            else str(additional_targets) if isinstance(additional_targets, (str, Path))
            else "dataframe"
        ),
        "generate_release_photometry": bool(generate_release_photometry),
        "release_photometry_targets": (
            None if release_photometry_targets is None else [str(name) for name in release_photometry_targets]
        ),
        "overwrite_release_photometry": bool(overwrite_release_photometry),
        "forced_photometry_version": forced_photometry_version,
        "hsh_image_catalog": None if hsh_image_catalog is None else str(hsh_image_catalog),
        "refresh_hsh_data": bool(refresh_hsh_data),
        "hsh_peak_half_width_t_e": float(hsh_peak_half_width_t_e),
        "hsh_event_half_width_t_e": float(hsh_event_half_width_t_e),
        "include_previously_observed": bool(include_previously_observed),
        "show_queried_targets": bool(show_queried_targets),
        "target_registry_path": str(registry_path),
        "generate_monitoring_report": bool(generate_monitoring_report),
        "monitoring_report_plots_per_page": int(monitoring_report_plots_per_page),
        "monitoring_layers": None if monitoring_layers is None else [str(layer) for layer in monitoring_layers],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if verbose:
        print(f"Pipeline completed in {(perf_counter() - started) / 60:.1f} min", flush=True)
    return combined, paths
