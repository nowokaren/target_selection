import json

import numpy as np
import pandas as pd

from target_selection.prioritization import PriorityConfig, evaluate_priority
from target_selection.planning import add_ogle_event_parameters


def _jd(value: str) -> float:
    return float(pd.Timestamp(value, tz="UTC").to_julian_date())


def test_evaluate_priority_assigns_categories_from_evidence_and_orders(tmp_path):
    as_of = "2026-10-08"
    now = _jd(as_of)
    targets = pd.DataFrame([
        {"target_id": "OGLE-2026-BLG-1001", "event_peak_hjd": now + 12, "event_duration_days": 30, "u0": 0.2, "magnitude_last": 15.8},
        {"target_id": "OGLE-2026-BLG-1002", "event_peak_hjd": now, "event_duration_days": 30, "u0": 0.05, "magnitude_last": 16.0},
        {"target_id": "OGLE-2026-BLG-1003", "event_peak_hjd": now, "event_duration_days": 30, "u0": 0.1, "magnitude_last": 15.5},
        {"target_id": "ZTF-LPV", "classification": "long period variable", "magnitude_last": 15.0},
        {"target_id": "unknown", "magnitude_last": 16.0},
    ])
    observations = pd.DataFrame([
        {"target_id": "OGLE-2026-BLG-1001", "epoch_hjd": now - 5, "magnitude": 17.0, "magnitude_err": 0.01, "band": "I", "survey": "OGLE"},
        {"target_id": "OGLE-2026-BLG-1001", "epoch_hjd": now - 3, "magnitude": 16.8, "magnitude_err": 0.01, "band": "I", "survey": "OGLE"},
        {"target_id": "OGLE-2026-BLG-1001", "epoch_hjd": now - 1, "magnitude": 16.6, "magnitude_err": 0.01, "band": "I", "survey": "OGLE"},
        {"target_id": "OGLE-2026-BLG-1002", "epoch_hjd": now - 1, "magnitude": 16.0, "magnitude_err": 0.02, "band": "I", "survey": "OGLE"},
        {"target_id": "OGLE-2026-BLG-1003", "epoch_hjd": now, "magnitude": 15.5, "magnitude_err": 0.01, "band": "V", "survey": "HSH"},
    ])
    visibility = pd.DataFrame({
        "target_id": targets.target_id,
        "n_visible_nights": [0, 1, 1, 1, 1],
        "best_observable_minutes": [0, 90, 90, 90, 90],
    })
    assessed = evaluate_priority(
        targets, observations, visibility, as_of=as_of,
        config=PriorityConfig(planned_visit_interval_days=7), output_dir=tmp_path,
    ).set_index("target_id")

    assert assessed.loc["OGLE-2026-BLG-1001", "priority_category"] == "NUEVO-RISE"
    assert assessed.loc["OGLE-2026-BLG-1002", "priority_category"] == "NUEVO-PEAK"
    assert assessed.loc["OGLE-2026-BLG-1003", "priority_category"] == "CONTINUIDAD"
    assert assessed.loc["ZTF-LPV", "priority_category"] == "DESCARTAR"
    assert "unknown" not in assessed.index
    assert assessed.loc["OGLE-2026-BLG-1001", "rise_significance_sigma"] > 3
    assert assessed.loc["OGLE-2026-BLG-1003", "maximum_magnification"] > 5
    assert assessed.loc["OGLE-2026-BLG-1003", "n_casleo_photometry"] == 1
    assert assessed.loc["OGLE-2026-BLG-1001", "priority_rank"] < assessed.loc["OGLE-2026-BLG-1002", "priority_rank"]
    assert (tmp_path / "priority_assessment.csv").exists()
    assert "unknown" in pd.read_csv(tmp_path / "priority_discarded_no_photometry.csv").target_id.tolist()
    run_config = json.loads((tmp_path / "priority_config.json").read_text())
    assert run_config["priority_config"]["rise_significance_sigma"] == 3.0
    assert run_config["as_of_utc"].startswith(as_of)


def test_casleo_phase_coverage_is_used_without_inventing_photometry():
    as_of = "2026-10-08"
    now = _jd(as_of)
    targets = pd.DataFrame([{"target_id": "OGLE-2026-BLG-1003", "event_peak_hjd": now - 20, "event_duration_days": 30, "u0": 0.1}])
    coverage = pd.DataFrame([{"target_id": "OGLE-2026-BLG-1003", "hsh_peak_n": 4}])
    photometry = pd.DataFrame([{
        "target_id": targets.iloc[0].target_id, "epoch_hjd": now - 1,
        "magnitude": 17.0, "survey": "OGLE",
    }])
    result = evaluate_priority(targets, photometry, casleo_coverage=coverage, as_of=as_of)
    row = result.iloc[0]
    assert row.priority_category == "CONTINUIDAD"
    assert row.n_casleo_observations == 4
    assert row.n_casleo_photometry == 0
    assert row.n_casleo_coverage_points == 4
    assert row.casleo_phases == ["peak"]


