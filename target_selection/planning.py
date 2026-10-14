"""Reusable products for seasonal observing planning.

The module deliberately keeps the planning steps separate: visibility is a
nightly table, summaries are target-level tables, and report assembly only
combines already-created PNG products.  This makes the same functions useful
from notebooks and from a terminal script.
"""

from __future__ import annotations

from pathlib import Path
import re
import textwrap
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.table import Table
import numpy as np
import pandas as pd

from .reporting import write_table_png
from .tasks.visibility import evaluate_visibility as _evaluate_visibility
from .tasks.visibility import save_visibility_plots as _save_visibility_plots


def _target_column(frame: pd.DataFrame) -> str:
    for name in ("target_id", "Target"):
        if name in frame:
            return name
    raise ValueError("Targets must contain target_id or Target.")


def _save_frame(frame: pd.DataFrame, output_dir: str | Path | None, filename: str) -> pd.DataFrame:
    """Save a tabular result under its standard filename when requested."""
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output / filename, index=False)
    return frame


def _merge_candidate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Merge duplicate target rows without discarding source-specific metadata."""
    def is_missing(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, (list, tuple, np.ndarray, pd.Series, pd.Index)):
            return len(value) == 0
        try:
            return bool(pd.isna(value))
        except (TypeError, ValueError):
            return False

    work = frame.copy()
    work["target_id"] = work["target_id"].astype(str)
    duplicate_mask = work.duplicated("target_id", keep=False)
    if not duplicate_mask.any():
        source_columns = [column for column in ("provider", "input_sources") if column in work]
        if source_columns:
            source_values = work[source_columns].fillna("").astype(str).agg(",".join, axis=1)
            work["source_providers"] = source_values.map(
                lambda value: ",".join(sorted({item.strip().lower() for item in value.split(",") if item.strip()}))
            )
            work["input_sources"] = work["source_providers"]
        return work.reset_index(drop=True)
    rows = []
    for target_id, group in work.loc[duplicate_mask].groupby("target_id", sort=False):
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
            if is_missing(merged.get(column)) or str(merged.get(column)).strip() in {"", "nan", "None"}:
                if not usable.empty:
                    merged[column] = usable.iloc[0]
        rows.append(merged)
    merged_by_id = {str(row["target_id"]): row for row in rows}
    result = work.drop_duplicates("target_id", keep="first").copy()
    for index in result.index:
        replacement = merged_by_id.get(str(result.at[index, "target_id"]))
        if replacement is not None:
            result.loc[index, replacement.index] = replacement
    return result.reset_index(drop=True)


def _has_mop_record(row: pd.Series) -> bool:
    """Return whether a combined target has an actual MOP event record."""
    # The MOP visibility table can contain a generated ``mop_link`` even when
    # the target has no event record (the link may return HTTP 404).  An
    # explicit unavailable/error status therefore takes precedence over that
    # link and must not trigger a photometry request.
    status_value = row.get("mop_parameters_status", "")
    status = str(status_value).strip().casefold()
    if status in {"unavailable", "error", "failed", "missing", "not_found", "nan", "none", ""}:
        if "mop_parameters_status" in row and status != "":
            return False
    if status in {"available", "ok", "success", "loaded"}:
        return True
    for column in ("mop_link", "mop_target_id", "mop_target_name"):
        value = row.get(column, "")
        if pd.notna(value) and str(value).strip() not in {"", "nan", "None", "<NA>"}:
            return True
    return False


def load_candidates(
    providers: Sequence[str] | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    filters: dict | None = None,
    include_photometry: bool = False,
    cache_dir: str | Path = "outputs/planning/cache",
    output_dir: str | Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Load and combine targets from configured providers.

    TOMs that support a native visibility query receive the requested dates.
    Other providers use their normal target catalogue query.  Photometry is
    intentionally collected in the separate ``collect_provider_photometry``
    step so it can be cached and filtered before expensive survey queries.

    If ``providers`` is omitted, every registered TOM is attempted.  A
    provider can be omitted explicitly by passing the desired subset, for
    example ``providers=("mop", "opm")``.  Registered TOMs that are not yet
    configured or do not implement target loading are skipped with a message.
    """
    from .data_source import data_source

    selected_providers = (
        tuple(str(name).strip().lower() for name in providers if str(name).strip())
        if providers is not None
        else tuple(data_source.list(kind="tom"))
    )
    frames: list[pd.DataFrame] = []
    for name in selected_providers:
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
        elif name.lower() == "bhtom":
            kwargs["cache_dir"] = Path(cache_dir) / "bhtom"
        try:
            frame = provider.load_targets(**kwargs)
        except (NotImplementedError, FileNotFoundError, RuntimeError, PermissionError) as exc:
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
        return _save_frame(pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg", "provider"]), output_dir, "targets_from_TOMs.csv")
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if "target_id" not in combined and "Target" in combined:
        combined = combined.rename(columns={"Target": "target_id"})
    if "target_id" in combined:
        combined["target_id"] = combined["target_id"].astype(str)
        combined = _merge_candidate_rows(combined)
    return _save_frame(combined, output_dir, "targets_from_TOMs.csv")


def add_ogle_event_parameters(
    targets: pd.DataFrame,
    *,
    event_catalog: pd.DataFrame | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Fill missing event-fit parameters from OGLE EWS metadata when available.

    Existing parameters from a TOM are retained. The EWS catalog only fills
    targets with matching OGLE IDs and missing t0/tE/u0 values.
    """
    if targets is None or targets.empty:
        return targets.copy() if targets is not None else pd.DataFrame()
    result = targets.copy()
    if "target_id" not in result and "Target" in result:
        result = result.rename(columns={"Target": "target_id"})
    if "target_id" not in result:
        raise ValueError("Targets must contain target_id or Target.")
    if event_catalog is None:
        from .data_source import data_source

        try:
            event_catalog = data_source.ogle.load_targets()
        except Exception as exc:
            if verbose:
                print(f"[planning] OGLE event parameters unavailable: {exc}", flush=True)
            return result
    if event_catalog is None or event_catalog.empty:
        return result
    if "target_id" not in event_catalog and "Target" in event_catalog:
        event_catalog = event_catalog.rename(columns={"Target": "target_id"})
    if "target_id" not in event_catalog:
        return result
    parameters = event_catalog.copy()
    parameters["target_id"] = parameters["target_id"].astype(str)
    parameters = parameters.drop_duplicates("target_id").set_index("target_id")
    target_ids = result["target_id"].astype(str)
    matched = target_ids.isin(parameters.index)
    aliases = {
        "t0_hjd": ("t0_hjd", "mop_t_0_hjd", "mop_t0_hjd", "t_0_HJD", "event_peak_hjd", "Tmax (HJD)"),
        "tE_days": ("tE_days", "mop_t_e_days", "mop_te_days", "t_E_days", "event_duration_days", "tau"),
        "u0": ("u0", "mop_u_0", "mop_u0", "u_0", "Umin"),
    }
    added = 0
    for canonical, names in aliases.items():
        source = next((name for name in names if name in parameters.columns), None)
        if source is None:
            continue
        from_catalog = pd.to_numeric(target_ids.map(parameters[source]), errors="coerce")
        existing = pd.Series(np.nan, index=result.index, dtype=float)
        for name in names:
            if name in result:
                parsed = pd.to_numeric(result[name], errors="coerce")
                existing = existing.fillna(parsed)
        fill = existing.isna() & matched & from_catalog.notna()
        if fill.any():
            if canonical not in result:
                result[canonical] = np.nan
            result.loc[fill, canonical] = from_catalog.loc[fill]
            added += int(fill.sum())
    if verbose and added:
        print(f"[planning] OGLE EWS parameters filled for {added} target parameter values.", flush=True)
    return result


def collect_photometry(
    targets: pd.DataFrame,
    *,
    providers: Sequence[str] | None = None,
    include_associated_surveys: bool = True,
    cache_dir: str | Path = "outputs/planning/cache",
    output_dir: str | Path | None = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Collect normalized photometry and automatically add associated surveys."""
    from .data_source import data_source

    selected_providers = (
        tuple(str(name).strip().lower() for name in providers if str(name).strip())
        if providers is not None else tuple(data_source.list(kind="tom"))
    )
    work = targets.copy()
    if "target_id" not in work and "Target" in work:
        work = work.rename(columns={"Target": "target_id"})
    records: list[pd.DataFrame] = []
    for name in selected_providers:
        provider = data_source.get(name)
        if not hasattr(provider, "photometry"):
            continue
        source_mask = pd.Series(True, index=work.index)
        if "source_providers" in work:
            source_mask = work["source_providers"].fillna("").astype(str).str.split(",").map(lambda values: name.lower() in {item.strip().lower() for item in values})
        elif "provider" in work:
            source_mask = work["provider"].astype(str).str.lower().eq(name.lower())
        if name.lower() == "mop":
            source_mask &= work.apply(_has_mop_record, axis=1)
        provider_targets = work.loc[source_mask].copy()
        if provider_targets.empty:
            continue
        try:
            points = provider.photometry(provider_targets, cache_dir=Path(cache_dir) / name, errors="ignore", verbose=verbose)
        except TypeError:
            points = provider.photometry(provider_targets)
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
        lsst_targets = work.loc[work["target_id"].astype(str).str.upper().str.startswith("LSST")].copy()
        if not lsst_targets.empty:
            try:
                lsst_input = lsst_targets.rename(columns={
                    "target_id": "Target", "ra_deg": "RA_deg", "dec_deg": "Dec_deg",
                }).copy()
                points = data_source.lsst.photometry(
                    lsst_input,
                    method="dia_combined_catalog",
                    verbose=verbose,
                )
                if points is not None and not points.empty:
                    if "Target" in points and "target_id" not in points:
                        points = points.rename(columns={"Target": "target_id"})
                    points["provider"] = "lsst"
                    records.append(points)
                    if verbose:
                        print(f"[planning] lsst: {len(points):,} DiaSource/forced photometry rows", flush=True)
            except Exception as exc:
                if verbose:
                    print(f"[planning] Complementary LSST DIA photometry unavailable: {exc}", flush=True)
    if not records:
        return _save_frame(pd.DataFrame(columns=["target_id", "provider", "epoch_hjd", "magnitude", "magnitude_err", "band"]), output_dir, "observations.csv")
    return _save_frame(_deduplicate_observations(pd.concat(records, ignore_index=True, sort=False)), output_dir, "observations.csv")


def _deduplicate_observations(observations: pd.DataFrame) -> pd.DataFrame:
    """Remove repeated survey measurements retrieved through multiple paths."""
    if observations is None or observations.empty:
        return observations
    result = observations.copy()
    if "target_id" not in result:
        return result

    def coalesce(columns: Sequence[str], default: object = np.nan) -> pd.Series:
        values = pd.Series(default, index=result.index, dtype=object)
        for column in columns:
            if column in result:
                candidate = result[column].copy()
                candidate = candidate.mask(candidate.isna() | candidate.astype(str).isin({"", "nan", "None"}))
                missing = values.isna()
                values.loc[missing] = candidate.loc[missing]
        return values

    result["band"] = coalesce(("band", "Filter", "filter")).fillna("").astype(str).str.strip()
    result["survey"] = coalesce(("survey", "Source", "source", "telescope", "facility", "provider")).fillna("").astype(str).str.strip()
    result["access_provider"] = coalesce(("access_provider", "provider", "Provider")).fillna("").astype(str).str.strip().str.lower()
    result["magnitude"] = pd.to_numeric(coalesce(("magnitude", "Magnitude")), errors="coerce")
    epoch = pd.to_numeric(coalesce(("epoch_hjd",)), errors="coerce")
    epoch_mjd = pd.to_numeric(coalesce(("epoch_mjd",)), errors="coerce")
    epoch = epoch.fillna(epoch_mjd + 2400000.5)
    time_values = coalesce(("Timestamp", "observation_time"), default=pd.NaT)
    parsed_time = pd.to_datetime(time_values, errors="coerce", utc=True)
    timestamp_hjd = parsed_time.map(lambda value: value.to_julian_date() if pd.notna(value) else np.nan)
    result["epoch_hjd"] = epoch.fillna(timestamp_hjd)
    result["_survey_key"] = result["survey"].str.upper().str.replace(r"[ -]+", "_", regex=True)
    result["_band_key"] = result["band"].str.upper().str.replace(r"[ -]+", "_", regex=True)
    result["_band_key"] = result["_band_key"].str.replace(r"^(?:OGLE|ZTF|MOA)_", "", regex=True)
    result["_epoch_key"] = pd.to_numeric(result["epoch_hjd"], errors="coerce").round(6)
    result["_directness"] = np.where(
        result["access_provider"].eq(result["_survey_key"].str.lower()), 0, 1
    )
    result["_row_order"] = np.arange(len(result))
    usable = result["magnitude"].notna() & result["_epoch_key"].notna()
    duplicate_key = ["target_id", "_survey_key", "_band_key", "_epoch_key"]
    duplicate_rows = result.loc[usable].sort_values([*duplicate_key, "_directness", "_row_order"])
    keep = duplicate_rows.drop_duplicates(duplicate_key, keep="first").index
    result = pd.concat([result.loc[~usable], result.loc[keep]], axis=0).sort_index()
    return result.drop(columns=["_survey_key", "_band_key", "_epoch_key", "_directness", "_row_order"], errors="ignore").reset_index(drop=True)


def add_latest_magnitude(
    targets: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Add the preferred latest magnitude derived from all loaded photometry.

    Selection order is: latest ``OGLE_I`` measurement, latest measurement in
    any ``I`` band, and finally the latest measurement in any band.  The
    source and band are retained in ``magnitude_source_last`` and
    ``band_last``.  Provider-specific columns already present in ``targets``
    are preserved as details; they are only used as a fallback when no
    usable photometric row exists.
    """
    result = targets.copy()
    if "target_id" not in result and "Target" in result:
        result = result.rename(columns={"Target": "target_id"})
    if "target_id" not in result:
        raise ValueError("Targets must contain target_id or Target.")

    for column in ("magnitude_last", "band_last", "magnitude_source_last", "magnitude_epoch_last"):
        if column not in result:
            result[column] = np.nan

    if observations is None or observations.empty or "target_id" not in observations:
        return _save_frame(result, output_dir, "targets_loaded.csv")

    work = observations.copy()
    def coalesce(columns: Sequence[str], default: object = np.nan) -> pd.Series:
        values = pd.Series(default, index=work.index, dtype=object)
        for column in columns:
            if column in work:
                candidate = work[column].copy()
                candidate = candidate.mask(candidate.isna() | candidate.astype(str).isin({"", "nan", "None"}))
                missing = values.isna()
                values.loc[missing] = candidate.loc[missing]
        return values

    magnitude = pd.to_numeric(coalesce(("magnitude", "Magnitude")), errors="coerce")
    band = coalesce(("band", "Filter")).fillna("").astype(str).str.strip()
    # ``Source`` identifies the survey behind TOM-hosted measurements (for
    # example OGLE points retrieved through MOP), whereas ``Provider`` only
    # identifies the service from which the row was downloaded.
    source = coalesce(("Source", "provider", "Provider")).fillna("").astype(str).str.strip()
    epoch = pd.to_numeric(work.get("epoch_hjd", pd.Series(np.nan, index=work.index)), errors="coerce")
    epoch_mjd = pd.to_numeric(work.get("epoch_mjd", pd.Series(np.nan, index=work.index)), errors="coerce")
    epoch = epoch.fillna(epoch_mjd + 2400000.5)
    timestamp_values = coalesce(("Timestamp", "observation_time"), default=pd.NaT)
    timestamp = pd.to_datetime(timestamp_values, errors="coerce", utc=True)
    timestamp_jd = timestamp.map(lambda value: value.to_julian_date() if pd.notna(value) else np.nan)
    epoch = epoch.fillna(timestamp_jd)

    usable = magnitude.notna() & epoch.notna() & work["target_id"].notna()
    if not usable.any():
        return _save_frame(result, output_dir, "targets_loaded.csv")
    work = pd.DataFrame({
        "target_id": work.loc[usable, "target_id"].astype(str),
        "magnitude": magnitude.loc[usable],
        "band": band.loc[usable],
        "source": source.loc[usable],
        "epoch": epoch.loc[usable],
    })
    normalized_band = work["band"].str.upper().str.replace("-", "_", regex=False).str.replace(" ", "_", regex=False)
    source_upper = work["source"].str.upper()
    work["priority"] = np.select(
        [
            normalized_band.eq("OGLE_I") | (source_upper.eq("OGLE") & normalized_band.eq("I")),
            normalized_band.str.split("_").str[-1].eq("I"),
        ],
        [0, 1],
        default=2,
    )
    latest = (
        work.sort_values(["target_id", "priority", "epoch"], ascending=[True, True, False])
        .drop_duplicates("target_id", keep="first")
        .rename(columns={"epoch": "magnitude_epoch_last", "source": "magnitude_source_last"})
    )
    latest = latest.set_index("target_id")
    result["target_id"] = result["target_id"].astype(str)
    result = result.set_index("target_id")
    result["band_last"] = result["band_last"].astype(object)
    result["magnitude_source_last"] = result["magnitude_source_last"].astype(object)
    for target_id, row in latest.iterrows():
        result.loc[target_id, "magnitude_last"] = row["magnitude"]
        result.loc[target_id, "band_last"] = row["band"]
        result.loc[target_id, "magnitude_source_last"] = row["magnitude_source_last"]
        result.loc[target_id, "magnitude_epoch_last"] = row["magnitude_epoch_last"]
    return _save_frame(result.reset_index(), output_dir, "targets_loaded.csv")


# Compatibility aliases for notebooks created before the public names were
# shortened.  New code should use ``load_candidates`` and
# ``collect_photometry``.
load_provider_targets = load_candidates
collect_provider_photometry = collect_photometry


def apply_magnitude_limit(
    targets: pd.DataFrame,
    maximum: float | None,
    *,
    visibility_summary: pd.DataFrame | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame | tuple[pd.DataFrame, pd.DataFrame]:
    """Keep targets below ``maximum`` and optionally synchronize visibility.

    If ``visibility_summary`` is supplied, the returned value is
    ``(filtered_targets, filtered_visibility_summary)``.  This keeps the
    target and summary tables synchronized without requiring notebook code to
    repeat the target-ID intersection.
    """
    if maximum is None:
        result = _save_frame(targets.copy(), output_dir, "targets_after_magnitude.csv")
        if visibility_summary is None:
            return result
        return result, visibility_summary.loc[visibility_summary["target_id"].isin(result["target_id"])].copy()
    result = targets.copy()
    candidates = ["magnitude_last", "magnitude", "mag_now", "mop_mag_now"]
    magnitude = next((pd.to_numeric(result[column], errors="coerce") for column in candidates if column in result), None)
    if magnitude is None:
        result["passes_magnitude"] = False
    else:
        result["passes_magnitude"] = magnitude.le(float(maximum))
    result = _save_frame(result.loc[result["passes_magnitude"]].copy(), output_dir, "targets_after_magnitude.csv")
    if visibility_summary is None:
        return result
    return result, visibility_summary.loc[visibility_summary["target_id"].isin(result["target_id"])].copy()


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


def evaluate_visibility(
    targets: pd.DataFrame,
    output_path: str | Path,
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    observing_windows=None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate visibility and write the three canonical target tables.

    ``output_path`` is a directory containing exactly these standard files:
    ``visibility_by_night.csv``, ``visibility_summary.csv`` and
    ``visible_targets.csv``.  The returned values are, in the same order, the
    nightly table, the target-level summary and the targets visible on at
    least one requested night.
    """
    out = Path(output_path)
    out.mkdir(parents=True, exist_ok=True)
    selection = _evaluate_visibility(
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
    write_table_png(
        summary,
        out / "visibility_summary.png",
        title="Visibility summary",
        subtitle=f"One row per target; nights evaluated from {start_date} to {end_date or start_date} at {observatory}.",
        fontsize=7.0,
    )
    visible_ids = set(summary.loc[summary["n_visible_nights"].gt(0), "target_id"])
    target_id = _target_column(targets)
    visible_targets = targets.loc[targets[target_id].astype(str).isin({str(item) for item in visible_ids})].copy()
    visible_targets.to_csv(out / "visible_targets.csv", index=False)
    return selection, summary, visible_targets


def visibility_plots(
    targets: pd.DataFrame,
    output_path: str | Path,
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    minimum_altitude_deg: float = 40.0,
    minimum_observable_minutes: float = 90.0,
    observing_windows=None,
    selection: pd.DataFrame | None = None,
    verbose: bool = True,
) -> list[Path]:
    """Generate nightly visibility plots in ``output_path``."""
    if selection is not None and not selection.empty:
        target_column = _target_column(targets)
        selection_id = "target_id" if "target_id" in selection else "Target"
        allowed_ids = set(targets[target_column].astype(str))
        selection = selection.loc[selection[selection_id].astype(str).isin(allowed_ids)].copy()
    return _save_visibility_plots(
        targets,
        start_date=start_date,
        end_date=end_date,
        output_dir=output_path,
        observatory=observatory,
        minimum_altitude_deg=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        observing_windows=observing_windows,
        selection=selection,
        verbose=verbose,
    )


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
    selection, summary, _ = evaluate_visibility(
        targets, out, start_date=start_date, end_date=end_date,
        observatory=observatory, minimum_altitude_deg=minimum_altitude_deg,
        minimum_observable_minutes=minimum_observable_minutes,
        observing_windows=observing_windows, verbose=verbose,
    )
    summary.to_csv(out / "visibility_summary.csv", index=False)
    summary_png = write_table_png(
        summary,
        out / "visibility_summary.png",
        title="Visibility summary",
        subtitle=f"One row per target; nights evaluated from {start_date} to {end_date or start_date} at {observatory}.",
        fontsize=7.0,
    )
    plots = visibility_plots(
        targets,
        start_date=start_date,
        end_date=end_date,
        output_path=out / "visibility_plots",
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
    write_csv: bool = True,
) -> tuple[Path, Path]:
    """Render the shared priority assessment; optionally write its CSV."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"{name}.csv"
    png_path = out / f"{name}.png"
    if write_csv:
        priority.to_csv(csv_path, index=False)
    stage_pattern = re.compile(r"^(?P<survey>.+)_(?P<stage>pre_baseline|rise|peak|fall|post_baseline)_points$")
    has_stage_columns = any(stage_pattern.match(str(column)) for column in priority.columns)
    if has_stage_columns:
        _write_compact_priority_table_png(priority, png_path, title=title, subtitle=subtitle)
    else:
        write_table_png(priority, png_path, title=title, subtitle=subtitle, fontsize=6.8)
    return csv_path, png_path


def _write_compact_priority_table_png(
    priority: pd.DataFrame,
    output_path: str | Path,
    *,
    title: str,
    subtitle: str | None = None,
) -> Path:
    """Render a compact, grouped priority table without empty top margins."""
    stage_pattern = re.compile(r"^(?P<survey>.+)_(?P<stage>pre_baseline|rise|peak|fall|post_baseline)_points$")
    labels = {
        "priority_rank": "Priority", "target_id": "Target", "priority_category": "Category",
        "category_rank": "Cat. rank", "priority_reason": "Reason", "event_stage": "Stage",
        "event_model_parameters_available": "Model params", "t0_hjd": "t0 HJD",
        "tE_days": "tE days", "rise_significance_sigma": "Rise sigma",
        "expected_delta_mag": "Predicted dmag", "days_since_external_photometry": "Last epoch",
        "last_external_survey": "Latest survey", "magnitude_assessment": "Last mag",
        "band_last": "Band", "n_visible_nights": "Visible nights",
        "best_observable_minutes": "Visible min", "best_night": "Best night",
    }
    metadata = [name for name in labels if name in priority]
    stage_columns = [
        str(column) for column in priority.columns
        if stage_pattern.match(str(column))
        and pd.to_numeric(priority[str(column)], errors="coerce").fillna(0).sum() >= 10
    ]
    columns = metadata + stage_columns
    if not columns:
        return write_table_png(priority, output_path, title=title, subtitle=subtitle, fontsize=6.8)

    display = priority.reindex(columns=columns).copy()
    if "priority_reason" in display:
        display["priority_reason"] = display["priority_reason"].fillna("").astype(str).str.replace(r"\s*\n\s*", " ", regex=True).str.replace(r"\s+", " ", regex=True)
    header_labels = []
    weights = []
    for column in columns:
        match = stage_pattern.match(column)
        label = labels.get(column, f"{match.group('survey')} {match.group('stage')}" if match else column)
        header_labels.append(label)
        max_chars = max([len(label), *(len(str(value)) for value in display[column].dropna().head(80))])
        weights.append(min(max(max_chars, 5), 32 if column == "priority_reason" else 20))
    weight_sum = sum(weights)
    widths = [weight / weight_sum for weight in weights]
    nrows, ncols = display.shape
    fig_width = max(13.0, min(34.0, 0.48 * sum(weights) ** 0.72))
    fig_height = max(3.8, min(90.0, 0.22 * nrows + 1.0))
    fig, axis = plt.subplots(figsize=(fig_width, fig_height))
    axis.set_axis_off()
    fig.subplots_adjust(left=0.008, right=0.992, bottom=0.008, top=0.90)
    fig.text(0.01, 0.985, title, ha="left", va="top", fontsize=13, fontweight="bold")
    if subtitle:
        fig.text(0.01, 0.952, subtitle.replace("\n", " "), ha="left", va="top", fontsize=8)
    table = axis.table(
        cellText=display.fillna("").astype(str).values.tolist(),
        colLabels=header_labels,
        colWidths=widths,
        cellLoc="center",
        bbox=[0.0, 0.0, 1.0, 0.985],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(max(4.0, min(7.0, 720.0 / max(nrows, 1))))
    for (row, column), cell in table.get_celld().items():
        cell.set_edgecolor("#c8d0d8")
        cell.set_linewidth(0.35)
        if row == 0:
            cell.set_facecolor("#34495e")
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
            cell.get_text().set_wrap(False)
            cell.set_height(0.075)
        else:
            source_column = columns[column]
            if source_column in metadata:
                cell.set_facecolor("#f2f5f7")
            elif source_column.lower().startswith(("hsh_", "js_", "casleo_")):
                cell.set_facecolor("#eaf4ef")
            else:
                cell.set_facecolor("#edf3fa")
            cell.get_text().set_wrap(False)
            cell.set_height(0.035)
            if source_column == "priority_reason":
                cell.get_text().set_ha("left")
    fig.savefig(output_path, dpi=220, bbox_inches="tight", pad_inches=0.03)
    plt.close(fig)
    return Path(output_path)


def _write_priority_table_png(
    priority: pd.DataFrame,
    output_path: str | Path,
    *,
    title: str,
    subtitle: str | None = None,
) -> Path:
    """Render a grouped priority table following the observing-plan layout."""
    pattern = re.compile(r"^(?P<survey>.+)_(?P<stage>pre_baseline|rise|peak|fall|post_baseline)_points$")
    stage_titles = {
        "pre_baseline": "Pre-baseline\n(τ < -2)",
        "rise": "Rise\n(-2 ≤ τ < -0.3)",
        "peak": "Peak\n(|τ| ≤ 0.3)",
        "fall": "Fall\n(0.3 < τ ≤ 2)",
        "post_baseline": "Post-baseline\n(τ > 2)",
    }
    metadata = [
        ("priority_rank", "Priority\nrank"), ("target_id", "Target"),
        ("priority_category", "Category"), ("category_rank", "Rank in\ncategory"),
        ("priority_reason", "Reason"), ("event_stage", "Stage"),
        ("event_model_parameters_available", "Model\nparams?"),
        ("rise_significance_sigma", "Rise\nσ"), ("expected_delta_mag", "Predicted\nΔmag"),
        ("days_since_external_photometry", "Last epoch"),
        ("last_external_survey", "Latest\nsurvey"),
        ("external_stale_threshold_days", "Stale after\n[days]"),
        ("source_providers", "TOM\nsource"), ("magnitude_assessment", "Last\nmag"),
        ("band_last", "Band"), ("n_visible_nights", "Visible\nnights"),
        ("best_observable_minutes", "Best\nvisible [min]"), ("best_night", "Best\nnight"),
    ]
    parameters = [("tE_days", "tE\n[days]"), ("t0_hjd", "t0\n[HJD]"), ("u0", "u0")]
    existing = set(priority.columns)
    columns = [name for name, _ in metadata if name in existing]
    labels = [label for name, label in metadata if name in existing]
    groups: list[tuple[int, int, str]] = []
    if columns:
        groups.append((0, len(columns), "Target info"))
    stage_columns: dict[str, list[tuple[str, str]]] = {stage: [] for stage in stage_titles}
    for column in priority.columns:
        match = pattern.match(str(column))
        if match:
            stage_columns[match.group("stage")].append((match.group("survey"), str(column)))
    survey_points: dict[str, int] = {}
    for values in stage_columns.values():
        for survey, column in values:
            survey_points[survey] = survey_points.get(survey, 0) + int(
                pd.to_numeric(priority[column], errors="coerce").fillna(0).sum()
            )
    active_surveys = {survey for survey, count in survey_points.items() if count >= 10}
    for stage, stage_title in stage_titles.items():
        start = len(columns)
        for survey, column in sorted(stage_columns[stage]):
            if survey not in active_surveys:
                continue
            columns.append(column)
            labels.append(survey.upper().replace("_", " "))
        if len(columns) > start:
            groups.append((start, len(columns), stage_title))
    parameter_start = len(columns)
    for name, label in parameters:
        if name in existing:
            columns.append(name)
            labels.append(label)
    if len(columns) > parameter_start:
        groups.append((parameter_start, len(columns), "Microlensing\nparameters"))
    if not columns:
        return write_table_png(priority, output_path, title=title, subtitle=subtitle, fontsize=6.8)

    display = priority.reindex(columns=columns).copy()
    for column in display.columns:
        if column.endswith("_points"):
            values = pd.to_numeric(display[column], errors="coerce")
            display[column] = values.map(lambda value: "-" if pd.isna(value) else f"{int(value):,}")
        elif column in {"magnitude_last", "magnitude_assessment", "tE_days", "u0", "t0_hjd", "rise_significance_sigma", "expected_delta_mag", "days_since_external_photometry"}:
            values = pd.to_numeric(display[column], errors="coerce")
            display[column] = values.map(lambda value: "-" if pd.isna(value) else f"{value:.1f}")
        elif column == "best_observable_minutes":
            values = pd.to_numeric(display[column], errors="coerce")
            display[column] = values.map(lambda value: "-" if pd.isna(value) else f"{value:.0f}")
        else:
            display[column] = display[column].fillna("-").astype(str).replace({"nan": "-", "None": "-"})

    wrap_width = 18
    wrapped_labels = [textwrap.fill(str(label), wrap_width) for label in labels]
    wrapped_data = [[textwrap.fill(str(value), wrap_width) for value in row] for row in display.values.tolist()]
    widths = [max(5, min(wrap_width, max(len(line) for line in [textwrap.fill(str(labels[index]), wrap_width)] + [textwrap.fill(str(value), wrap_width) for value in display.iloc[:, index].tolist()]))) for index in range(len(columns))]
    width_total = sum(widths)
    col_widths = [value / width_total for value in widths]
    row_lines = [max(1, max(text.count("\n") + 1 for text in [wrapped_labels[index] for index in range(len(columns))]))]
    row_lines.extend(max(1, max(text.count("\n") + 1 for text in row)) for row in wrapped_data)
    row_units = [1.5, 1.35, *[max(1.0, lines) for lines in row_lines[1:]]]
    row_heights = [value / sum(row_units) for value in row_units]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(max(16.0, min(36.0, .65 * width_total)), max(4.5, 1.4 + .34 * len(wrapped_data))), dpi=220)
    axis.axis("off")
    table_bottom, table_height = .02, .89
    table = Table(axis, bbox=[0, table_bottom, 1, table_height])
    # Add a complete rectangular grid.  Group headers are visually merged by
    # removing the internal vertical edges; using a complete grid avoids the
    # blank gaps produced by overlapping colspan cells in matplotlib.table.
    for column in range(len(columns)):
        table.add_cell(0, column, col_widths[column], row_heights[0], text="", loc="center", facecolor="#b5cddd")
    for column, label in enumerate(wrapped_labels):
        table.add_cell(1, column, col_widths[column], row_heights[1], text=label, loc="center", facecolor="#e5eef4")
    for row_index, row in enumerate(wrapped_data, start=2):
        for column, value in enumerate(row):
            table.add_cell(row_index, column, col_widths[column], row_heights[row_index], text=value, loc="center", facecolor="#f7f7f7" if row_index % 2 else "#ffffff")
    axis.add_table(table)
    group_labels = {label for _, _, label in groups}
    for (row_index, column), cell in table.get_celld().items():
        cell.set_edgecolor("#202020")
        cell.set_linewidth(.65)
        cell.get_text().set_fontsize(6.5 if len(columns) < 22 else 5.7)
        if row_index == 0:
            # Keep the whole group row uniform.  Internal top-row borders are
            # drawn in the header fill color; explicit group boundaries below
            # provide the merged-header appearance without diagonal artifacts.
            cell.set_edgecolor("#b5cddd")
            cell.set_linewidth(.4)
        if row_index == 1:
            cell.get_text().set_weight("bold")
    for start, end, label in groups:
        center_x = sum(col_widths[:start]) + sum(col_widths[start:end]) / 2
        center_y = table_bottom + table_height * (1 - row_heights[0] / 2)
        axis.text(center_x, center_y, label, transform=axis.transAxes, ha="center", va="center", fontsize=6.5 if len(columns) < 22 else 5.7, fontweight="bold")
        for boundary in (start, end):
            x = sum(col_widths[:boundary])
            axis.plot((x, x), (table_bottom, table_bottom + table_height), transform=axis.transAxes, color="#202020", linewidth=.65, clip_on=False)
    figure.suptitle(title, y=.995, fontsize=15, fontweight="bold")
    if subtitle:
        figure.text(.5, .958, subtitle, ha="center", va="top", fontsize=8.5)
    figure.savefig(output, dpi=220, bbox_inches="tight", pad_inches=.08)
    plt.close(figure)
    return output


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
            pdf.savefig(figure, dpi=240, bbox_inches="tight", pad_inches=.05)
            plt.close(figure)
    return output
