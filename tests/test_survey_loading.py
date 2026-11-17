import pandas as pd

from target_selection.planning import merge_candidate_targets
from target_selection.tasks.surveys import (load_observed_survey_targets, observed_survey_epochs, update_observed_survey_target_catalog)


def test_load_js_inventory_imports_observed_rows_and_returns_epochs(tmp_path):
    inventory = tmp_path / "js.csv"
    pd.DataFrame({
        "target": ["OGLE-2025-BLG-0001", "OGLE-2025-BLG-0002"],
        "date": ["2026-02-11", "2026-02-12"],
        "filter_or_grating": ["I", "V"],
        "exposure_s": [300, 300],
        "product_status": ["observed", "planned"],
    }).to_csv(inventory, index=False)
    database = tmp_path / "targets.sqlite"

    targets = load_observed_survey_targets(
        providers=("JS",), database_path=database, js_inventory_path=inventory,
        verbose=False,
    )
    epochs = observed_survey_epochs(targets, providers=("JS",), database_path=database)

    assert targets["target_id"].tolist() == ["OGLE-2025-BLG-0001"]
    assert targets.iloc[0]["source_providers"] == "js"
    assert targets.iloc[0]["is_previously_observed"]
    assert epochs["provider"].tolist() == ["JS"]
    assert epochs.iloc[0]["band"] == "I"


def test_candidate_merge_preserves_tom_coordinates_and_survey_provenance():
    candidates = pd.DataFrame({
        "target_id": ["OGLE-2025-BLG-0001"], "provider": ["opm"],
        "ra_deg": [270.0], "dec_deg": [-30.0],
    })
    observed = pd.DataFrame({
        "target_id": ["OGLE-2025-BLG-0001"], "provider": ["hsh"],
        "ra_deg": [None], "dec_deg": [None], "is_previously_observed": [True],
    })

    merged = merge_candidate_targets(candidates, observed)

    assert len(merged) == 1
    assert merged.iloc[0]["ra_deg"] == 270.0
    assert merged.iloc[0]["source_providers"] == "hsh,opm"
    assert merged.iloc[0]["is_previously_observed"]


def test_observed_target_catalog_accumulates_coordinates_and_tom_source(tmp_path):
    catalog_path = tmp_path / "catalogs" / "observed_targets.csv"
    observed = pd.DataFrame([{
        "target_id": "OGLE-2026-BLG-0001", "ra_deg": 10.0, "dec_deg": -20.0,
        "source_providers": "hsh,js", "is_previously_observed": True,
    }, {
        "target_id": "missing-position", "ra_deg": None, "dec_deg": None,
        "source_providers": "hsh", "is_previously_observed": True,
    }])
    tom_rows = pd.DataFrame([{
        "target_id": "OGLE-2026-BLG-0001", "ra_deg": 11.0, "dec_deg": -21.0,
        "source_providers": "mop,opm,hsh,js", "magnitude_last": 16.1,
    }])
    catalog = update_observed_survey_target_catalog(observed, tom_rows, catalog_path=catalog_path)

    assert catalog.loc[0, "ra_deg"] == 11.0
    assert catalog.loc[0, "tom_source"] == "mop,opm"
    assert catalog.loc[0, "observed_by"] == "hsh,js"
    assert catalog.loc[0, "magnitude_last"] == 16.1

    updated = update_observed_survey_target_catalog(observed.iloc[0:0], pd.DataFrame(), catalog_path=catalog_path)
    assert updated.loc[0, "target_id"] == "OGLE-2026-BLG-0001"
    assert updated.loc[0, "tom_source"] == "mop,opm"


def test_observed_target_catalog_can_be_empty_and_reloaded(tmp_path):
    path = tmp_path / "catalogs" / "observed_targets.csv"
    empty = update_observed_survey_target_catalog(pd.DataFrame(), pd.DataFrame(), catalog_path=path)

    assert empty.empty
    assert update_observed_survey_target_catalog(pd.DataFrame(), pd.DataFrame(), catalog_path=path).empty
