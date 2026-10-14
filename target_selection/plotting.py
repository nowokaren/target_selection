"""Visual summaries for the MOP--DP2 LSDB comparison."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm, Normalize

INK = "#243B53"


def _intersection_table(catalog: pd.DataFrame, columns: list[str], labels: list[str] | None = None) -> pd.DataFrame:
    """Return non-empty set intersections, ordered by decreasing population."""
    labels = labels or columns
    data = catalog.loc[:, columns].fillna(False).astype(bool).copy()
    data.columns = labels
    table = data.groupby(labels, dropna=False).size().reset_index(name="targets")
    return table.sort_values("targets", ascending=False, kind="stable").reset_index(drop=True)


def plot_upset(
    catalog: pd.DataFrame,
    columns: list[str],
    labels: list[str] | None = None,
    title: str = "Intersecciones",
    min_count: int = 1,
    max_intersections: int | None = 20,
    order: str = "count",
):
    """Draw an UpSet-style intersection plot without an extra dependency.

    The returned table has one row per non-empty combination and can be saved
    alongside the figure for exact numerical reporting.
    """
    labels = labels or columns
    table = _intersection_table(catalog, columns, labels)
    table = table.loc[table["targets"] >= min_count].copy()
    if order == "completeness":
        # First show the intersection with every property, then intersections
        # missing one property, two properties, and so on. The secondary mask
        # ordering makes the progression deterministic without using counts.
        present = table[labels].astype(int)
        table["_n_present"] = present.sum(axis=1)
        table["_mask"] = present.to_numpy().dot(2 ** np.arange(len(labels))[::-1])
        table = table.sort_values(
            ["_n_present", "_mask"], ascending=[False, False], kind="stable"
        ).drop(columns=["_n_present", "_mask"])
    elif order != "count":
        raise ValueError("order must be 'count' or 'completeness'")
    if max_intersections is not None:
        table = table.head(max_intersections).copy()
    table = table.reset_index(drop=True)

    fig = plt.figure(figsize=(max(10, 0.55 * len(table)), 7.5))
    grid = fig.add_gridspec(2, 1, height_ratios=[3, 1.7], hspace=0.08)
    ax_bar = fig.add_subplot(grid[0])
    ax_mat = fig.add_subplot(grid[1], sharex=ax_bar)
    x = np.arange(len(table))
    ax_bar.bar(x, table["targets"], color="#3B82F6", width=.72)
    for i, value in enumerate(table["targets"]):
        ax_bar.text(i, value, f"{value:,}", ha="center", va="bottom", fontsize=8)
    ax_bar.set_ylabel("Number of targets")
    ax_bar.set_title(title, loc="left", fontsize=16, weight="bold")
    ax_bar.grid(axis="y", alpha=.25)
    ax_bar.tick_params(axis="x", labelbottom=False)

    ax_mat.set_ylim(-.7, len(labels) - .3)
    ax_mat.set_yticks(np.arange(len(labels)))
    ax_mat.set_yticklabels(labels)
    ax_mat.set_xticks(x)
    ax_mat.set_xticklabels([str(i + 1) for i in x], fontsize=8)
    ax_mat.set_xlabel("Products availability combinations")
    ax_mat.grid(axis="x", alpha=.2)
    for i, row in table.iterrows():
        selected = []
        for j, label in enumerate(labels):
            on = bool(row[label])
            ax_mat.scatter(i, j, s=85, color="#2563EB" if on else "#D9E2EC", zorder=3)
            if on:
                selected.append(j)
        if len(selected) > 1:
            ax_mat.plot([i, i], [min(selected), max(selected)], color="#2563EB", lw=2, zorder=2)
    ax_mat.invert_yaxis()
    fig.subplots_adjust(left=.16, right=.98, top=.90, bottom=.14, hspace=.08)
    return fig, table


def plot_match_skyplot(
    catalog: pd.DataFrame,
    category_column: str = "catalog_match_category",
    ra_column: str = "ra_deg",
    dec_column: str = "dec_deg",
    title: str = "MOP targets por categoría de match DP2",
):
    """Plot MOP targets in equatorial coordinates, colored by match category."""
    data = catalog.dropna(subset=[ra_column, dec_column, category_column]).copy()
    data["ra_plot"] = ((pd.to_numeric(data[ra_column]) + 180) % 360) - 180
    order = [
        "no_catalog_match_and_no_valid_visit",
        "coverage_without_catalog_match",
        "object_only",
        "diaobject_only",
        "object_and_diaobject",
    ]
    colors = {
        "no_catalog_match_and_no_valid_visit": "#9AA5B1",
        "coverage_without_catalog_match": "#60A5FA",
        "object_only": "#F59E0B",
        "diaobject_only": "#EF4444",
        "object_and_diaobject": "#8B5CF6",
    }
    labels = {
        "no_catalog_match_and_no_valid_visit": "sin catálogo ni visita registrada",
        "coverage_without_catalog_match": "cobertura sin match catalogado",
        "object_only": "sólo Object",
        "diaobject_only": "sólo DiaObject",
        "object_and_diaobject": "Object y DiaObject",
    }
    fig, ax = plt.subplots(figsize=(12, 6.5))
    for category in order:
        subset = data.loc[data[category_column].eq(category)]
        if subset.empty:
            continue
        ax.scatter(subset["ra_plot"], subset[dec_column], s=13, alpha=.72,
                   color=colors[category], label=f"{labels[category]} ({len(subset):,})", linewidths=0)
    ax.set_xlim(180, -180)
    ax.set_ylim(-90, 90)
    ax.set_xlabel("RA [deg] (ascensión recta; eje invertido)")
    ax.set_ylabel("Dec [deg]")
    ax.set_title(title, loc="left", fontsize=16, weight="bold")
    ax.grid(alpha=.22)
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -0.12), ncol=2, frameon=False)
    fig.subplots_adjust(left=.08, right=.98, top=.90, bottom=.22)
    return fig


def plot_upset_with_band_heatmap(
    catalog: pd.DataFrame,
    intersection_columns: list[str],
    band_property_columns: dict[str, str],
    labels: list[str] | None = None,
    title: str = "Intersecciones con información por banda",
    order: str = "completeness",
):
    """Plot one UpSet bar per target combination plus a per-band heatmap.

    Bars count targets exactly once. The heatmap below shows, for each
    intersection, the fraction of its targets satisfying the selected
    band-level property in each filter. Thus band values are descriptive and
    are not stacked into the target counts.
    """
    labels = labels or intersection_columns
    data = catalog.loc[:, intersection_columns].fillna(False).astype(bool)
    base = data.groupby(intersection_columns, dropna=False).size().reset_index(name="targets")
    if order == "completeness":
        present = base[intersection_columns].astype(int)
        base["_n_present"] = present.sum(axis=1)
        base["_mask"] = present.to_numpy().dot(2 ** np.arange(len(intersection_columns))[::-1])
        base = base.sort_values(["_n_present", "_mask"], ascending=[False, False], kind="stable")
    else:
        base = base.sort_values("targets", ascending=False, kind="stable")
    base = base.reset_index(drop=True)

    fractions = []
    for _, row in base.iterrows():
        mask = np.ones(len(catalog), dtype=bool)
        for column in intersection_columns:
            mask &= catalog[column].fillna(False).astype(bool).to_numpy() == bool(row[column])
        subset = catalog.loc[mask]
        fractions.append([
            pd.to_numeric(subset.get(column, False), errors="coerce").fillna(0).astype(bool).mean()
            if len(subset) else 0.0
            for column in band_property_columns.values()
        ])
    band_names = list(band_property_columns)
    fractions = np.asarray(fractions, dtype=float).T

    fig = plt.figure(figsize=(max(11, .62 * len(base)), 9.2))
    grid = fig.add_gridspec(3, 1, height_ratios=[3.0, 1.55, 1.9], hspace=.08)
    x = np.arange(len(base))
    ax_bar = fig.add_subplot(grid[0])
    ax_mat = fig.add_subplot(grid[1], sharex=ax_bar)
    ax_band = fig.add_subplot(grid[2], sharex=ax_bar)
    ax_bar.bar(x, base["targets"], color="#3B82F6", width=.72)
    for i, value in enumerate(base["targets"]):
        ax_bar.text(i, value, f"{value:,}", ha="center", va="bottom", fontsize=8)
    ax_bar.set_ylabel("Targets")
    ax_bar.set_title(title, loc="left", fontsize=16, weight="bold")
    ax_bar.grid(axis="y", alpha=.25)
    ax_bar.tick_params(axis="x", labelbottom=False)

    ax_mat.set_ylim(-.7, len(labels) - .3)
    ax_mat.set_yticks(np.arange(len(labels)))
    ax_mat.set_yticklabels(labels)
    ax_mat.tick_params(axis="x", labelbottom=False)
    ax_mat.grid(axis="x", alpha=.2)
    for i, (_, row) in enumerate(base.iterrows()):
        selected = []
        for j, column in enumerate(intersection_columns):
            on = bool(row[column])
            ax_mat.scatter(i, j, s=80, color="#2563EB" if on else "#D9E2EC", zorder=3)
            if on:
                selected.append(j)
        if len(selected) > 1:
            ax_mat.plot([i, i], [min(selected), max(selected)], color="#2563EB", lw=2, zorder=2)
    ax_mat.invert_yaxis()

    image = ax_band.imshow(fractions, aspect="auto", vmin=0, vmax=1, cmap="YlGnBu")
    ax_band.set_yticks(np.arange(len(band_names)))
    ax_band.set_yticklabels(band_names)
    ax_band.set_xticks(x)
    ax_band.set_xticklabels([str(i + 1) for i in x], fontsize=8)
    ax_band.set_xlabel("Combinación ordenada por completitud")
    ax_band.set_ylabel("Banda\nfracción")
    for iy in range(len(band_names)):
        for ix in range(len(base)):
            if len(base) <= 25:
                ax_band.text(ix, iy, f"{fractions[iy, ix]:.0%}", ha="center", va="center", fontsize=7,
                             color="white" if fractions[iy, ix] > .55 else INK)
    fig.colorbar(image, ax=ax_band, pad=.01, fraction=.025, label="fracción de targets con la propiedad")
    fig.subplots_adjust(left=.16, right=.96, top=.91, bottom=.12, hspace=.08)
    return fig, base, fractions


def plot_upset_with_band_bars(
    catalog: pd.DataFrame,
    intersection_columns: list[str],
    band_condition_columns: dict[str, dict[str, str]],
    labels: list[str] | None = None,
    title: str = "Intersecciones y condiciones cumplidas por banda",
    order: str = "completeness",
):
    """Plot UpSet intersections with grouped bars for same-band conditions.

    ``band_condition_columns`` maps each intersection column to a mapping from
    band name to the corresponding per-band boolean column. For each UpSet
    intersection and band, the lower grouped bar is the fraction of targets
    in that intersection that satisfy all *positive* band-resolved conditions
    of the intersection in that same band. Conditions without a per-band
    mapping (for example Object/DiaObject matches) are used only in the
    intersection definition. A target may contribute to several band bars.
    """
    labels = labels or intersection_columns
    data = catalog.loc[:, intersection_columns].fillna(False).astype(bool)
    base = data.groupby(intersection_columns, dropna=False).size().reset_index(name="targets")
    if order == "completeness":
        present = base[intersection_columns].astype(int)
        base["_n_present"] = present.sum(axis=1)
        base["_mask"] = present.to_numpy().dot(2 ** np.arange(len(intersection_columns))[::-1])
        base = base.sort_values(["_n_present", "_mask"], ascending=[False, False], kind="stable")
    elif order == "count":
        base = base.sort_values("targets", ascending=False, kind="stable")
    else:
        raise ValueError("order must be 'count' or 'completeness'")
    base = base.drop(columns=[c for c in ("_n_present", "_mask") if c in base]).reset_index(drop=True)

    bands = list(next(iter(band_condition_columns.values())).keys())
    fractions = np.full((len(bands), len(base)), np.nan, dtype=float)
    counts = np.zeros((len(bands), len(base)), dtype=int)
    for ix, row in base.iterrows():
        subset_mask = np.ones(len(catalog), dtype=bool)
        for column in intersection_columns:
            subset_mask &= catalog[column].fillna(False).astype(bool).to_numpy() == bool(row[column])
        subset = catalog.loc[subset_mask]
        for ib, band in enumerate(bands):
            requirements = []
            for intersection_column in intersection_columns:
                if not bool(row[intersection_column]):
                    continue
                per_band = band_condition_columns.get(intersection_column, {})
                if band in per_band and per_band[band] in subset:
                    requirements.append(subset[per_band[band]].fillna(False).astype(bool).to_numpy())
            if requirements:
                same_band = np.logical_and.reduce(requirements)
                counts[ib, ix] = int(same_band.sum())
                fractions[ib, ix] = same_band.mean() if len(same_band) else np.nan

    n_intersections = len(base)
    fig = plt.figure(figsize=(max(12, .72 * n_intersections), 9.4))
    grid = fig.add_gridspec(3, 1, height_ratios=[2.7, 1.55, 3.0], hspace=.08)
    ax_bar = fig.add_subplot(grid[0])
    ax_mat = fig.add_subplot(grid[1], sharex=ax_bar)
    ax_band = fig.add_subplot(grid[2], sharex=ax_bar)
    x = np.arange(n_intersections)

    ax_bar.bar(x, base["targets"], color="#3B82F6", width=.72)
    for i, value in enumerate(base["targets"]):
        ax_bar.text(i, value, f"{value:,}", ha="center", va="bottom", fontsize=8)
    ax_bar.set_ylabel("Targets")
    ax_bar.set_title(title, loc="left", fontsize=16, weight="bold")
    ax_bar.grid(axis="y", alpha=.25)
    ax_bar.tick_params(axis="x", labelbottom=False)

    ax_mat.set_ylim(-.7, len(labels) - .3)
    ax_mat.set_yticks(np.arange(len(labels)))
    ax_mat.set_yticklabels(labels)
    ax_mat.tick_params(axis="x", labelbottom=False)
    ax_mat.grid(axis="x", alpha=.2)
    for i, (_, row) in enumerate(base.iterrows()):
        selected = []
        for j, column in enumerate(intersection_columns):
            on = bool(row[column])
            ax_mat.scatter(i, j, s=80, color="#2563EB" if on else "#D9E2EC", zorder=3)
            if on:
                selected.append(j)
        if len(selected) > 1:
            ax_mat.plot([i, i], [min(selected), max(selected)], color="#2563EB", lw=2, zorder=2)
    ax_mat.invert_yaxis()

    width = min(.12, .72 / max(len(bands), 1))
    offsets = (np.arange(len(bands)) - (len(bands) - 1) / 2) * width
    colors = plt.get_cmap("tab10")(np.linspace(0, .85, len(bands)))
    for ib, (band, offset, color) in enumerate(zip(bands, offsets, colors, strict=True)):
        bars = ax_band.bar(x + offset, fractions[ib], width=width * .9, color=color, label=band)
        for rect, count, fraction in zip(bars, counts[ib], fractions[ib], strict=True):
            if np.isfinite(fraction) and fraction > 0:
                ax_band.text(rect.get_x() + rect.get_width() / 2, rect.get_height() + .018,
                             f"{count:,}", ha="center", va="bottom", fontsize=6, rotation=90)
    ax_band.set_ylim(0, 1.18)
    ax_band.set_ylabel("Fracción dentro de la combinación")
    ax_band.set_xlabel("Combinación ordenada por completitud")
    ax_band.set_xticks(x)
    ax_band.set_xticklabels([str(i + 1) for i in x], fontsize=8)
    ax_band.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    ax_band.grid(axis="y", alpha=.25)
    ax_band.legend(title="Banda", ncol=len(bands), loc="upper center", bbox_to_anchor=(.5, 1.18), frameon=False)
    fig.subplots_adjust(left=.17, right=.98, top=.90, bottom=.12, hspace=.08)
    return fig, base, counts, fractions


def plot_upset_with_band_conditions(
    catalog: pd.DataFrame,
    intersection_columns: list[str],
    condition_band_columns: dict[str, dict[str, str]],
    labels: list[str] | None = None,
    title: str = "Condiciones por banda dentro de cada combinación",
    order: str = "completeness",
):
    """Plot the fraction of each UpSet intersection with each condition/band.

    Unlike ``plot_upset_with_band_bars``, this function counts every condition
    independently. It therefore reveals cross-band cases such as a target
    with coadd and metrics in one band but valid cutout pixels in another.
    The same target may contribute to multiple conditions and bands.
    """
    labels = labels or intersection_columns
    data = catalog.loc[:, intersection_columns].fillna(False).astype(bool)
    base = data.groupby(intersection_columns, dropna=False).size().reset_index(name="targets")
    if order == "completeness":
        present = base[intersection_columns].astype(int)
        base["_n_present"] = present.sum(axis=1)
        base["_mask"] = present.to_numpy().dot(2 ** np.arange(len(intersection_columns))[::-1])
        base = base.sort_values(["_n_present", "_mask"], ascending=[False, False], kind="stable")
    elif order == "count":
        base = base.sort_values("targets", ascending=False, kind="stable")
    else:
        raise ValueError("order must be 'count' or 'completeness'")
    base = base.drop(columns=[c for c in ("_n_present", "_mask") if c in base]).reset_index(drop=True)

    condition_names = list(condition_band_columns)
    bands = list(next(iter(condition_band_columns.values())).keys())
    fractions = np.full((len(condition_names) * len(bands), len(base)), np.nan, dtype=float)
    counts = np.zeros_like(fractions, dtype=int)
    row_labels = []
    for condition in condition_names:
        for band in bands:
            row_labels.append((condition, band))

    for ix, row in base.iterrows():
        subset_mask = np.ones(len(catalog), dtype=bool)
        for column in intersection_columns:
            subset_mask &= catalog[column].fillna(False).astype(bool).to_numpy() == bool(row[column])
        subset = catalog.loc[subset_mask]
        for ir, (condition, band) in enumerate(row_labels):
            column = condition_band_columns[condition][band]
            if column not in subset or not len(subset):
                continue
            values = subset[column].fillna(False).astype(bool).to_numpy()
            counts[ir, ix] = int(values.sum())
            fractions[ir, ix] = values.mean()

    fig = plt.figure(figsize=(max(12, .72 * len(base)), 11.0))
    grid = fig.add_gridspec(3, 1, height_ratios=[2.2, 1.35, 5.8], hspace=.08)
    ax_bar = fig.add_subplot(grid[0])
    ax_mat = fig.add_subplot(grid[1], sharex=ax_bar)
    ax_heat = fig.add_subplot(grid[2], sharex=ax_bar)
    x = np.arange(len(base))

    ax_bar.bar(x, base["targets"], color="#3B82F6", width=.72)
    for i, value in enumerate(base["targets"]):
        ax_bar.text(i, value, f"{value:,}", ha="center", va="bottom", fontsize=8)
    ax_bar.set_ylabel("Targets")
    ax_bar.set_title(title, loc="left", fontsize=16, weight="bold")
    ax_bar.grid(axis="y", alpha=.25)
    ax_bar.tick_params(axis="x", labelbottom=False)

    ax_mat.set_ylim(-.7, len(labels) - .3)
    ax_mat.set_yticks(np.arange(len(labels)))
    ax_mat.set_yticklabels(labels)
    ax_mat.tick_params(axis="x", labelbottom=False)
    ax_mat.grid(axis="x", alpha=.2)
    for i, (_, row) in enumerate(base.iterrows()):
        selected = []
        for j, column in enumerate(intersection_columns):
            on = bool(row[column])
            ax_mat.scatter(i, j, s=75, color="#2563EB" if on else "#D9E2EC", zorder=3)
            if on:
                selected.append(j)
        if len(selected) > 1:
            ax_mat.plot([i, i], [min(selected), max(selected)], color="#2563EB", lw=2, zorder=2)
    ax_mat.invert_yaxis()

    image = ax_heat.imshow(fractions, aspect="auto", vmin=0, vmax=1, cmap="YlGnBu")
    ax_heat.set_yticks(np.arange(len(row_labels)))
    ax_heat.set_yticklabels([f"{condition} — {band}" for condition, band in row_labels], fontsize=8)
    ax_heat.set_xticks(x)
    ax_heat.set_xticklabels([str(i + 1) for i in x], fontsize=8)
    ax_heat.set_xlabel("Combination ordered by completeness")
    ax_heat.set_ylabel("Condition and band")
    for ir in range(len(row_labels)):
        for ix in range(len(base)):
            if np.isfinite(fractions[ir, ix]):
                text_color = "white" if fractions[ir, ix] > .55 else INK
                ax_heat.text(ix, ir, f"{fractions[ir, ix]:.0%} ({counts[ir, ix]:,})",
                             ha="center", va="center", fontsize=6.5, color=text_color)
    ax_heat.grid(False)
    fig.colorbar(image, ax=ax_heat, pad=.01, fraction=.025, label="Target fraction")
    fig.subplots_adjust(left=.25, right=.96, top=.93, bottom=.10, hspace=.08)
    return fig, base, counts, fractions, row_labels


def save_figure(fig, path: str | Path, dpi: int = 160) -> None:
    """Save a figure, creating its parent directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight")


