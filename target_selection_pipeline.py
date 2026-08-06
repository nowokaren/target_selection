"""Configurable MOP + Rubin pipeline for notebooks and scripts.

Rubin modules are not imported at module load time. MOP, TAP, and Butler
connections are created lazily and can also be injected.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from time import perf_counter
from pathlib import Path
from typing import Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm.std import tqdm

from data_release_config import DataReleaseConfig, get_data_release


CACHE_VERSION = 3
TARGET_REPORT_VERSION = 6


def _safe_name(value: object) -> str:
    """Convert a target name into a safe directory/file component."""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return text or "unknown_target"


def create_run_structure(
    root_dir: str | Path, start_date: str, end_date: str,
    data_release: str | DataReleaseConfig = "DP2",
) -> dict[str, Path]:
    """Create and return the folders used by one analysis run."""
    label = start_date if start_date == end_date else f"{start_date}_to_{end_date}"
    release = get_data_release(data_release)
    base_dir = Path(root_dir) if release.name == "DP2" else Path(root_dir) / _safe_name(release.name)
    run_dir = base_dir / label
    paths = {
        "run": run_dir,
        "tables": run_dir / "tables",
        "sky_plots": run_dir / "sky_plots",
        "visibility_plots": run_dir / "visibility_plots",
        "targets": run_dir / "targets",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def query_release_coverage(
    targets: pd.DataFrame,
    tap_service,
    search_radius: float = 11 / 60,
    max_workers: int = 4,
    data_release: str | DataReleaseConfig = "DP2",
) -> pd.DataFrame:
    """Query visit coverage using release-specific TAP names."""
    release = get_data_release(data_release)
    columns = ["Target", "visitId", "expMidptMJD", "band", "detector"]

    def query_one(values):
        name, ra, dec = values
        query = f"""
            SELECT {release.visit_select("vd")}
            FROM {release.tap_visit_table} AS vd
            WHERE CONTAINS(
                POINT('ICRS', vd.{release.tap_ra}, vd.{release.tap_dec}),
                CIRCLE('ICRS', {ra}, {dec}, {search_radius})
            ) = 1
        """
        job = tap_service.submit_job(query)
        job.run()
        job.wait(phases=["COMPLETED", "ERROR"])
        if job.phase == "ERROR":
            job.raise_if_error()
        data = job.fetch_result().to_table().to_pandas()
        if not data.empty:
            data["Target"] = name
        return data

    values = [
        (row.Target, float(row.RA_deg), float(row.Dec_deg))
        for row in targets[["Target", "RA_deg", "Dec_deg"]].itertuples(index=False)
    ]
    workers = max(1, min(int(max_workers), len(values))) if values else 1
    if workers == 1:
        results = [query_one(value) for value in values]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(query_one, values))
    records = [data for data in results if not data.empty]
    return pd.concat(records, ignore_index=True) if records else pd.DataFrame(columns=columns)


def query_release_visit_centers(
    tap_service,
    data_release: str | DataReleaseConfig = "DP2",
) -> pd.DataFrame:
    """Return one approximate sky position per visit for coverage maps.

    Detector centers are averaged server-side, reducing the release-wide table
    to one row per visit before transfer. The result is intended for coarse
    coverage visualization, not exact footprint calculations.
    """
    release = get_data_release(data_release)
    query = f"""
        SELECT
            vd.{release.visit_columns['visitId']} AS visitId,
            AVG(vd.{release.tap_ra}) AS ra,
            AVG(vd.{release.tap_dec}) AS dec
        FROM {release.tap_visit_table} AS vd
        GROUP BY vd.{release.visit_columns['visitId']}
    """
    job = tap_service.submit_job(query)
    job.run()
    job.wait(phases=["COMPLETED", "ERROR"])
    if job.phase == "ERROR":
        job.raise_if_error()
    result = job.fetch_result().to_table().to_pandas()
    result.columns = [str(column).lower() for column in result.columns]
    expected = ["visitid", "ra", "dec"]
    missing = [column for column in expected if column not in result]
    if missing:
        raise ValueError(f"Visit-center query is missing columns: {missing}")
    return result[expected].rename(columns={"visitid": "visitId"})


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
    result["priority"] = False
    for column in ("tap_priority", "tap_priority_longte", "mop_tap_priority", "mop_tap_priority_longte"):
        if column in targets:
            values = targets[column].astype(str).str.strip().str.casefold()
            result["priority"] |= ~values.isin({"", "0", "0.0", "false", "nan", "none"})

    direct_columns = {
        "mag_now": "mag_now", "Min airmass": "min_airmass",
        "n_visible_nights": "visible_nights", "coverage_n_visits": "release_n_visits",
        "coverage_n_calexps": "release_n_calexps", "coverage_bands": "release_bands",
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
        "Target", "priority", "release_n_visits", *[f"n_visits_{c.removeprefix('coverage_n_visits_')}" for c in band_columns],
        "mop_photometry_points", "t_E_days", "t_0_HJD", "u_0", "mag_now", "min_airmass", "visible_nights",
    ]
    display = result[display_columns].copy()
    integer_columns = {"release_n_visits", "mop_photometry_points", "visible_nights", *[c for c in display if c.startswith("n_visits_")]}
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
    labels = {
        "Target": "Target", "priority": "Priority", "release_n_visits": "Visits total",
        "mop_photometry_points": "MOP phot.", "t_E_days": "t_E [days]",
        "t_0_HJD": "t_0 [HJD]", "u_0": "u_0", "mag_now": "mag now",
        "min_airmass": "min airmass", "visible_nights": "visible nights",
    }
    labels.update({column: column.replace("n_visits_", "visits " ) for column in display.columns if column.startswith("n_visits_")})
    display = display.rename(columns=labels)

    n_visible = len(result)
    n_matched = int(result["matched_release"].sum())
    n_photometry = int(result["mop_photometry_points"].gt(0).sum())
    n_matched_photometry = int(result["matched_with_photometry"].sum())
    text_lengths = [
        min(26, max(6, len(str(column)) + 1, *(len(str(value)) + 1 for value in display[column])))
        for column in display.columns
    ]
    width_units = np.asarray(text_lengths, dtype=float)
    column_widths = (width_units / width_units.sum()).tolist()
    fig_width = max(8.5, min(20, width_units.sum() * .068))
    fig_height = max(5, 1.25 + .285 * (len(display) + 1))
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
        cellText=display.values, colLabels=display.columns, cellLoc="center", colLoc="center",
        colWidths=column_widths, loc="center", bbox=[0, 0, 1, 1],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    for (row, _), cell in table.get_celld().items():
        cell.PAD = .018
        if row == 0:
            cell.set_facecolor("#d9eaf7")
            cell.set_text_props(weight="bold")
        elif row % 2 == 0:
            cell.set_facecolor("#f5f5f5")
    fig.savefig(png_path, dpi=180, bbox_inches="tight", pad_inches=.08)
    plt.close(fig)
    return result


def plot_sky_dual_metric(
    targets: pd.DataFrame,
    left_metric: str,
    right_metric: str,
    output_path: str | Path,
    title: str | None = None,
    left_label: str | None = None,
    right_label: str | None = None,
    projection: str = "galactic",
    show_regions: bool = True,
    bulge_zoom: tuple[float, float] | None = None,
    marker_encoding: str = "split_color",
    coverage_background: pd.DataFrame | None = None,
    coverage_resolution: int = 19,
    coverage_label: str = "Data Release visits",
) -> None:
    """Plot target metrics with an optional low-resolution coverage layer.

    ``marker_encoding`` accepts ``"split_color"`` (two colored halves) or
    ``"color_size"`` (left metric as color, right metric as marker size).
    ``coverage_background`` must contain visit-center ``ra`` and ``dec``
    columns and is rendered as a muted coarse histogram.
    """
    if marker_encoding not in {"split_color", "color_size"}:
        raise ValueError(
            "marker_encoding must be 'split_color' or 'color_size'."
        )
    if int(coverage_resolution) < 4:
        raise ValueError("coverage_resolution must be at least 4.")
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    from matplotlib.path import Path as MarkerPath
    from matplotlib.colors import Normalize

    data = targets.dropna(subset=["RA_deg", "Dec_deg"]).reset_index(drop=True)
    data["_reference_number"] = np.arange(1, len(data) + 1)
    left_values = pd.to_numeric(data[left_metric], errors="coerce")
    right_values = pd.to_numeric(data[right_metric], errors="coerce").fillna(0)
    left_scale_array = left_values.to_numpy(dtype=float)
    right_scale_array = right_values.to_numpy(dtype=float)

    coords = SkyCoord(data["RA_deg"].to_numpy() * u.deg, data["Dec_deg"].to_numpy() * u.deg, frame="icrs")
    use_galactic = projection.casefold() == "galactic"
    if use_galactic:
        galactic = coords.galactic
        wrapped_longitude = galactic.l.wrap_at(180 * u.deg)
        if bulge_zoom is not None:
            lon_limit, lat_limit = bulge_zoom
            longitude_all = wrapped_longitude.degree
            latitude_all = galactic.b.degree
            inside = (np.abs(longitude_all) <= lon_limit) & (np.abs(latitude_all) <= lat_limit)
            data = data.loc[inside].reset_index(drop=True)
            left_values = left_values.loc[inside].reset_index(drop=True)
            right_values = right_values.loc[inside].reset_index(drop=True)
            longitude = np.asarray(longitude_all)[inside]
            latitude = np.asarray(latitude_all)[inside]
            xlabel, ylabel = "Galactic longitude l [deg]", "Galactic latitude b [deg]"
        else:
            longitude = wrapped_longitude.radian
            latitude = galactic.b.radian
            xlabel, ylabel = "Galactic longitude l", "Galactic latitude b"
    else:
        longitude = coords.ra.wrap_at(180 * u.deg).radian
        latitude = coords.dec.radian
        xlabel, ylabel = "RA", "Dec"

    background_longitude = background_latitude = None
    if coverage_background is not None and not coverage_background.empty:
        required = {"ra", "dec"}
        if not required.issubset(coverage_background.columns):
            raise ValueError("coverage_background must contain ra and dec columns.")
        background = coverage_background.copy()
        background["ra"] = pd.to_numeric(background["ra"], errors="coerce")
        background["dec"] = pd.to_numeric(background["dec"], errors="coerce")
        background = background.dropna(subset=["ra", "dec"])
        background_coords = SkyCoord(
            background["ra"].to_numpy() * u.deg,
            background["dec"].to_numpy() * u.deg, frame="icrs",
        )
        if use_galactic:
            background_galactic = background_coords.galactic
            background_lon_deg = background_galactic.l.wrap_at(180 * u.deg).degree
            background_lat_deg = background_galactic.b.degree
            if bulge_zoom is not None:
                inside_background = (
                    (np.abs(background_lon_deg) <= bulge_zoom[0])
                    & (np.abs(background_lat_deg) <= bulge_zoom[1])
                )
                background_longitude = background_lon_deg[inside_background]
                background_latitude = background_lat_deg[inside_background]
            else:
                background_longitude = np.deg2rad(background_lon_deg)
                background_latitude = np.deg2rad(background_lat_deg)
        else:
            background_longitude = background_coords.ra.wrap_at(180 * u.deg).radian
            background_latitude = background_coords.dec.radian

    def semicircle_marker(side: str) -> MarkerPath:
        start, stop = ((np.pi / 2, 3 * np.pi / 2) if side == "left" else (-np.pi / 2, np.pi / 2))
        angles = np.linspace(start, stop, 32)
        arc = np.column_stack([np.cos(angles), np.sin(angles)])
        vertices = np.vstack([[0, 0], arc, [0, 0]])
        codes = [MarkerPath.MOVETO] + [MarkerPath.LINETO] * len(arc) + [MarkerPath.CLOSEPOLY]
        return MarkerPath(vertices, codes)

    fig = plt.figure(figsize=(15, 6.7))
    map_projection = None if bulge_zoom is not None else "mollweide"
    has_coverage_background = coverage_background is not None and not coverage_background.empty
    map_height = .70 if has_coverage_background else .74
    ax = fig.add_axes([.025, .045, .69, map_height], projection=map_projection)
    left_colorbar_ax = fig.add_axes([.07, .888, .285, .022])
    right_colorbar_ax = fig.add_axes([.385, .888, .285, .022])
    legend_ax = fig.add_axes([.72, .035, .275, .86])
    legend_ax.axis("off")
    fig.text(.37, .975, title or "MOP + Rubin targets", ha="center", va="top", fontsize=12)

    if use_galactic and show_regions:
        plane_limit = 8 if bulge_zoom is not None else np.deg2rad(8)
        ax.axhspan(-plane_limit, plane_limit, color="gold", alpha=0.10, zorder=0)
        ax.scatter([0], [0], marker="x", color="darkorange", s=70, linewidths=1.3, zorder=2)
        ax.text(0.02, 0.08, "Galactic bulge", transform=ax.transAxes, color="darkorange", fontsize=9)
        if bulge_zoom is None:
            for lon, lat, label in [(280, -33, "LMC"), (303, -44, "SMC")]:
                x = np.deg2rad(((lon + 180) % 360) - 180)
                y = np.deg2rad(lat)
                ax.scatter([x], [y], marker="s", facecolors="none", edgecolors="deepskyblue", s=60, zorder=2)
                ax.text(x, y, f"  {label}", color="deepskyblue", fontsize=8, va="center")

    coverage_mappable = None
    if background_longitude is not None and len(background_longitude):
        resolution = int(coverage_resolution)
        # Match the approximate cell count of HEALPix: 12 * nside**2.
        full_lon_bins = max(8, int(round(np.sqrt(24) * resolution)))
        full_lat_bins = max(4, int(round(np.sqrt(6) * resolution)))
        if bulge_zoom is None:
            grid_size = (full_lon_bins, full_lat_bins)
            grid_extent = (-np.pi, np.pi, -np.pi / 2, np.pi / 2)
        else:
            grid_size = (
                max(4, int(round(full_lon_bins * 2 * bulge_zoom[0] / 360))),
                max(3, int(round(full_lat_bins * 2 * bulge_zoom[1] / 180))),
            )
            grid_extent = (-bulge_zoom[0], bulge_zoom[0], -bulge_zoom[1], bulge_zoom[1])
        from matplotlib.colors import LinearSegmentedColormap, LogNorm
        muted_greys = LinearSegmentedColormap.from_list(
            "muted_greys", plt.get_cmap("Greys")(np.linspace(.38, .95, 256)),
        )
        coverage_mappable = ax.hexbin(
            background_longitude, background_latitude,
            gridsize=grid_size, extent=grid_extent, mincnt=1,
            cmap=muted_greys, norm=LogNorm(vmin=1),
            alpha=.65, linewidths=0, zorder=.25,
        )

    marker_size = 34
    left_array = left_values.to_numpy(dtype=float)
    right_array = right_values.to_numpy(dtype=float)
    left_cmap = plt.get_cmap("viridis").copy()
    right_cmap = plt.get_cmap("plasma").copy()

    def positive_norm(values):
        positive = values[np.isfinite(values) & (values > 0)]
        if positive.size == 0:
            return Normalize(vmin=0.5, vmax=1.0)
        vmin, vmax = float(positive.min()), float(positive.max())
        if np.isclose(vmin, vmax):
            delta = max(abs(vmin) * .01, .01)
            vmin, vmax = vmin - delta, vmax + delta
        return Normalize(vmin=vmin, vmax=vmax)

    left_norm = positive_norm(left_scale_array)
    right_norm = positive_norm(right_scale_array)
    left_marker = semicircle_marker("left")
    right_marker = semicircle_marker("right")

    both_zero = (
        np.isfinite(left_array) & np.isfinite(right_array)
        & (left_array == 0) & (right_array == 0)
    )

    if marker_encoding == "split_color":
        def draw_half(values, marker, cmap, norm):
            positive = np.isfinite(values) & (values > 0) & ~both_zero
            zero = np.isfinite(values) & (values == 0) & ~both_zero
            missing = ~np.isfinite(values) & ~both_zero
            ax.scatter(longitude[positive], latitude[positive], c=values[positive], cmap=cmap, norm=norm,
                       marker=marker, s=marker_size, edgecolor="none", zorder=3)
            ax.scatter(longitude[zero], latitude[zero], color="black", marker=marker,
                       s=marker_size, edgecolor="none", zorder=3)
            ax.scatter(longitude[missing], latitude[missing], color="lightgray", marker=marker,
                       s=marker_size, edgecolor="none", zorder=3)

        draw_half(left_array, left_marker, left_cmap, left_norm)
        draw_half(right_array, right_marker, right_cmap, right_norm)
        regular = ~both_zero
        ax.scatter(longitude[regular], latitude[regular], s=marker_size, facecolors="none",
                   edgecolors="black", linewidths=.35, zorder=3.2)
    else:
        finite_visits = np.where(np.isfinite(right_array) & (right_array > 0), right_array, 0)
        max_visits = float(finite_visits.max()) if finite_visits.size else 0.0
        scaled = np.sqrt(finite_visits / max_visits) if max_visits > 0 else np.zeros_like(finite_visits)
        sizes = 18 + 72 * scaled
        positive_color = np.isfinite(left_array) & (left_array > 0) & ~both_zero
        zero_color = np.isfinite(left_array) & (left_array == 0) & ~both_zero
        missing_color = ~np.isfinite(left_array) & ~both_zero
        ax.scatter(longitude[positive_color], latitude[positive_color], c=left_array[positive_color],
                   cmap=left_cmap, norm=left_norm, s=sizes[positive_color],
                   edgecolors="black", linewidths=.45, zorder=3)
        ax.scatter(longitude[zero_color], latitude[zero_color], color="black",
                   s=sizes[zero_color], edgecolors="black", linewidths=.45, zorder=3)
        ax.scatter(longitude[missing_color], latitude[missing_color], color="lightgray",
                   s=sizes[missing_color], edgecolors="black", linewidths=.45, zorder=3)
        if max_visits > 0:
            from matplotlib.lines import Line2D
            legend_values = np.unique(np.rint(np.quantile(finite_visits[finite_visits > 0], [0, .5, 1])).astype(int))
            handles = [
                Line2D([], [], linestyle="", marker="o", markerfacecolor="white",
                       markeredgecolor="black", markersize=np.sqrt(18 + 72 * np.sqrt(value / max_visits)),
                       label=str(value))
                for value in legend_values
            ]
            ax.legend(handles=handles, title=f"{right_label or right_metric}\n(marker size)",
                      loc="lower left", fontsize=7, title_fontsize=7, framealpha=.88)

    left_mappable = plt.cm.ScalarMappable(norm=left_norm, cmap=left_cmap)
    right_mappable = plt.cm.ScalarMappable(norm=right_norm, cmap=right_cmap)
    ax.scatter(longitude[both_zero], latitude[both_zero], s=6, color="black",
               edgecolors="none", zorder=3.2)

    for number, row in data.iterrows():
        ax.annotate(str(row["_reference_number"]), (longitude[number], latitude[number]),
                    xytext=(4, 4), textcoords="offset points",
                    fontsize=8 if bulge_zoom is not None else 7, zorder=4)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if bulge_zoom is not None:
        ax.set_xlim(-bulge_zoom[0], bulge_zoom[0])
        ax.set_ylim(-bulge_zoom[1], bulge_zoom[1])
        ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.35)

    fig.colorbar(left_mappable, cax=left_colorbar_ax, orientation="horizontal")
    left_title = (
        f"Left half: {left_label or left_metric}"
        if marker_encoding == "split_color"
        else f"Color: {left_label or left_metric}"
    )
    left_colorbar_ax.set_title(left_title, fontsize=8, pad=3)
    left_colorbar_ax.tick_params(labelsize=7, pad=1)
    if marker_encoding == "split_color":
        fig.colorbar(right_mappable, cax=right_colorbar_ax, orientation="horizontal")
        right_colorbar_ax.set_title(f"Right half: {right_label or right_metric}", fontsize=8, pad=3)
        right_colorbar_ax.tick_params(labelsize=7, pad=1)
    else:
        right_colorbar_ax.axis("off")
    note_y = .765 if coverage_mappable is not None else .815
    if coverage_mappable is not None:
        coverage_colorbar_ax = fig.add_axes([.2275, .808, .285, .022])
        fig.colorbar(coverage_mappable, cax=coverage_colorbar_ax, orientation="horizontal")
        coverage_colorbar_ax.set_title(
            f"Background: {coverage_label} per low-resolution cell", fontsize=7, pad=1,
        )
        coverage_colorbar_ax.tick_params(labelsize=6, pad=1)
    zero_note = (
        "Black half = 0  |  Small black dot = both 0  |  Gray = missing"
        if marker_encoding == "split_color"
        else "Black = color value 0  |  Small black dot = both values 0  |  Gray = missing"
    )
    fig.text(.37, note_y, zero_note, ha="center", va="center", fontsize=7)

    references = "\n".join(f"{int(row['_reference_number'])}: {row['Target']}" for _, row in data.iterrows())
    legend_ax.text(0, 1, "References", va="top", fontsize=10, weight="bold")
    legend_ax.text(0, .955, references, va="top", fontsize=7, family="monospace", linespacing=1.18)
    fig.savefig(output_path, dpi=180, bbox_inches="tight", pad_inches=.04)
    plt.close(fig)


def _write_report_versions(path: Path, versions: dict[str, int]) -> None:
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
) -> None:
    """Generate one flat PNG per target and reuse previously queried coverage."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    versions_path = output_dir / "report_versions.json"
    try:
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        versions = {}
    grouped = {name: data for name, data in coverage_rows.groupby("Target", sort=False)}
    empty_coverage = coverage_rows.iloc[0:0].copy()
    total = len(targets)
    plot_errors = []
    generated = skipped = no_coadd = 0
    if verbose:
        print(
            "Legend — reused: current PNG; generated: new PNG; "
            "no coadd: no PNG; errors: isolated failures.",
            flush=True,
        )
    progress = tqdm(
        targets.iterrows(), total=total, desc="Reports", unit="target",
        disable=not verbose, dynamic_ncols=True, mininterval=1.0,
    )

    def update_progress() -> None:
        progress.set_postfix(
            reused=skipped, generated=generated, no_coadd=no_coadd, errors=len(plot_errors), refresh=False,
        )

    for _, row in progress:
        target_name = str(row["Target"])
        file_key = _safe_name(target_name)
        report_path = output_dir / f"{file_key}_target_report.png"
        current_version = str(versions.get(file_key, ""))
        if report_path.exists() and current_version == str(TARGET_REPORT_VERSION) and not overwrite:
            skipped += 1
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
            _write_report_versions(versions_path, versions)
            update_progress()
            continue
        figure.savefig(report_path, dpi=180, bbox_inches="tight")
        plt.close(figure)
        versions[file_key] = TARGET_REPORT_VERSION
        _write_report_versions(versions_path, versions)
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
    from mop_api import MOPClient

    return MOPClient()


