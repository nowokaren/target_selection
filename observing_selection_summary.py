"""Compact observing-planning summaries split by microlensing stage."""

from __future__ import annotations

from pathlib import Path
import re

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from target_region import classify_target_region

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



def _visibility_pass_mask(values: pd.Series) -> pd.Series:
    """Coerce visibility flags safely after CSV cache round-trips."""
    if values.dtype == bool:
        return values.fillna(False)
    return values.map(
        lambda value: str(value).strip().casefold() in {"1", "true", "yes", "y"}
        if pd.notna(value) else False
    )


def _has_current_mop_data(data: pd.DataFrame) -> pd.Series:
    """Identify rows with a current MOP magnitude or physical event parameter."""
    magnitude = pd.to_numeric(data.get("mag_now"), errors="coerce")
    parameter_columns = [
        column for column in ("t_E_days", "t_0_HJD", "u_0") if column in data
    ]
    parameters = (
        data[parameter_columns].apply(pd.to_numeric, errors="coerce").notna().any(axis=1)
        if parameter_columns else pd.Series(False, index=data.index)
    )
    return magnitude.gt(0) | parameters

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




def _abbreviate_filter_name(value: object, *, maximum_length: int = 12) -> str:
    """Return a compact, unambiguous-enough label for a photometric filter."""
    text = "" if pd.isna(value) else str(value).strip()
    if not text:
        return "—"
    normalized = re.sub(r"[ _-]+", "_", text).strip("_").upper()
    aliases = {
        "OGLE_I": "I", "GAIA_G": "G", "ZTF_G": "g", "ZTF_R": "r",
        "ZTF_I": "i", "MOA_RED": "MOA-R", "MOA_BLUE": "MOA-B",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized.endswith("_I"):
        return "I"
    if normalized.endswith("_V"):
        return "V"
    if normalized.endswith("_G"):
        return "G"
    if len(text) <= maximum_length:
        return text
    return f"{text[:maximum_length - 1]}…"

def _last_mop_magnitudes(
    targets: pd.DataFrame, photometry_dir: str | Path
) -> pd.DataFrame:
    """Return one preferred recent MOP magnitude and its compact filter per target.

    ``OGLE_I`` is preferred, followed by ``G``. When neither exists, the most
    recently observed valid point in any filter is used instead.
    """
    records: list[dict[str, object]] = []
    photometry_dir = Path(photometry_dir)
    for target in targets["Target"].astype(str).drop_duplicates():
        path = photometry_dir / f"{_safe_target_name(target)}.csv"
        if not path.exists():
            continue
        try:
            data = pd.read_csv(path)
        except OSError:
            continue
        required = {"Timestamp", "Magnitude"}
        if not required.issubset(data.columns):
            continue
        data = data.copy()
        data["Timestamp"] = pd.to_datetime(data["Timestamp"], errors="coerce", utc=True)
        data["Magnitude"] = pd.to_numeric(data["Magnitude"], errors="coerce")
        data = data.dropna(subset=["Timestamp", "Magnitude"])
        if data.empty:
            continue
        filters = data.get("Filter", pd.Series("", index=data.index)).fillna("").astype(str)
        normalized_filters = filters.str.strip().str.casefold().str.replace(" ", "_", regex=False)
        preferred = pd.Series(False, index=data.index)
        for filter_name in ("ogle_i", "g"):
            matches = normalized_filters.eq(filter_name)
            if matches.any():
                preferred = matches
                break
        selected = data.loc[preferred] if preferred.any() else data
        row = selected.sort_values("Timestamp").iloc[-1]
        filter_label = _abbreviate_filter_name(row.get("Filter", ""))
        records.append({
            "Target": target,
            "last_mag": float(row["Magnitude"]),
            "last_mag_filter": filter_label,
        })
    return pd.DataFrame(records, columns=["Target", "last_mag", "last_mag_filter"])


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
    include_reference: bool = True,
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
    passes_visibility = _visibility_pass_mask(targets["passes_visibility_filter"])
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
    selected["has_current_mop_data"] = _has_current_mop_data(selected)
    selected["target_region"] = selected["Target"].map(classify_target_region)

    summaries = [
        _mop_stage_counts(selected, mop_photometry_dir, peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e),
        _hsh_stage_hours(selected, hsh_image_catalog, peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e),
    ]
    if include_reference:
        summaries.append(_release_stage_hours(
            selected, coverage_rows, visit_exptime_s=release_visit_exptime_s,
            peak_half_width_t_e=peak_half_width_t_e, event_half_width_t_e=event_half_width_t_e,
        ))
    last_magnitudes = _last_mop_magnitudes(selected, mop_photometry_dir)
    result = selected[[
        "Target", "target_region", "mag_now", "visible_hours", "best_observation_date", "visible_from", "visible_to",
        "stage_classification_available", "has_current_mop_data", "t_E_days", "t_0_HJD", "u_0",
    ]].copy()
    result = result.merge(last_magnitudes, on="Target", how="left")
    for summary in summaries:
        result = result.merge(summary, on="Target", how="left")
    stage_providers = [("mop", "points"), ("hsh", "hours")]
    if include_reference:
        stage_providers.append(("release", "hours"))
    stage_columns = [
        f"{provider}_{stage}_{measure}"
        for stage, _, _ in STAGES
        for provider, measure in stage_providers
    ]
    for column in stage_columns:
        result[column] = pd.to_numeric(result.get(column), errors="coerce").fillna(0)
    return result.sort_values(["mag_now", "Target"], na_position="last").reset_index(drop=True)


def save_observing_selection_summary(
    summary: pd.DataFrame,
    csv_path: str | Path,
    png_path: str | Path,
    *,
    release_name: str | None,
    release_visit_exptime_s: float | None = 30.0,
    peak_half_width_t_e: float = 0.3,
    event_half_width_t_e: float = 2.0,
    include_reference: bool = True,
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
    columns = ["Target", "target_region", "mag_now", "last_mag", "last_mag_filter", "visible_hours"]
    labels = ["Target", "Region", "mag now", "Last mag", "Last filter", "Visible [h]"]
    top_groups = ["" for _ in columns]
    stage_headers = ["" for _ in columns]
    survey_headers = list(labels)
    stage_ranges: list[tuple[int, int, str]] = []
    for stage, title, criterion in STAGES:
        start_column = len(columns)
        stage_columns = [f"mop_{stage}_points", f"hsh_{stage}_hours"]
        headers = ["MOP [N points]", "HSH [h]"]
        if include_reference:
            stage_columns.append(f"release_{stage}_hours")
            headers.append(f"{release_name} [h]")
        columns.extend(stage_columns)
        survey_headers.extend(headers)
        stage_headers.extend([f"{title} ({criterion})"] * len(stage_columns))
        top_groups.extend([""] * len(stage_columns))
        stage_ranges.append((start_column, start_column + len(stage_columns), f"{title} ({criterion})"))
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
    top_groups[stage_start + (parameter_start - stage_start) // 2] = "Microlensing event stage"
    top_groups[parameter_start + 1] = "Microlensing parameters"
    display = summary[columns].copy()

    for column in display.columns:
        numeric = pd.to_numeric(display[column], errors="coerce")
        if column.startswith("mop_"):
            display[column] = numeric.map(lambda value: "—" if pd.isna(value) else str(int(value)))
        elif column.startswith(("hsh_", "release_")) or column in {"mag_now", "last_mag", "visible_hours", "t_E_days", "t_0_HJD", "u_0"}:
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
    # Stage criteria are merged across the available survey columns, so the
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
    title = "Observing selection summary" + (f" — {release_name}" if include_reference and release_name else "")
    fig.suptitle(title, y=.985, fontsize=15, fontweight="bold")
    if include_reference:
        exposure_note = (
            f"MOP: point count. HSH: total WCS-image exposure time. {release_name}: unique visits × {release_visit_exptime_s:g} s nominal exposure."
            if release_visit_exptime_s else f"MOP: point count. HSH: total WCS-image exposure time. {release_name}: visit duration unavailable."
        )
    else:
        exposure_note = "MOP: point count. HSH: total WCS-image exposure time."
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
        for stage, _, _ in STAGES:
            value = pd.to_numeric(row_data.get(f"hsh_{stage}_hours"), errors="coerce")
            if pd.notna(value) and float(value) > 0:
                hsh_column = columns.index(f"hsh_{stage}_hours")
                table[(row_index, hsh_column)].set_text_props(
                    weight="bold", color="black"
                )

    # Black is reserved exclusively for stages that have not yet occurred.
    # Apply this after alternate-row shading so it is consistent for every row.
    now_mjd = (pd.Timestamp.now(tz="UTC") - pd.Timestamp("1858-11-17", tz="UTC")) / pd.Timedelta(days=1)
    stage_order = [stage for stage, _, _ in STAGES]
    for row_index, (_, row_data) in enumerate(summary.iterrows(), start=3):
        if not bool(row_data.get("stage_classification_available", False)):
            continue
        t0 = pd.to_numeric(row_data.get("t_0_HJD"), errors="coerce")
        te = pd.to_numeric(row_data.get("t_E_days"), errors="coerce")
        if pd.isna(t0) or pd.isna(te) or te <= 0:
            continue
        tau = (float(now_mjd) + 2400000.5 - float(t0)) / float(te)
        if tau < -event_half_width_t_e:
            current_index = 0
        elif tau < -peak_half_width_t_e:
            current_index = 1
        elif abs(tau) <= peak_half_width_t_e:
            current_index = 2
        elif tau <= event_half_width_t_e:
            current_index = 3
        else:
            current_index = 4
        for stage_index in range(current_index + 1, len(STAGES)):
            start, end, _ = stage_ranges[stage_index]
            for column_index in range(start, end):
                cell = table[(row_index, column_index)]
                cell.set_facecolor("black")
                cell.get_text().set_color("black")
        # A zero in a completed stage means that the stage occurred but that
        # particular provider has no data. It is distinct from a future stage.
        for stage_index in range(current_index):
            start, end, _ = stage_ranges[stage_index]
            for column_index, column_name in enumerate(columns[start:end], start):
                value = pd.to_numeric(row_data.get(column_name), errors="coerce")
                if pd.notna(value) and float(value) == 0.0:
                    cell = table[(row_index, column_index)]
                    cell.set_facecolor("#d9d9d9")
                    cell.get_text().set_color("#666666")

    # A dim target label indicates that neither a usable current MOP magnitude
    # nor a physical MOP event parameter is available for that target.
    faded_columns = [columns.index(name) for name in ("Target", "mag_now", "last_mag", "last_mag_filter", "t_E_days", "t_0_HJD", "u_0")]
    for row_index, (_, row_data) in enumerate(summary.iterrows(), start=3):
        if not bool(row_data.get("has_current_mop_data", False)):
            for column_index in faded_columns:
                table[(row_index, column_index)].get_text().set_color("#8a8a8a")

    stage_dividers = [start for start, _, _ in stage_ranges]
    for start_column in stage_dividers:
        divider = float(sum(widths[:start_column]))
        ax.plot(
            [divider, divider], [0, 1], transform=ax.transAxes,
            color="#38566f", linewidth=1.45, zorder=5,
        )
    fig.savefig(png_path, dpi=180, bbox_inches="tight", pad_inches=.08)
    plt.close(fig)
