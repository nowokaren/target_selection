import pandas as pd

from target_selection.planning import _merge_candidate_rows, build_pipeline_audit, collect_photometry, load_candidates
from target_selection.prioritization import select_schedulable_targets


def test_merged_candidate_provenance_covers_unique_and_shared_targets():
    candidates = pd.DataFrame({
        "target_id": ["BHTOM-only", "MOP-only", "shared", "shared"],
        "provider": ["bhtom", "mop", "bhtom", "opm"],
        "ra_deg": [10.0, 20.0, 30.0, 30.0],
        "dec_deg": [-10.0, -20.0, -30.0, -30.0],
    })

    merged = _merge_candidate_rows(candidates).set_index("target_id")
    assert merged.loc["BHTOM-only", "source_providers"] == "bhtom"
    assert merged.loc["MOP-only", "source_providers"] == "mop"
    assert merged.loc["shared", "source_providers"] == "bhtom,opm"
    assert merged["input_sources"].equals(merged["source_providers"])


def test_collect_photometry_uses_provider_error_policy_and_daily_cache(monkeypatch, tmp_path):
    calls = []

    class Provider:
        def photometry(self, targets, *, errors="raise"):
            assert errors == "ignore"
            assert targets["target_id"].tolist() == ["visible"]
            calls.append("query")
            return pd.DataFrame({"target_id": ["visible"], "epoch_mjd": [61000.0], "magnitude": [17.0]})

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    targets = pd.DataFrame({"target_id": ["visible"], "source_providers": ["bhtom"]})
    points = collect_photometry(
        targets, providers=("bhtom",), include_associated_surveys=False,
        cache_dir=tmp_path, verbose=False,
    )
    cached = collect_photometry(
        targets, providers=("bhtom",), include_associated_surveys=False,
        cache_dir=tmp_path, verbose=False,
    )
    assert len(points) == 1
    assert points.loc[0, "provider"] == "bhtom"
    assert calls == ["query"]
    assert cached.equals(points)


def test_collect_photometry_can_search_visible_survey_targets_in_toms(monkeypatch):
    class Provider:
        def photometry(self, targets, *, errors="raise"):
            assert targets["target_id"].tolist() == ["HSH-event"]
            return pd.DataFrame({
                "target_id": ["HSH-event"], "epoch_mjd": [61000.0], "magnitude": [17.0],
            })

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    targets = pd.DataFrame({"target_id": ["HSH-event"], "source_providers": ["hsh"]})

    points = collect_photometry(
        targets, providers=("bhtom",), query_all_tom_providers=True,
        include_associated_surveys=False, verbose=False,
    )

    assert points["provider"].tolist() == ["bhtom"]


def test_collect_photometry_reuses_mop_cache_for_non_mop_discovery(monkeypatch, tmp_path):
    cache = tmp_path / "mop" / "photometry"
    cache.mkdir(parents=True)
    pd.DataFrame({
        "Timestamp": ["2025-10-19 01:42:00"], "Magnitude": [15.039],
    }).to_csv(cache / "OGLE-2025-BLG-0675.csv", index=False)

    class Provider:
        def photometry(self, targets, *, errors="raise"):
            assert targets["target_id"].tolist() == ["OGLE-2025-BLG-0675"]
            return pd.DataFrame({
                "target_id": ["OGLE-2025-BLG-0675"], "epoch_mjd": [60967.57],
                "magnitude": [15.039], "band": ["OGLE_I"],
            })

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    targets = pd.DataFrame({
        "target_id": ["OGLE-2025-BLG-0675", "no-MOP-record-or-cache"],
        "source_providers": ["hsh", "hsh"],
    })
    points = collect_photometry(
        targets, providers=("mop",), query_all_tom_providers=True,
        include_associated_surveys=False, cache_dir=tmp_path, verbose=False,
    )
    assert points["target_id"].tolist() == ["OGLE-2025-BLG-0675"]
    assert points["provider"].tolist() == ["mop"]


def test_load_candidates_forces_provider_refresh_once_per_day(monkeypatch, tmp_path):
    calls = []

    class Provider:
        def load_targets(self, *, filters=None, cache_dir=None, refresh=False):
            calls.append(refresh)
            return pd.DataFrame({"target_id": ["event"], "ra_deg": [10.0], "dec_deg": [-20.0]})

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    first = load_candidates(
        providers=("bhtom",), cache_dir=tmp_path, verbose=False,
    )
    second = load_candidates(
        providers=("bhtom",), cache_dir=tmp_path, verbose=False,
    )

    assert calls == [True]
    assert first["target_id"].tolist() == second["target_id"].tolist() == ["event"]


