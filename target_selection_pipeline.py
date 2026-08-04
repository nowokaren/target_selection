"""Pipeline MOP + Rubin configurable para ejecutar desde un notebook o un script.

El archivo no importa módulos de Rubin al importarse. El notebook debe pasarle
los objetos ``tap_service`` y, opcionalmente, una función ``target_plotter``.
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

from data_release_config import DataReleaseConfig, get_data_release


CACHE_VERSION = 3
TARGET_REPORT_VERSION = 4


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
    fig_width = max(16, min(28, width_units.sum() * .115))
    fig_height = max(5, 1.25 + .285 * (len(display) + 1))
    fig = plt.figure(figsize=(fig_width, fig_height))
    ax = fig.add_axes([.015, .015, .97, .885])
    ax.axis("off")
    fig.suptitle(f"Resumen de targets MOP + Rubin {release_name}", fontsize=15, fontweight="bold", y=.995)
    fig.text(
        .5, .955,
        f"Visibles: {n_visible}  |  Con cobertura {release_name}: {n_matched}  |  "
        f"Con fotometría MOP: {n_photometry}  |  Con cobertura + fotometría: {n_matched_photometry}",
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


# Backward-compatible aliases; prefer the release-neutral public names above.
query_dp2_coverage = query_release_coverage
summarize_dp2_coverage = summarize_release_coverage


def plot_sky_metric(
    targets: pd.DataFrame,
    metric: str,
    output_path: str | Path,
    title: str | None = None,
    projection: str = "galactic",
    show_regions: bool = True,
) -> None:
    """Plot targets on a sky projection, with a separate reference panel.

    The default Galactic Mollweide projection makes the Galactic plane and
    bulge easy to identify. ``projection="equatorial"`` keeps RA/Dec instead.
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    data = targets.dropna(subset=["RA_deg", "Dec_deg"]).reset_index(drop=True)
    values = pd.to_numeric(data[metric], errors="coerce").fillna(0)

    coords = SkyCoord(data["RA_deg"].to_numpy() * u.deg, data["Dec_deg"].to_numpy() * u.deg, frame="icrs")
    use_galactic = projection.casefold() == "galactic"
    if use_galactic:
        galactic = coords.galactic
        longitude = galactic.l.wrap_at(180 * u.deg).radian
        latitude = galactic.b.radian
        xlabel, ylabel = "Galactic longitude l", "Galactic latitude b"
    else:
        longitude = coords.ra.wrap_at(180 * u.deg).radian
        latitude = coords.dec.radian
        xlabel, ylabel = "RA", "Dec"

    fig = plt.figure(figsize=(16, 9), constrained_layout=True)
    grid = fig.add_gridspec(1, 2, width_ratios=[3.5, 1.7], wspace=0.22)
    ax = fig.add_subplot(grid[0], projection="mollweide")
    side = grid[1].subgridspec(2, 1, height_ratios=[1, 5], hspace=0.25)
    colorbar_ax = fig.add_subplot(side[0])
    legend_ax = fig.add_subplot(side[1])
    legend_ax.axis("off")

    if use_galactic and show_regions:
        ax.axhspan(np.deg2rad(-8), np.deg2rad(8), color="gold", alpha=0.10, zorder=0)
        ax.scatter([0], [0], marker="x", color="darkorange", s=80, linewidths=1.5, zorder=2)
        ax.text(0.02, 0.08, "Bulbo", transform=ax.transAxes, color="darkorange", fontsize=9)
        # Approximate Galactic coordinates of the Magellanic Clouds.
        for lon, lat, label in [(280, -33, "LMC"), (303, -44, "SMC")]:
            x = np.deg2rad(((lon + 180) % 360) - 180)
            y = np.deg2rad(lat)
            ax.scatter([x], [y], marker="s", facecolors="none", edgecolors="deepskyblue", s=70, zorder=2)
            ax.text(x, y, f"  {label}", color="deepskyblue", fontsize=8, va="center")

    scatter = ax.scatter(longitude, latitude, c=values, cmap="viridis", s=45, edgecolor="black", linewidth=.3, zorder=3)
    for number, row in data.iterrows():
        ax.annotate(str(number + 1), (longitude[number], latitude[number]), xytext=(3, 3), textcoords="offset points", fontsize=7, zorder=4)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title or f"Targets colored by {metric}")
    ax.grid(True, alpha=0.35)
    fig.colorbar(scatter, cax=colorbar_ax, label=metric, orientation="horizontal")
    legend = "\n".join(f"{i + 1}: {name}" for i, name in enumerate(data["Target"]))
    legend_ax.text(0, 1, "Referencias", va="top", fontsize=10, weight="bold")
    legend_ax.text(0, 0.95, legend, va="top", fontsize=7, family="monospace", linespacing=1.25)
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def _write_report_versions(path: Path, versions: dict[str, int]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(versions, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _migrate_target_report(output_dir: Path, target_name: str, report_path: Path) -> int | None:
    """Move the newest legacy per-target report into the flat layout."""
    candidates = sorted(
        output_dir.glob(f"*_{_safe_name(target_name)}/target_report.png"),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    if not candidates:
        return None
    source = candidates[0]
    version_path = source.parent / ".report_version"
    try:
        version = int(version_path.read_text().strip())
    except (OSError, ValueError):
        version = 0
    source.replace(report_path)
    for candidate in candidates:
        if candidate.exists():
            candidate.unlink()
        legacy_version = candidate.parent / ".report_version"
        legacy_version.unlink(missing_ok=True)
        try:
            candidate.parent.rmdir()
        except OSError:
            pass
    return version


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
    generated = skipped = no_coadd = migrated = 0
    for number, (_, row) in enumerate(targets.iterrows(), start=1):
        target_name = str(row["Target"])
        file_key = _safe_name(target_name)
        report_path = output_dir / f"{file_key}_target_report.png"
        if not report_path.exists():
            legacy_version = _migrate_target_report(output_dir, target_name, report_path)
            if legacy_version is not None:
                versions[file_key] = legacy_version
                migrated += 1
                _write_report_versions(versions_path, versions)
        if verbose and (number == 1 or number % 10 == 0 or number == total):
            print(
                f"    targets revisados: {number}/{total} | generados={generated}, "
                f"migrados={migrated}, cache={skipped}, sin coadd={no_coadd}, errores={len(plot_errors)}",
                flush=True,
            )
        current_version = str(versions.get(file_key, ""))
        if report_path.exists() and current_version == str(TARGET_REPORT_VERSION) and not overwrite:
            skipped += 1
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
                print(f"    error en {row['Target']}: {short_message}", flush=True)
            continue
        if figure is None:
            no_coadd += 1
            report_path.unlink(missing_ok=True)
            versions.pop(file_key, None)
            _write_report_versions(versions_path, versions)
            continue
        figure.savefig(report_path, dpi=180, bbox_inches="tight")
        plt.close(figure)
        versions[file_key] = TARGET_REPORT_VERSION
        _write_report_versions(versions_path, versions)
        generated += 1
    if verbose:
        print(
            f"    reportes terminados: generados={generated}, migrados={migrated}, "
            f"cache={skipped}, sin coadd={no_coadd}, errores={len(plot_errors)}", flush=True,
        )
    pd.DataFrame(plot_errors, columns=["Target", "error"]).to_csv(
        output_dir.parent / "plot_errors.csv", index=False
    )


def run_pipeline(
    mop,
    tap_service,
    start_date: str,
    end_date: str | None = None,
    root_dir: str | Path = "target_selection_outputs",
    observatory: str = "El Leoncito",
    target_plotter: Callable | None = None,
    max_workers: int = 4,
    reuse_cache: bool = True,
    overwrite_target_plots: bool = False,
    verbose: bool = True,
    data_release: str | DataReleaseConfig = "DP2",
) -> tuple[pd.DataFrame, dict[str, Path]]:
    """Run the pipeline with a configurable Rubin data-release profile."""
    release = get_data_release(data_release)
    end_date = end_date or start_date
    paths = create_run_structure(root_dir, start_date, end_date, release)
    daily_path = paths["tables"] / "visible_targets_daily.csv"
    summary_path = paths["tables"] / "visible_summary.csv"
    coverage_path = paths["tables"] / "coverage_raw.csv"
    legacy_dp2_path = paths["tables"] / "dp2_coverage_raw.csv"
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
        print("[1/5] Targets visibles MOP" + (" (cache)" if cache_valid else ""), flush=True)
    if cache_valid and daily_path.exists():
        daily = pd.read_csv(daily_path)
    else:
        daily = mop.visible_targets(
            observatory=observatory, start_date=start_date, end_date=end_date,
            sort_by_mag=False,
        )
        daily.to_csv(daily_path, index=False)

    if verbose:
        print("[2/5] Parámetros + fotometría MOP" + (" (cache)" if cache_valid else ""), flush=True)
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
        print(f"[3/5] Cobertura {release.name}" + (" (cache)" if cache_valid else f" ({max_workers} workers)"), flush=True)
    if cache_valid and coverage_path.exists():
        coverage_rows = pd.read_csv(coverage_path)
    elif cache_valid and release.name == "DP2" and legacy_dp2_path.exists():
        # Migrate the previous DP2-specific cache without repeating TAP queries.
        coverage_rows = pd.read_csv(legacy_dp2_path)
        coverage_rows.to_csv(coverage_path, index=False)
    else:
        coverage_rows = query_release_coverage(
            summary, tap_service, max_workers=max_workers, data_release=release
        )
        coverage_rows.to_csv(coverage_path, index=False)

    if verbose:
        print("[4/5] Tablas y mapas", flush=True)
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

    if "coverage_n_visits" in combined:
        plot_sky_metric(combined, "coverage_n_visits", paths["sky_plots"] / "sky_by_coverage_visits.png", f"Targets colored by {release.name} visits")
    if "mag_now" in combined:
        plot_sky_metric(combined, "mag_now", paths["sky_plots"] / "sky_by_mag_now.png", "Targets colored by MOP mag_now")
    if target_plotter is not None:
        if verbose:
            print("[5/5] Reportes por target", flush=True)
        save_target_reports(
            combined, target_plotter, paths["targets"], coverage_rows,
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
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if verbose:
        print(f"Pipeline completo en {(perf_counter() - started) / 60:.1f} min", flush=True)
    return combined, paths
