import pandas as pd

from observing_selection_summary import (
    build_observing_selection_summary,
    save_observing_selection_summary,
)


def test_observing_selection_summary_groups_stage_time_and_saves_products(tmp_path):
    targets = pd.DataFrame({
        "Target": ["faint", "bright"],
        "passes_visibility_filter": [True, True],
        "mag_now": [19.2, 17.4],
        "max_observable_minutes": [90.0, 150.0],
        "best_observation_date": ["2026-08-01", "2026-08-01"],
        "best_observable_start_local": ["22:00", "21:00"],
        "best_observable_end_local": ["23:30", "23:30"],
        "mop_t_0_hjd": [2460000.5, 2460000.5],
        "mop_t_e_days": [10.0, 10.0],
        "mop_u_0": [0.2, 0.1],
    })
    photometry = tmp_path / "mop"
    photometry.mkdir()
    pd.DataFrame({"Timestamp": ["2023-02-25T00:00:00Z", "2023-01-01T00:00:00Z"]}).to_csv(
        photometry / "bright.csv", index=False
    )
    hsh = pd.DataFrame({
        "object": ["bright"], "imagetyp": ["object"], "astromet": ["yes"],
        "obj_stat": ["OK"], "clmatch": [True], "mjd-obs": [60000.0],
        "filter": ["(5) I"], "exptime": [300.0], "airmass": [1.2],
    })
    hsh_path = tmp_path / "hsh.csv"
    hsh.to_csv(hsh_path, index=False)
    coverage = pd.DataFrame({
        "Target": ["bright", "bright"], "visitId": [12, 12],
        "detector": [1, 2], "expMidptMJD": [60000.0, 60000.0],
    })

    summary = build_observing_selection_summary(
        targets, coverage, photometry, hsh_image_catalog=hsh_path, release_visit_exptime_s=30,
    )
    assert summary["Target"].tolist() == ["bright", "faint"]
    bright = summary.set_index("Target").loc["bright"]
    assert bright["mop_peak_points"] == 1
    assert bright["hsh_peak_hours"] == 300 / 3600
    assert bright["release_peak_hours"] == 30 / 3600
    assert bright["visible_from"] == "21:00"

    csv_path, png_path = tmp_path / "summary.csv", tmp_path / "summary.png"
    save_observing_selection_summary(summary, csv_path, png_path, release_name="DP2")
    assert csv_path.exists() and png_path.exists() and png_path.stat().st_size > 0




def test_observing_summary_prefers_ogle_i_for_last_magnitude(tmp_path):
    targets = pd.DataFrame({
        "Target": ["event"], "passes_visibility_filter": [True],
        "max_observable_minutes": [90.0], "mag_now": [17.0],
        "mop_t_0_hjd": [float("nan")], "mop_t_e_days": [float("nan")],
    })
    photometry = tmp_path / "mop"
    photometry.mkdir()
    pd.DataFrame({
        "Timestamp": ["2026-01-03", "2026-01-04", "2026-01-05"],
        "Filter": ["G", "OGLE_I", "r"],
        "Magnitude": [17.4, 16.8, 18.1],
        "Telescope": ["Gaia", "", "Other scope"],
        "Source": ["Gaia", "OGLE", "Other"],
    }).to_csv(photometry / "event.csv", index=False)

    summary = build_observing_selection_summary(
        targets, pd.DataFrame(), photometry, include_reference=False,
    ).iloc[0]

    assert summary["last_mag"] == 16.8
    assert summary["last_mag_filter"] == "I"


def test_observing_summary_abbreviates_long_last_filter_names(tmp_path):
    targets = pd.DataFrame({
        "Target": ["event"], "passes_visibility_filter": [True],
        "max_observable_minutes": [90.0], "mag_now": [17.0],
        "mop_t_0_hjd": [float("nan")], "mop_t_e_days": [float("nan")],
    })
    photometry = tmp_path / "mop"
    photometry.mkdir()
    pd.DataFrame({
        "Timestamp": ["2026-01-05"],
        "Filter": ["Very-long-custom-filter-name"],
        "Magnitude": [17.2],
    }).to_csv(photometry / "event.csv", index=False)

    summary = build_observing_selection_summary(
        targets, pd.DataFrame(), photometry, include_reference=False,
    ).iloc[0]

    assert summary["last_mag_filter"] == "Very-long-c…"

def test_observing_summary_uses_boolean_visibility_flags_after_csv_roundtrip(tmp_path):
    targets = pd.DataFrame({
        "Target": ["selected", "rejected"],
        "passes_visibility_filter": ["True", "False"],
        "max_observable_minutes": [90.0, 120.0],
        "mag_now": [17.1, 17.2],
    })
    summary = build_observing_selection_summary(
        targets, pd.DataFrame(), tmp_path / "missing", include_reference=False,
    )
    assert summary["Target"].tolist() == ["selected"]


def test_observing_summary_marks_rows_without_current_mop_data(tmp_path):
    targets = pd.DataFrame({
        "Target": ["mop-data", "no-mop-data"],
        "passes_visibility_filter": [True, True],
        "max_observable_minutes": [90.0, 90.0],
        "mag_now": [17.1, 0.0],
        "mop_t_e_days": [20.0, float("nan")],
        "mop_t_0_hjd": [2461200.0, float("nan")],
        "mop_u_0": [0.2, float("nan")],
    })
    summary = build_observing_selection_summary(
        targets, pd.DataFrame(), tmp_path / "missing", include_reference=False,
    ).set_index("Target")
    assert summary.loc["mop-data", "has_current_mop_data"]
    assert not summary.loc["no-mop-data", "has_current_mop_data"]
    assert not summary.loc["no-mop-data", "stage_classification_available"]

def test_observing_selection_summary_excludes_hsh_only_targets(tmp_path):
    targets = pd.DataFrame({
        "Target": ["hsh-only"], "passes_visibility_filter": [False],
        "hsh_n_astrometric": [4], "mag_now_x": [18.2],
        "max_observable_minutes": [20], "mop_t_0_hjd": [2460932.5],
        "mop_t_e_days": [20.0], "mop_u_0": [0.1],
    })
    summary = build_observing_selection_summary(
        targets, pd.DataFrame(), tmp_path / "missing", release_visit_exptime_s=30,
    )
    assert summary.empty
