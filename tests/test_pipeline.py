import pandas as pd

from data_release_config import get_data_release
from target_selection_pipeline import (
    plot_sky_dual_metric,
    query_release_visit_centers,
    run_target_selection,
    summarize_release_coverage,
)


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


def test_main_api_has_simple_defaults():
    import inspect
    import target_selection_pipeline as module

    signature = inspect.signature(run_target_selection)
    assert list(signature.parameters)[:2] == ["start_date", "end_date"]
    assert signature.parameters["data_release"].default == "DP2"
    assert signature.parameters["observatory"].default == "El Leoncito"
    assert not hasattr(module, "run_pipeline")


class _FakeResult:
    def __init__(self, frame):
        self._frame = frame

    def to_table(self):
        return self

    def to_pandas(self):
        return self._frame


class _FakeJob:
    phase = "COMPLETED"

    def __init__(self, frame):
        self._frame = frame

    def run(self):
        return None

    def wait(self, phases):
        return None

    def fetch_result(self):
        return _FakeResult(self._frame)


class _FakeTapService:
    def __init__(self, frame):
        self.frame = frame
        self.query = None

    def submit_job(self, query):
        self.query = query
        return _FakeJob(self.frame)


def test_visit_center_query_aggregates_server_side():
    service = _FakeTapService(pd.DataFrame({
        "visitId": [1, 2], "ra": [10.0, 20.0], "dec": [-5.0, -6.0],
    }))
    result = query_release_visit_centers(service, "DP2")
    assert list(result.columns) == ["visitId", "ra", "dec"]
    assert "GROUP BY" in service.query
    assert "AVG(vd.ra)" in service.query


def test_sky_map_supports_both_marker_encodings_and_background(tmp_path):
    targets = pd.DataFrame({
        "Target": ["A", "B", "C"],
        "RA_deg": [266.4, 270.0, 280.0],
        "Dec_deg": [-29.0, -30.0, -25.0],
        "mag_now": [18.0, 0.0, 19.5],
        "coverage_n_visits": [12, 0, 48],
    })
    background = pd.DataFrame({
        "ra": [260.0, 262.0, 266.0, 268.0, 270.0, 272.0],
        "dec": [-32.0, -30.0, -29.0, -28.0, -27.0, -25.0],
    })
    for encoding in ("split_color", "color_size"):
        output = tmp_path / f"{encoding}.png"
        plot_sky_dual_metric(
            targets, "mag_now", "coverage_n_visits", output,
            marker_encoding=encoding, coverage_background=background,
            coverage_resolution=8,
        )
        assert output.exists() and output.stat().st_size > 0


def test_sky_map_rejects_unknown_encoding(tmp_path):
    targets = pd.DataFrame({
        "Target": ["A"], "RA_deg": [1.0], "Dec_deg": [1.0],
        "mag_now": [18.0], "coverage_n_visits": [1],
    })
    import pytest
    with pytest.raises(ValueError, match="marker_encoding"):
        plot_sky_dual_metric(
            targets, "mag_now", "coverage_n_visits", tmp_path / "bad.png",
            marker_encoding="unknown",
        )
