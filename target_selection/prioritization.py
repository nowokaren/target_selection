"""Reusable microlensing follow-up prioritization helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd


def apply_magnitude_limits(events: pd.DataFrame, limits: dict[str, float | None]) -> pd.DataFrame:
    """Add ``passes_magnitude`` using survey-specific limiting magnitudes."""
    result = events.copy()
    if "magnitude" not in result:
        result["passes_magnitude"] = True
        return result
    result["passes_magnitude"] = True
    magnitudes = pd.to_numeric(result["magnitude"], errors="coerce")
    for survey, limit in limits.items():
        if limit is not None:
            mask = result["survey"].eq(survey)
            result.loc[mask, "passes_magnitude"] = magnitudes.loc[mask].le(float(limit))
    return result


def summarize_photometry(photometry: pd.DataFrame) -> pd.DataFrame:
    """Summarize observed phase flags without fitting an event model."""
    columns = ["target_id", "n_photometry", "first_observation", "last_observation",
               "has_baseline", "has_rise", "has_peak", "has_decline", "event_state"]
    if photometry.empty:
        return pd.DataFrame(columns=columns)
    work = photometry.copy()
    work["observation_time"] = pd.to_datetime(work["observation_time"], errors="coerce")
    rows = []
    for target_id, group in work.groupby("target_id"):
        phase = group.get("phase", pd.Series(index=group.index, dtype=object))
        state = group.get("event_state", pd.Series(index=group.index, dtype=object))
        rows.append({
            "target_id": target_id,
            "n_photometry": len(group),
            "first_observation": group["observation_time"].min(),
            "last_observation": group["observation_time"].max(),
            "has_baseline": bool(phase.eq("baseline").any()),
            "has_rise": bool(phase.eq("rise").any()),
            "has_peak": bool(phase.eq("peak").any()),
            "has_decline": bool(phase.eq("decline").any()),
            "event_state": state.dropna().iloc[-1] if state.notna().any() else "unknown",
        })
    return pd.DataFrame(rows)


def empty_coverage(events: pd.DataFrame) -> pd.DataFrame:
    """Create a no-Rubin-products coverage frame for a disabled query branch."""
    result = events[["target_id", "ra_deg", "dec_deg", "survey"]].copy()
    result["has_visit_coverage"] = False
    result["has_coadd"] = False
    return result


def _bool_column(frame: pd.DataFrame, name: str, default: bool = False) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=bool)
    return pd.Series(frame[name], index=frame.index, dtype="boolean").fillna(default).astype(bool)


def build_priority_table(
    events: pd.DataFrame,
    visibility: pd.DataFrame,
    photometry_summary: pd.DataFrame,
    coverage: pd.DataFrame,
) -> pd.DataFrame:
    """Build an auditable starting priority score from separate evidence columns."""
    result = events.copy()
    if visibility.empty:
        result["visible_this_season"] = False
    else:
        visible = visibility.groupby("target_id")["selected_for_visibility"].any().rename("visible_this_season")
        result = result.merge(visible, on="target_id", how="left")
        result["visible_this_season"] = result["visible_this_season"].fillna(False).astype(bool)
    result = result.merge(photometry_summary, on="target_id", how="left")
    result = result.merge(coverage.drop(columns=["survey"], errors="ignore"), on=["target_id", "ra_deg", "dec_deg"], how="left", suffixes=("", "_rubin"))
    result["visible_score"] = _bool_column(result, "visible_this_season").astype(int)
    result["magnitude_score"] = _bool_column(result, "passes_magnitude").astype(int)
    result["information_gap_score"] = (~_bool_column(result, "has_peak")).astype(int) + (~_bool_column(result, "has_decline")).astype(int)
    if "event_duration_days" in result:
        result["short_event_score"] = pd.to_numeric(result["event_duration_days"], errors="coerce").fillna(0).lt(30).astype(int)
    else:
        result["short_event_score"] = 0
    result["rubin_score"] = _bool_column(result, "has_coadd").astype(int)
    result["priority_score"] = (3 * result["visible_score"] + 2 * result["magnitude_score"] + 2 * result["information_gap_score"] + result["short_event_score"] + result["rubin_score"])
    result["priority_reason"] = np.where(result["visible_score"].eq(1), "visible this season", "not visible or not evaluated")
    return result.sort_values(["priority_score", "target_id"], ascending=[False, True])
