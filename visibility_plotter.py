"""Local nightly visibility plots for MOP target-selection runs."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
from astropy.time import Time
from astropy.utils import iers

# RSP notebook environments may have an offline/stale IERS table. Visibility
# calculations only need a stable Earth-orientation fallback, so do not fail
# when dates extend beyond the table's predictive range.
iers.conf.auto_download = False
iers.conf.auto_max_age = None


VISIBILITY_PLOT_VERSION = 27


OBSERVATORIES = {
    "El Leoncito": {
        "latitude_deg": -31.798527,
        "longitude_deg": -69.295583,
        "height_m": 2552.0,
        "timezone": "America/Argentina/San_Juan",
    },
}


def get_observatory(observatory: str | dict = "El Leoncito") -> tuple[EarthLocation, ZoneInfo, str]:
    """Resolve a named or custom observatory configuration."""
    if isinstance(observatory, str):
        if observatory not in OBSERVATORIES:
            choices = ", ".join(OBSERVATORIES)
            raise ValueError(f"Unknown observatory {observatory!r}. Options: {choices}")
        config = OBSERVATORIES[observatory]
        name = observatory
    else:
        config = observatory
        name = str(config.get("name", "Custom observatory"))
    location = EarthLocation.from_geodetic(
        lon=float(config["longitude_deg"]) * u.deg,
        lat=float(config["latitude_deg"]) * u.deg,
        height=float(config.get("height_m", 0)) * u.m,
    )
    return location, ZoneInfo(str(config["timezone"])), name


def _night_times(
    night: str | date,
    timezone: ZoneInfo,
    step_minutes: int,
    observing_windows=None,
    location: EarthLocation | None = None,
) -> tuple[pd.DatetimeIndex, Time]:
    """Return a civil-twilight-to-dawn grid, extended for later assigned time."""
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
    night_date = pd.Timestamp(night).date()
    fallback_start = pd.Timestamp(f"{night_date} 18:00", tz=timezone)
    if location is None:
        start = fallback_start
    else:
        search_start = pd.Timestamp(f"{night_date} 12:00", tz=timezone)
        preview_times = pd.date_range(search_start, fallback_start, freq="5min")
        preview_time = Time(preview_times.to_pydatetime())
        preview_frame = AltAz(obstime=preview_time, location=location)
        sun_altitude = get_body("sun", preview_time, location=location).transform_to(preview_frame).alt.degree
        twilight = np.flatnonzero(sun_altitude < 0)
        start = preview_times[int(twilight[0])] if len(twilight) else fallback_start
    default_end = pd.Timestamp(night_date, tz=timezone) + pd.Timedelta(days=1, hours=7)
    allocated_ends = []
    for _, end_clock in resolve_observing_windows(night, observing_windows):
        end = _clock_time(night, end_clock, timezone)
        if end <= start:
            end += pd.Timedelta(days=1)
        allocated_ends.append(end)
    end = max([default_end, *allocated_ends])
    local_times = pd.date_range(start, end, freq=f"{step_minutes}min")
    return local_times, Time(local_times.to_pydatetime())


def _visibility_target_label(row: pd.Series, observable_minutes: float | None = None) -> str:
    """Format one target legend label with observable time and current MOP magnitude."""
    name = str(row.get("Target", "Target"))
    minutes = pd.to_numeric(
        row.get("observable_minutes", np.nan) if observable_minutes is None else observable_minutes,
        errors="coerce",
    )
    magnitude = pd.to_numeric(row.get("mag_now", np.nan), errors="coerce")
    hours_text = f"{float(minutes) / 60:.1f} h" if pd.notna(minutes) else "hours unavailable"
    magnitude_text = f"mag={float(magnitude):.1f}" if pd.notna(magnitude) else "mag=—"
    return f"{name} ({hours_text}, {magnitude_text})"



def _visibility_magnitude_label(row: pd.Series) -> str:
    """Format a compact current-magnitude label for a target row."""
    magnitude = pd.to_numeric(row.get("mag_now", np.nan), errors="coerce")
    if pd.isna(magnitude) or float(magnitude) <= 0:
        return "—"
    return f"{float(magnitude):.1f}"

def _source_flag(value) -> bool:
    """Interpret nullable boolean fields consistently across CSV round-trips."""
    if value is None or pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return bool(value)


def _visibility_source_group(row: pd.Series) -> str:
    """Classify one row for source-aware visibility plot grouping."""
    source_text = " ".join(
        str(row.get(column, ""))
        for column in ("target_source", "input_sources", "query_source", "observatory_providers")
    ).casefold()
    followup = _source_flag(row.get("is_previously_observed")) or any(
        token in source_text for token in ("hsh", "casleo_hsh", "js", "casleo_js")
    )
    mop = _source_flag(row.get("is_mop_visible_in_run")) or "mop" in source_text
    if followup:
        return "HSH/JS-observed"
    if mop:
        return "MOP-only"
    return "Other targets"


def _visibility_line_style(row: pd.Series, *, show_source_styles: bool) -> str:
    """Return a semantic line style only when source groups share one plot."""
    if not show_source_styles:
        return "-"
    return {
        "MOP-only": "-",
        "HSH/JS-observed": "--",
        "Other targets": "-.",
    }[_visibility_source_group(row)]


def _split_visibility_targets(
    targets: pd.DataFrame, *, max_targets_per_plot: int = 20,
) -> list[tuple[str, pd.DataFrame]]:
    """Split a nightly target list into source-aware chunks of bounded size."""
    if max_targets_per_plot < 1:
        raise ValueError("max_targets_per_plot must be positive.")
    unique = targets.drop_duplicates("Target").copy()
    if len(unique) <= max_targets_per_plot:
        return [("all", unique)]

    unique["_visibility_source_group"] = unique.apply(_visibility_source_group, axis=1)
    groups = ("MOP-only", "HSH/JS-observed", "Other targets")
    chunks: list[tuple[str, pd.DataFrame]] = []
    for group in groups:
        rows = unique.loc[unique["_visibility_source_group"].eq(group)].drop(
            columns="_visibility_source_group"
        )
        for start in range(0, len(rows), max_targets_per_plot):
            chunks.append((group, rows.iloc[start:start + max_targets_per_plot].copy()))
    return chunks


def _visibility_part_slug(source_group: str) -> str:
    """Return a stable filename component for a source-group visibility part."""
    return {
        "MOP-only": "mop_only",
        "HSH/JS-observed": "hsh_js_observed",
        "Other targets": "other",
    }.get(source_group, "targets")


def _window_pairs(value) -> list[tuple[str, str]]:
    """Normalize one global or per-night list of local clock intervals."""
    if value is None:
        return [("18:00", "06:00")]
    if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, str) for item in value):
        return [(value[0], value[1])]
    if isinstance(value, list):
        if len(value) == 2 and all(isinstance(item, str) for item in value):
            return [(value[0], value[1])]
        pairs = []
        for item in value:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("Each observing window must be a pair such as ('20:00', '02:00').")
            pairs.append((str(item[0]), str(item[1])))
        return pairs
    raise ValueError("Observing windows must be a ('HH:MM', 'HH:MM') pair or a list of pairs.")


def resolve_observing_windows(night: str | date, observing_windows=None) -> list[tuple[str, str]]:
    """Resolve local observing intervals for one night.

    ``observing_windows`` can be one ``('HH:MM', 'HH:MM')`` pair applied to
    every night, or a mapping with a ``'default'`` entry and ISO-date
    overrides. An empty list for a date explicitly means no allocated time.
    Clock times before noon are interpreted on the following calendar day.
    """
    if isinstance(observing_windows, dict):
        key = pd.Timestamp(night).date().isoformat()
        value = observing_windows.get(key, observing_windows.get("default"))
    else:
        value = observing_windows
    return _window_pairs(value)


def _clock_time(night: str | date, clock: str, timezone: ZoneInfo) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(f"2000-01-01 {clock}")
    except ValueError as exc:
        raise ValueError(f"Invalid local clock time {clock!r}; use HH:MM.") from exc
    night_date = pd.Timestamp(night).date()
    day_offset = 0 if parsed.hour >= 12 else 1
    timestamp = pd.Timestamp(night_date) + pd.Timedelta(days=day_offset, hours=parsed.hour, minutes=parsed.minute)
    return timestamp.tz_localize(timezone)


def observing_window_mask(
    local_times: pd.DatetimeIndex,
    night: str | date,
    timezone: ZoneInfo,
    observing_windows=None,
) -> tuple[np.ndarray, list[tuple[str, str]]]:
    """Return the sample mask of allocated local observing time for one night."""
    windows = resolve_observing_windows(night, observing_windows)
    mask = np.zeros(len(local_times), dtype=bool)
    for start_clock, end_clock in windows:
        start = _clock_time(night, start_clock, timezone)
        end = _clock_time(night, end_clock, timezone)
        if end <= start:
            end += pd.Timedelta(days=1)
        mask |= np.asarray((local_times >= start) & (local_times <= end), dtype=bool)
    return mask, windows


def _window_label(windows: list[tuple[str, str]]) -> str:
    return "none" if not windows else ", ".join(f"{start}–{end}" for start, end in windows)


def evaluate_nightly_visibility(
    targets: pd.DataFrame,
    night: str | date,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 30.0,
    time_step_minutes: int = 1,
    observing_windows=None,
) -> pd.DataFrame:
    """Add per-target visibility metrics for one astronomical night."""
    if time_step_minutes < 1:
        raise ValueError("time_step_minutes must be positive.")
    location, timezone, _ = get_observatory(observatory)
    local_times, times = _night_times(
        night, timezone, time_step_minutes, observing_windows, location=location,
    )
    allocated_time, windows = observing_window_mask(
        local_times, night, timezone, observing_windows,
    )
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
    frame = AltAz(obstime=times, location=location)
    sun_altitude = get_body("sun", times, location=location).transform_to(frame).alt.degree
    astronomical_night = sun_altitude < -18
    eligible_time = astronomical_night & allocated_time
    night_samples = int(eligible_time.sum())

    evaluated = targets.drop_duplicates("Target").copy() if "Target" in targets else targets.copy()
    evaluated["RA_deg"] = pd.to_numeric(evaluated.get("RA_deg"), errors="coerce")
    evaluated["Dec_deg"] = pd.to_numeric(evaluated.get("Dec_deg"), errors="coerce")
    peak_altitudes = []
    night_fractions = []
    observable_minutes = []
    observable_starts = []
    observable_ends = []
    eligible_times = local_times[eligible_time]
    for _, row in evaluated.iterrows():
        if pd.isna(row.get("RA_deg")) or pd.isna(row.get("Dec_deg")) or night_samples == 0:
            peak_altitudes.append(np.nan)
            night_fractions.append(0.0)
            observable_minutes.append(0.0)
            observable_starts.append("")
            observable_ends.append("")
            continue
        coord = SkyCoord(float(row["RA_deg"]) * u.deg, float(row["Dec_deg"]) * u.deg)
        altitude = coord.transform_to(frame).alt.degree
        night_altitude = altitude[eligible_time]
        visible = night_altitude >= minimum_altitude
        visible_times = eligible_times[visible]
        peak_altitudes.append(float(np.nanmax(night_altitude)))
        night_fractions.append(float(visible.mean()))
        observable_minutes.append(float(visible.sum() * time_step_minutes))
        observable_starts.append(visible_times[0].strftime("%H:%M") if len(visible_times) else "")
        observable_ends.append(visible_times[-1].strftime("%H:%M") if len(visible_times) else "")

    evaluated["peak_altitude_deg"] = peak_altitudes
    evaluated["observable_night_fraction"] = night_fractions
    evaluated["observable_night_percent"] = 100 * evaluated["observable_night_fraction"]
    evaluated["observable_minutes"] = observable_minutes
    evaluated["observable_start_local"] = observable_starts
    evaluated["observable_end_local"] = observable_ends
    evaluated["allocated_astronomical_minutes"] = float(night_samples * time_step_minutes)
    evaluated["observing_windows"] = _window_label(windows)
    return evaluated


def select_nightly_targets(
    targets: pd.DataFrame,
    night: str | date,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    observing_windows=None,
    return_all: bool = False,
) -> pd.DataFrame:
    """Select targets observable above a minimum altitude for a minimum time."""
    if minimum_observable_minutes <= 0:
        raise ValueError("minimum_observable_minutes must be positive.")
    evaluated = evaluate_nightly_visibility(
        targets, night, observatory=observatory, minimum_altitude=minimum_altitude,
        time_step_minutes=time_step_minutes, observing_windows=observing_windows,
    )
    selected = evaluated["observable_minutes"].ge(minimum_observable_minutes)
    evaluated["selected_for_visibility"] = selected
    evaluated["visibility_rejection_reasons"] = np.where(
        selected,
        "",
        f"fewer than {minimum_observable_minutes:g} observable minutes above "
        f"{minimum_altitude:g} degrees during the allocated astronomical night",
    )
    evaluated = evaluated.sort_values(
        ["selected_for_visibility", "observable_minutes", "peak_altitude_deg"],
        ascending=[False, False, False],
    )
    return evaluated if return_all else evaluated.loc[evaluated["selected_for_visibility"]].copy()


def plot_nightly_visibility(
    targets: pd.DataFrame,
    night: str | date,
    output_path: str | Path,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    time_step_minutes: int = 1,
    observing_windows=None,
    part_label: str | None = None,
) -> None:
    """Plot altitude, airmass, twilight, and Moon altitude for one night."""
    if time_step_minutes < 1:
        raise ValueError("time_step_minutes must be positive.")
    location, timezone, observatory_name = get_observatory(observatory)
    local_times, times = _night_times(
        night, timezone, time_step_minutes, observing_windows, location=location,
    )
    allocated_time, windows = observing_window_mask(
        local_times, night, timezone, observing_windows,
    )
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
    # Restrict the plotted interval to twilight and nighttime.  The time grid
    # may extend to an allocated end time after sunrise, but daytime is not
    # useful for this visibility view and should never appear on the x-axis.
    initial_frame = AltAz(obstime=times, location=location)
    initial_sun = get_body("sun", times, location=location)
    initial_sun_altitude = initial_sun.transform_to(initial_frame).alt.degree
    night_indices = np.flatnonzero(initial_sun_altitude < 0)
    if len(night_indices):
        start_index = int(night_indices[0])
        sunrise_indices = np.flatnonzero(
            (np.arange(len(initial_sun_altitude)) > start_index)
            & (initial_sun_altitude >= 0)
        )
        end_index = int(sunrise_indices[0]) if len(sunrise_indices) else len(times)
        local_times = local_times[start_index:end_index]
        times = times[start_index:end_index]
        allocated_time = allocated_time[start_index:end_index]

    frame = AltAz(obstime=times, location=location)
    sun = get_body("sun", times, location=location)
    sun_altitude = sun.transform_to(frame).alt.degree
    eligible_time = (sun_altitude < -18) & allocated_time
    moon = get_body("moon", times, location=location)
    moon_altitude = moon.transform_to(frame).alt.degree
    elongation = sun.separation(moon).radian
    moon_illumination = float(np.nanmean((1 - np.cos(elongation)) / 2) * 100)

    unique = targets.drop_duplicates("Target").copy() if "Target" in targets else targets.copy()
    unique["RA_deg"] = pd.to_numeric(unique.get("RA_deg"), errors="coerce")
    unique["Dec_deg"] = pd.to_numeric(unique.get("Dec_deg"), errors="coerce")
    unique = unique.dropna(subset=["RA_deg", "Dec_deg"])
    # Give the altitude-bar panel enough physical height for one readable row
    # per target. The previous capped GridSpec ratio compressed labels when a
    # night contained many targets.
    target_count = len(unique)
    bar_panel_height = max(1.8, 0.24 * max(target_count, 1))
    figure_height = max(8.5, 6.8 + bar_panel_height)
    fig, axes = plt.subplots(
        2, 1, figsize=(11.5, figure_height), sharex=True,
        gridspec_kw={"height_ratios": (5.0, bar_panel_height)},
    )
    ax, altitude_bar_axis = axes

    twilight = [
        ((sun_altitude < 0) & (sun_altitude >= -6), "#ffe0a3", "Civil twilight"),
        ((sun_altitude < -6) & (sun_altitude >= -12), "#b8cbe3", "Nautical twilight"),
        ((sun_altitude < -12) & (sun_altitude >= -18), "#7f96b3", "Astronomical twilight"),
        (sun_altitude < -18, "#eef1f6", "Astronomical night"),
    ]
    for mask, color, _ in twilight:
        ax.fill_between(local_times, 0, 90, where=mask, color=color, alpha=.72, step="mid", zorder=0)
    if not allocated_time.all():
        ax.fill_between(
            local_times, 0, 90, where=~allocated_time, color="0.20", alpha=.20,
            step="mid", zorder=1,
        )

    palette = np.vstack([
        plt.get_cmap(name)(np.arange(20))
        for name in ("tab20", "tab20b", "tab20c")
    ])
    palette = palette[np.r_[np.arange(0, 60, 2), np.arange(1, 60, 2)]]
    source_groups = {_visibility_source_group(row) for _, row in unique.iterrows()}
    show_source_styles = len(source_groups) > 1
    altitude_rows = []
    altitude_names = []
    for target_index, (_, row) in enumerate(unique.iterrows()):
        color = palette[target_index % len(palette)]
        coord = SkyCoord(float(row["RA_deg"]) * u.deg, float(row["Dec_deg"]) * u.deg)
        altitude = coord.transform_to(frame).alt.degree
        observable_minutes = pd.to_numeric(row.get("observable_minutes"), errors="coerce")
        if pd.isna(observable_minutes):
            observable_minutes = float(((altitude >= minimum_altitude) & eligible_time).sum() * time_step_minutes)
        ax.plot(
            local_times, altitude, lw=1.35,
            ls=_visibility_line_style(row, show_source_styles=show_source_styles),
            color=color,
                label=_visibility_target_label(row, observable_minutes), zorder=3)
        altitude_rows.append(np.asarray(altitude, dtype=float))
        altitude_names.append(str(row.get("Target", "Target")))

    colorbar = None

    # Lower panel: one horizontal time bar per target, colored by altitude.
    # Values below 30 degrees are shown as a pale background; the color scale
    # itself is fixed to 30--90 degrees for comparisons between nights.
    if altitude_rows:
        altitude_matrix = np.vstack(altitude_rows)
        altitude_matrix = np.ma.masked_where(altitude_matrix < 30.0, altitude_matrix)
        altitude_bins = np.arange(30.0, 95.0, 5.0)
        bin_colors = plt.get_cmap("Blues")(np.linspace(.35, .95, len(altitude_bins) - 1))
        cmap = ListedColormap(bin_colors)
        cmap.set_bad("#e7f1fb")
        norm = BoundaryNorm(altitude_bins, cmap.N, clip=True)
        x_values = mdates.date2num(local_times)
        if len(x_values) > 1:
            step = float(np.median(np.diff(x_values)))
        else:
            step = 1 / 1440
        x_edges = np.r_[x_values - step / 2, x_values[-1] + step / 2]
        image = altitude_bar_axis.imshow(
            altitude_matrix, origin="lower", aspect="auto",
            extent=(x_edges[0], x_edges[-1], -0.5, len(altitude_names) - 0.5),
            cmap=cmap, norm=norm, interpolation="nearest",
        )
        altitude_bar_axis.set_yticks(np.arange(len(altitude_names)))
        altitude_bar_axis.set_yticklabels(
            altitude_names, fontsize=8 if target_count <= 45 else 7
        )
        altitude_bar_axis.hlines(
            np.arange(-.5, len(altitude_names) + .5, 1),
            x_edges[0], x_edges[-1], colors="black", linewidth=.22, zorder=4,
        )
        altitude_bar_axis.set_ylabel("Targets", fontsize=8)
        # Print the current magnitude alongside each altitude bar on the right.
        # It is a row annotation rather than a second scientific scale.
        magnitude_axis = altitude_bar_axis.twinx()
        magnitude_axis.patch.set_visible(False)
        magnitude_axis.spines["left"].set_visible(False)
        magnitude_axis.spines["right"].set_visible(False)
        magnitude_axis.yaxis.set_label_position("right")
        magnitude_axis.yaxis.tick_right()
        magnitude_axis.set_ylim(altitude_bar_axis.get_ylim())
        magnitude_axis.set_yticks(np.arange(len(altitude_names)))
        magnitude_axis.set_yticklabels(
            [_visibility_magnitude_label(row) for _, row in unique.iterrows()],
            fontsize=8 if target_count <= 45 else 7,
        )
        magnitude_axis.tick_params(axis="y", length=0, pad=4)
        altitude_bar_axis.set_xlabel(f"Local time\n[{timezone.key}]")
        altitude_bar_axis.grid(False)
        colorbar = fig.colorbar(
            image, ax=altitude_bar_axis, orientation="horizontal", location="bottom",
            pad=.11, fraction=.07, aspect=45,
            boundaries=altitude_bins, ticks=altitude_bins, spacing="proportional",
        )
        colorbar.set_label("Altitude bins [deg]", fontsize=8)
        colorbar.ax.tick_params(labelsize=7)
    else:
        altitude_bar_axis.text(.5, .5, "No targets selected", transform=altitude_bar_axis.transAxes,
                               ha="center", va="center")
        altitude_bar_axis.set_yticks([])

    ax.plot(local_times, moon_altitude, color="0.25", lw=1.4, ls="--",
            label=f"Moon ({moon_illumination:.0f}% illuminated)", zorder=2)
    ax.axhline(minimum_altitude, color="firebrick", lw=1, ls=":",
               label=f"Minimum altitude ({minimum_altitude:g}°)")
    ax.set_ylim(0, 90)
    ax.set_xlim(local_times[0], local_times[-1])
    ax.margins(x=0)
    ax.set_ylabel("Altitude [deg]")
    ax.tick_params(axis="y", labelsize=8)
    ax.set_xlabel("")
    altitude_bar_axis.set_xlabel("")
    title = (
        f"Target visibility — {observatory_name} — {pd.Timestamp(night).date()} "
        f"(allocated: {_window_label(windows)})"
    )
    if part_label:
        title = f"{title} — {part_label}"
    # Both panels share the time axis, but keep tick labels visible on both.
    for panel in (ax, altitude_bar_axis):
        panel.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=timezone))
        panel.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone))
        panel.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[15, 30, 45], tz=timezone))
        panel.grid(axis="x", which="major", color="0.25", alpha=.42, linewidth=.75, zorder=2)
        panel.grid(axis="x", which="minor", color="0.35", alpha=.24, linewidth=.25, zorder=2)
    # Use the upper panel's bottom edge as the single shared time scale.
    # Native shared-axis labels are disabled and drawn manually in the gap,
    # which prevents Matplotlib from moving them to the bottom panel.
    ax.tick_params(axis="x", which="both", bottom=True, labelbottom=False, top=False, labeltop=False)
    altitude_bar_axis.tick_params(
        axis="x", which="both", bottom=False, labelbottom=False,
        top=True, labeltop=False, pad=1,
    )
    major_times = pd.date_range(
        start=pd.Timestamp(local_times[0]).floor("h"),
        end=pd.Timestamp(local_times[-1]).ceil("h"),
        freq="1h",
        tz=pd.Timestamp(local_times[0]).tz,
    )
    for timestamp in major_times:
        if local_times[0] <= timestamp.to_pydatetime() <= local_times[-1]:
            ax.text(
                mdates.date2num(timestamp.to_pydatetime()), -.035,
                timestamp.strftime("%H:%M"), transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=8, clip_on=False, zorder=20,
            )
    ax.text(
        -.095, -.012, f"Local time\n[{timezone.key}]", transform=ax.transAxes,
        ha="center", va="top", multialignment="center", fontsize=7, clip_on=False, zorder=20,
    )
    # Emphasize half-hour grid lines over quarter-hour lines.
    local_index = pd.DatetimeIndex(local_times)
    for timestamp in local_index[local_index.minute.isin([15, 30, 45])].unique():
        x = mdates.date2num(timestamp.to_pydatetime())
        width = .52 if timestamp.minute == 30 else .25
        for panel in (ax, altitude_bar_axis):
            panel.axvline(x, color="0.25", alpha=.30, linewidth=width, zorder=2)

    airmass_axis = ax.twinx()
    altitude_ticks = np.array([90, 60, 45, 30, 20], dtype=float)
    airmass_values = 1 / np.sin(np.deg2rad(altitude_ticks))
    airmass_axis.set_ylim(ax.get_ylim())
    airmass_axis.set_yticks(altitude_ticks)
    airmass_axis.set_yticklabels([f"{value:.2f}" for value in airmass_values])
    airmass_axis.set_ylabel("Approximate airmass")

    twilight_handles = [Patch(facecolor=color, alpha=.72, label=label) for _, color, label in twilight]
    allocation_handles = [] if allocated_time.all() else [
        Patch(facecolor="0.20", alpha=.20, label="Outside allocated time")
    ]
    handles, labels = ax.get_legend_handles_labels()
    source_style_handles = []
    if show_source_styles:
        source_style_handles = [
            Line2D(
                [0], [0], color="0.15", lw=1.35,
                ls={"MOP-only": "-", "HSH/JS-observed": "--", "Other targets": "-."}[group],
                label=f"{group} target",
            )
            for group in ("MOP-only", "HSH/JS-observed", "Other targets")
            if group in source_groups
        ]
    legend_items = (
        len(handles) + len(source_style_handles) + len(twilight_handles)
        + len(allocation_handles)
    )
    ncol = min(8, max(4, int(np.ceil(legend_items / 8))))
    legend = fig.legend(
        handles + source_style_handles + twilight_handles + allocation_handles,
        labels + [item.get_label() for item in source_style_handles + twilight_handles + allocation_handles],
        loc="upper center", bbox_to_anchor=(.5, .95), ncol=ncol,
        fontsize=7, frameon=False, borderaxespad=0.,
    )
    fig.suptitle(title, y=.98, fontsize=10)
    fig.subplots_adjust(hspace=.12, bottom=.18, top=.83, left=.08, right=.91)
    # ``subplots_adjust`` can otherwise move the colorbar over the altitude
    # matrix. Reposition it explicitly below the lower panel.
    if colorbar is not None:
        bar_panel = altitude_bar_axis.get_position()
        colorbar.ax.set_position([
            bar_panel.x0 + .15 * bar_panel.width,
            max(.035, bar_panel.y0 - .068),
            .70 * bar_panel.width,
            .014,
        ])
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=170, bbox_inches="tight", pad_inches=.08)
    plt.close(fig)


def plot_selected_visibility(
    selected_targets: pd.DataFrame,
    night: str | date,
    output_path: str | Path,
    **plot_options,
) -> None:
    """Plot exactly the targets supplied by the observer, without filtering."""
    plot_nightly_visibility(selected_targets, night, output_path, **plot_options)


def save_selected_visibility_plots(
    selected_targets: pd.DataFrame,
    output_dir: str | Path,
    *,
    date_column: str = "observation_date",
    max_targets_per_plot: int = 20,
    overwrite: bool = False,
    verbose: bool = True,
    **plot_options,
) -> list[Path]:
    """Create source-aware final visibility plots with at most 20 targets each."""
    if date_column not in selected_targets:
        raise ValueError(f"Missing date column: {date_column}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.to_datetime(selected_targets[date_column], errors="coerce").dt.date.dropna().unique()
    paths = []
    for index, night in enumerate(sorted(dates), start=1):
        mask = pd.to_datetime(selected_targets[date_column], errors="coerce").dt.date.eq(night)
        nightly = selected_targets.loc[mask].copy()
        parts = _split_visibility_targets(nightly, max_targets_per_plot=max_targets_per_plot)
        expected = []
        for part_index, (source_group, part) in enumerate(parts, start=1):
            if len(parts) == 1:
                path = output_dir / f"{night.isoformat()}_selected_visibility.png"
                label = None
            else:
                path = output_dir / (
                    f"{night.isoformat()}_selected_part_{part_index:02d}_"
                    f"{_visibility_part_slug(source_group)}_visibility.png"
                )
                label = f"{source_group} — part {part_index}/{len(parts)}"
            expected.append((path, part, label))
        stale = [output_dir / f"{night.isoformat()}_selected_visibility.png"]
        stale.extend(output_dir.glob(f"{night.isoformat()}_selected_part_*_visibility.png"))
        for path in stale:
            if path not in {item[0] for item in expected}:
                path.unlink(missing_ok=True)
        for path, part, label in expected:
            paths.append(path)
            if path.exists() and not overwrite:
                continue
            plot_selected_visibility(part, night, path, part_label=label, **plot_options)
        if verbose:
            print(
                f"      selected visibility: {index}/{len(dates)} "
                f"({night}, {len(nightly)} targets, {len(parts)} plot(s))",
                flush=True,
            )
    return paths




def compile_visibility_plot_pages(
    visibility_plots: str | Path | list[str | Path],
    output_path: str | Path,
) -> Path:
    """Compile already-generated nightly visibility PNGs into a dated PDF.

    One PDF page is written per observing date. Every source-aware part for
    that date remains a separate panel, preserving the exact target labels,
    styles, and criteria of the PNG produced by ``save_nightly_visibility_plots``.
    """
    if isinstance(visibility_plots, (str, Path)):
        candidate = Path(visibility_plots)
        image_paths = (
            sorted(candidate.glob("*_visibility.png"))
            if candidate.is_dir() else [candidate]
        )
    else:
        image_paths = sorted(Path(path) for path in visibility_plots)
    image_paths = [
        path for path in image_paths
        if path.exists() and path.suffix.lower() == ".png" and len(path.name) >= 10
    ]
    dated_paths: dict[str, list[Path]] = {}
    for path in image_paths:
        date_text = path.name[:10]
        try:
            pd.Timestamp(date_text)
        except (TypeError, ValueError):
            continue
        dated_paths.setdefault(date_text, []).append(path)
    if not dated_paths:
        raise ValueError("No generated nightly visibility PNGs were found.")

    destination = Path(output_path).with_suffix(".pdf")
    destination.parent.mkdir(parents=True, exist_ok=True)
    from matplotlib.backends.backend_pdf import PdfPages

    with PdfPages(destination) as pdf:
        for date_text, paths in sorted(dated_paths.items()):
            panels = []
            for path in sorted(paths):
                image = plt.imread(path)
                height = 11.0 * image.shape[0] / image.shape[1]
                panels.append((path, image, height))
            page_height = max(5.0, 1.30 + sum(height for _, _, height in panels) + .32 * len(panels))
            fig = plt.figure(figsize=(12.0, page_height))
            fig.suptitle(
                "Nightly target-visibility plots", x=.04, y=.988,
                ha="left", fontsize=14, weight="bold",
            )
            fig.text(
                .04, .965,
                "Panels are the generated visibility plots; titles identify the target group and part.",
                ha="left", va="top", fontsize=8,
            )
            fig.text(
                .965, .983, f"Observing date\n{date_text}",
                ha="right", va="top", fontsize=10, weight="bold",
                bbox={"boxstyle": "round,pad=.35", "facecolor": "#edf4fb", "edgecolor": "#4d6d8a"},
            )
            cursor = page_height - 1.10
            for panel_index, (path, image, height) in enumerate(panels, start=1):
                stem = path.stem
                if "_part_" in stem:
                    suffix = stem.split("_part_", 1)[1].removesuffix("_visibility")
                    part_number, source = suffix.split("_", 1)
                    source = source.replace("mop-only", "MOP-only").replace(
                        "hsh-js-observed", "HSH/JS-observed"
                    ).replace("-", " ")
                    caption = f"{source} — part {int(part_number)}/{len(panels)}"
                else:
                    caption = "All selected targets"
                cursor -= .20
                fig.text(.04, cursor / page_height, caption, ha="left", va="top", fontsize=9, weight="bold")
                cursor -= height
                axis = fig.add_axes([.04, cursor / page_height, .92, height / page_height])
                axis.imshow(image)
                axis.axis("off")
                cursor -= .12
            pdf.savefig(fig, bbox_inches="tight", pad_inches=.05)
            plt.close(fig)
    return destination


def plot_visibility_sequence(
    selected_targets: pd.DataFrame,
    output_path: str | Path,
    *,
    date_column: str = "observation_date",
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    minimum_observable_minutes: float | None = None,
    time_step_minutes: int = 1,
    x_reference_every: int = 4,
    output_format: str | None = None,
    observing_windows=None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Path:
    """Stack chronologically ordered nightly visibility panels in one figure.

    ``start_date``/``end_date`` allow an empty selection to still produce a
    dated blank sequence, which is useful when no target passes the criterion.
    """
    if date_column not in selected_targets:
        raise ValueError(f"Missing date column: {date_column}")
    if x_reference_every < 1:
        raise ValueError("x_reference_every must be positive.")
    if time_step_minutes < 1:
        raise ValueError("time_step_minutes must be positive.")

    data = selected_targets.copy()
    data["_plot_date"] = pd.to_datetime(data[date_column], errors="coerce").dt.date
    data = data.dropna(subset=["_plot_date"])
    nights = sorted(data["_plot_date"].unique())
    if not nights and start_date is not None:
        final_date = end_date or start_date
        nights = list(pd.date_range(start_date, final_date, freq="D").date)
    if not nights:
        raise ValueError(
            f"No valid observing dates were found in {date_column!r}; "
            "pass start_date/end_date when the selection is empty."
        )

    output_path = Path(output_path)
    if output_format is None:
        output_format = output_path.suffix.lstrip(".") or "pdf"
    output_format = output_format.lower().lstrip(".")
    if output_path.suffix.lower() != f".{output_format}":
        output_path = output_path.with_suffix(f".{output_format}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    location, timezone, observatory_name = get_observatory(observatory)
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
    target_names = list(dict.fromkeys(data.get("Target", pd.Series(dtype=str)).astype(str)))
    palette = np.vstack([
        plt.get_cmap(name)(np.arange(20))
        for name in ("tab20", "tab20b", "tab20c")
    ])
    palette = palette[np.r_[np.arange(0, 60, 2), np.arange(1, 60, 2)]]
    target_colors = {name: palette[index % len(palette)] for index, name in enumerate(target_names)}

    timelines = [
        _night_times(night, timezone, time_step_minutes, observing_windows, location=location)[0]
        for night in nights
    ]
    sequence_hours = max(
        float((timeline[-1] - timeline[0]).total_seconds() / 3600)
        for timeline in timelines
    )
    reference_start = timelines[0][0]
    fig, axes = plt.subplots(
        len(nights), 1, figsize=(16, max(4.5, 2.25 * len(nights))),
        sharex=True, squeeze=False,
    )
    axes = axes[:, 0]
    twilight_colors = ("#fff4c2", "#ffe0a3", "#b8cbe3", "#7f96b3", "#eef1f6")
    twilight_labels = (
        "Day", "Civil twilight", "Nautical twilight",
        "Astronomical twilight", "Astronomical night",
    )

    for panel_index, (ax, night) in enumerate(zip(axes, nights)):
        local_times, times = _night_times(
            night, timezone, time_step_minutes, observing_windows, location=location,
        )
        allocated_time, _ = observing_window_mask(
            local_times, night, timezone, observing_windows,
        )
        elapsed_hours = np.asarray((local_times - local_times[0]).total_seconds() / 3600)
        frame = AltAz(obstime=times, location=location)
        sun = get_body("sun", times, location=location)
        sun_altitude = sun.transform_to(frame).alt.degree
        moon_altitude = get_body("moon", times, location=location).transform_to(frame).alt.degree
        twilight_masks = (
            sun_altitude >= 0,
            (sun_altitude < 0) & (sun_altitude >= -6),
            (sun_altitude < -6) & (sun_altitude >= -12),
            (sun_altitude < -12) & (sun_altitude >= -18),
            sun_altitude < -18,
        )
        for mask, color in zip(twilight_masks, twilight_colors):
            ax.fill_between(
                elapsed_hours, 0, 90, where=mask, color=color,
                alpha=.72, step="mid", zorder=0,
            )
        if not allocated_time.all():
            ax.fill_between(
                elapsed_hours, 0, 90, where=~allocated_time, color="0.20",
                alpha=.20, step="mid", zorder=1,
            )

        nightly = data.loc[data["_plot_date"].eq(night)].drop_duplicates("Target")
        for _, row in nightly.iterrows():
            if pd.isna(row.get("RA_deg")) or pd.isna(row.get("Dec_deg")):
                continue
            name = str(row.get("Target", "Target"))
            coord = SkyCoord(float(row["RA_deg"]) * u.deg, float(row["Dec_deg"]) * u.deg)
            altitude = coord.transform_to(frame).alt.degree
            ax.plot(elapsed_hours, altitude, color=target_colors[name], lw=1.15, zorder=3)

        ax.plot(elapsed_hours, moon_altitude, color="0.25", lw=1.1, ls="--", zorder=2)
        ax.axhline(minimum_altitude, color="firebrick", lw=.9, ls=":")
        ax.set(xlim=(0, sequence_hours), ylim=(0, 92), ylabel="Alt. [deg]")
        ax.set_yticks([0, 30, 60, 90])
        ax.grid(alpha=.22, zorder=1)
        ax.text(
            .012, .94, pd.Timestamp(night).strftime("%Y-%m-%d"),
            transform=ax.transAxes, ha="left", va="top", fontsize=9, weight="bold",
            bbox={"boxstyle": "round,pad=.25", "facecolor": "white",
                  "edgecolor": ".35", "alpha": .9},
        )
    major_ticks = np.arange(0, int(np.floor(sequence_hours)) + 1, 1)
    minor_ticks = np.arange(0, sequence_hours + .001, .25)
    major_labels = [
        (reference_start + pd.Timedelta(hours=float(hour))).strftime("%H:%M")
        for hour in major_ticks
    ]
    for panel_index, ax in enumerate(axes):
        show_reference = panel_index % x_reference_every == 0 or panel_index == len(nights) - 1
        ax.set_xticks(major_ticks)
        ax.set_xticks(minor_ticks, minor=True)
        ax.set_xticklabels(major_labels, ha="right")
        ax.xaxis.set_ticks_position("bottom")
        ax.tick_params(
            axis="x", which="major", labelbottom=show_reference,
            labelsize=8, pad=3, length=3,
        )
        ax.tick_params(axis="x", which="minor", length=2)
        if show_reference:
            for label in ax.get_xticklabels(which="major"):
                label.set_visible(True)

    target_handles = []
    for name in target_names:
        target_rows = data.loc[data["Target"].astype(str).eq(name)]
        target_row = target_rows.iloc[0]
        hours = (
            pd.to_numeric(target_rows["observable_minutes"], errors="coerce").max()
            if "observable_minutes" in target_rows else np.nan
        )
        target_handles.append(
            Line2D(
                [0], [0], color=target_colors[name], lw=1.4,
                label=_visibility_target_label(target_row, hours),
            )
        )
    reference_handles = [
        Line2D([0], [0], color="0.25", lw=1.1, ls="--", label="Moon"),
        Line2D([0], [0], color="firebrick", lw=.9, ls=":",
               label=f"Minimum altitude ({minimum_altitude:g}°)"),
    ]
    twilight_handles = [
        Patch(facecolor=color, alpha=.72, label=label)
        for color, label in zip(twilight_colors, twilight_labels)
    ]
    allocation_handles = [Patch(facecolor="0.20", alpha=.20, label="Outside allocated time")]
    handles = target_handles + reference_handles + twilight_handles + allocation_handles
    legend_columns = min(10, max(5, int(np.ceil(len(handles) / 6))))
    fig.legend(
        handles=handles, loc="lower center", ncol=legend_columns,
        fontsize=6.5, frameon=False, bbox_to_anchor=(.5, .002),
    )
    legend_rows = np.ceil(len(handles) / legend_columns)
    fig.suptitle(f"Selected-target visibility — {observatory_name}", y=.998, fontsize=13)
    if minimum_observable_minutes is not None:
        criteria = (
            f"1. At least {minimum_observable_minutes / 60:g} hours during allocated astronomical night "
            f"at altitude ≥ {minimum_altitude:g}° (approximately airmass < 1.5)."
        )
    else:
        criteria = "1. Targets were supplied as an observer-approved final selection."
    fig.text(
        .5, .979, criteria, ha="center", va="top", fontsize=12, linespacing=1.2,
        bbox={"boxstyle": "round,pad=.30", "facecolor": "white", "edgecolor": ".45", "alpha": .94},
    )
    fig.subplots_adjust(
        top=.956, bottom=min(.15, .018 + .014 * legend_rows),
        left=.065, right=.99, hspace=.17,
    )
    fig.savefig(
        output_path, format=output_format, dpi=170,
        bbox_inches="tight", pad_inches=.08,
    )
    plt.close(fig)
    return output_path

def build_visibility_selection(
    daily_targets: pd.DataFrame,
    start_date: str,
    end_date: str,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    observing_windows=None,
) -> pd.DataFrame:
    """Evaluate every supplied target on every requested night."""
    dates = pd.date_range(start_date, end_date, freq="D")
    date_values = daily_targets.get(
        "observation_date", pd.Series(index=daily_targets.index, dtype=str)
    ).astype(str)
    rows = []
    for night in dates:
        night_string = night.date().isoformat()
        nightly_targets = daily_targets.loc[date_values.eq(night_string)].copy()
        evaluated = select_nightly_targets(
            nightly_targets, night_string, observatory=observatory,
            minimum_altitude=minimum_altitude,
            minimum_observable_minutes=minimum_observable_minutes,
            time_step_minutes=time_step_minutes, observing_windows=observing_windows,
            return_all=True,
        )
        if "observation_date" not in evaluated:
            evaluated.insert(0, "observation_date", night_string)
        rows.append(evaluated)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def summarize_visibility_selection(selection: pd.DataFrame) -> pd.DataFrame:
    """Summarize nightly pass/fail visibility decisions once per target."""
    columns = [
        "Target", "mag_now", "visibility_nights_evaluated", "visibility_nights_selected",
        "passes_visibility_filter", "max_peak_altitude_deg", "max_observable_minutes",
        "max_observable_night_percent", "best_observation_date", "best_observable_start_local",
        "best_observable_end_local", "observing_windows", "best_rejection_reason",
    ]
    if selection.empty or "Target" not in selection:
        return pd.DataFrame(columns=columns)
    data = selection.copy()
    selected = data.get("selected_for_visibility", pd.Series(False, index=data.index)).astype(bool)
    data["_selected"] = selected
    numeric = ("peak_altitude_deg", "observable_minutes", "observable_night_percent")
    for column in numeric:
        data[column] = pd.to_numeric(data.get(column), errors="coerce")
    summary = data.groupby("Target", sort=False).agg(
        visibility_nights_evaluated=("observation_date", "nunique"),
        visibility_nights_selected=("_selected", "sum"),
        max_peak_altitude_deg=("peak_altitude_deg", "max"),
        max_observable_minutes=("observable_minutes", "max"),
        max_observable_night_percent=("observable_night_percent", "max"),
        observing_windows=("observing_windows", "first"),
    ).reset_index()
    if "mag_now" in data:
        magnitudes = data.groupby("Target", sort=False)["mag_now"].first()
        summary["mag_now"] = summary["Target"].map(magnitudes)
    else:
        summary["mag_now"] = np.nan
    best_rows = data.loc[
        data.groupby("Target", sort=False)["observable_minutes"].idxmax(),
        ["Target", "observation_date", "observable_start_local", "observable_end_local"],
    ].rename(columns={
        "observation_date": "best_observation_date",
        "observable_start_local": "best_observable_start_local",
        "observable_end_local": "best_observable_end_local",
    })
    summary = summary.merge(best_rows, on="Target", how="left")
    summary["passes_visibility_filter"] = summary["visibility_nights_selected"].gt(0)
    rejected = data.loc[~data["_selected"], ["Target", "visibility_rejection_reasons"]]
    reasons = rejected.groupby("Target", sort=False)["visibility_rejection_reasons"].first()
    summary["best_rejection_reason"] = summary["Target"].map(reasons).fillna("")
    return summary[columns]


def save_nightly_visibility_plots(
    daily_targets: pd.DataFrame,
    start_date: str,
    end_date: str,
    output_dir: str | Path,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    observing_windows=None,
    target_scope: str = "mop_daily",
    selection: pd.DataFrame | None = None,
    max_targets_per_plot: int = 20,
    overwrite: bool = False,
    verbose: bool = True,
) -> list[Path]:
    """Create source-aware filtered visibility plots with at most 20 targets each."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    visibility_config = {
        "version": VISIBILITY_PLOT_VERSION,
        "minimum_altitude": float(minimum_altitude),
        "minimum_observable_minutes": float(minimum_observable_minutes),
        "time_step_minutes": int(time_step_minutes),
        "observing_windows": observing_windows,
        "target_scope": target_scope,
        "max_targets_per_plot": int(max_targets_per_plot),
    }
    config_path = output_dir / "visibility_plot_config.json"
    try:
        previous_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        previous_config = None
    config_changed = previous_config != json.loads(json.dumps(visibility_config, default=str))
    effective_overwrite = overwrite or config_changed
    dates = pd.date_range(start_date, end_date, freq="D")
    selection = selection.copy() if selection is not None else build_visibility_selection(
        daily_targets, start_date, end_date, observatory=observatory,
        minimum_altitude=minimum_altitude,
        minimum_observable_minutes=minimum_observable_minutes,
        time_step_minutes=time_step_minutes, observing_windows=observing_windows,
    )
    paths = []
    for index, night in enumerate(dates, start=1):
        night_string = night.date().isoformat()
        evaluated = selection.loc[
            selection.get("observation_date", pd.Series(dtype=str)).astype(str).eq(night_string)
        ].copy()
        selected = evaluated.loc[evaluated["selected_for_visibility"].astype(bool)].copy()
        parts = _split_visibility_targets(
            selected, max_targets_per_plot=max_targets_per_plot
        )
        expected = []
        for part_index, (source_group, part) in enumerate(parts, start=1):
            if len(parts) == 1:
                path = output_dir / f"{night_string}_visibility.png"
                label = None
            else:
                path = output_dir / (
                    f"{night_string}_part_{part_index:02d}_"
                    f"{_visibility_part_slug(source_group)}_visibility.png"
                )
                label = f"{source_group} — part {part_index}/{len(parts)}"
            expected.append((path, part, label))
        stale = [output_dir / f"{night_string}_visibility.png"]
        stale.extend(output_dir.glob(f"{night_string}_part_*_visibility.png"))
        for path in stale:
            if path not in {item[0] for item in expected}:
                path.unlink(missing_ok=True)
        for path, part, label in expected:
            paths.append(path)
            if path.exists() and not effective_overwrite:
                continue
            plot_nightly_visibility(
                part, night_string, path, observatory=observatory,
                minimum_altitude=minimum_altitude, time_step_minutes=time_step_minutes,
                observing_windows=observing_windows, part_label=label,
            )
        if verbose:
            print(
                f"      visibility: {index}/{len(dates)} "
                f"({night_string}, {len(selected)}/{len(evaluated)} selected, "
                f"{len(parts)} plot(s))", flush=True,
            )
    if not selection.empty:
        selection.to_csv(output_dir / "visibility_selection.csv", index=False)
    config_path.write_text(json.dumps(visibility_config, indent=2, default=str), encoding="utf-8")
    return paths
