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
