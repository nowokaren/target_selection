import pandas as pd

from target_registry import TargetRegistry
from target_selection.data_availability import select_targets_by_data_availability


def test_with_data_scope_accepts_mop_photometry_and_followup_images(tmp_path):
    targets = pd.DataFrame(
        {
            "Target": ["mop-event", "hsh-event", "no-data-event"],
            "RA_deg": [270.0, 271.0, 272.0],
            "Dec_deg": [-30.0, -31.0, -32.0],
        }
    )
    cache_dir = tmp_path / "mop_photometry"
    cache_dir.mkdir()
    pd.DataFrame({"Timestamp": ["2026-08-01T00:00:00Z"]}).to_csv(
        cache_dir / "mop-event.csv", index=False
    )

    database = TargetRegistry(tmp_path / "targets.sqlite")
    database.import_survey_observations(
        "HSH",
        targets.loc[[1], ["Target", "RA_deg", "Dec_deg"]].assign(mjd=61000.0),
    )

    included, excluded = select_targets_by_data_availability(
        targets,
        mop_photometry_dir=cache_dir,
        include_mop_photometry=True,
        database=database,
        observation_providers=("HSH",),
    )

    assert included["Target"].tolist() == ["mop-event", "hsh-event"]
    assert included.set_index("Target").loc["mop-event", "mop_photometry_points"] == 1
    assert included.set_index("Target").loc["hsh-event", "followup_observation_points"] == 1
    assert excluded["Target"].tolist() == ["no-data-event"]
    assert excluded.iloc[0]["target_data_exclusion_reason"]


def test_all_scope_keeps_targets_without_active_source_data(tmp_path):
    targets = pd.DataFrame(
        {"Target": ["no-data-event"], "RA_deg": [270.0], "Dec_deg": [-30.0]}
    )

    included, excluded = select_targets_by_data_availability(
        targets, target_data_scope="all"
    )

    assert included["Target"].tolist() == ["no-data-event"]
    assert not included.iloc[0]["has_target_data"]
    assert excluded.empty
