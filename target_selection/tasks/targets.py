"""Target collection and target-table normalization tasks."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from observatory_observations import canonical_target_name
from target_selection.sources.adapters import _normalize_targets


def collect_mop_targets(
    *,
    start_date: str,
    end_date: str | None = None,
    observatory: str = "El Leoncito",
    mop=None,
    include_event_data: bool = True,
    cache_dir: str | Path = "outputs",
    max_workers: int = 4,
    refresh: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """Collect MOP visible targets and optionally enrich parameters/photometry."""
    if verbose:
        print(f"[targets] Querying MOP visibility for {start_date} to {end_date or start_date}.", flush=True)
    if mop is None:
        from target_selection.sources.mop import create_client
        mop = create_client()
    end_date = end_date or start_date
    daily = mop.visible_targets(
        observatory=observatory,
        start_date=start_date,
        end_date=end_date,
        sort_by_mag=False,
    )
    if daily.empty or not include_event_data:
        targets = _normalize_targets(daily, "mop") if not daily.empty else daily
        if not targets.empty:
            targets["is_mop_visible_in_run"] = True
        if verbose:
            print(f"[targets] MOP targets collected: {len(targets)}", flush=True)
        return targets
    cache = Path(cache_dir)
    summary = mop.visibility_summary(
        observatory=observatory,
        start_date=start_date,
        end_date=end_date,
        include_microlensing_parameters=True,
        parameter_errors="ignore",
        daily_targets=daily,
        parameter_max_workers=max_workers,
        parameter_cache_dir=cache / "mop_event_cache",
        photometry_dir=cache / "mop_photometry",
        refresh_parameters=refresh,
    ).assign(target_source="mop", is_mop_visible_in_run=True)
    from target_selection.sources.mop import apply_authoritative_coordinates

    result = apply_authoritative_coordinates(summary)
    if verbose:
        print(f"[targets] MOP targets with event data: {len(result)}", flush=True)
    return result


def load_target_list(path: str | Path, *, source_name: str = "user_targets", column_map: Mapping[str, str] | None = None, verbose: bool = True) -> pd.DataFrame:
    """Load and normalize a user-provided target CSV."""
    if verbose:
        print(f"[targets] Loading {source_name} targets from {path}.", flush=True)
    result = _normalize_targets(pd.read_csv(path), source_name, column_map=column_map)
    if verbose:
        print(f"[targets] Normalized targets: {len(result)}", flush=True)
    return result


def merge_targets(*tables: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Merge target tables by canonical name, keeping highest coordinate priority."""
    usable = [table.copy() for table in tables if table is not None and not table.empty]
    if not usable:
        if verbose:
            print("[targets] No non-empty target tables to merge.", flush=True)
        return pd.DataFrame(columns=["Target", "RA_deg", "Dec_deg"])
    merged = pd.concat(usable, ignore_index=True, sort=False)
    merged["target_key"] = merged["Target"].map(canonical_target_name)
    merged["_input_order"] = range(len(merged))
    merged["_coordinate_rank"] = (
        pd.to_numeric(merged.get("coordinate_priority", 0), errors="coerce").fillna(0)
    )
    source_columns = [
        name for name in ("target_source", "observatory_providers", "input_sources") if name in merged
    ]
    if source_columns:
        sources = (
            merged.assign(_source=merged[source_columns].fillna("").astype(str).agg(",".join, axis=1))
            .groupby("target_key")["_source"]
            .agg(lambda values: ",".join(sorted({item for value in values for item in value.split(",") if item})))
        )
    else:
        sources = pd.Series(dtype=str)
    merged = merged.sort_values(
        ["target_key", "_coordinate_rank", "_input_order"],
        ascending=[True, False, True],
        kind="stable",
    ).drop_duplicates("target_key", keep="first")
    if not sources.empty:
        merged["input_sources"] = merged["target_key"].map(sources)
    result = merged.drop(columns=["target_key", "_input_order", "_coordinate_rank"]).reset_index(drop=True)
    if verbose:
        print(f"[targets] Merged unique targets: {len(result)}", flush=True)
    return result


def restrict_targets(targets: pd.DataFrame, target_names: Iterable[str] | None, *, verbose: bool = True) -> pd.DataFrame:
    """Return only requested targets, preserving input order."""
    if target_names is None:
        result = targets.copy()
        if verbose:
            print(f"[targets] No target restriction; using {len(result)} targets.", flush=True)
        return result
    wanted = {canonical_target_name(name) for name in target_names}
    keys = targets["Target"].map(canonical_target_name)
    result = targets.loc[keys.isin(wanted)].copy().reset_index(drop=True)
    if verbose:
        print(f"[targets] Restriction kept {len(result)}/{len(targets)} targets.", flush=True)
    return result
