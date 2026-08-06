"""Local nightly visibility plots for MOP target-selection runs."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body
from astropy.time import Time
from astropy.utils import iers


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


def _night_times(night: str | date, timezone: ZoneInfo, step_minutes: int) -> tuple[pd.DatetimeIndex, Time]:
    night_date = pd.Timestamp(night).date()
    start = pd.Timestamp(f"{night_date} 18:00", tz=timezone)
    local_times = pd.date_range(start, start + pd.Timedelta(hours=12), freq=f"{step_minutes}min")
    return local_times, Time(local_times.to_pydatetime())


def evaluate_nightly_visibility(
    targets: pd.DataFrame,
    night: str | date,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 30.0,
    time_step_minutes: int = 1,
) -> pd.DataFrame:
    """Add per-target visibility metrics for one astronomical night."""
    if time_step_minutes < 1:
        raise ValueError("time_step_minutes must be positive.")
    location, timezone, _ = get_observatory(observatory)
    _, times = _night_times(night, timezone, time_step_minutes)
    iers.conf.auto_download = False
    frame = AltAz(obstime=times, location=location)
    sun_altitude = get_body("sun", times, location=location).transform_to(frame).alt.degree
    astronomical_night = sun_altitude < -18
    night_samples = int(astronomical_night.sum())

    evaluated = targets.drop_duplicates("Target").copy() if "Target" in targets else targets.copy()
    evaluated["RA_deg"] = pd.to_numeric(evaluated.get("RA_deg"), errors="coerce")
    evaluated["Dec_deg"] = pd.to_numeric(evaluated.get("Dec_deg"), errors="coerce")
    peak_altitudes = []
    night_fractions = []
    observable_minutes = []
    for _, row in evaluated.iterrows():
        if pd.isna(row.get("RA_deg")) or pd.isna(row.get("Dec_deg")) or night_samples == 0:
            peak_altitudes.append(np.nan)
            night_fractions.append(0.0)
            observable_minutes.append(0.0)
            continue
        coord = SkyCoord(float(row["RA_deg"]) * u.deg, float(row["Dec_deg"]) * u.deg)
        altitude = coord.transform_to(frame).alt.degree
        night_altitude = altitude[astronomical_night]
        visible = night_altitude >= minimum_altitude
        peak_altitudes.append(float(np.nanmax(night_altitude)))
        night_fractions.append(float(visible.mean()))
        observable_minutes.append(float(visible.sum() * time_step_minutes))

    evaluated["peak_altitude_deg"] = peak_altitudes
    evaluated["observable_night_fraction"] = night_fractions
    evaluated["observable_night_percent"] = 100 * evaluated["observable_night_fraction"]
    evaluated["observable_minutes"] = observable_minutes
    return evaluated


def select_nightly_targets(
    targets: pd.DataFrame,
    night: str | date,
    *,
    observatory: str | dict = "El Leoncito",
    minimum_altitude: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    time_step_minutes: int = 1,
    return_all: bool = False,
) -> pd.DataFrame:
    """Select targets observable above a minimum altitude for a minimum time."""
    if minimum_observable_minutes <= 0:
        raise ValueError("minimum_observable_minutes must be positive.")
    evaluated = evaluate_nightly_visibility(
        targets, night, observatory=observatory, minimum_altitude=minimum_altitude,
        time_step_minutes=time_step_minutes,
    )
    selected = evaluated["observable_minutes"].ge(minimum_observable_minutes)
    evaluated["selected_for_visibility"] = selected
    evaluated["visibility_rejection_reasons"] = np.where(
        selected,
        "",
        f"fewer than {minimum_observable_minutes:g} observable minutes above "
        f"{minimum_altitude:g} degrees",
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
) -> None:
    """Plot altitude, airmass, twilight, and Moon altitude for one night."""
    if time_step_minutes < 1:
        raise ValueError("time_step_minutes must be positive.")
    location, timezone, observatory_name = get_observatory(observatory)
    local_times, times = _night_times(night, timezone, time_step_minutes)
    iers.conf.auto_download = False
    frame = AltAz(obstime=times, location=location)
    sun = get_body("sun", times, location=location)
    sun_altitude = sun.transform_to(frame).alt.degree
    moon = get_body("moon", times, location=location)
    moon_altitude = moon.transform_to(frame).alt.degree
    elongation = sun.separation(moon).radian
    moon_illumination = float(np.nanmean((1 - np.cos(elongation)) / 2) * 100)

    unique = targets.drop_duplicates("Target").copy() if "Target" in targets else targets.copy()
    unique["RA_deg"] = pd.to_numeric(unique.get("RA_deg"), errors="coerce")
    unique["Dec_deg"] = pd.to_numeric(unique.get("Dec_deg"), errors="coerce")
    unique = unique.dropna(subset=["RA_deg", "Dec_deg"])
    figure_height = max(7.0, 6.4 + .035 * len(unique))
    fig, ax = plt.subplots(figsize=(16, figure_height))

    twilight = [
        (sun_altitude >= 0, "#fff4c2", "Day"),
        ((sun_altitude < 0) & (sun_altitude >= -6), "#ffe0a3", "Civil twilight"),
        ((sun_altitude < -6) & (sun_altitude >= -12), "#b8cbe3", "Nautical twilight"),
        ((sun_altitude < -12) & (sun_altitude >= -18), "#7f96b3", "Astronomical twilight"),
        (sun_altitude < -18, "#eef1f6", "Astronomical night"),
    ]
    for mask, color, _ in twilight:
        ax.fill_between(local_times, 0, 90, where=mask, color=color, alpha=.72, step="mid", zorder=0)

    palette = np.vstack([
        plt.get_cmap(name)(np.arange(20))
        for name in ("tab20", "tab20b", "tab20c")
    ])
    palette = palette[np.r_[np.arange(0, 60, 2), np.arange(1, 60, 2)]]
    line_styles = ("-", "--", "-.")
    for target_index, (_, row) in enumerate(unique.iterrows()):
        color = palette[target_index % len(palette)]
        line_style = line_styles[(target_index // len(palette)) % len(line_styles)]
        coord = SkyCoord(float(row["RA_deg"]) * u.deg, float(row["Dec_deg"]) * u.deg)
        altitude = coord.transform_to(frame).alt.degree
        ax.plot(local_times, altitude, lw=1.35, ls=line_style, color=color,
                label=str(row.get("Target", "Target")), zorder=3)

    ax.plot(local_times, moon_altitude, color="0.25", lw=1.4, ls="--",
            label=f"Moon ({moon_illumination:.0f}% illuminated)", zorder=2)
    ax.axhline(minimum_altitude, color="firebrick", lw=1, ls=":",
               label=f"Minimum altitude ({minimum_altitude:g}°)")
    ax.set_ylim(0, 92)
    ax.set_xlim(local_times[0], local_times[-1])
    ax.margins(x=0)
    ax.set_ylabel("Altitude [deg]")
    ax.set_xlabel(f"Local time [{timezone.key}]")
    ax.set_title(f"Target visibility — {observatory_name} — {pd.Timestamp(night).date()}")
    ax.grid(alpha=.25, zorder=1)
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=timezone))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone))
    ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[0, 15, 30, 45], tz=timezone))
    ax.tick_params(axis="x")

    airmass_axis = ax.twinx()
    altitude_ticks = np.array([90, 60, 45, 30, 20], dtype=float)
    airmass_values = 1 / np.sin(np.deg2rad(altitude_ticks))
    airmass_axis.set_ylim(ax.get_ylim())
    airmass_axis.set_yticks(altitude_ticks)
    airmass_axis.set_yticklabels([f"{value:.2f}" for value in airmass_values])
    airmass_axis.set_ylabel("Approximate airmass")

    twilight_handles = [Patch(facecolor=color, alpha=.72, label=label) for _, color, label in twilight]
    handles, labels = ax.get_legend_handles_labels()
    legend_items = len(handles) + len(twilight_handles)
    ncol = min(8, max(4, int(np.ceil(legend_items / 8))))
    ax.legend(handles + twilight_handles, labels + [item.get_label() for item in twilight_handles],
              loc="upper center", bbox_to_anchor=(.5, -.18), ncol=ncol, fontsize=7, frameon=False)
    legend_rows = np.ceil(legend_items / ncol)
    fig.subplots_adjust(bottom=min(.38, .17 + .017 * legend_rows))
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
    overwrite: bool = False,
    verbose: bool = True,
    **plot_options,
) -> list[Path]:
    """Create final visibility plots for an observer-selected table."""
    if date_column not in selected_targets:
        raise ValueError(f"Missing date column: {date_column}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.to_datetime(selected_targets[date_column], errors="coerce").dt.date.dropna().unique()
    paths = []
    for index, night in enumerate(sorted(dates), start=1):
        path = output_dir / f"{night.isoformat()}_selected_visibility.png"
        paths.append(path)
        if path.exists() and not overwrite:
            continue
        mask = pd.to_datetime(selected_targets[date_column], errors="coerce").dt.date.eq(night)
        nightly = selected_targets.loc[mask].copy()
        plot_selected_visibility(nightly, night, path, **plot_options)
        if verbose:
            print(f"      selected visibility: {index}/{len(dates)} ({night}, {len(nightly)} targets)", flush=True)
    return paths



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
) -> Path:
    """Stack chronologically ordered nightly visibility panels in one figure."""
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
    if not nights:
        raise ValueError("No valid observing dates were found.")

    output_path = Path(output_path)
    if output_format is None:
        output_format = output_path.suffix.lstrip(".") or "pdf"
    output_format = output_format.lower().lstrip(".")
    if output_path.suffix.lower() != f".{output_format}":
        output_path = output_path.with_suffix(f".{output_format}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    location, timezone, observatory_name = get_observatory(observatory)
    iers.conf.auto_download = False
    target_names = list(dict.fromkeys(data.get("Target", pd.Series(dtype=str)).astype(str)))
    palette = np.vstack([
        plt.get_cmap(name)(np.arange(20))
        for name in ("tab20", "tab20b", "tab20c")
    ])
    palette = palette[np.r_[np.arange(0, 60, 2), np.arange(1, 60, 2)]]
    target_colors = {name: palette[index % len(palette)] for index, name in enumerate(target_names)}

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
        local_times, times = _night_times(night, timezone, time_step_minutes)
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
        ax.set(xlim=(0, 12), ylim=(0, 92), ylabel="Alt. [deg]")
        ax.set_yticks([0, 30, 60, 90])
        ax.grid(alpha=.22, zorder=1)
        ax.text(
            .012, .94, pd.Timestamp(night).strftime("%Y-%m-%d"),
            transform=ax.transAxes, ha="left", va="top", fontsize=9, weight="bold",
            bbox={"boxstyle": "round,pad=.25", "facecolor": "white",
                  "edgecolor": ".35", "alpha": .9},
        )
    major_ticks = np.arange(0, 13, 1)
    minor_ticks = np.arange(0, 12.01, .25)
    major_labels = [f"{(18 + hour) % 24:02d}:00" for hour in major_ticks]
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

    target_handles = [
        Line2D([0], [0], color=target_colors[name], lw=1.4, label=name)
        for name in target_names
    ]
    reference_handles = [
        Line2D([0], [0], color="0.25", lw=1.1, ls="--", label="Moon"),
        Line2D([0], [0], color="firebrick", lw=.9, ls=":",
               label=f"Minimum altitude ({minimum_altitude:g}°)"),
    ]
    twilight_handles = [
        Patch(facecolor=color, alpha=.72, label=label)
        for color, label in zip(twilight_colors, twilight_labels)
    ]
    handles = target_handles + reference_handles + twilight_handles
    legend_columns = min(10, max(5, int(np.ceil(len(handles) / 6))))
    fig.legend(
        handles=handles, loc="lower center", ncol=legend_columns,
        fontsize=6.5, frameon=False, bbox_to_anchor=(.5, .002),
    )
    legend_rows = np.ceil(len(handles) / legend_columns)
    fig.suptitle(f"Selected-target visibility — {observatory_name}", y=.998, fontsize=13)
    if minimum_observable_minutes is not None:
        criteria = (
            f"1. At least {minimum_observable_minutes / 60:g} hours during astronomical night "
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
    overwrite: bool = False,
    verbose: bool = True,
) -> list[Path]:
    """Create one automatically filtered visibility plot per requested date."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(start_date, end_date, freq="D")
    date_values = daily_targets.get(
        "observation_date", pd.Series(index=daily_targets.index, dtype=str)
    ).astype(str)
    paths = []
    selection_rows = []
    for index, night in enumerate(dates, start=1):
        night_string = night.date().isoformat()
        path = output_dir / f"{night_string}_visibility.png"
        paths.append(path)
        nightly_targets = daily_targets.loc[date_values.eq(night_string)].copy()
        evaluated = select_nightly_targets(
            nightly_targets, night_string, observatory=observatory,
            minimum_altitude=minimum_altitude,
            minimum_observable_minutes=minimum_observable_minutes,
            time_step_minutes=time_step_minutes,
            return_all=True,
        )
        evaluated.insert(0, "observation_date", night_string) if "observation_date" not in evaluated else None
        selection_rows.append(evaluated)
        selected = evaluated.loc[evaluated["selected_for_visibility"]].copy()
        if not path.exists() or overwrite:
            plot_nightly_visibility(
                selected, night_string, path, observatory=observatory,
                minimum_altitude=minimum_altitude, time_step_minutes=time_step_minutes,
            )
        if verbose:
            print(
                f"      visibility: {index}/{len(dates)} "
                f"({night_string}, {len(selected)}/{len(evaluated)} selected)", flush=True,
            )
    if selection_rows:
        pd.concat(selection_rows, ignore_index=True).to_csv(
            output_dir / "visibility_selection.csv", index=False
        )
    return paths