def test_visibility_changes_feasibility_not_scientific_category():
    as_of = "2026-10-08"
    now = _jd(as_of)
    target = pd.DataFrame([{
        "target_id": "OGLE-2026-BLG-1004", "event_peak_hjd": now + 12,
        "event_duration_days": 30, "u0": 0.2,
    }])
    curve = pd.DataFrame([
        {"target_id": target.iloc[0].target_id, "epoch_hjd": now - 5 + i * 2,
         "magnitude": 17.0 - i * 0.2, "magnitude_err": 0.01,
         "band": "I", "survey": "OGLE"}
        for i in range(3)
    ])
    not_visible = pd.DataFrame([{"target_id": target.iloc[0].target_id, "n_visible_nights": 0}])
    visible = pd.DataFrame([{"target_id": target.iloc[0].target_id, "n_visible_nights": 2}])
    category_a = evaluate_priority(target, curve, not_visible, as_of=as_of).iloc[0].priority_category
    category_b = evaluate_priority(target, curve, visible, as_of=as_of).iloc[0].priority_category
    assert category_a == category_b == "NUEVO-RISE"


def test_sparse_survey_column_preserves_provider_labels_and_recent_data():
    as_of = "2026-10-07"
    now = _jd(as_of)
    target = pd.DataFrame([{
        "target_id": "OGLE-2026-BLG-0888",
        "mop_t_0_hjd": now - 10,
        "mop_t_e_days": 80,
        "mop_u_0": 0.2,
    }])
    observations = pd.DataFrame([
        {"target_id": target.iloc[0].target_id, "epoch_hjd": now - 30 + day,
         "magnitude": np.nan if day == 7 else 16.3 + day * 0.001,
         "Magnitude": 16.3 + day * 0.001,
         "magnitude_err": .03, "survey": np.nan, "Source": "ATLAS",
         "provider": "mop", "band": "o"}
        for day in range(8)
    ] + [
        {"target_id": target.iloc[0].target_id, "epoch_hjd": now - 100,
         "magnitude": 17.0, "magnitude_err": .05, "survey": "OGLE",
         "Source": np.nan, "provider": "ogle", "band": "I"},
    ])
    result = evaluate_priority(target, observations, as_of=as_of).iloc[0]

    assert result.event_stage != "unknown"
    assert result.last_external_survey == "ATLAS"
    assert result.n_external_surveys == 2
    assert result.n_photometry == 9
    assert result.days_since_external_photometry == 23
    assert not result.stale_external_data
    assert result.external_stale_threshold_days >= 30


def test_ogle_ews_parameters_fill_only_missing_tom_values():
    targets = pd.DataFrame([
        {"target_id": "OGLE-2026-BLG-0888", "mop_t_0_hjd": np.nan, "mop_t_e_days": np.nan},
        {"target_id": "OGLE-2026-BLG-0001", "mop_t_0_hjd": 2461000.0, "mop_t_e_days": 50.0},
    ])
    catalog = pd.DataFrame([
        {"target_id": "OGLE-2026-BLG-0888", "t0_hjd": 2461100.0, "tE_days": 60.0, "u0": 0.2},
        {"target_id": "OGLE-2026-BLG-0001", "t0_hjd": 2461200.0, "tE_days": 30.0, "u0": 0.1},
    ])
    enriched = add_ogle_event_parameters(targets, event_catalog=catalog, verbose=False).set_index("target_id")

    assert enriched.loc["OGLE-2026-BLG-0888", "t0_hjd"] == 2461100.0
    assert enriched.loc["OGLE-2026-BLG-0888", "tE_days"] == 60.0
    assert enriched.loc["OGLE-2026-BLG-0001", "mop_t_0_hjd"] == 2461000.0
    assert pd.isna(enriched.loc["OGLE-2026-BLG-0001", "t0_hjd"])


def test_empty_assessment_still_writes_a_reproducible_schema(tmp_path):
    empty = evaluate_priority(pd.DataFrame(), pd.DataFrame(), as_of="2026-10-08", output_dir=tmp_path)
    assert "priority_category" in empty
    assert (tmp_path / "priority_assessment.csv").exists()
    assert (tmp_path / "priority_config.json").exists()
