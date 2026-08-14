"""Availability-based target filtering shared by analysis workflows."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from mop_photometry import _safe_target_name


VALID_TARGET_DATA_SCOPES = frozenset({"with_data", "all"})


def normalize_target_data_scope(value: object) -> str:
    """Validate the target-data selection mode."""
    scope = str(value).strip().lower()
    if scope not in VALID_TARGET_DATA_SCOPES:
        choices = ", ".join(sorted(VALID_TARGET_DATA_SCOPES))
        raise ValueError(f"target_data_scope must be one of: {choices}")
    return scope


def _count_cached_mop_photometry(targets: pd.DataFrame, directory: Path) -> pd.Series:
    """Count cached MOP light-curve rows without loading complete files."""
    def count(target_name: object) -> int:
        path = directory / f"{_safe_target_name(target_name)}.csv"
        if not path.exists():
            return 0
        try:
            return len(pd.read_csv(path, usecols=["Timestamp"]))
        except (OSError, ValueError):
            return 0

    return targets["Target"].map(count).astype(int)


def annotate_target_data_availability(
    targets: pd.DataFrame,
    *,
    mop_photometry_dir: str | Path | None = None,
    include_mop_photometry: bool = False,
    database=None,
    observation_providers: Iterable[str] = (),
    photometry_sources: Iterable[str] = (),
) -> pd.DataFrame:
    """Annotate targets with data available from active providers and surveys.

    MOP contributes cached light-curve points. Active follow-up surveys contribute
    either imported image epochs or normalized photometry; other providers can
    contribute normalized photometry stored in the persistent registry.
    """
    data = targets.copy()
    for column in (
        "mop_photometry_points",
        "followup_observation_points",
        "provider_photometry_points",
    ):
        data[column] = 0
    data["target_data_sources"] = ""

    if data.empty or "Target" not in data:
        data["has_target_data"] = pd.Series(dtype=bool)
        data["excluded_missing_target_data"] = pd.Series(dtype=bool)
        data["target_data_exclusion_reason"] = pd.Series(dtype=str)
        return data

    source_labels: dict[str, set[str]] = {str(name): set() for name in data["Target"]}
    if include_mop_photometry and mop_photometry_dir is not None:
        data["mop_photometry_points"] = _count_cached_mop_photometry(
            data, Path(mop_photometry_dir)
        )
        for target_name in data.loc[data["mop_photometry_points"].gt(0), "Target"]:
            source_labels[str(target_name)].add("MOP photometry")

    if database is not None:
        providers = tuple(dict.fromkeys(str(item).upper() for item in observation_providers))
        if providers:
            epochs = database.observation_epochs(data, providers=providers)
            if not epochs.empty:
                counts = epochs.groupby("Target", sort=False).size()
                data["followup_observation_points"] = (
                    data["Target"].map(counts).fillna(0).astype(int)
                )
                for target_name, group in epochs.groupby("Target", sort=False):
                    labels = sorted({str(value).upper() for value in group["provider"]})
                    source_labels[str(target_name)].update(
                        f"{label} images" for label in labels
                    )

        sources = tuple(dict.fromkeys(str(item) for item in photometry_sources))
        if sources:
            photometry = database.photometry(data, sources=sources)
            if not photometry.empty:
                counts = photometry.groupby("Target", sort=False).size()
                data["provider_photometry_points"] = (
                    data["Target"].map(counts).fillna(0).astype(int)
                )
                for target_name, group in photometry.groupby("Target", sort=False):
                    labels = sorted({str(value) for value in group["provider"]})
                    source_labels[str(target_name)].update(
                        f"{label} photometry" for label in labels
                    )

    data["has_target_data"] = data[
        ["mop_photometry_points", "followup_observation_points", "provider_photometry_points"]
    ].sum(axis=1).gt(0)
    data["target_data_sources"] = data["Target"].map(
        lambda name: ", ".join(sorted(source_labels[str(name)]))
    )
    data["excluded_missing_target_data"] = ~data["has_target_data"]
    data["target_data_exclusion_reason"] = data["excluded_missing_target_data"].map(
        lambda excluded: "No photometry or observation data from an active source" if excluded else ""
    )
    return data


def select_targets_by_data_availability(
    targets: pd.DataFrame,
    *,
    target_data_scope: str = "with_data",
    **availability_kwargs,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return analysis targets and targets excluded by the configured data mode."""
    scope = normalize_target_data_scope(target_data_scope)
    annotated = annotate_target_data_availability(targets, **availability_kwargs)
    if scope == "all":
        return annotated.reset_index(drop=True), annotated.iloc[0:0].copy()
    excluded = annotated.loc[annotated["excluded_missing_target_data"]].copy()
    included = annotated.loc[~annotated["excluded_missing_target_data"]].copy()
    return included.reset_index(drop=True), excluded.reset_index(drop=True)
