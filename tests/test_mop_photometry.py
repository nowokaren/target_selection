import pandas as pd

from mop_photometry import (
    prepare_lightcurve_data, select_lightcurve_filters,
    split_mop_candidates_without_event_data,
)


def test_filter_priority_prefers_ogle_i_and_g():
    data = pd.DataFrame({"Filter": ["r", "OGLE_I", "G", "r"]})
    assert select_lightcurve_filters(data) == ["OGLE_I", "G"]


def test_prepare_lightcurve_data_removes_invalid_rows():
    data = pd.DataFrame({
        "Filter": ["G", "G"],
        "Timestamp": ["2025-01-01", "invalid"],
        "Magnitude": [18.25, "bad"],
        "Error": [0.1, -1],
    })
    result = prepare_lightcurve_data(data, "G")
    assert len(result) == 1
    assert result.iloc[0]["Magnitude"] == 18.25


def test_mop_visible_candidates_without_event_data_are_excluded(tmp_path):
    photometry_dir = tmp_path / "mop_photometry"
    photometry_dir.mkdir()
    pd.DataFrame({"Timestamp": ["2026-08-01"], "Filter": ["I"]}).to_csv(
        photometry_dir / "photometry_only.csv", index=False
    )
    targets = pd.DataFrame({
        "Target": ["missing", "page_only", "parameters_only", "photometry_only", "followup_only"],
        "is_mop_visible_in_run": [True, True, True, True, False],
        "mop_parameters_status": ["unavailable", "available", "available", "unavailable", "unavailable"],
        "mop_t_e_days": [None, None, 20.0, None, None],
    })

    included, excluded = split_mop_candidates_without_event_data(
        targets, photometry_dir=photometry_dir,
    )

    assert excluded["Target"].tolist() == ["missing", "page_only"]
    assert excluded.iloc[0]["mop_data_exclusion_reason"] == (
        "MOP candidate has neither event parameters nor photometry"
    )
    assert set(included["Target"]) == {"parameters_only", "photometry_only", "followup_only"}
    assert included.set_index("Target").loc["photometry_only", "mop_photometry_points"] == 1
