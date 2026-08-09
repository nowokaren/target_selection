"""Compact observing-planning summaries split by microlensing stage."""

from __future__ import annotations

from pathlib import Path
import re

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from observatory_observations import (
    _numeric_parameter,
    assign_microlensing_stage,
    canonical_target_name,
    load_hsh_astrometry_catalog,
)


STAGES = (
    ("pre_baseline", "Pre-baseline", "τ < -2"),
    ("rise", "Rise", "-2 ≤ τ < -0.3"),
    ("peak", "Peak", "|τ| ≤ 0.3"),
    ("fall", "Fall", "0.3 < τ ≤ 2"),
    ("post_baseline", "Post-baseline", "τ > 2"),
)


def _safe_target_name(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._") or "unknown_target"


def _stage_rows(
    observations: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    peak_half_width_t_e: float,
    event_half_width_t_e: float,
) -> pd.DataFrame:
    """Classify normalized observations using the target MOP parameters."""
    if observations.empty:
        return observations.copy()
    classified = assign_microlensing_stage(
        observations, targets,
        peak_half_width_t_e=peak_half_width_t_e,
        event_half_width_t_e=event_half_width_t_e,
    )
    return classified


def _mop_stage_counts(
    targets: pd.DataFrame,
    photometry_dir: str | Path,
    *,
    peak_half_width_t_e: float,
    event_half_width_t_e: float,
) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    photometry_dir = Path(photometry_dir)
    for target in targets["Target"].astype(str).drop_duplicates():
        path = photometry_dir / f"{_safe_target_name(target)}.csv"
        if not path.exists():
            continue
        try:
            data = pd.read_csv(path, usecols=["Timestamp"])
        except (OSError, ValueError):
            continue
        timestamps = pd.to_datetime(data["Timestamp"], errors="coerce", utc=True)
        mjd = (timestamps - pd.Timestamp("1858-11-17", tz="UTC")) / pd.Timedelta(days=1)
        records.append(pd.DataFrame({
            "Target": target,
            "target_key": canonical_target_name(target),
            "mjd": pd.to_numeric(mjd, errors="coerce"),
        }))
    if not records:
        return pd.DataFrame(columns=["Target", *[f"mop_{stage}_points" for stage, _, _ in STAGES]])
    data = pd.concat(records, ignore_index=True).dropna(subset=["mjd"])
    data = _stage_rows(
        data, targets, peak_half_width_t_e=peak_half_width_t_e,
        event_half_width_t_e=event_half_width_t_e,
    )
    counts = data.pivot_table(
        index="Target", columns="microlensing_stage", values="mjd", aggfunc="size", fill_value=0,
    )
    counts = counts.reindex(columns=[stage for stage, _, _ in STAGES], fill_value=0)
    counts.columns = [f"mop_{stage}_points" for stage in counts.columns]
    return counts.reset_index()


def _hsh_stage_hours(
    targets: pd.DataFrame,
    hsh_image_catalog: str | Path | None,
    *,
    peak_half_width_t_e: float,
    event_half_width_t_e: float,
) -> pd.DataFrame:
    columns = ["Target", *[f"hsh_{stage}_hours" for stage, _, _ in STAGES]]
    if hsh_image_catalog is None:
        return pd.DataFrame(columns=columns)
    data = load_hsh_astrometry_catalog(hsh_image_catalog)
    lookup = targets[["Target"]].drop_duplicates().copy()
    lookup["target_key"] = lookup["Target"].map(canonical_target_name)
    data = data.merge(lookup, on="target_key", how="inner")
    if data.empty:
        return pd.DataFrame(columns=columns)
    data = _stage_rows(
        data, targets, peak_half_width_t_e=peak_half_width_t_e,
        event_half_width_t_e=event_half_width_t_e,
    )
    data["hours"] = pd.to_numeric(data["exptime_s"], errors="coerce").fillna(0).clip(lower=0) / 3600
    hours = data.pivot_table(
        index="Target", columns="microlensing_stage", values="hours", aggfunc="sum", fill_value=0,
    )
    hours = hours.reindex(columns=[stage for stage, _, _ in STAGES], fill_value=0)
    hours.columns = [f"hsh_{stage}_hours" for stage in hours.columns]
    return hours.reset_index()


def _release_stage_hours(
    targets: pd.DataFrame,
    coverage_rows: pd.DataFrame,
    *,
    visit_exptime_s: float | None,
    peak_half_width_t_e: float,
    event_half_width_t_e: float,
) -> pd.DataFrame:
    columns = ["Target", *[f"release_{stage}_hours" for stage, _, _ in STAGES]]
    if coverage_rows.empty or visit_exptime_s is None or visit_exptime_s <= 0:
        return pd.DataFrame(columns=columns)
    required = {"Target", "visitId", "expMidptMJD"}
    if not required.issubset(coverage_rows):
        return pd.DataFrame(columns=columns)
    data = coverage_rows[["Target", "visitId", "expMidptMJD"]].copy()
    data["mjd"] = pd.to_numeric(data.pop("expMidptMJD"), errors="coerce")
    data = data.dropna(subset=["mjd"]).drop_duplicates(["Target", "visitId"])
    data["target_key"] = data["Target"].map(canonical_target_name)
    data = _stage_rows(
        data, targets, peak_half_width_t_e=peak_half_width_t_e,
        event_half_width_t_e=event_half_width_t_e,
    )
    data["hours"] = float(visit_exptime_s) / 3600
    hours = data.pivot_table(
        index="Target", columns="microlensing_stage", values="hours", aggfunc="sum", fill_value=0,
    )
    hours = hours.reindex(columns=[stage for stage, _, _ in STAGES], fill_value=0)
    hours.columns = [f"release_{stage}_hours" for stage in hours.columns]
    return hours.reset_index()


def build_observing_selection_summary(
    targets: pd.DataFrame,
    coverage_rows: pd.DataFrame,
    mop_photometry_dir: str | Path,
    *,
    hsh_image_catalog: str | Path | None = None,
    release_visit_exptime_s: float | None = 30.0,
    peak_half_width_t_e: float = 0.3,
    event_half_width_t_e: float = 2.0,
) -> pd.DataFrame:
    """Build a bright-to-faint table for targets passing the local visibility filter.

    MOP provides photometric epochs but no exposure durations, so MOP columns
    are point counts. HSH columns sum all imported WCS-image exposure times.
    Release rows are detector de-duplicated to visits and use the configured
    nominal visit exposure because the TAP coverage table has no exposure time.
    """
    required = {"Target", "passes_visibility_filter"}
    missing = required - set(targets.columns)
    if missing:
        raise ValueError(f"Targets are missing columns: {sorted(missing)}")
    passes_visibility = targets["passes_visibility_filter"].fillna(False).astype(bool)
    # Keep the table consistent with the observing-selection criterion: a
    # target must pass the configured altitude/time cut (40 deg and 90 min by
    # default).  HSH observations are reported only for those selected rows.
    if "max_observable_minutes" in targets:
        passes_visibility &= pd.to_numeric(targets["max_observable_minutes"], errors="coerce").fillna(0).gt(0)
    selected = targets.loc[passes_visibility].copy()
    selected = selected.drop_duplicates("Target", keep="first")
    if selected.empty:
        return pd.DataFrame(columns=["Target", "mag_now", "visible_hours"])
    magnitude_columns = [
        column for column in ("mag_now", "mag_now_x", "mop_mag_now", "mag_now_y")
        if column in selected
    ]
    if magnitude_columns:
        magnitudes = selected[magnitude_columns].apply(pd.to_numeric, errors="coerce").bfill(axis=1).iloc[:, 0]
    else:
        magnitudes = pd.Series(np.nan, index=selected.index)
    observable_minutes = (
        selected["max_observable_minutes"]
        if "max_observable_minutes" in selected else pd.Series(0.0, index=selected.index)
    )
    selected["mag_now"] = pd.to_numeric(magnitudes, errors="coerce")
    selected["mag_now"] = selected["mag_now"].mask(selected["mag_now"].le(0))
    selected["visible_hours"] = pd.to_numeric(observable_minutes, errors="coerce").fillna(0) / 60
    for column, destination in (
        ("best_observable_start_local", "visible_from"),
        ("best_observable_end_local", "visible_to"),
        ("best_observation_date", "best_observation_date"),
    ):
        selected[destination] = selected[column].fillna("") if column in selected else ""
    selected["t_E_days"] = _numeric_parameter(selected.get("mop_t_e_days", pd.Series(np.nan, index=selected.index)))
    selected["t_0_HJD"] = _numeric_parameter(selected.get("mop_t_0_hjd", pd.Series(np.nan, index=selected.index)))
    selected["u_0"] = _numeric_parameter(selected.get("mop_u_0", pd.Series(np.nan, index=selected.index)))
    selected["stage_classification_available"] = selected["t_E_days"].gt(0) & selected["t_0_HJD"].notna()

    summaries = [
        _mop_stage_counts(selected, mop_photometry_dir, peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e),
        _hsh_stage_hours(selected, hsh_image_catalog, peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e),
        _release_stage_hours(selected, coverage_rows, visit_exptime_s=release_visit_exptime_s, peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e),
    ]
    result = selected[[
        "Target", "mag_now", "visible_hours", "best_observation_date", "visible_from", "visible_to",
        "stage_classification_available", "t_E_days", "t_0_HJD", "u_0",
    ]].copy()
    for summary in summaries:
        result = result.merge(summary, on="Target", how="left")
    stage_columns = [
        f"{provider}_{stage}_{measure}"
        for stage, _, _ in STAGES
        for provider, measure in (("mop", "points"), ("hsh", "hours"), ("release", "hours"))
    ]
    for column in stage_columns:
        result[column] = pd.to_numeric(result.get(column), errors="coerce").fillna(0)
    return result.sort_values(["mag_now", "Target"], na_position="last").reset_index(drop=True)


def save_observing_selection_summary(
    summary: pd.DataFrame,
    csv_path: str | Path,
    png_path: str | Path,
    *,
    release_name: str,
    release_visit_exptime_s: float | None = 30.0,
    peak_half_width_t_e: float = 0.3,
    event_half_width_t_e: float = 2.0,
) -> None:
    """Write the observing-planning CSV and a grouped, compact PNG table."""
    csv_path, png_path = Path(csv_path), Path(png_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(csv_path, index=False)
    if summary.empty:
        fig, ax = plt.subplots(figsize=(10, 2.2))
        ax.axis("off")
        ax.text(.5, .5, "No targets pass the local visibility criteria.", ha="center", va="center", fontsize=12)
        fig.savefig(png_path, dpi=180, bbox_inches="tight", pad_inches=.08)
        plt.close(fig)
        return

    # Scheduling columns are kept together at the far right so the event
    # measurements remain the visual focus of the table.
    columns = ["Target", "mag_now", "visible_hours"]
    labels = ["Target", "mag now", "Visible [h]"]
    top_groups = ["" for _ in columns]
    stage_headers = ["" for _ in columns]
    survey_headers = list(labels)
    stage_ranges: list[tuple[int, int, str]] = []
    for stage, title, criterion in STAGES:
        start_column = len(columns)
        stage_columns = [
            f"mop_{stage}_points", f"hsh_{stage}_hours", f"release_{stage}_hours",
        ]
        columns.extend(stage_columns)
        survey_headers.extend(["MOP [N points]", "HSH [h]", f"{release_name} [h]"])
        stage_headers.extend([f"{title} ({criterion})"] * 3)
        top_groups.extend(["", "", ""])
        stage_ranges.append((start_column, start_column + 3, f"{title} ({criterion})"))
    parameter_start = len(columns)
    columns.extend(["t_E_days", "t_0_HJD", "u_0"])
    survey_headers.extend(["t_E [days]", "t_0 [date]", "u_0"])
    stage_headers.extend(["", "", ""])
    top_groups.extend(["", "", ""])
    schedule_start = len(columns)
    columns.extend(["visible_from", "visible_to", "best_observation_date"])
    survey_headers.extend(["Visible from", "Visible to", "Best night"])
    stage_headers.extend(["", "", ""])
    top_groups.extend(["", "", ""])
    stage_start = stage_ranges[0][0]
    top_groups[stage_start + 7] = "Microlensing event stage"
    top_groups[parameter_start + 1] = "Microlensing parameters"
    display = summary[columns].copy()

    for column in display.columns:
        numeric = pd.to_numeric(display[column], errors="coerce")
        if column.startswith("mop_"):
            display[column] = numeric.map(lambda value: "—" if pd.isna(value) else str(int(value)))
        elif column.startswith(("hsh_", "release_")) or column in {"mag_now", "visible_hours", "t_E_days", "t_0_HJD", "u_0"}:
            display[column] = numeric.map(lambda value: "—" if pd.isna(value) else f"{value:.1f}")
    unavailable = ~summary["stage_classification_available"].astype(bool)
    stage_indices = [index for index, column in enumerate(columns) if column.startswith(("mop_", "hsh_", "release_"))]
    for row_index in np.flatnonzero(unavailable):
        for column_index in stage_indices:
            display.iat[row_index, column_index] = "—"

    def format_hjd_date(value: object) -> str:
        number = pd.to_numeric(value, errors="coerce")
        if pd.isna(number) or not 2_000_000 < float(number) < 3_000_000:
            return "—"
        timestamp = pd.Timestamp("1858-11-17") + pd.to_timedelta(float(number) - 2400000.5, unit="D")
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec")[timestamp.month - 1]
        return f"{timestamp.day:02d}-{month}-{timestamp.year:04d}"

    if "t_0_HJD" in display:
        display["t_0_HJD"] = display["t_0_HJD"].map(format_hjd_date)

    def title_for_column(title: str, values: pd.Series) -> str:
        text = str(title)
        longest_value = max((len(str(value)) for value in values), default=0)
        if len(text) <= longest_value or " " not in text:
            return text
        first, remainder = text.split(" ", 1)
        return f"{first}\n{remainder}"

    survey_headers = [
        title_for_column(title, display.iloc[:, index])
        for index, title in enumerate(survey_headers)
    ]

    # Widths are derived from the longest displayed value in each column. The
    # stage criteria are merged across their three survey columns, so the
    # individual widths only need to accommodate the survey name and values.
    width_units = []
    for index, column in enumerate(columns):
        candidates = [str(value) for value in display.iloc[:, index]]
        candidates.extend(survey_headers[index].splitlines())
        width_units.append(max(2.2, max(len(value) for value in candidates) + .25))
    widths = np.asarray(width_units, dtype=float)
    widths = (widths / widths.sum()).tolist()
    fig = plt.figure(figsize=(max(11, min(20, sum(width_units) * .045)), max(5.0, 1.35 + .27 * (len(display) + 3))))
    ax = fig.add_axes([.008, .025, .984, .91])
    ax.axis("off")
    fig.suptitle(f"Observing selection summary — {release_name}", y=.985, fontsize=15, fontweight="bold")
    exposure_note = (
        f"MOP: point count. HSH: total WCS-image exposure time. {release_name}: unique visits × {release_visit_exptime_s:g} s nominal exposure."
        if release_visit_exptime_s else f"MOP: point count. HSH: total WCS-image exposure time. {release_name}: visit duration unavailable."
    )
    fig.text(.5, .955, "Rows pass the local visibility filter; sorted by current MOP magnitude. " + exposure_note,
             ha="center", va="top", fontsize=8.3)
    table = ax.table(
        cellText=[top_groups, stage_headers, survey_headers, *display.values.tolist()],
        cellLoc="center", colLoc="center", colWidths=widths, loc="center", bbox=[0, 0, 1, 1],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(6.3)

    total_rows = len(display) + 3

    def merged_rectangle(row: int, start_column: int, end_column: int, text_value: str, facecolor: str, fontsize: float) -> None:
        x = float(sum(widths[:start_column]))
        width = float(sum(widths[start_column:end_column]))
        height = 1.0 / total_rows
        y = 1.0 - (row + 1) * height
        for column in range(start_column, end_column):
            table[(row, column)].set_visible(False)
        ax.add_patch(Rectangle(
            (x, y), width, height, transform=ax.transAxes,
            facecolor=facecolor, edgecolor="#5f7890", linewidth=.8, zorder=4,
        ))
        ax.text(
            x + width / 2, y + height / 2, text_value,
            transform=ax.transAxes, ha="center", va="center",
            fontsize=fontsize, fontweight="bold", zorder=5,
        )

    # Hide the unused upper header cells over the basic columns.
    for row in (0, 1):
        for column in range(stage_start):
            table[(row, column)].set_visible(False)
    merged_rectangle(0, stage_start, parameter_start, "Microlensing event stage", "#b5cddd", 7.0)
    for start_column, end_column, header in stage_ranges:
        merged_rectangle(1, start_column, end_column, header, "#c9dfef", 6.2)
    merged_rectangle(0, parameter_start, len(columns), "Microlensing parameters", "#b5cddd", 7.0)

    # Black out stages that are still in the future relative to the current
    # event phase.  This avoids implying that a post-peak stage has already
    # been sampled when the event is currently rising or near its peak.
    now_mjd = (pd.Timestamp.now(tz="UTC") - pd.Timestamp("1858-11-17", tz="UTC")) / pd.Timedelta(days=1)
    stage_order = [stage for stage, _, _ in STAGES]
    for row_index, (_, row_data) in enumerate(summary.iterrows(), start=3):
        t0 = pd.to_numeric(row_data.get("t_0_HJD"), errors="coerce")
        te = pd.to_numeric(row_data.get("t_E_days"), errors="coerce")
        if pd.isna(t0) or pd.isna(te) or te <= 0:
            continue
        tau = (float(now_mjd) + 2400000.5 - float(t0)) / float(te)
        if tau < -event_half_width_t_e:
            current_stage = "pre_baseline"
        elif tau < -peak_half_width_t_e:
            current_stage = "rise"
        elif abs(tau) <= peak_half_width_t_e:
            current_stage = "peak"
        elif tau <= event_half_width_t_e:
            current_stage = "fall"
        else:
            current_stage = "post_baseline"
        current_index = stage_order.index(current_stage)
        for stage_index in range(current_index + 1, len(stage_order)):
            start = stage_start + stage_index * 3
            for column_index in range(start, start + 3):
                cell = table[(row_index, column_index)]
                cell.set_facecolor("black")
                cell.get_text().set_color("black")
        # A completed stage with zero observations is meaningful: it was
        # already observable for the event, but this provider has no points.
        # Use a light gray cell instead of leaving a misleading white zero.
        if current_index > 0:
            for stage_index in range(current_index):
                start = stage_start + stage_index * 3
                for provider_offset, provider in enumerate(("mop", "hsh", "release")):
                    value = pd.to_numeric(row_data.get(f"{provider}_{stage_order[stage_index]}_{'points' if provider == 'mop' else 'hours'}"), errors="coerce")
                    if pd.notna(value) and float(value) == 0.0:
                        cell = table[(row_index, start + provider_offset)]
                        cell.set_facecolor("#d9d9d9")
                        cell.get_text().set_color("#666666")

    for (row, column), cell in table.get_celld().items():
        cell.PAD = .013
        if row == 0:
            if cell.get_visible():
                cell.set_text_props(weight="bold", fontsize=7.0)
                cell.visible_edges = "BRTL"
        elif row == 1:
            if cell.get_visible():
                cell.set_text_props(weight="bold", fontsize=6.2)
                cell.visible_edges = "BRTL"
        elif row == 2:
            cell.set_facecolor("#e8f2f8")
            cell.set_text_props(weight="bold", fontsize=6.0)
            cell.visible_edges = "BRTL"
        elif row % 2 == 1:
            cell.set_facecolor("#f6f6f6")
    # Emphasize positive HSH exposure values so previously observed stages
    # can be found immediately among the provider columns.
    for row_index, (_, row_data) in enumerate(summary.iterrows(), start=3):
        for stage_index, (stage, _, _) in enumerate(STAGES):
            value = pd.to_numeric(row_data.get(f"hsh_{stage}_hours"), errors="coerce")
            if pd.notna(value) and float(value) > 0:
                table[(row_index, stage_start + stage_index * 3 + 1)].set_text_props(
                    weight="bold", color="black"
                )

    first_stage = columns.index("mop_pre_baseline_points")
    divider = float(sum(widths[:first_stage]))
    ax.plot([divider, divider], [0, 1], transform=ax.transAxes, color="#5f7890", linewidth=1.1, zorder=5)
    fig.savefig(png_path, dpi=180, bbox_inches="tight", pad_inches=.08)
    plt.close(fig)
