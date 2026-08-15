"""Multipage monitoring light-curve reports across MOP, Rubin, and local surveys."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import re
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

from mop_photometry import load_event_photometry, prepare_lightcurve_data, select_lightcurve_filters
from release_photometry import prepare_release_lightcurve_data


DEFAULT_MONITORING_LAYERS = (
    "mop_photometry",
    "release_epochs",
    "release_photometry",
    "hsh",
    "js",
)
VALID_MONITORING_LAYERS = frozenset(DEFAULT_MONITORING_LAYERS)
PROVIDER_STYLES = {
    "LSST": {"color": "#6b7280", "alpha": 0.16, "label": "Rubin/LSST epochs"},
    "HSH": {"color": "#d97706", "alpha": 0.18, "label": "HSH images"},
    "JS": {"color": "#0f766e", "alpha": 0.18, "label": "JS images"},
}


def normalize_monitoring_layers(layers: Iterable[str] | None = None) -> tuple[str, ...]:
    """Validate and normalize the selectable monitoring-report layers."""
    result = DEFAULT_MONITORING_LAYERS if layers is None else tuple(str(item).lower() for item in layers)
    unknown = set(result) - VALID_MONITORING_LAYERS
    if unknown:
        choices = ", ".join(sorted(VALID_MONITORING_LAYERS))
        raise ValueError(f"Unknown monitoring layers: {sorted(unknown)}. Options: {choices}")
    return tuple(dict.fromkeys(result))


def _mjd_to_dates(values: pd.Series | np.ndarray) -> np.ndarray:
    mjd = pd.to_numeric(values, errors="coerce")
    timestamps = pd.to_datetime(mjd, unit="D", origin="1858-11-17", errors="coerce")
    timestamps = timestamps[~pd.isna(timestamps)]
    return mdates.date2num(timestamps.to_numpy(dtype="datetime64[us]"))


def _monitoring_future_xmax(created_at: object | None = None) -> float:
    """Return the common light-curve horizon: two calendar months after creation."""
    timestamp = pd.Timestamp.now(tz="UTC") if created_at is None else pd.Timestamp(created_at)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(None)
    return float(mdates.date2num((timestamp + pd.DateOffset(months=2)).to_pydatetime()))




def _sort_targets_by_mag_now(targets: pd.DataFrame) -> pd.DataFrame:
    """Sort targets from bright to faint using valid current MOP magnitudes.

    Missing, non-numerical, and zero magnitudes are placed last because MOP
    uses zero to represent an unavailable current magnitude.
    """
    records = targets.drop_duplicates("Target").copy()
    if "mag_now" in records:
        magnitude = pd.to_numeric(records["mag_now"], errors="coerce")
    else:
        magnitude = pd.Series(np.nan, index=records.index, dtype=float)
    records["_mag_now_sort"] = magnitude.where(magnitude.gt(0))
    return (
        records.sort_values(
            ["_mag_now_sort", "Target"],
            na_position="last",
            kind="stable",
        )
        .drop(columns="_mag_now_sort")
        .reset_index(drop=True)
    )
def _nominal_parameter(value: object) -> float:
    """Extract a numerical parameter value from MOP value±uncertainty text."""
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
    return float(match.group()) if match else np.nan


def _microlensing_zoom_limits(target: pd.Series) -> tuple[float, float] | None:
    """Return Matplotlib date limits for the MOP window t0 ± 2 tE."""
    t_e_days = _nominal_parameter(target.get("mop_t_e_days", np.nan))
    t_0_hjd = _nominal_parameter(target.get("mop_t_0_hjd", np.nan))
    if not np.isfinite(t_e_days) or t_e_days <= 0 or not np.isfinite(t_0_hjd):
        return None
    center = _mjd_to_dates(pd.Series([t_0_hjd - 2400000.5]))
    if not len(center):
        return None
    return float(center[0] - 2 * t_e_days), float(center[0] + 2 * t_e_days)


def _shade_epochs(
    ax, dates: np.ndarray, *, provider: str, exposure_seconds: pd.Series | np.ndarray | None = None,
    ymin: float = 0.0, ymax: float = 1.0,
) -> Patch | None:
    """Shade one vertical coverage lane, darkening nights with more exposure time."""
    if not len(dates):
        return None
    style = PROVIDER_STYLES[provider]
    exposures = (
        pd.to_numeric(pd.Series(exposure_seconds), errors="coerce").fillna(0.0).to_numpy(float)
        if exposure_seconds is not None else np.zeros(len(dates), dtype=float)
    )
    if len(exposures) != len(dates):
        exposures = np.zeros(len(dates), dtype=float)
    data = pd.DataFrame({"night": np.floor(dates), "exposure_s": exposures})
    grouped = data.groupby("night", sort=False).agg(
        n_epochs=("night", "size"), exposure_s=("exposure_s", "sum"),
    )
    has_exposure = bool(np.isfinite(exposures).any() and np.any(exposures > 0))
    for night, values in grouped.iterrows():
        amount = float(values["exposure_s"] / 3600) if has_exposure else float(values["n_epochs"])
        alpha = style["alpha"] + .22 * min(1.0, amount / (2.0 if has_exposure else 5.0))
        ax.axvspan(
            float(night), float(night) + 1.0, ymin=ymin, ymax=ymax,
            color=style["color"], alpha=alpha, lw=0, zorder=0,
        )
    count = int(grouped["n_epochs"].sum())
    detail = f", {grouped['exposure_s'].sum() / 3600:.1f} h" if has_exposure else ""
    return Patch(
        facecolor=style["color"], alpha=style["alpha"],
        label=f"{style['label']} (N={count}{detail})",
    )

def _provider_photometry_points(data: pd.DataFrame | None, provider: str) -> pd.DataFrame:
    """Return normalized local-survey photometry for one provider, if supplied."""
    if data is None or data.empty or "provider" not in data:
        return pd.DataFrame(columns=["Timestamp", "Magnitude", "Error", "Filter"])
    points = data.loc[data["provider"].astype(str).str.upper().eq(provider)].copy()
    if points.empty:
        return pd.DataFrame(columns=["Timestamp", "Magnitude", "Error", "Filter"])
    if "Timestamp" in points:
        timestamps = pd.to_datetime(points["Timestamp"], errors="coerce")
    elif "mjd" in points:
        timestamps = pd.to_datetime(pd.to_numeric(points["mjd"], errors="coerce"), unit="D", origin="1858-11-17", errors="coerce")
    else:
        return pd.DataFrame(columns=["Timestamp", "Magnitude", "Error", "Filter"])
    magnitude_column = next((column for column in ("Magnitude", "magnitude", "mag") if column in points), None)
    if magnitude_column is None:
        return pd.DataFrame(columns=["Timestamp", "Magnitude", "Error", "Filter"])
    error_column = next((column for column in ("Error", "error", "mag_error") if column in points), None)
    filter_column = next((column for column in ("Filter", "filter", "band") if column in points), None)
    normalized = pd.DataFrame({
        "Timestamp": timestamps,
        "Magnitude": pd.to_numeric(points[magnitude_column], errors="coerce"),
        "Error": pd.to_numeric(points[error_column], errors="coerce") if error_column else np.nan,
        "Filter": points[filter_column].astype(str) if filter_column else "",
    })
    return normalized.dropna(subset=["Timestamp", "Magnitude"])


def _plot_provider_photometry(ax, data: pd.DataFrame, provider: str) -> tuple[object | None, bool]:
    """Plot local photometry for a provider and return a legend handle and state."""
    if data.empty:
        return None, False
    style = PROVIDER_STYLES[provider]
    dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
    errors = data["Error"].to_numpy(float)
    yerr = np.where(np.isfinite(errors) & (errors >= 0), errors, np.nan)
    handle = ax.errorbar(
        dates, data["Magnitude"].to_numpy(float), yerr=yerr, fmt="o", ms=3.0,
        mfc="white", mec=style["color"], ecolor=style["color"], color=style["color"],
        alpha=.9, label=f"{provider} photometry (N={len(data)})", zorder=4,
    )
    return handle, True


def plot_monitoring_lightcurve(
    target: pd.Series,
    photometry: pd.DataFrame,
    *,
    release_photometry: pd.DataFrame | None = None,
    lsst_coverage: pd.DataFrame | None = None,
    observatory_epochs: pd.DataFrame | None = None,
    observatory_photometry: pd.DataFrame | None = None,
    data_release: str = "DP2",
    layers: Iterable[str] | None = None,
    title: str | None = None,
    show_legend: bool = True,
    future_xmax: float | None = None,
    enforce_future_horizon: bool = True,
    ax=None,
) -> plt.Axes:
    """Plot selected MOP, Rubin, HSH, and JS monitoring layers for one target."""
    selected_layers = normalize_monitoring_layers(layers)
    if ax is None:
        _, ax = plt.subplots(figsize=(10, 3.6))
    name = str(target["Target"])
    photometry = photometry if photometry is not None else pd.DataFrame()
    handles: list = []
    has_photometry = False
    filters = select_lightcurve_filters(photometry) if "mop_photometry" in selected_layers else []
    for filter_name in filters:
        data = prepare_lightcurve_data(photometry, filter_name)
        if data.empty:
            continue
        dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
        magnitudes = data["Magnitude"].to_numpy(float)
        errors = data["Error"].to_numpy(float) if "Error" in data else None
        valid = np.isfinite(dates) & np.isfinite(magnitudes)
        if not valid.any():
            continue
        yerr = None if errors is None else np.where(np.isfinite(errors[valid]) & (errors[valid] >= 0), errors[valid], np.nan)
        item = ax.errorbar(dates[valid], magnitudes[valid], yerr=yerr, fmt=".", ms=2.7, alpha=.8, label=f"MOP {filter_name}", zorder=3)
        handles.append(item)
        has_photometry = True

    # Optional published Rubin DIA/forced-source light curves. These are
    # distinct from VisitDetector coverage: a visit may exist without a valid
    # DIA photometric point.
    if "release_photometry" in selected_layers and release_photometry is not None and not release_photometry.empty:
        release_bands = sorted(str(band) for band in release_photometry.get("band", pd.Series(dtype=str)).dropna().unique())
        cmap = plt.get_cmap("tab10")
        for index, band in enumerate(release_bands):
            data = prepare_release_lightcurve_data(release_photometry, band)
            if data.empty:
                continue
            dates = mdates.date2num(data["Timestamp"].to_numpy(dtype="datetime64[us]"))
            magnitudes = data["Magnitude"].to_numpy(float)
            errors = data["Error"].to_numpy(float)
            valid = np.isfinite(dates) & np.isfinite(magnitudes)
            if not valid.any():
                continue
            yerr = np.where(np.isfinite(errors[valid]) & (errors[valid] >= 0), errors[valid], np.nan)
            item = ax.errorbar(
                dates[valid], magnitudes[valid], yerr=yerr, fmt="s", ms=2.8,
                mfc=cmap(index % 10), mec="black", mew=.3, color=cmap(index % 10),
                alpha=.9, label=f"{data_release} DIA {band} (N={valid.sum()})", zorder=4,
            )
            handles.append(item)
            has_photometry = True

    coverage_layers: list[dict] = []
    if (
        "release_epochs" in selected_layers and lsst_coverage is not None
        and not lsst_coverage.empty and "expMidptMJD" in lsst_coverage
    ):
        epochs = pd.to_numeric(lsst_coverage["expMidptMJD"], errors="coerce").dropna().drop_duplicates()
        dates = _mjd_to_dates(epochs)
        if len(dates):
            coverage_layers.append({
                "provider": "LSST", "dates": dates, "exposure_seconds": None,
                "label": f"{data_release} epochs (N={len(dates)})",
            })

    all_epochs = observatory_epochs if observatory_epochs is not None else pd.DataFrame()
    for provider, layer in (("HSH", "hsh"), ("JS", "js")):
        if layer not in selected_layers:
            continue
        local_points = _provider_photometry_points(observatory_photometry, provider)
        handle, plotted = _plot_provider_photometry(ax, local_points, provider)
        if plotted:
            handles.append(handle)
            has_photometry = True
            continue
        subset = all_epochs.loc[
            all_epochs["provider"].astype(str).str.upper().eq(provider)
        ].copy() if not all_epochs.empty and "provider" in all_epochs else pd.DataFrame()
        if subset.empty or "mjd" not in subset:
            continue
        subset["mjd"] = pd.to_numeric(subset["mjd"], errors="coerce")
        subset = subset.dropna(subset=["mjd"])
        if subset.empty:
            continue
        coverage_layers.append({
            "provider": provider,
            "dates": _mjd_to_dates(subset["mjd"]),
            "exposure_seconds": subset.get("exptime_s"),
            "label": None,
        })

    lane_count = len(coverage_layers)
    for index, coverage_layer in enumerate(coverage_layers):
        ymin = (lane_count - index - 1) / lane_count
        ymax = (lane_count - index) / lane_count
        handle = _shade_epochs(
            ax, coverage_layer["dates"], provider=coverage_layer["provider"],
            exposure_seconds=coverage_layer["exposure_seconds"], ymin=ymin, ymax=ymax,
        )
        if handle is not None:
            if coverage_layer["label"] is not None:
                handle.set_label(coverage_layer["label"])
            handles.append(handle)

    visible = bool(target.get("is_mop_visible_in_run", False))
    observed = bool(target.get("is_previously_observed", False))
    status = ", ".join(label for label, value in (("MOP visible", visible), ("previously observed", observed)) if value) or "registry target"
    te = target.get("mop_t_e_days", np.nan)
    t0 = target.get("mop_t_0_hjd", np.nan)
    parameter_text = "; ".join(f"{label}={value}" for label, value in (("t_E", te), ("t_0", t0)) if pd.notna(value))
    default_title = f"{name} — {status}" + (f" — {parameter_text}" if parameter_text else "")
    ax.set_title(default_title if title is None else title, fontsize=9)
    if handles and show_legend:
        ax.legend(handles=handles, loc="best", fontsize=7, frameon=False, ncol=min(3, len(handles)))
    if has_photometry:
        ax.invert_yaxis()
    else:
        ax.text(.5, .5, "No selected photometry available", transform=ax.transAxes, ha="center", va="center")
    ax.set_ylabel("Magnitude")
    locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.set_xlabel("Date")
    ax.grid(alpha=.22, zorder=1)
    if enforce_future_horizon:
        ax.set_xlim(right=_monitoring_future_xmax() if future_xmax is None else future_xmax)
    return ax


def create_monitoring_report(
    targets: pd.DataFrame,
    output_path: str | Path,
    *,
    mop,
    mop_photometry_dir: str | Path,
    release_photometry: pd.DataFrame | None = None,
    lsst_coverage: pd.DataFrame | None = None,
    observatory_epochs: pd.DataFrame | None = None,
    observatory_photometry: pd.DataFrame | None = None,
    data_release: str = "DP2",
    layers: Iterable[str] | None = None,
    plots_per_page: int = 3,
    photometry_loader: Callable[[pd.Series], pd.DataFrame] | None = None,
    created_at: object | None = None,
) -> dict[str, int | Path]:
    """Create a multipage PDF with selected MOP, Rubin, HSH, and JS layers.

    The default layers are MOP photometry, selected Data Release epochs, and
    HSH/JS data. A local provider is rendered as photometric points when its
    normalized photometry is supplied; otherwise all of its imported image epochs are shaded.
    """
    selected_layers = normalize_monitoring_layers(layers)
    if plots_per_page < 1:
        raise ValueError("plots_per_page must be at least one.")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = _sort_targets_by_mag_now(targets)
    future_xmax = _monitoring_future_xmax(created_at)
    coverage_groups = (
        {name: group for name, group in lsst_coverage.groupby("Target", sort=False)}
        if lsst_coverage is not None and not lsst_coverage.empty and "Target" in lsst_coverage else {}
    )
    epoch_groups = (
        {name: group for name, group in observatory_epochs.groupby("Target", sort=False)}
        if observatory_epochs is not None and not observatory_epochs.empty and "Target" in observatory_epochs else {}
    )
    photometry_groups = (
        {name: group for name, group in observatory_photometry.groupby("Target", sort=False)}
        if observatory_photometry is not None and not observatory_photometry.empty and "Target" in observatory_photometry else {}
    )
    release_photometry_groups = (
        {name: group for name, group in release_photometry.groupby("Target", sort=False)}
        if release_photometry is not None and not release_photometry.empty and "Target" in release_photometry else {}
    )
    loader = photometry_loader or (
        lambda target: load_event_photometry(target, mop=mop, cache_dir=mop_photometry_dir)
    )
    pages = 0
    with PdfPages(output_path) as pdf:
        for start in range(0, len(records), plots_per_page):
            page = records.iloc[start:start + plots_per_page]
            fig = plt.figure(figsize=(14.0, 3.5 * plots_per_page))
            grid = fig.add_gridspec(
                plots_per_page, 2, width_ratios=(2.25, 1.0), hspace=.48, wspace=.18,
            )
            for row_index, (_, target) in enumerate(page.iterrows()):
                name = str(target["Target"])
                target_photometry = loader(target) if "mop_photometry" in selected_layers else pd.DataFrame()
                main_axis = fig.add_subplot(grid[row_index, 0])
                zoom_axis = fig.add_subplot(grid[row_index, 1])
                plot_kwargs = {
                    "release_photometry": release_photometry_groups.get(name),
                    "lsst_coverage": coverage_groups.get(name),
                    "observatory_epochs": epoch_groups.get(name),
                    "observatory_photometry": photometry_groups.get(name),
                    "data_release": data_release,
                    "layers": selected_layers,
                }
                plot_monitoring_lightcurve(target, target_photometry, ax=main_axis, future_xmax=future_xmax, **plot_kwargs)
                limits = _microlensing_zoom_limits(target)
                if limits is None:
                    zoom_axis.set_axis_off()
                    continue
                plot_monitoring_lightcurve(
                    target, target_photometry, ax=zoom_axis, show_legend=False,
                    title="MOP zoom: $t_0 \\pm 2t_E$", enforce_future_horizon=False, **plot_kwargs,
                )
                zoom_axis.set_xlim(*limits)
            for row_index in range(len(page), plots_per_page):
                fig.add_subplot(grid[row_index, 0]).set_axis_off()
                fig.add_subplot(grid[row_index, 1]).set_axis_off()
            fig.suptitle("Target-selection monitoring report", fontsize=12, y=.995)
            fig.subplots_adjust(top=.94, bottom=.06, left=.06, right=.985)
            pdf.savefig(fig)
            plt.close(fig)
            pages += 1
    return {"path": output_path, "n_targets": len(records), "n_pages": pages}
