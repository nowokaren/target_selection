"""Discoverable product index for each analysis run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def write_product_index(
    paths: Mapping[str, Path],
    *,
    reference_survey: str,
) -> Path:
    """Write a compact index containing only products that exist."""
    run = Path(paths["run"])
    tables = Path(paths["tables"])
    candidates = {
        "configuration": (
            run / "analysis_config.json",
            "Exact normalized configuration used for the run",
        ),
        "targets": (
            tables / "combined_targets.csv",
            "Complete enriched target result",
        ),
        "planning_targets": (
            tables / "targets.csv",
            "Complete target result for a planning-only run",
        ),
        "visibility_summary": (
            tables / "visibility_target_summary.csv",
            "Per-target local visibility decision",
        ),
        "visibility_details": (
            Path(paths.get("visibility_plots", run / "visibility_plots"))
            / "visibility_selection.csv",
            "Per-target and per-night visibility metrics",
        ),
        "planning_visibility": (
            tables / "visibility.csv",
            "Per-target and per-night planning-only visibility metrics",
        ),
        "observing_selection": (
            tables / "observing_selection_summary.csv",
            "Compact observing-planning table",
        ),
        "source_catalog": (
            tables / "source_catalog.csv",
            "Targets grouped by provider or survey provenance",
        ),
        "source_updates": (
            tables / "source_updates.csv",
            "Sources imported or refreshed for this run",
        ),
        "targets_without_selected_data": (
            tables / "targets_without_selected_data.csv",
            "Targets excluded by the active target-data selection mode",
        ),
        "mop_candidates_without_data": (
            tables / "mop_candidates_without_data.csv",
            "MOP-visible candidates excluded because no event parameters or photometry were available",
        ),
        "sky_map": (
            Path(paths.get("sky_plots", run / "sky_plots"))
            / "sky_by_mag_and_hsh_points.png",
            "Locally selected targets with HSH image data",
        ),
        "sky_bulge_zoom": (
            Path(paths.get("sky_plots", run / "sky_plots"))
            / "sky_bulge_zoom_mag_and_hsh_points.png",
            "Bulge zoom for locally selected targets with HSH image data",
        ),
        "reference_sky_map": (
            Path(paths.get("sky_plots", run / "sky_plots"))
            / "sky_by_mag_and_visits.png",
            "Locally selected targets with reference-survey visit coverage",
        ),
        "reference_sky_bulge_zoom": (
            Path(paths.get("sky_plots", run / "sky_plots"))
            / "sky_bulge_zoom_mag_and_visits.png",
            "Bulge zoom for targets with reference-survey visit coverage",
        ),
        "monitoring_report": (
            Path(paths.get("monitoring_reports", run / "monitoring_reports"))
            / "lightcurves.pdf",
            "Provider light curves and follow-up/reference temporal coverage",
        ),
        "target_reports": (
            Path(paths.get("target_reports", run / "target_reports")),
            "Shared folder with individual target dashboard PNGs",
        ),
    }

    def display_path(path: Path) -> str:
        try:
            return str(path.relative_to(run))
        except ValueError:
            return str(path)

    products = {
        name: {
            "path": display_path(path),
            "description": description,
        }
        for name, (path, description) in candidates.items()
        if path.exists()
    }
    payload = {
        "reference_survey": reference_survey,
        "run_directory": str(run),
        "recommended_products": products,
        "additional_diagnostics": "See the tables and plot directories for backend diagnostics.",
    }
    destination = run / "product_index.json"
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return destination


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
    from matplotlib.colors import Normalize, LinearSegmentedColormap

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
        from matplotlib.colors import LogNorm
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
    # Trim the white ends of the sequential maps so the minimum positive
    # value is still visibly colored in every sky product.
    left_cmap = LinearSegmentedColormap.from_list(
        "target_blues", plt.get_cmap("Blues")(np.linspace(.35, .95, 256))
    )
    right_cmap = LinearSegmentedColormap.from_list(
        "target_greens", plt.get_cmap("Greens")(np.linspace(.35, .95, 256))
    )

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