def _create_tap_service(release: DataReleaseConfig):
    from lsst.rsp import RSPDiscovery

    return RSPDiscovery(release.rsp_instance).get_tap_client()


def _create_butler(release: DataReleaseConfig):
    from lsst.daf.butler import Butler

    options = {}
    if release.butler_collections is not None:
        options["collections"] = release.butler_collections
    return Butler(release.butler_repo, **options)


def _create_default_target_plotter(
    *, mop, tap_service, butler, release: DataReleaseConfig, root_dir: str | Path,
):
    from photometry import load_event_photometry
    from target_report import plot_target

    butler = butler or _create_butler(release)
    photometry_dir = Path(root_dir) / "photometry"

    def standard_target_plotter(target, coverage):
        photometry = load_event_photometry(
            target, mop=mop, cache_dir=photometry_dir,
        )
        return plot_target(
            target, butler=butler, tap_service=tap_service,
            data_release=release, calexps=coverage, photometry=photometry,
        )

    return standard_target_plotter


def run_target_selection(
    start_date: str,
    end_date: str | None = None,
    *,
    data_release: str | DataReleaseConfig = "DP2",
    root_dir: str | Path = "outputs",
    observatory: str = "El Leoncito",
    mop=None,
    tap_service=None,
    butler=None,
    target_plotter: Callable | bool | None = None,
    max_workers: int = 4,
    reuse_cache: bool = True,
    overwrite_target_plots: bool = False,
    sky_marker_encoding: str = "split_color",
    show_coverage_background: bool = False,
    coverage_resolution: int = 19,
    generate_visibility_plots: bool = True,
    overwrite_visibility_plots: bool = False,
    visibility_minimum_altitude: float = 30.0,
    visibility_minimum_peak_altitude: float = 50.0,
    visibility_minimum_night_fraction: float = 0.50,
    visibility_selection_rule: str = "all",
    visibility_time_step_minutes: int = 10,
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
    if target_plotter is False:
        report_plotter = None
    elif target_plotter is None:
        report_plotter = _create_default_target_plotter(
            mop=mop, tap_service=tap_service, butler=butler,
            release=release, root_dir=root_dir,
        )
    elif callable(target_plotter):
        report_plotter = target_plotter
    else:
        raise TypeError("target_plotter must be callable, False, or None.")
    end_date = end_date or start_date
    paths = create_run_structure(root_dir, start_date, end_date, release)
    daily_path = paths["tables"] / "visible_targets_daily.csv"
    summary_path = paths["tables"] / "visible_summary.csv"
    coverage_path = paths["tables"] / "coverage_raw.csv"
    manifest_path = paths["run"] / "manifest.json"
    started = perf_counter()
    cache_valid = False
    if reuse_cache and manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache_valid = previous.get("cache_version") == CACHE_VERSION
        except (OSError, ValueError, TypeError):
            cache_valid = False

    if verbose:
        print("[1/5] Visible MOP targets" + (" (cache)" if cache_valid else ""), flush=True)
    if cache_valid and daily_path.exists():
        daily = pd.read_csv(daily_path)
    else:
        daily = mop.visible_targets(
            observatory=observatory, start_date=start_date, end_date=end_date,
            sort_by_mag=False,
        )
        daily.to_csv(daily_path, index=False)

    if generate_visibility_plots:
        from visibility_plotter import save_nightly_visibility_plots
        if verbose:
            print("      Nightly visibility plots", flush=True)
        save_nightly_visibility_plots(
            daily, start_date, end_date, paths["visibility_plots"],
            observatory=observatory, minimum_altitude=visibility_minimum_altitude,
            minimum_peak_altitude=visibility_minimum_peak_altitude,
            minimum_night_fraction=visibility_minimum_night_fraction,
            selection_rule=visibility_selection_rule,
            time_step_minutes=visibility_time_step_minutes,
            overwrite=overwrite_visibility_plots, verbose=verbose,
        )

    if verbose:
        print("[2/5] MOP parameters + photometry" + (" (cache)" if cache_valid else ""), flush=True)
    if cache_valid and summary_path.exists():
        summary = pd.read_csv(summary_path)
    else:
        summary = mop.visibility_summary(
            observatory=observatory, start_date=start_date, end_date=end_date,
            include_microlensing_parameters=True, parameter_errors="ignore",
            daily_targets=daily, parameter_max_workers=max_workers,
            parameter_cache_dir=Path(root_dir) / "mop_event_cache",
            photometry_dir=Path(root_dir) / "photometry",
            refresh_parameters=not reuse_cache,
        )
        summary.to_csv(summary_path, index=False)

    if verbose:
        print(f"[3/5] {release.name} coverage" + (" (cache)" if cache_valid else f" ({max_workers} workers)"), flush=True)
    if cache_valid and coverage_path.exists():
        coverage_rows = pd.read_csv(coverage_path)
    else:
        coverage_rows = query_release_coverage(
            summary, tap_service, max_workers=max_workers, data_release=release
        )
        coverage_rows.to_csv(coverage_path, index=False)

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
        print("[4/5] Tables and sky maps", flush=True)
    coverage_summary = summarize_release_coverage(coverage_rows)
    coverage_summary.to_csv(paths["tables"] / "coverage_summary.csv", index=False)
    combined = summary.merge(coverage_summary, on="Target", how="left")
    combined.to_csv(paths["tables"] / "combined_targets.csv", index=False)
    save_target_summary(
        combined, photometry_dir=Path(root_dir) / "photometry",
        csv_path=paths["tables"] / "target_summary.csv",
        png_path=paths["tables"] / "target_summary.png",
        release_name=release.name,
    )

    if {"coverage_n_visits", "mag_now"}.issubset(combined.columns):
        plot_sky_dual_metric(
            combined, "mag_now", "coverage_n_visits",
            paths["sky_plots"] / "sky_by_mag_and_visits.png",
            title=f"MOP targets with {release.name} coverage",
            left_label="MOP mag_now", right_label=f"{release.name} visits",
            marker_encoding=sky_marker_encoding,
            coverage_background=visit_centers,
            coverage_resolution=coverage_resolution,
            coverage_label=f"{release.name} visits",
        )
        plot_sky_dual_metric(
            combined, "mag_now", "coverage_n_visits",
            paths["sky_plots"] / "sky_bulge_zoom_mag_and_visits.png",
            title=f"Galactic bulge zoom — MOP + {release.name}",
            left_label="MOP mag_now", right_label=f"{release.name} visits",
            bulge_zoom=(20, 12),
            marker_encoding=sky_marker_encoding,
            coverage_background=visit_centers,
            coverage_resolution=coverage_resolution,
            coverage_label=f"{release.name} visits",
        )
    if report_plotter is not None:
        if verbose:
            print("[5/5] Per-target reports", flush=True)
        save_target_reports(
            combined, report_plotter, paths["targets"], coverage_rows,
            overwrite=overwrite_target_plots, verbose=verbose,
        )

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_release": release.name,
        "observatory": observatory, "start_date": start_date, "end_date": end_date,
        "n_daily_rows": int(len(daily)), "n_targets": int(len(combined)),
        "max_workers": int(max_workers), "reuse_cache": bool(reuse_cache),
        "cache_version": CACHE_VERSION,
        "target_report_version": TARGET_REPORT_VERSION,
        "overwrite_target_plots": bool(overwrite_target_plots),
        "sky_marker_encoding": sky_marker_encoding,
        "show_coverage_background": bool(show_coverage_background),
        "coverage_resolution": int(coverage_resolution),
        "generate_visibility_plots": bool(generate_visibility_plots),
        "overwrite_visibility_plots": bool(overwrite_visibility_plots),
        "visibility_minimum_altitude": float(visibility_minimum_altitude),
        "visibility_minimum_peak_altitude": float(visibility_minimum_peak_altitude),
        "visibility_minimum_night_fraction": float(visibility_minimum_night_fraction),
        "visibility_selection_rule": visibility_selection_rule,
        "visibility_time_step_minutes": int(visibility_time_step_minutes),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if verbose:
        print(f"Pipeline completed in {(perf_counter() - started) / 60:.1f} min", flush=True)
    return combined, paths
