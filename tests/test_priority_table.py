import pandas as pd

from target_selection.planning import filter_targets_with_valid_coordinates, save_priority_products
from target_selection.prioritization import build_priority_table


def test_priority_table_groups_photometry_and_hsh_exposure_by_event_stage(tmp_path):
    event = "OGLE-2025-BLG-0001"
    priority = pd.DataFrame({
        "target_id": [event, "ZTF26aa00001", "ZTF26aa00002"], "tom_source": ["MOP, BHTOM", "OPM", "OPM"],
        "priority_rank": [1, 2, 3], "priority_category": ["NUEVO-PEAK", "REVISAR", "REVISAR"],
        "t0_hjd": [2461000.0, float("nan"), float("nan")], "tE_days": [10.0, float("nan"), float("nan")],
        "u0": [0.2, float("nan"), float("nan")], "galactic_lng": [0, 120, 120],
        "galactic_lat": [0, 5, 20], "ra_deg": [266, 180, 80], "dec_deg": [-29, 0, -70],
    })
    observations = pd.DataFrame({
        "target_id": [event, event, event, event, event, event, "ZTF26aa00001"],
        "epoch_hjd": [2460970.0, 2460970.0, 2460990.0, 2461000.0, 2461010.0, 2461030.0, 2460990.0],
        "magnitude": [17.1, 17.1, 16.9, 16.8, 16.9, 17.2, 18.0],
        "survey": ["OGLE"] * 7, "band": ["I"] * 7,
        "provider": ["BHTOM", "BHTOM", "OPM", "MOP", "OPM", "OGLE", "OPM"],
    })
    survey_epochs = pd.DataFrame({
        "Target": [event, event], "provider": ["HSH", "HSH"],
        "mjd": [60999.5, 61000.5], "exptime_s": [300, 600],
    })
    visibility = pd.DataFrame({
        "target_id": [event, event], "observable_minutes": [100, 200],
        "peak_altitude_deg": [60, 50],
    })

    table = build_priority_table(
        priority, observations, survey_epochs, visibility, as_of="2026-10-16",
    )

    assert table.loc[0, "tom_source"] == "MOP, BHTOM"
    assert "priority_rank" not in table.columns
    assert "priority_category" not in table.columns
    assert table.loc[0, "event_subtype"] == "BLG"
    assert table.loc[0, "event_survey"] == "OGLE"
    assert table.loc[0, "stage"] == "post_baseline"
    assert table.loc[0, "photometry_sources"] == "BHTOM (1); MOP (1); OGLE (1); OPM (2)"
    assert table.loc[0, "last_magnitude"] == 17.2
    assert table.loc[0, "bhtom_pre_baseline_points"] == 1
    assert table.loc[0, "opm_rise_points"] == 1
    assert table.loc[0, "mop_peak_points"] == 1
    assert table.loc[0, "opm_fall_points"] == 1
    assert table.loc[0, "ogle_post_baseline_points"] == 1
    assert table.loc[0, "hsh_peak_points"] == 2
    assert table.loc[0, "visible_minutes_above_40"] == 300
    assert table.loc[0, "minimum_airmass"] > 1
    assert pd.isna(table.loc[1, "opm_rise_points"])
    assert pd.isna(table.loc[1, "hsh_rise_points"])
    assert table.loc[1, "stage"] == "unknown"
    assert table.loc[1, "event_subtype"] == "GD"
    assert table.loc[2, "event_subtype"] == "LMC"

    csv_path, png_path = save_priority_products(table, tmp_path, name="priority")
    assert csv_path.exists()
    assert png_path.exists()
    assert (tmp_path / "priority.pdf").exists()


def test_priority_table_uses_microlensing_parameter_aliases_and_plain_ra_dec():
    target = pd.DataFrame({
        "target_id": ["ZTF26example"], "tom_source": ["MOP"],
        "Tmax (HJD)": [2461000.0], "tau": [30.0], "Umin": [0.2],
        "RA": [266.0], "Dec": [-29.0],
    })
    table = build_priority_table(
        target, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), as_of="2026-10-16",
    )

    assert table.loc[0, "days_since_t0"] > 0
    assert table.loc[0, "tE_days"] == 30.0
    assert table.loc[0, "u0"] == 0.2
    assert table.loc[0, "event_subtype"] == "BLG"


def test_priority_table_normalizes_mjd_t0_to_relative_days():
    target = pd.DataFrame({"target_id": ["ZTF26example"], "t0_hjd": [61000.0], "tE_days": [30.0]})
    table = build_priority_table(
        target, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), as_of="2026-10-16",
    )

    assert abs(table.loc[0, "days_since_t0"] - (pd.Timestamp("2026-10-16", tz="UTC").to_julian_date() - 61000.0 - 2400000.5)) < 1e-8

    invalid_t0 = pd.DataFrame({"target_id": ["Gaia24btg"], "t0_hjd": [0.0], "tE_days": [0.0]})
    invalid = build_priority_table(
        invalid_t0, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), as_of="2026-10-16",
    )
    assert pd.isna(invalid.loc[0, "days_since_t0"])
    assert pd.isna(invalid.loc[0, "tE_days"])


def test_priority_table_uses_reported_stage_when_event_parameters_are_missing():
    target = pd.DataFrame({
        "target_id": ["OGLE-2024-BLG-0001"], "t0_hjd": [0],
        "tE_days": [0], "event_stage": ["rise"],
    })
    table = build_priority_table(
        target, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), as_of="2026-10-16",
    )

    assert table.loc[0, "stage"] == "rise"


def test_coordinate_filter_drops_missing_or_out_of_range_positions():
    targets = pd.DataFrame({
        "target_id": ["valid", "missing", "bad_ra", "bad_dec"],
        "RA_deg": [10, 20, 360, 10], "Dec_deg": [-20, None, 0, 91],
    })

    result = filter_targets_with_valid_coordinates(targets)

    assert result.target_id.tolist() == ["valid"]
    assert result.loc[0, "ra_deg"] == 10
    assert result.loc[0, "dec_deg"] == -20


def test_last_data_prefers_latest_i_measurement_over_newer_other_filter():
    target = pd.DataFrame({"target_id": ["OGLE-2025-BLG-0001"], "t0_hjd": [2461000.0], "tE_days": [30.0]})
    observations = pd.DataFrame({
        "target_id": ["OGLE-2025-BLG-0001"] * 2,
        "epoch_hjd": [2460990.0, 2461005.0], "magnitude": [17.0, 16.0],
        "survey": ["OGLE", "ZTF"], "band": ["Cousins I", "g"],
        "provider": ["OGLE", "MOP"],
    })
    table = build_priority_table(
        target, observations, pd.DataFrame(), pd.DataFrame(), as_of="2026-10-16",
    )

    assert table.loc[0, "last_magnitude"] == 17.0
    assert table.loc[0, "last_survey"] == "OGLE"
    assert table.loc[0, "last_filter"] == "Cousins I"
