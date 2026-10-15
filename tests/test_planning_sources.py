import pandas as pd

from target_selection.planning import _merge_candidate_rows, collect_photometry
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


def test_collect_photometry_uses_provider_error_policy(monkeypatch):
    class Provider:
        def photometry(self, targets, *, errors="raise"):
            assert errors == "ignore"
            assert targets["target_id"].tolist() == ["visible"]
            return pd.DataFrame({"target_id": ["visible"], "epoch_mjd": [61000.0], "magnitude": [17.0]})

    from target_selection.data_source import data_source

    monkeypatch.setattr(data_source, "get", lambda name: Provider())
    targets = pd.DataFrame({"target_id": ["visible"], "source_providers": ["bhtom"]})
    points = collect_photometry(targets, providers=("bhtom",), include_associated_surveys=False, verbose=False)
    assert len(points) == 1
    assert points.loc[0, "provider"] == "bhtom"


def test_final_selection_uses_current_visibility_not_provider_metadata():
    candidates = pd.DataFrame({"target_id": ["visible", "hidden"], "n_visible_nights": [0, 4]})
    priority = pd.DataFrame({
        "target_id": ["visible", "hidden"], "priority_category": ["NUEVO-PEAK", "NUEVO-PEAK"],
        "n_visible_nights": [1, 0], "priority_rank": [1, 2],
    })
    assert select_schedulable_targets(candidates, priority)["target_id"].tolist() == ["visible"]