def make_skyplots(
    catalog: pd.DataFrame,
    visit_centers: pd.DataFrame,
    output_dir: str | Path,
    bands: tuple[str, ...] = tuple("ugrizy"),
) -> None:
    """Write per-band visit, all-band visit and complete-coadd sky plots."""
    bands = tuple(bands)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    centers = visit_centers.copy()
    centers.columns = [str(column).lower() for column in centers.columns]
    centers["band"] = centers["band"].astype(str).str.lower()
    centers["ra"] = pd.to_numeric(centers["ra"], errors="coerce")
    centers["dec"] = pd.to_numeric(centers["dec"], errors="coerce")
    centers = centers.dropna(subset=["ra", "dec"])

    def scatter(axis, background, points, count):
        if not background.empty:
            axis.scatter(background.ra, background.dec, s=1, c="0.75", alpha=.18, rasterized=True)
        points = points.loc[points[count].fillna(0).gt(0)]
        if points.empty:
            return None
        values = points[count].clip(lower=1)
        norm = LogNorm(vmin=1, vmax=max(2, values.max())) if values.max() > 1 else Normalize(0, 1)
        return axis.scatter(
            points.ra_deg, points.dec_deg, c=values, s=12,
            cmap="viridis", norm=norm, linewidths=0,
        )

    fig, axes = plt.subplots(2, 3, figsize=(18, 8), sharex=True, sharey=True)
    last = None
    for axis, band in zip(axes.flat, bands, strict=True):
        last = scatter(axis, centers.loc[centers.band.eq(band)], catalog, f"n_images_{band}")
        axis.set_title(band)
        axis.set_xlim(360, 0)
        axis.set_ylim(-90, 20)
        axis.set_xlabel("RA [deg]")
        axis.set_ylabel("Dec [deg]")
    if last is not None:
        fig.colorbar(last, ax=axes.ravel().tolist(), label="Number of visits")
    fig.tight_layout()
    fig.savefig(output_dir / "visits_by_band.png", dpi=180)
    plt.close(fig)

    background = centers.drop_duplicates(["visitid", "band"]) if "visitid" in centers else centers
    fig, axis = plt.subplots(figsize=(12, 6))
    last = scatter(axis, background, catalog, "n_images_total")
    axis.set_title("MOP targets with DP2 visits")
    axis.set_xlim(360, 0)
    axis.set_ylim(-90, 20)
    axis.set_xlabel("RA [deg]")
    axis.set_ylabel("Dec [deg]")
    if last is not None:
        fig.colorbar(last, ax=axis, label="Total number of visits")
    fig.tight_layout()
    fig.savefig(output_dir / "visits_all_bands.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(12, 6))
    last = scatter(axis, centers.iloc[0:0], catalog, "coadd_n_valid_bands")
    axis.set_title("MOP targets with complete coadds")
    axis.set_xlim(360, 0)
    axis.set_ylim(-90, 20)
    axis.set_xlabel("RA [deg]")
    axis.set_ylabel("Dec [deg]")
    if last is not None:
        fig.colorbar(last, ax=axis, label="Bands with complete coadds")
    fig.tight_layout()
    fig.savefig(output_dir / "valid_coadds_all_bands.png", dpi=180)
    plt.close(fig)
