"""Evidence-based microlensing follow-up assessment and ordering."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from typing import Any
from pathlib import Path

import numpy as np
import pandas as pd


CATEGORY_ORDER = {
    "NUEVO-RISE": 0,
    "NUEVO-PEAK": 1,
    "CONTINUIDAD": 2,
    "DIAGNÓSTICO": 3,
    "OPORTUNISTA": 4,
    "REVISAR": 5,
    "DESCARTAR": 6,
}
SCHEDULABLE_CATEGORIES = frozenset({
    "NUEVO-RISE", "NUEVO-PEAK", "CONTINUIDAD", "DIAGNÓSTICO", "OPORTUNISTA",
})


def select_schedulable_targets(candidates: pd.DataFrame, priority: pd.DataFrame) -> pd.DataFrame:
    """Use current-run visibility and evidence categories for the final plan."""
    current = priority[["target_id", "priority_category", "n_visible_nights", "priority_rank"]]
    feasible = candidates.drop(columns=["priority_category", "priority_rank", "n_visible_nights"], errors="ignore").merge(
        current, on="target_id", how="left",
    )
    selected = feasible.loc[
        feasible["priority_category"].isin(SCHEDULABLE_CATEGORIES)
        & pd.to_numeric(feasible["n_visible_nights"], errors="coerce").fillna(0).gt(0)
    ]
    return selected.sort_values("priority_rank").drop(columns=["priority_rank", "n_visible_nights"])


@dataclass(frozen=True)
class PriorityConfig:
    """Explicit, reviewable cutoffs for the first rule-based implementation."""

    rise_significance_sigma: float = 3.0
    minimum_rise_points: int = 3
    rise_lookback_days: float = 90.0
    stale_cadence_factor: float = 2.0
    stale_minimum_days: float = 30.0
    planned_visit_interval_days: float = 7.0
    peak_half_width_t_e: float = 0.3
    event_half_width_t_e: float = 2.0
    magnitude_limits: tuple[tuple[str, float], ...] = (("HSH", 16.5), ("JS", 18.0))


def empty_coverage(events: pd.DataFrame) -> pd.DataFrame:
    """Create an empty LSST coverage table for a disabled query branch."""
    columns = [name for name in ("target_id", "ra_deg", "dec_deg", "survey") if name in events]
    result = events[columns].copy()
    result["has_visit_coverage"] = False
    result["has_coadd"] = False
    return result


def _numeric(frame: pd.DataFrame, names: tuple[str, ...], default: float = np.nan) -> pd.Series:
    value = pd.Series(default, index=frame.index, dtype=float)
    for name in names:
        if name in frame:
            direct = pd.to_numeric(frame[name], errors="coerce")
            parsed = pd.to_numeric(
                frame[name].astype(str).str.extract(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", expand=False),
                errors="coerce",
            )
            value = value.fillna(direct).fillna(parsed)
    return value


def _epoch_hjd(frame: pd.DataFrame) -> pd.Series:
    epoch = _numeric(frame, ("epoch_hjd", "hjd"))
    epoch = epoch.fillna(_numeric(frame, ("epoch_mjd", "mjd")) + 2400000.5)
    for column in ("observation_time", "Timestamp", "timestamp"):
        if column in frame:
            stamp = pd.to_datetime(frame[column], errors="coerce", utc=True)
            jd = stamp.map(lambda x: x.to_julian_date() if pd.notna(x) else np.nan)
            epoch = epoch.fillna(jd)
    return epoch


def _as_hjd(value: str | date | datetime | pd.Timestamp | None) -> float:
    stamp = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return float(stamp.tz_convert("UTC").to_julian_date())


def _first_text(row: pd.Series, names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if pd.notna(value) and str(value).strip().casefold() not in {"", "nan", "none", "<na>"}:
            return str(value).strip()
    return ""


def _coalesced_text(frame: pd.DataFrame, names: tuple[str, ...], default: str = "") -> pd.Series:
    """Choose the first non-empty value per row, even when columns are sparse."""
    value = pd.Series("", index=frame.index, dtype=object)
    for name in names:
        if name not in frame:
            continue
        candidate = frame[name]
        valid = candidate.notna() & candidate.astype(str).str.strip().ne("")
        missing = value.fillna("").astype(str).str.strip().eq("")
        value.loc[missing & valid] = candidate.loc[missing & valid]
    return value.fillna(default).astype(str)


def _coalesced_numeric(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    """Choose the first numeric value per row across sparse aliases."""
    value = pd.Series(np.nan, index=frame.index, dtype=float)
    for name in names:
        if name in frame:
            value = value.fillna(pd.to_numeric(frame[name], errors="coerce"))
    return value


def _joined_text(row: pd.Series, names: tuple[str, ...]) -> str:
    values = []
    for name in names:
        value = row.get(name)
        if pd.notna(value) and str(value).strip().casefold() not in {"", "nan", "none", "<na>"}:
            values.append(str(value).strip())
    return " ".join(values)


def _lens_magnification(u: float) -> float:
    if not np.isfinite(u) or u <= 0:
        return np.nan
    return (u * u + 2) / (u * np.sqrt(u * u + 4))


def _model_delta_mag(t0: float, te: float, u0: float, now: float, interval: float) -> float:
    if not (np.isfinite(t0) and np.isfinite(te) and te > 0 and np.isfinite(u0) and u0 > 0):
        return np.nan
    u_now = np.sqrt(u0**2 + ((now - t0) / te) ** 2)
    u_next = np.sqrt(u0**2 + ((now + interval - t0) / te) ** 2)
    a_now, a_next = _lens_magnification(u_now), _lens_magnification(u_next)
    return float(abs(2.5 * np.log10(a_next / a_now)))


def _rise_evidence(group: pd.DataFrame, now: float, config: PriorityConfig) -> tuple[float, int]:
    """Return strongest brightening slope significance and its point count."""
    if group.empty:
        return np.nan, 0
    data = group.copy()
    data["epoch"] = _epoch_hjd(data)
    data["mag"] = _coalesced_numeric(data, ("magnitude", "Magnitude", "mag"))
    data = data.loc[data["epoch"].between(now - config.rise_lookback_days, now) & data["mag"].notna()].copy()
    if data.empty:
        return np.nan, 0
    data["band_key"] = _coalesced_text(data, ("band", "Filter", "filter")).str.upper()
    data["survey_key"] = _coalesced_text(data, ("survey", "Source", "provider"), "unknown").str.upper()
    strongest, strongest_n = np.nan, 0
    for _, curve in data.groupby(["survey_key", "band_key"], dropna=False):
        curve = curve.sort_values("epoch")
        if len(curve) < config.minimum_rise_points or curve["epoch"].nunique() < config.minimum_rise_points:
            continue
        x = curve["epoch"].to_numpy(dtype=float)
        y = curve["mag"].to_numpy(dtype=float)
        x = x - x.mean()
        err = _coalesced_numeric(curve, ("magnitude_err", "Magnitude Error", "magnitude_error", "Error")).to_numpy(dtype=float)
        valid_err = np.isfinite(err) & (err > 0)
        weights = np.where(valid_err, 1.0 / np.square(err), 1.0)
        design = np.column_stack((np.ones(len(x)), x))
        try:
            covariance = np.linalg.inv(design.T @ (weights[:, None] * design))
            beta = covariance @ (design.T @ (weights * y))
            residual = y - design @ beta
            dof = max(len(y) - 2, 1)
            scale = max(float(np.sum(weights * residual**2) / dof), 1.0 if valid_err.any() else 0.0)
            slope_error = np.sqrt(max(covariance[1, 1] * scale, 0.0))
            significance = float(-beta[1] / slope_error) if slope_error > 0 else np.nan
        except np.linalg.LinAlgError:
            continue
        if np.isfinite(significance) and (not np.isfinite(strongest) or significance > strongest):
            strongest, strongest_n = significance, len(curve)
    return strongest, strongest_n


def _event_stage(t0: float, te: float, now: float, config: PriorityConfig) -> tuple[str, float]:
    if not (np.isfinite(t0) and np.isfinite(te) and te > 0):
        return "unknown", np.nan
    tau = (now - t0) / te
    if tau < -config.event_half_width_t_e:
        stage = "pre_baseline"
    elif tau < -config.peak_half_width_t_e:
        stage = "rise"
    elif tau <= config.peak_half_width_t_e:
        stage = "peak"
    elif tau <= config.event_half_width_t_e:
        stage = "decline"
    else:
        stage = "post_baseline"
    return stage, float(tau)


def _casleo_mask(observations: pd.DataFrame) -> pd.Series:
    labels = pd.Series("", index=observations.index, dtype=str)
    for column in ("survey", "provider", "access_provider", "source", "Source"):
        if column in observations:
            labels = labels + " " + observations[column].fillna("").astype(str)
    return labels.str.contains(r"\b(?:HSH|JS|CASLEO)\b", case=False, regex=True)


def _is_explicit_veto(row: pd.Series) -> tuple[bool, str]:
    status = _joined_text(row, ("event_state", "event_status", "status", "classification", "object_class", "class", "type", "event_class", "variability_class")).casefold()
    if any(word in status for word in ("long period variable", "long-period variable", "lpv", "variable star")):
        return True, f"clasificación reportada: {status}"
    if any(word in status for word in ("finished", "terminated", "completed", "ended", "event over")):
        return True, f"estado reportado: {status}"
    return False, ""


def _classify(row: pd.Series, metrics: dict[str, Any], config: PriorityConfig) -> tuple[str, str]:
    veto, veto_reason = _is_explicit_veto(row)
    planning_question = _first_text(row, ("planning_question", "observation_question", "priority_question"))
    if veto:
        return "DESCARTAR", veto_reason

    stage = metrics["event_stage"]
    plausible_label = _joined_text(row, ("event_type", "classification", "object_class", "class", "survey", "target_id", "event_class")).casefold()
    labeled_microlensing = any(term in plausible_label for term in ("microlens", "microlensing", "ogle-20", "blg-"))
    has_model = np.isfinite(metrics["t0_hjd"]) and np.isfinite(metrics["tE_days"]) and metrics["tE_days"] > 0
    rising = (metrics["rise_significance_sigma"] >= config.rise_significance_sigma and
              metrics["rise_points"] >= config.minimum_rise_points)
    plausible = labeled_microlensing or has_model or rising
    if not plausible:
        return "REVISAR", "faltan etiqueta o datos suficientes para confirmar microlensing"
    if stage == "post_baseline":
        if planning_question:
            return "OPORTUNISTA", f"pregunta explícita sobre baseline: {planning_question}"
        return "DESCARTAR", "modelo ubica el evento en baseline posterior"

    if metrics["n_casleo_observations"] > 0:
        if stage in {"rise", "peak", "decline"}:
            detectable = (np.isfinite(metrics["expected_delta_mag"]) and
                          np.isfinite(metrics["detectable_delta_mag"]) and
                          metrics["expected_delta_mag"] >= metrics["detectable_delta_mag"])
            uncovered = stage not in metrics["casleo_phases"]
            if detectable or uncovered:
                return "CONTINUIDAD", "CASLEO tiene datos y la próxima visita mide cambio o añade fase"
        if metrics["stale_external_data"]:
            return "DIAGNÓSTICO", "evento plausible con brecha en fotometría externa reciente"
        if stage in {"rise", "peak", "decline"} and not np.isfinite(metrics["detectable_delta_mag"]):
            return "REVISAR", "hay cobertura CASLEO, pero falta una incertidumbre fotométrica para probar cambio medible"
        if planning_question:
            return "OPORTUNISTA", f"pregunta explícita: {planning_question}"
        return "DESCARTAR", "el seguimiento no añade cambio medible ni fase nueva"

    if stage == "rise":
        if rising:
            return "NUEVO-RISE", "subida reciente significativa y etapa de ascenso"
        if metrics["stale_external_data"]:
            return "DIAGNÓSTICO", "señal previa plausible, pero faltan datos recientes"
        return "REVISAR", "etapa de ascenso sin significancia suficiente en datos recientes"
    if stage == "peak":
        return "NUEVO-PEAK", "evento plausible cerca del máximo"
    if metrics["stale_external_data"]:
        return "DIAGNÓSTICO", "evento plausible con fotometría externa desactualizada"
    if stage == "decline":
        if planning_question:
            return "OPORTUNISTA", f"pregunta explícita: {planning_question}"
        return "OPORTUNISTA", "evento plausible en descenso; prioridad menor que subida o máximo"
    if stage == "unknown":
        return "REVISAR", "faltan parámetros t0/tE para ubicar la etapa del evento"
    return "REVISAR", "fase actual fuera de las ventanas priorizadas"


def evaluate_priority(
    targets: pd.DataFrame,
    observations: pd.DataFrame,
    visibility_summary: pd.DataFrame | None = None,
    *,
    casleo_coverage: pd.DataFrame | None = None,
    as_of: str | date | datetime | pd.Timestamp | None = None,
    config: PriorityConfig | None = None,
    output_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Assess every target once, assign a category, and order within categories.

    Visibility is merged as feasibility evidence; it does not change scientific
    category. Thresholds and windows are explicit in ``PriorityConfig``.
    """
    config = config or PriorityConfig()
    as_of_stamp = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if as_of_stamp.tzinfo is None:
        as_of_stamp = as_of_stamp.tz_localize("UTC")
    as_of_utc = as_of_stamp.tz_convert("UTC")
    if targets is None or targets.empty:
        empty_columns = [
            "target_id", "priority_category", "priority_rank", "category_rank",
            "assessment_status", "priority_reason", "event_stage",
            "event_model_parameters_available", "t0_hjd", "tE_days",
            "rise_significance_sigma", "expected_delta_mag", "detectable_delta_mag",
            "n_photometry", "n_casleo_observations", "n_casleo_photometry",
            "n_casleo_coverage_points", "casleo_phases", "days_since_external_photometry",
            "last_external_survey", "external_median_cadence_days",
            "external_stale_threshold_days", "stale_external_data",
            "magnitude_assessment", "n_visible_nights",
            "best_observable_minutes",
        ]
        empty = pd.DataFrame(columns=empty_columns)
        if output_dir is not None:
            output = Path(output_dir)
            output.mkdir(parents=True, exist_ok=True)
            empty.to_csv(output / "priority_assessment.csv", index=False)
            run_config = {"as_of_utc": as_of_utc.isoformat(), "priority_config": asdict(config), "category_order": CATEGORY_ORDER}
            (output / "priority_config.json").write_text(json.dumps(run_config, indent=2) + "\n")
        return empty
    if "target_id" not in targets:
        if "Target" not in targets:
            raise ValueError("Targets must contain target_id or Target.")
        targets = targets.rename(columns={"Target": "target_id"})
    result = targets.copy()
    result["target_id"] = result["target_id"].astype(str)
    if result["target_id"].duplicated().any():
        raise ValueError("Targets must be deduplicated to one row per target_id before priority assessment.")

    now = _as_hjd(as_of_utc)
    obs = observations.copy() if observations is not None else pd.DataFrame()
    if not obs.empty:
        if "target_id" not in obs:
            raise ValueError("Observations must contain target_id.")
        obs["target_id"] = obs["target_id"].astype(str)
        obs["_epoch_hjd"] = _epoch_hjd(obs)
        obs["_magnitude"] = _coalesced_numeric(obs, ("magnitude", "Magnitude", "mag"))
        obs = obs.loc[obs["_epoch_hjd"].notna() & obs["_magnitude"].notna()].copy()
        if not obs.empty:
            survey = _coalesced_text(obs, ("survey", "Source", "provider"), "unknown")
            band = _coalesced_text(obs, ("band", "Filter", "filter"))
            obs["_survey_key"] = survey.fillna("unknown").astype(str).str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            obs["_band_key"] = band.fillna("").astype(str).str.casefold().str.replace(r"[^a-z0-9]+", "_", regex=True)
            obs["_epoch_key"] = obs["_epoch_hjd"].round(6)
            method = obs.get("measurement_method", pd.Series("", index=obs.index)).astype(str)
            obs["_method_rank"] = method.map({"dia_forced_catalog": 0, "dia_source_catalog": 1}).fillna(0)
            obs = obs.sort_values("_method_rank").drop_duplicates(
                ["target_id", "_survey_key", "_band_key", "_epoch_key"], keep="first"
            ).drop(columns=["_survey_key", "_band_key", "_epoch_key", "_method_rank"])
    else:
        obs = pd.DataFrame(columns=["target_id", "_epoch_hjd", "_magnitude"])

    if visibility_summary is not None and not visibility_summary.empty:
        vis = visibility_summary.copy()
        vis["target_id"] = vis["target_id"].astype(str)
        overlap = (set(result.columns) & set(vis.columns)) - {"target_id"}
        # Current-run visibility supersedes provider metadata for other windows.
        result = result.drop(columns=list(overlap)).merge(vis, on="target_id", how="left")
    coverage_by_id: dict[str, pd.Series] = {}
    if casleo_coverage is not None and not casleo_coverage.empty:
        coverage = casleo_coverage.copy()
        if "target_id" not in coverage and "Target" in coverage:
            coverage = coverage.rename(columns={"Target": "target_id"})
        if "target_id" in coverage:
            coverage["target_id"] = coverage["target_id"].astype(str)
            coverage_by_id = {str(row["target_id"]): row for _, row in coverage.drop_duplicates("target_id").iterrows()}

    rows: list[dict[str, Any]] = []
    groups = {key: frame for key, frame in obs.groupby("target_id", sort=False)} if not obs.empty else {}
    limit_map = dict(config.magnitude_limits)
    for _, target in result.iterrows():
        target_id = str(target["target_id"])
        curve = groups.get(target_id, obs.iloc[0:0]).copy()
        t0 = float(_numeric(pd.DataFrame([target]), (
            "t0_hjd", "mop_t_0_hjd", "mop_t0_hjd", "t_0_HJD", "t_0_hjd",
            "event_peak_hjd", "peak_hjd", "Tmax (HJD)",
        )).iloc[0])
        te = float(_numeric(pd.DataFrame([target]), (
            "tE_days", "mop_t_e_days", "mop_te_days", "t_E_days", "t_e_days",
            "event_duration_days", "tau",
        )).iloc[0])
        u0 = float(_numeric(pd.DataFrame([target]), ("u0", "mop_u_0", "mop_u0", "u_0", "Umin")).iloc[0])
        stage, tau = _event_stage(t0, te, now, config)
        rise_sigma, rise_points = _rise_evidence(curve, now, config)
        casleo = _casleo_mask(curve) if not curve.empty else pd.Series(False, index=curve.index)
        casleo_curve = curve.loc[casleo]
        external = curve.loc[~casleo]
        latest_external = float(external["_epoch_hjd"].max()) if not external.empty else np.nan
        days_since_external = now - latest_external if np.isfinite(latest_external) else np.nan
        external["_survey_key"] = _coalesced_text(external, ("survey", "Source", "provider"), "unknown")
        latest_external_survey = ""
        cadence = np.nan
        external_survey_names: list[str] = []
        if not external.empty:
            survey_latest = external.groupby("_survey_key")["_epoch_hjd"].max().sort_values()
            external_survey_names = [str(name) for name in survey_latest.index]
            latest_external_survey = str(survey_latest.index[-1])
            latest_curve = external.loc[external["_survey_key"].eq(latest_external_survey)]
            external_times = np.sort(latest_curve["_epoch_hjd"].dropna().unique())
            cadence = float(np.median(np.diff(external_times))) if len(external_times) >= 3 else np.nan
        cadence_threshold = config.stale_cadence_factor * cadence if np.isfinite(cadence) and cadence > 0 else 0.0
        stale_threshold = max(config.stale_minimum_days, cadence_threshold)
        stale = bool(not np.isfinite(latest_external) or days_since_external > stale_threshold)
        phases: set[str] = set()
        if np.isfinite(t0) and np.isfinite(te) and te > 0 and not casleo_curve.empty:
            casleo_tau = (casleo_curve["_epoch_hjd"] - t0) / te
            phases.update({"fall" if phase == "decline" else phase for phase in
                           (_event_stage(t0, te, float(epoch), config)[0] for epoch in casleo_curve["_epoch_hjd"])})
        coverage_row = coverage_by_id.get(target_id)
        coverage_count = 0
        if coverage_row is not None:
            for phase in ("pre_baseline", "rise", "peak", "fall", "post_baseline"):
                phase_values = [coverage_row.get(f"{survey}_{phase}_{suffix}") for survey in ("hsh", "js") for suffix in ("n", "points")]
                phase_count = max([int(pd.to_numeric(value, errors="coerce") or 0) for value in phase_values if pd.notna(value)] or [0])
                if phase_count > 0:
                    phases.add(phase)
                    coverage_count += phase_count
            if not coverage_count:
                coverage_count = int(pd.to_numeric(coverage_row.get("hsh_n_images", 0), errors="coerce") or 0)
        casleo_count = max(len(casleo_curve), coverage_count)
        expected_delta = _model_delta_mag(t0, te, u0, now, config.planned_visit_interval_days)
        errors = _coalesced_numeric(casleo_curve, ("magnitude_err", "Magnitude Error", "magnitude_error", "Error")).dropna()
        detectable_delta = float(np.sqrt(2) * errors.median()) if not errors.empty else np.nan
        model_mag = np.nan
        target_mag = _numeric(pd.DataFrame([target]), ("magnitude_hsh", "magnitude_last", "magnitude", "mag_now")).iloc[0]
        if np.isfinite(target_mag):
            model_mag = float(target_mag)
        elif not curve.empty:
            latest = curve.sort_values("_epoch_hjd").iloc[-1]
            model_mag = float(latest["_magnitude"])
        metric = {
            "t0_hjd": t0, "tE_days": te, "u0": u0, "event_stage": stage,
            "event_model_parameters_available": bool(np.isfinite(t0) and np.isfinite(te) and te > 0),
            "tau_tE": tau, "days_to_peak": t0 - now if np.isfinite(t0) else np.nan,
            "expected_delta_mag": expected_delta, "detectable_delta_mag": detectable_delta,
            "rise_significance_sigma": rise_sigma, "rise_points": rise_points,
            "n_photometry": len(curve), "n_casleo_observations": casleo_count,
            "n_casleo_photometry": len(casleo_curve),
            "n_casleo_coverage_points": coverage_count,
            "casleo_phases": sorted(phases), "days_since_external_photometry": days_since_external,
            "last_external_survey": latest_external_survey,
            "external_median_cadence_days": cadence,
            "external_stale_threshold_days": stale_threshold,
            "external_surveys": external_survey_names,
            "n_external_surveys": len(external_survey_names),
            "stale_external_data": stale,
            "magnitude_assessment": model_mag,
        }
        if np.isfinite(t0) and np.isfinite(te) and te > 0 and not curve.empty:
            tau_obs = (curve["_epoch_hjd"] - t0) / te
            obs_stage = pd.Series("unknown", index=curve.index, dtype=object)
            obs_stage.loc[tau_obs.lt(-config.event_half_width_t_e)] = "pre_baseline"
            obs_stage.loc[tau_obs.ge(-config.event_half_width_t_e) & tau_obs.lt(-config.peak_half_width_t_e)] = "rise"
            obs_stage.loc[tau_obs.ge(-config.peak_half_width_t_e) & tau_obs.le(config.peak_half_width_t_e)] = "peak"
            obs_stage.loc[tau_obs.gt(config.peak_half_width_t_e) & tau_obs.le(config.event_half_width_t_e)] = "fall"
            obs_stage.loc[tau_obs.gt(config.event_half_width_t_e)] = "post_baseline"
            survey_labels = _coalesced_text(curve, ("survey", "Source", "provider"), "unknown")
            survey_keys = survey_labels.str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")
            counts = pd.DataFrame({"survey": survey_keys, "phase": obs_stage}).groupby(["survey", "phase"]).size()
            for (survey_key, phase), count in counts.items():
                if phase != "unknown":
                    metric[f"{survey_key}_{phase}_points"] = int(count)
        category, reason = _classify(target, metric, config)
        metric.update({"target_id": target_id, "priority_category": category, "priority_reason": reason})
        metric["category_order"] = CATEGORY_ORDER[category]
        metric["_rank_primary"] = {
            "NUEVO-RISE": -rise_sigma if np.isfinite(rise_sigma) else np.inf,
            "NUEVO-PEAK": abs(tau) if np.isfinite(tau) else np.inf,
            "CONTINUIDAD": -(expected_delta / detectable_delta) if np.isfinite(expected_delta) and np.isfinite(detectable_delta) and detectable_delta > 0 else np.inf,
            "DIAGNÓSTICO": -days_since_external if np.isfinite(days_since_external) else np.inf,
            "OPORTUNISTA": model_mag if np.isfinite(model_mag) else np.inf,
            "REVISAR": -rise_sigma if np.isfinite(rise_sigma) else np.inf,
            "DESCARTAR": 0,
        }[category]
        metric["maximum_magnification"] = _lens_magnification(u0)
        for instrument, limit in limit_map.items():
            metric[f"passes_{instrument.lower()}_magnitude_limit"] = bool(model_mag <= limit) if np.isfinite(model_mag) else pd.NA
        rows.append(metric)

    assessment = pd.DataFrame(rows)
    no_photometry = assessment["n_photometry"].fillna(0).eq(0)
    if no_photometry.any():
        discarded_ids = set(assessment.loc[no_photometry, "target_id"].astype(str))
        if output_dir is not None:
            discarded = result.loc[result["target_id"].astype(str).isin(discarded_ids)].copy()
            discarded["discard_reason"] = "No se encontró fotometría utilizable en las fuentes consultadas."
            if "photometry_requested" in discarded:
                skipped = ~discarded["photometry_requested"].fillna(False).astype(bool)
                discarded.loc[skipped, "discard_reason"] = "Fuera del alcance de fotometría solicitado; no se consultaron curvas."
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            discarded.to_csv(Path(output_dir) / "priority_discarded_no_photometry.csv", index=False)
        assessment = assessment.loc[~no_photometry].copy()
        result = result.loc[~result["target_id"].astype(str).isin(discarded_ids)].copy()
    elif output_dir is not None:
        discarded_path = Path(output_dir) / "priority_discarded_no_photometry.csv"
        discarded_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=[*result.columns, "discard_reason"]).to_csv(discarded_path, index=False)
    assessment = assessment.sort_values(["category_order", "_rank_primary", "target_id"], kind="stable").reset_index(drop=True)
    assessment["category_rank"] = assessment.groupby("priority_category", sort=False).cumcount() + 1
    assessment["priority_rank"] = np.arange(1, len(assessment) + 1)
    assessment["assessment_status"] = np.where(assessment["priority_category"].eq("REVISAR"), "review", "classified")
    assessment = assessment.drop(columns=["_rank_primary"])
    overlap = (set(result.columns) & set(assessment.columns)) - {"target_id"}
    result = result.drop(columns=list(overlap))
    result = result.merge(assessment, on="target_id", how="left")
    result = result.sort_values("priority_rank", kind="stable").reset_index(drop=True)
    if output_dir is not None:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        result.to_csv(output / "priority_assessment.csv", index=False)
        run_config = {
            "as_of_utc": as_of_utc.isoformat(),
            "priority_config": asdict(config),
            "category_order": CATEGORY_ORDER,
        }
        (output / "priority_config.json").write_text(json.dumps(run_config, indent=2) + "\n")
    return result
