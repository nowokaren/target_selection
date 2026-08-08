import pandas as pd

from observatory_observations import (
    canonical_target_name,
    summarize_hsh_observations,
)


def test_canonical_name_accepts_dg_gd_variant():
    assert canonical_target_name("OGLE-2024-DG-0016") == canonical_target_name("ogle_2024_gd_0016")


def test_hsh_summary_counts_usable_images_by_stage_and_filter(tmp_path):
    catalogue = pd.DataFrame({
        "object": ["OGLE-2024-DG-0016"] * 4,
        "imagetyp": ["object"] * 4,
        "astromet": ["yes", "yes", "yes", "no"],
        "obj_stat": ["OK", "OK", "OUTSIDE", "OK"],
        "clmatch": [True, True, False, True],
        "mjd-obs": [60000.0, 60010.0, 60020.0, 60030.0],
        "filter": ["(5) I", "(3) V", "(5) I", "(5) I"],
        "exptime": [300.0, 200.0, 300.0, 300.0],
        "airmass": [1.2, 1.4, 2.0, 1.3],
    })
    path = tmp_path / "hsh.csv"
    catalogue.to_csv(path, index=False)
    targets = pd.DataFrame({
        "Target": ["OGLE-2024-GD-0016", "unobserved"],
        "mop_t_0_hjd": [2460005.5, 2460005.5],
        "mop_t_e_days": [10.0, 10.0],
    })

    summary = summarize_hsh_observations(targets, path).set_index("Target")

    row = summary.loc["OGLE-2024-GD-0016"]
    assert row["hsh_n_astrometric"] == 3
    assert row["hsh_n_usable"] == 2
    assert row["hsh_exptime_s"] == 500.0
    assert row["hsh_pre_baseline_n"] == 0
    assert row["hsh_rise_n"] == 1
    assert row["hsh_fall_n"] == 1
    assert row["hsh_n_I"] == 1
    assert row["hsh_n_V"] == 1
    assert summary.loc["unobserved", "hsh_quality_status"] == "not_observed"
