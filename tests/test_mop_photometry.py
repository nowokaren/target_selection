import pandas as pd

from mop_photometry import (
    prepare_lightcurve_data, select_lightcurve_filters,
    split_mop_candidates_without_event_data, normalize_photometry_provenance, load_event_photometry,
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


def test_empty_fetch_is_reported_and_not_cached(tmp_path, capsys):
    class EmptyMop:
        def photometry(self, *args, **kwargs):
            return pd.DataFrame(columns=["Timestamp", "Magnitude", "Source"])

    result = load_event_photometry("empty-target", mop=EmptyMop(), cache_dir=tmp_path)
    assert result.empty
    assert not (tmp_path / "empty-target.csv").exists()
    assert "Photometry skipped for empty-target" in capsys.readouterr().out


def test_mop_provenance_fills_telescope_from_survey():
    result = normalize_photometry_provenance(pd.DataFrame({
        "Telescope": [""], "Source": ["OGLE"], "Timestamp": ["2025-01-01"],
        "Magnitude": [17.0],
    }))
    assert result.loc[0, "Telescope"] == "OGLE"
    assert result.loc[0, "Provider"] == "MOP"
