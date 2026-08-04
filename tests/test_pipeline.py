import pandas as pd

from data_release_config import get_data_release
from target_selection_pipeline import summarize_release_coverage


def test_dp2_profile_is_available():
    release = get_data_release("DP2")
    assert release.name == "DP2"
    assert release.tap_visit_table


def test_coverage_summary_counts_unique_visits_per_band():
    rows = pd.DataFrame(
        {
            "Target": ["event", "event", "event"],
            "visitId": [10, 10, 11],
            "detector": [1, 2, 1],
            "expMidptMJD": [60000.0, 60000.0, 60001.0],
            "band": ["g", "g", "r"],
        }
    )
    summary = summarize_release_coverage(rows).iloc[0]
    assert summary["coverage_n_visits"] == 2
    assert summary["coverage_n_calexps"] == 3
    assert summary["coverage_n_visits_g"] == 1
    assert summary["coverage_n_visits_r"] == 1