def test_load_candidates_passes_configured_bhtom_query(monkeypatch, tmp_path):
    calls = []

    class Provider:
        def load_targets(self, *, filters=None, cache_dir=None, refresh=False, classification=None, query=None, max_pages=None):
            calls.append((classification, query, max_pages))
            return pd.DataFrame({"target_id": ["event"], "ra_deg": [10.0], "dec_deg": [-20.0]})

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    load_candidates(
        providers=("bhtom",), cache_dir=tmp_path, verbose=False,
        provider_settings={"bhtom": {
            "classification": "Microlensing Event",
            "query": {"importanceMin": 2}, "max_pages": 3,
        }},
    )
    assert calls == [("Microlensing Event", {"importanceMin": 2}, 3)]


def test_final_selection_uses_visibility_for_inclusion_not_priority_category():
    candidates = pd.DataFrame({"target_id": ["visible", "hidden"], "n_visible_nights": [0, 4]})
    priority = pd.DataFrame({
        "target_id": ["visible", "hidden"], "priority_category": ["DESCARTAR", "NUEVO-PEAK"],
        "n_visible_nights": [1, 0], "priority_rank": [1, 2],
    })
    assert select_schedulable_targets(candidates, priority)["target_id"].tolist() == ["visible"]


def test_pipeline_audit_marks_each_gate_and_first_exclusion(tmp_path):
    candidates = pd.DataFrame({"target_id": ["hidden", "no-data", "no-I", "no-epoch", "kept"], "ra_deg": range(5)})
    visibility = pd.DataFrame({
        "target_id": candidates.target_id, "n_visible_nights": [0, 1, 1, 1, 1],
    })
    visible = visibility.loc[visibility.n_visible_nights.gt(0)]
    measurements = pd.DataFrame({
        "target_id": ["no-I", "no-epoch", "kept"], "provider": ["bhtom"] * 3,
        "magnitude": [17.0] * 3,
    })
    assessment = pd.DataFrame({
        "target_id": ["no-I", "no-epoch", "kept"],
        "latest_i_magnitude": [19.0, 16.0, 16.0],
        "latest_i_band": ["i"] * 3, "latest_i_epoch_hjd": [1.0] * 3,
        "latest_i_passes_hsh_limit": [False, True, True],
        "latest_i_passes_js_limit": [False, True, True],
        "latest_i_passes_any_telescope_limit": [False, True, True],
    })
    plottable = measurements.loc[measurements.target_id.eq("kept")]
    final = candidates.loc[candidates.target_id.eq("kept")]

    audit = build_pipeline_audit(
        candidates, visibility, visible, measurements, assessment, plottable, final,
        output_path=tmp_path / "pipeline_filter_audit.csv",
    ).set_index("target_id")

    assert audit.loc["hidden", "exclusion_stage"] == "visibility"
    assert audit.loc["no-data", "tom_photometry_status"] == "failed"
    assert audit.loc["no-data", "tom_photometry_query_status"] == "selected_TOMs_queried"
    assert audit.loc["no-I", "exclusion_stage"] == "latest_i"
    assert audit.loc["no-epoch", "plottable_photometry_status"] == "failed"
    assert audit.loc["kept", "final_status"] == "included"
    assert (tmp_path / "pipeline_filter_audit.csv").exists()


def test_pipeline_audit_accepts_associated_survey_photometry(tmp_path):
    candidates = pd.DataFrame({"target_id": ["ogle-event"]})
    visibility = pd.DataFrame({"target_id": ["ogle-event"], "n_visible_nights": [1]})
    survey_points = pd.DataFrame({
        "target_id": ["ogle-event"], "provider": ["ogle"], "magnitude": [15.2],
    })
    assessment = pd.DataFrame({
        "target_id": ["ogle-event"], "latest_i_magnitude": [15.2],
        "latest_i_band": ["I"], "latest_i_epoch_hjd": [2461236.0],
        "latest_i_passes_hsh_limit": [True], "latest_i_passes_js_limit": [True],
        "latest_i_passes_any_telescope_limit": [True],
    })

    audit = build_pipeline_audit(
        candidates, visibility, visibility, pd.DataFrame(columns=["target_id"]),
        assessment, survey_points, candidates, survey_measurements=survey_points,
        tom_plottable_photometry=pd.DataFrame(columns=["target_id"]),
        survey_plottable_photometry=survey_points,
        output_path=tmp_path / "pipeline_filter_audit.csv",
    ).set_index("target_id")

    row = audit.loc["ogle-event"]
    assert row["tom_photometry_status"] == "failed"
    assert row["survey_photometry_status"] == "passed"
    assert row["photometry_status"] == "passed"
    assert row["n_tom_photometry_points"] == 0
    assert row["n_survey_photometry_points"] == 1
    assert row["n_plottable_photometry_points"] == 1
    assert row["final_status"] == "included"
