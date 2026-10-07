"""Reusable products for seasonal observing planning.

The module deliberately keeps the planning steps separate: visibility is a
nightly table, summaries are target-level tables, and report assembly only
combines already-created PNG products.  This makes the same functions useful
from notebooks and from a terminal script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
import pandas as pd

from .reporting import write_table_png
from .tasks.visibility import evaluate_visibility, save_visibility_plots


def _target_column(frame: pd.DataFrame) -> str:
    for name in ("target_id", "Target"):
        if name in frame:
            return name
    raise ValueError("Targets must contain target_id or Target.")


def _merge_candidate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Merge duplicate target rows without discarding source-specific metadata."""
    work = frame.copy()
    work["target_id"] = work["target_id"].astype(str)
    rows = []
    for target_id, group in work.groupby("target_id", sort=False):
        merged = group.iloc[0].copy()
        source_names = []
        for value in group.get("provider", pd.Series(dtype=object)).dropna():
            source_names.extend(str(value).split(","))
        for value in group.get("input_sources", pd.Series(dtype=object)).dropna():
            source_names.extend(str(value).split(","))
        merged["source_providers"] = ",".join(sorted({item.strip().lower() for item in source_names if item.strip()}))
        merged["input_sources"] = merged["source_providers"]
        for column in group.columns:
            values = group[column]
            usable = values.loc[values.notna() & values.astype(str).str.strip().ne("")]
            if pd.isna(merged.get(column)) or str(merged.get(column)).strip() in {"", "nan", "None"}:
                if not usable.empty:
                    merged[column] = usable.iloc[0]
        rows.append(merged)
    return pd.DataFrame(rows).reset_index(drop=True)


def load_candidates(
    providers: Sequence[str],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    filters: dict | None = None,
    include_photometry: bool = False,
    cache_dir: str | Path = "outputs/planning/cache",
    verbose: bool = True,
) -> pd.DataFrame:
    """Load and combine targets from configured providers.

    TOMs that support a native visibility query receive the requested dates.
    Other providers use their normal target catalogue query.  Photometry is
    intentionally collected in the separate ``collect_provider_photometry``
    step so it can be cached and filtered before expensive survey queries.
    """
    from .data_source import data_source

    frames: list[pd.DataFrame] = []
    for name in providers:
        provider = data_source.get(name)
        kwargs = {"filters": filters or {}}
        if name.lower() == "mop":
            if not start_date:
                raise ValueError("MOP target loading requires start_date.")
            kwargs.update({
                "start_date": start_date,
                "end_date": end_date or start_date,
                "include_event_data": include_photometry,
                "cache_dir": Path(cache_dir) / "mop",
            })
        try:
            frame = provider.load_targets(**kwargs)
        except (NotImplementedError, FileNotFoundError) as exc:
            if verbose:
                print(f"[planning] Skipping {name}: {exc}", flush=True)
            continue
        if frame is not None and not frame.empty:
            frame = frame.copy()
            if "provider" not in frame:
                frame["provider"] = name
            frames.append(frame)
            if verbose:
                print(f"[planning] {name}: {len(frame):,} targets", flush=True)
    if not frames:
        return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg", "provider"])
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if "target_id" not in combined and "Target" in combined:
        combined = combined.rename(columns={"Target": "target_id"})
    if "target_id" in combined:
        combined["target_id"] = combined["target_id"].astype(str)
        combined = _merge_candidate_rows(combined)
    return combined


