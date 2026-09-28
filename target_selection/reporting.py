"""Reusable coverage summaries and diagnostic plots for collection reports."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence
import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric column, or an aligned all-NaN series if absent."""
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame[column].fillna(False).astype(bool)


def _style_table(table, fontsize: float) -> None:
    table.auto_set_font_size(False)
    table.set_fontsize(fontsize)
    for (row, column), cell in table.get_celld().items():
        cell.set_facecolor("white")
        cell.set_edgecolor("black")
        cell.set_linewidth(0)
        cell.PAD = 0.025
        if row == 0:
            cell.visible_edges = "TB"
            cell.set_linewidth(0.8)
            cell.set_text_props(weight="bold")
        elif row == len(table.get_celld()) - 1:
            cell.visible_edges = "B"
            cell.set_linewidth(0.8)


def add_cutout_status_columns(
    coverage: pd.DataFrame,
    manifest: pd.DataFrame | str | Path | None,
    *,
    bands: Sequence[str] = tuple("ugrizy"),
) -> pd.DataFrame:
    """Add per-band flags for coadds whose image contains no finite pixels."""
    result = coverage.copy()
    for band in bands:
        result[f"has_empty_coadd_{band}"] = False
    if manifest is None:
        result["has_empty_coadd"] = False
        return result
    table = pd.read_csv(manifest, low_memory=False) if isinstance(manifest, (str, Path)) else manifest.copy()
    if table.empty or "target_id" not in table:
        result["has_empty_coadd"] = False
        return result
    empty_by_target: dict[str, set[str]] = {}
    for record in table.to_dict("records"):
        target_id = str(record.get("target_id", ""))
        bands_for_target = empty_by_target.setdefault(target_id, set())
        try:
            diagnostics = json.loads(record.get("diagnostics_json", "[]") or "[]")
        except (TypeError, json.JSONDecodeError):
            diagnostics = []
        for diagnostic in diagnostics:
            if str(diagnostic.get("status", "")) == "empty_cutout" and str(diagnostic.get("band", "")) in bands:
                bands_for_target.add(str(diagnostic["band"]))
        if str(record.get("error_type", "")) == "empty_cutout":
            expected = str(record.get("expected_bands", "")).split(",")
            written = set(str(record.get("written_bands", "")).split(","))
            bands_for_target.update(band for band in expected if band in bands and band not in written)
    target_ids = result["target_id"].astype(str)
    for band in bands:
        result[f"has_empty_coadd_{band}"] = target_ids.map(lambda target: band in empty_by_target.get(target, set())).astype(bool)
    result["has_empty_coadd"] = result[[f"has_empty_coadd_{band}" for band in bands]].any(axis=1)
    return result

def write_collection_summaries(
    coverage: pd.DataFrame,
    output_dir: str | Path,
    *,
    bands: Sequence[str] = tuple("ugrizy"),
    category_column: str = "grade",
    category_label: str = "Grade",
    name: str = "collection",
    cutout_manifest: pd.DataFrame | str | Path | None = None,
) -> pd.DataFrame:
    """Write coverage tables and summary figures grouped by catalog category."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    group = coverage.loc[:, ~coverage.columns.astype(str).str.startswith("Unnamed:")].copy()
    group = add_cutout_status_columns(group, cutout_manifest, bands=bands)
    rows = []
    if category_column in group and group[category_column].notna().any():
        categories = sorted(group[category_column].dropna().unique(), key=str)
        category_groups = [(category, group.loc[group[category_column].eq(category)]) for category in categories]
    else:
        category_groups = [("all", group)]
    for category, subset in category_groups:
        category_text = str(category).strip()
        label = "unknown" if category_text.lower() in {"", "nan", "none", "unavailable"} else category_text
        rows.append({"category": label, "type": "visits", "total": int(_bool_series(subset, "has_visit_coverage").sum()), **{band: int(numeric_series(subset, f"n_images_{band}").fillna(0).gt(0).sum()) for band in bands}})
        rows.append({"category": label, "type": "coadds_found", "total": int(_bool_series(subset, "has_coadd").sum()), **{band: int(_bool_series(subset, f"has_coadd_{band}").sum()) for band in bands}})
        rows.append({"category": label, "type": "coadds_with_valid_metrics", "total": int(_bool_series(subset, "has_valid_coadd").sum()), **{band: int(_bool_series(subset, f"has_valid_coadd_{band}").sum()) for band in bands}})
        rows.append({"category": label, "type": "coadds_with_no_valid_pixels", "total": int(_bool_series(subset, "has_empty_coadd").sum()), **{band: int(_bool_series(subset, f"has_empty_coadd_{band}").sum()) for band in bands}})
    summary = pd.DataFrame(rows, columns=["category", "type", "total", *bands])
    summary.to_csv(out / f"summary_by_{category_column}.csv", index=False)

    band_rows = [{"band": band, "targets_with_visits": int(numeric_series(group, f"n_images_{band}").fillna(0).gt(0).sum()), "coadds_found": int(_bool_series(group, f"has_coadd_{band}").sum()), "coadds_with_valid_metrics": int(_bool_series(group, f"has_valid_coadd_{band}").sum()), "coadds_with_no_valid_pixels": int(_bool_series(group, f"has_empty_coadd_{band}").sum())} for band in bands]
    band_rows.append({"band": "total", "targets_with_visits": int(_bool_series(group, "has_visit_coverage").sum()), "coadds_found": int(_bool_series(group, "has_coadd").sum()), "coadds_with_valid_metrics": int(_bool_series(group, "has_valid_coadd").sum()), "coadds_with_no_valid_pixels": int(_bool_series(group, "has_empty_coadd").sum())})
    band_summary = pd.DataFrame(band_rows)
    band_summary.to_csv(out / "summary_by_band.csv", index=False)

    shown_band = band_summary.rename(columns={"band": "Band", "targets_with_visits": "Targets with visits", "coadds_found": "Coadds found", "coadds_with_valid_metrics": "Coadds with valid metrics", "coadds_with_no_valid_pixels": "Coadds with no valid pixels"}).copy()
    for column in shown_band.columns[1:]: shown_band[column] = shown_band[column].map(lambda value: f"{int(value):,}")
    fig, axis = plt.subplots(figsize=(8.5, 2.4), dpi=220)
    axis.axis("off")
    table = axis.table(cellText=shown_band.values, colLabels=list(shown_band.columns), cellLoc="center", loc="center", bbox=[0, 0, 1, 0.86])
    _style_table(table, 7)
    axis.set_title(f"{name}: coverage by band", fontsize=9, fontweight="bold", pad=2)
    fig.savefig(out / "summary_by_band.png", dpi=220, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)

    visit_columns = ["target_id", category_column, "RA", "DEC", *[f"n_images_{band}" for band in bands], "n_images_total", "has_visit_coverage"]
    group[[column for column in visit_columns if column in group]].to_csv(out / "individual_visits_by_target.csv", index=False)
    group.to_csv(out / "coverage_and_coadds.csv", index=False)

    shown = summary.rename(columns={"category": category_label, "type": "Type", "total": "Total"}).copy()
    shown["Type"] = shown["Type"].map({"visits": "Visits", "coadds_found": "Coadds found", "coadds_with_valid_metrics": "Coadds with\nvalid metrics", "coadds_with_no_valid_pixels": "Coadds with\nno valid pixels"})
    for column in shown.columns[2:]: shown[column] = shown[column].map(lambda value: f"{int(value):,}")
    fig, axis = plt.subplots(figsize=(9.5, 5.0), dpi=220)
    axis.axis("off")
    table = axis.table(cellText=shown.values, colLabels=list(shown.columns), cellLoc="center", loc="center", bbox=[0, 0.015, 1, 0.91])
    _style_table(table, 7.0)
    axis.set_title(f"{name}: visits and coadds by {category_label}", fontsize=10, fontweight="bold", y=0.975, pad=0)
    fig.savefig(out / f"summary_by_{category_column}.png", dpi=220, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return summary


def plot_collection_distributions(
    coverage: pd.DataFrame,
    output_dir: str | Path,
    *,
    bands: Sequence[str] = tuple("ugrizy"),
    category_column: str = "grade",
    category_label: str = "Grade",
    name: str = "collection",
) -> None:
    """Write clear, high-resolution coverage and property distributions.

    The first panel uses the total number of individual visits per target.  It
    does not stack per-band visit distributions, because stacking counts the
    same target once per band and can therefore exceed the catalog size.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    group = coverage.copy()
    colors = dict(zip(bands, plt.get_cmap("tab10").colors[:len(bands)]))

    visit_columns = [f"n_images_{band}" for band in bands if f"n_images_{band}" in group]
    total_visits = numeric_series(group, "n_images_total")
    if total_visits.isna().all() and visit_columns:
        total_visits = group[visit_columns].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    total_visits = total_visits.fillna(0)
    filters_with_visits = group[visit_columns].apply(pd.to_numeric, errors="coerce").fillna(0).gt(0).sum(axis=1) if visit_columns else pd.Series(0, index=group.index)
    found_bands = group[[f"has_coadd_{band}" for band in bands if f"has_coadd_{band}" in group]].fillna(False).astype(bool).sum(axis=1)
    valid_bands = group[[f"has_valid_coadd_{band}" for band in bands if f"has_valid_coadd_{band}" in group]].fillna(False).astype(bool).sum(axis=1)
    empty_bands = group[[f"has_empty_coadd_{band}" for band in bands if f"has_empty_coadd_{band}" in group]].fillna(False).astype(bool).sum(axis=1)

    def integer_limit(values: pd.Series, percentile: float = 99.0) -> tuple[int, int]:
        numeric = pd.to_numeric(values, errors="coerce").fillna(0)
        limit = max(1, int(np.ceil(np.nanpercentile(numeric, percentile)))) if len(numeric) else 1
        tail = int(numeric.gt(limit).sum())
        return limit, tail

    def set_tail_note(axis, tail: int, percentile: float, limit: int) -> None:
        if tail:
            axis.text(0.98, 0.97, f"x-axis shown to {percentile:.0f}th percentile ({limit});\n{tail:,} targets above", transform=axis.transAxes, ha="right", va="top", fontsize=7, color="dimgray")

    def visit_bin_width(limit: int) -> int:
        if limit <= 15:
            return 1
        if limit <= 40:
            return 2
        if limit <= 100:
            return 5
        return 10

    fig, axes = plt.subplots(2, 3, figsize=(14, 8), dpi=220)
    visit_limit, visit_tail = integer_limit(total_visits.loc[total_visits.gt(0)])
    positive_visits = total_visits.loc[total_visits.gt(0)]
    visit_width = visit_bin_width(visit_limit)
    axes[0, 0].hist(positive_visits.clip(upper=visit_limit), bins=np.arange(0.5, visit_limit + visit_width + 0.5, visit_width), color="tab:blue", edgecolor="none")
    axes[0, 0].set(title="Individual visits per target (targets with ≥1 visit)", xlabel="Total number of individual visits", ylabel="Number of targets", xlim=(0.5, visit_limit + 0.5))
    zero_visits = int(total_visits.eq(0).sum())
    axes[0, 0].text(0.98, 0.97, f"Targets with 0 visits excluded: {zero_visits:,}", transform=axes[0, 0].transAxes, ha="right", va="top", fontsize=7, color="dimgray")
    if visit_tail:
        axes[0, 0].text(0.98, 0.88, f"x-axis shown to 99th percentile ({visit_limit});\n{visit_tail:,} targets above", transform=axes[0, 0].transAxes, ha="right", va="top", fontsize=7, color="dimgray")

    x = np.arange(len(bands)); width = 0.22
    visit_targets = [int(pd.to_numeric(group.get(f"n_images_{band}", pd.Series(0, index=group.index)), errors="coerce").fillna(0).gt(0).sum()) for band in bands]
    found_targets = [int(group.get(f"has_coadd_{band}", pd.Series(False, index=group.index)).fillna(False).astype(bool).sum()) for band in bands]
    valid_targets = [int(group.get(f"has_valid_coadd_{band}", pd.Series(False, index=group.index)).fillna(False).astype(bool).sum()) for band in bands]
    empty_targets = [int(group.get(f"has_empty_coadd_{band}", pd.Series(False, index=group.index)).fillna(False).astype(bool).sum()) for band in bands]
    axes[0, 1].bar(x - 1.5 * width, visit_targets, width, label="Targets with visits", color="tab:blue")
    axes[0, 1].bar(x - 0.5 * width, found_targets, width, label="Coadds found", color="tab:orange")
    axes[0, 1].bar(x + 0.5 * width, valid_targets, width, label="Coadds with valid metrics", color="tab:green")
    axes[0, 1].bar(x + 1.5 * width, empty_targets, width, label="Coadds with no valid pixels", color="tab:red")
    axes[0, 1].set(xticks=x, xticklabels=bands, title="Coverage by band", xlabel="Band", ylabel="Number of targets")
    axes[0, 1].legend(fontsize=7)

    band_count_series = [filters_with_visits, found_bands, valid_bands, empty_bands]
    band_count_labels = ["Visits", "Coadds found", "Valid metrics", "No valid pixels"]
    band_count_colors = ["tab:blue", "tab:orange", "tab:green", "tab:red"]
    axes[0, 2].hist([values.loc[values.gt(0)] for values in band_count_series], bins=np.arange(0.5, len(bands) + 1.5, 1), label=band_count_labels, color=band_count_colors, alpha=0.65, rwidth=0.85)
    axes[0, 2].set(xticks=np.arange(1, len(bands) + 1), title="Covered bands per target (zero counts excluded)", xlabel="Number of bands", ylabel="Number of targets")
    axes[0, 2].legend(fontsize=7)
    zero_note = "Excluded zero counts:\n" + "\n".join(f"{label}: {int(values.eq(0).sum()):,}" for label, values in zip(band_count_labels, band_count_series))
    axes[0, 2].text(0.03, 0.97, zero_note, transform=axes[0, 2].transAxes, ha="left", va="top", fontsize=6.5, color="dimgray", bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "none", "pad": 2})

    def overlay(axis, columns, title, xlabel):
        values = [numeric_series(group, column).dropna() for column in columns]
        values = [value for value in values if len(value)]
        if values:
            edges = np.histogram_bin_edges(pd.concat(values), bins=25)
            for band, column in zip(bands, columns):
                value = numeric_series(group, column).dropna()
                if len(value):
                    axis.hist(value, bins=edges, color=colors[band], alpha=0.22, linewidth=1.4, label=band)
        axis.set(title=title, xlabel=xlabel, ylabel="Number of targets")
        axis.legend(title="Band", ncol=3, fontsize=8)

    overlay(axes[1, 0], [f"coadd_maglim_{band}" for band in bands], "Coadd limiting magnitude", "Limiting magnitude")
    overlay(axes[1, 1], [f"coadd_psf_fwhm_arcsec_{band}" for band in bands], "Coadd PSF FWHM", "FWHM [arcsec]")
    overlay(axes[1, 2], [f"visit_seeing_mean_arcsec_{band}" for band in bands], "Mean visit seeing", "Seeing [arcsec]")
    fig.suptitle(f"{name} — coverage and image properties", fontsize=14)
    fig.tight_layout()
    fig.savefig(out / "distributions.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    category_values = group[category_column].fillna("unknown").astype(str) if category_column in group and group[category_column].notna().any() else pd.Series("all", index=group.index)
    group = group.copy()
    group["_category_label"] = category_values.map(lambda value: "unknown" if value.lower() in {"", "nan", "none", "unavailable"} else value)
    group["_total_visits"] = total_visits
    group["_filters_with_visits"] = filters_with_visits
    group["_found_bands"] = found_bands
    group["_valid_bands"] = valid_bands
    group["_empty_bands"] = empty_bands
    group["_coadd_depth_mean"] = group[[f"coadd_maglim_{band}" for band in bands if f"coadd_maglim_{band}" in group]].mean(axis=1)
    group["_coadd_psf_mean"] = group[[f"coadd_psf_fwhm_arcsec_{band}" for band in bands if f"coadd_psf_fwhm_arcsec_{band}" in group]].mean(axis=1)
    group["_visit_seeing_mean"] = group[[f"visit_seeing_mean_arcsec_{band}" for band in bands if f"visit_seeing_mean_arcsec_{band}" in group]].mean(axis=1)
    category_colors = dict(zip(sorted(group["_category_label"].unique()), plt.get_cmap("tab10").colors))
    fig, axes = plt.subplots(2, 3, figsize=(14, 8), dpi=220)
    for category, color in category_colors.items():
        subset = group.loc[group["_category_label"].eq(category)]
        category_visits = subset.loc[subset["_total_visits"].gt(0), "_total_visits"]
        axes[0, 0].hist(category_visits.clip(upper=visit_limit), bins=np.arange(0.5, visit_limit + visit_width + 0.5, visit_width), histtype="step", linewidth=1.5, color=color, label=category)
        visit_by_band = [int(pd.to_numeric(subset.get(f"n_images_{band}", pd.Series(0, index=subset.index)), errors="coerce").fillna(0).gt(0).sum()) for band in bands]
        axes[0, 1].plot(np.arange(len(bands)), visit_by_band, marker="o", linewidth=1.5, color=color, label=category)
        for column, linestyle, suffix in (("_found_bands", "-", "found"), ("_valid_bands", "--", "valid metrics"), ("_empty_bands", ":", "no valid pixels")):
            values = subset[column]
            axes[0, 2].hist(values.loc[values.gt(0)], bins=np.arange(0.5, len(bands) + 1.5, 1), histtype="step", linewidth=1.5, color=color, linestyle=linestyle, label=f"{category} — {suffix}")
        for axis, column in zip(axes[1], ("_coadd_depth_mean", "_coadd_psf_mean", "_visit_seeing_mean")):
            values = pd.to_numeric(subset[column], errors="coerce").dropna()
            if len(values): axis.hist(values, bins=25, histtype="step", linewidth=1.5, color=color, label=category)
    labels = [("Total visits per target", "Total visits"), ("Targets with visits by band", "Band"), ("Coadd coverage by number of bands", "Number of bands"), ("Coadd depth", "Limiting magnitude"), ("Coadd PSF FWHM", "FWHM [arcsec]"), ("Visit seeing", "Seeing [arcsec]")]
    for axis, (title, xlabel) in zip(axes.flat, labels):
        axis.set_title(f"{title} by {category_label}", fontsize=9, pad=11)
        axis.set_xlabel(xlabel, fontsize=8, labelpad=4)
        axis.set_ylabel("Number of targets", fontsize=8, labelpad=4)
        axis.grid(alpha=0.25); axis.xaxis.set_major_locator(MaxNLocator(nbins=8))
    axes[0, 0].text(0.98, 0.97, f"Targets with 0 visits excluded: {zero_visits:,}", transform=axes[0, 0].transAxes, ha="right", va="top", fontsize=7, color="dimgray")
    axes[0, 1].set_xticks(np.arange(len(bands)), bands)
    axes[0, 0].legend(title=category_label, fontsize=8); axes[0, 1].legend(title=category_label, fontsize=8); axes[0, 2].legend(fontsize=7)
    fig.suptitle(f"{name} — distributions by {category_label}", fontsize=14, y=0.99)
    fig.subplots_adjust(left=0.07, right=0.98, bottom=0.08, top=0.88, hspace=0.62, wspace=0.34)
    fig.savefig(out / f"distributions_by_{category_column}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    # Additional per-category figures: the same individual-visit diagnostic,
    # separated by band and restricted to one category at a time. Targets with
    # zero visits in a band are excluded from that band's histogram.
    for category, subset in group.groupby("_category_label", sort=True):
        fig, axis = plt.subplots(figsize=(9, 6), dpi=220)
        for band, color in colors.items():
            values = numeric_series(subset, f"n_images_{band}").fillna(0)
            values = values.loc[values.gt(0)]
            if not len(values):
                continue
            band_limit, band_tail = integer_limit(values)
            band_width = visit_bin_width(band_limit)
            axis.hist(values.clip(upper=band_limit), bins=np.arange(0.5, band_limit + band_width + 0.5, band_width), histtype="step", linewidth=1.6, color=color, label=band)
        safe_category = "".join(character if character.isalnum() or character in "-_" else "_" for character in str(category))
        axis.set(title=f"Individual visits by band — {category_label}: {category}\n(targets with ≥1 visit in the band)", xlabel="Number of individual visits in band", ylabel="Number of targets", xlim=(0.5, None))
        axis.legend(title="Band", ncol=3, fontsize=8)
        axis.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(out / f"individual_visits_by_{category_column}_{safe_category}.png", dpi=300, bbox_inches="tight")
        plt.close(fig)