def collect_photometry(
    targets: pd.DataFrame,
    *,
    providers: Sequence[str] = ("mop", "opm"),
    include_associated_surveys: bool = True,
    cache_dir: str | Path = "outputs/planning/cache",
    verbose: bool = True,
) -> pd.DataFrame:
    """Collect normalized photometry and automatically add associated surveys."""
    from .data_source import data_source

    work = targets.copy()
    if "target_id" not in work and "Target" in work:
        work = work.rename(columns={"Target": "target_id"})
    records: list[pd.DataFrame] = []
    for name in providers:
        provider = data_source.get(name)
        if not hasattr(provider, "photometry"):
            continue
        source_mask = pd.Series(True, index=work.index)
        if "source_providers" in work:
            source_mask = work["source_providers"].fillna("").astype(str).str.split(",").map(lambda values: name.lower() in {item.strip().lower() for item in values})
        elif "provider" in work:
            source_mask = work["provider"].astype(str).str.lower().eq(name.lower())
        provider_targets = work.loc[source_mask].copy()
        if provider_targets.empty:
            continue
        try:
            points = provider.photometry(provider_targets, cache_dir=Path(cache_dir) / name, errors="ignore", verbose=verbose)
        except TypeError:
            points = provider.photometry(work)
        except Exception as exc:
            if verbose:
                print(f"[planning] Photometry unavailable from {name}: {exc}", flush=True)
            continue
        if points is not None and not points.empty:
            records.append(points.assign(provider=points.get("provider", name)))
            if verbose:
                print(f"[planning] {name}: {len(points):,} photometry rows", flush=True)
    if include_associated_surveys and "target_id" in work:
        ogle_targets = work.loc[work["target_id"].astype(str).str.upper().str.startswith("OGLE-")].copy()
        if not ogle_targets.empty:
            try:
                points = data_source.ogle.photometry(ogle_targets, cache_dir=Path(cache_dir) / "ogle", errors="ignore")
                if not points.empty:
                    records.append(points.assign(provider="ogle"))
                    if verbose:
                        print(f"[planning] ogle: {len(points):,} complementary photometry rows", flush=True)
            except Exception as exc:
                if verbose:
                    print(f"[planning] Complementary OGLE photometry unavailable: {exc}", flush=True)
    if not records:
        return pd.DataFrame(columns=["target_id", "provider", "epoch_hjd", "magnitude", "magnitude_err", "band"])
    return pd.concat(records, ignore_index=True, sort=False)


# Compatibility aliases for notebooks created before the public names were
# shortened.  New code should use ``load_candidates`` and
# ``collect_photometry``.
load_provider_targets = load_candidates
collect_provider_photometry = collect_photometry


def apply_magnitude_limit(targets: pd.DataFrame, maximum: float | None) -> pd.DataFrame:
    """Keep targets with a usable current magnitude below ``maximum``."""
    if maximum is None:
        return targets.copy()
    result = targets.copy()
    candidates = ["magnitude_last", "magnitude", "mag_now", "mop_mag_now"]
    magnitude = next((pd.to_numeric(result[column], errors="coerce") for column in candidates if column in result), None)
    if magnitude is None:
        result["passes_magnitude"] = False
    else:
        result["passes_magnitude"] = magnitude.le(float(maximum))
    return result.loc[result["passes_magnitude"]].copy()


def summarize_visibility(selection: pd.DataFrame) -> pd.DataFrame:
    """Create one visibility-summary row per target from target-night rows."""
    if selection.empty:
        return pd.DataFrame(columns=[
            "target_id", "n_nights_evaluated", "n_visible_nights",
            "total_observable_minutes", "best_observable_minutes",
            "best_night", "best_start_local", "best_end_local",
        ])
    work = selection.copy()
    identifier = _target_column(work)
    if identifier != "target_id":
        work = work.rename(columns={identifier: "target_id"})
    selected = work.get("selected_for_visibility", pd.Series(False, index=work.index)).fillna(False).astype(bool)
    minutes = pd.to_numeric(work.get("observable_minutes", 0), errors="coerce").fillna(0)
    work["_selected"] = selected
    work["_minutes"] = minutes
    rows = []
    for target_id, group in work.groupby("target_id", sort=False):
        best = group.sort_values(["_selected", "_minutes"], ascending=[False, False]).iloc[0]
        rows.append({
            "target_id": target_id,
            "n_nights_evaluated": int(len(group)),
            "n_visible_nights": int(group["_selected"].sum()),
            "total_observable_minutes": float(group.loc[group["_selected"], "_minutes"].sum()),
            "best_observable_minutes": float(group["_minutes"].max()),
            "best_night": best.get("observation_date", ""),
            "best_start_local": best.get("observable_start_local", ""),
            "best_end_local": best.get("observable_end_local", ""),
        })
    return pd.DataFrame(rows)


def save_visibility_products(
    targets: pd.DataFrame,
    output_dir: str | Path,
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    observing_windows=None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, list[Path], Path]:
    """Evaluate visibility and write nightly/summary CSV and PNG products."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    selection = evaluate_visibility(
        targets,
        start_date=start_date,
        end_date=end_date,
        observatory=observatory,
        minimum_altitude_deg=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        observing_windows=observing_windows,
        output_path=out / "visibility_by_night.csv",
        verbose=verbose,
    )
    summary = summarize_visibility(selection)
    summary.to_csv(out / "visibility_summary.csv", index=False)
    summary_png = write_table_png(
        summary,
        out / "visibility_summary.png",
        title="Visibility summary",
        subtitle=f"One row per target; nights evaluated from {start_date} to {end_date or start_date} at {observatory}.",
        fontsize=7.0,
    )
    plots = save_visibility_plots(
        targets,
        start_date=start_date,
        end_date=end_date,
        output_dir=out / "visibility_plots",
        observatory=observatory,
        minimum_altitude_deg=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        observing_windows=observing_windows,
        selection=selection,
        verbose=verbose,
    )
    return selection, summary, plots, summary_png


def save_target_sky_map(
    targets: pd.DataFrame,
    output_path: str | Path,
    *,
    color_column: str = "magnitude_last",
    title: str = "Target sky map",
) -> Path:
    """Save a sky map for the selected targets."""
    frame = targets.copy()
    ra = pd.to_numeric(frame.get("ra_deg", frame.get("RA_deg", frame.get("RA"))), errors="coerce")
    dec = pd.to_numeric(frame.get("dec_deg", frame.get("Dec_deg", frame.get("DEC"))), errors="coerce")
    mask = ra.notna() & dec.notna()
    values = pd.to_numeric(frame.get(color_column, pd.Series(np.nan, index=frame.index)), errors="coerce")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(9, 5), dpi=220)
    if mask.any():
        scatter = axis.scatter(ra[mask] % 360, dec[mask], c=values[mask], cmap="viridis", s=28, edgecolor="black", linewidth=.25)
        fig.colorbar(scatter, ax=axis, label=color_column.replace("_", " "))
    else:
        axis.text(.5, .5, "No targets with valid coordinates", ha="center", va="center", transform=axis.transAxes)
    axis.invert_xaxis()
    axis.grid(alpha=.25)
    axis.set(xlabel="RA [deg]", ylabel="Dec [deg]", title=title)
    fig.tight_layout()
    fig.savefig(output, dpi=220, bbox_inches="tight")
    plt.close(fig)
    return output


def save_priority_products(
    priority: pd.DataFrame,
    output_dir: str | Path,
    *,
    name: str = "priority_table",
    title: str = "Observing priority table",
    subtitle: str | None = None,
) -> tuple[Path, Path]:
    """Write a priority table as CSV and a PNG using the shared table style."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{name}.csv"
    png_path = out / f"{name}.png"
    priority.to_csv(csv_path, index=False)
    write_table_png(priority, png_path, title=title, subtitle=subtitle, fontsize=6.8)
    return csv_path, png_path


def save_planning_pdf(
    output_path: str | Path,
    *,
    sky_map: str | Path | None = None,
    table_images: Iterable[str | Path] = (),
    visibility_images: Iterable[str | Path] = (),
    lightcurve_images: Iterable[str | Path] = (),
    cutout_images: Iterable[str | Path] = (),
) -> Path:
    """Assemble the planning PNG products into one PDF in a fixed order."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = [sky_map, *table_images, *visibility_images, *lightcurve_images, *cutout_images]
    with PdfPages(output) as pdf:
        for item in paths:
            if item is None:
                continue
            path = Path(item)
            if not path.exists():
                continue
            image = plt.imread(path)
            height, width = image.shape[:2]
            figure, axis = plt.subplots(figsize=(11.0, max(4.0, 11.0 * height / width)))
            axis.imshow(image)
            axis.axis("off")
            pdf.savefig(figure, bbox_inches="tight", pad_inches=.05)
            plt.close(figure)
    return output
