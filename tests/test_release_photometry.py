import pandas as pd

from release_photometry import (
    _nanojansky_to_magnitude,
    query_dia_forced_photometry,
    load_target_release_photometry,
    prepare_forced_photometry_requests,
    save_target_release_photometry,
    prepare_release_lightcurve_data,
    summarize_release_forced_photometry,
)


def _targets():
    return pd.DataFrame({
        "Target": ["event-a", "event-b"],
        "RA_deg": [10.0, 20.0],
        "Dec_deg": [-20.0, -30.0],
    })


def _coverage():
    return pd.DataFrame({
        "Target": ["event-a", "event-a", "event-a", "event-b"],
        "visitId": [100, 100, 101, 100],
        "detector": [4, 4, 5, 4],
        "expMidptMJD": [60000.0, 60000.0, 60001.0, 60000.0],
        "band": ["i", "i", "r", "i"],
    })


def test_forced_photometry_requests_keep_unique_target_calexps():
    requests = prepare_forced_photometry_requests(_targets(), _coverage())
    assert len(requests) == 3
    assert set(requests["Target"]) == {"event-a", "event-b"}
    assert requests["visitId"].dtype.kind in "iu"
    assert requests["detector"].dtype.kind in "iu"


def test_forced_photometry_requests_can_select_targets():
    requests = prepare_forced_photometry_requests(
        _targets(), _coverage(), target_names=["event-a"],
    )
    assert len(requests) == 2
    assert set(requests["Target"]) == {"event-a"}


def test_forced_photometry_summary_and_plot_data_keep_valid_magnitudes():
    frame = pd.DataFrame({
        "Target": ["event-a", "event-a", "event-b"],
        "measurement_status": ["measured", "outside_calexp", "measured"],
        "magnitude": [19.2, float("nan"), 20.1],
        "magnitude_err": [0.03, float("nan"), 0.05],
        "expMidptMJD": [60000.0, 60001.0, 60002.0],
        "band": ["i", "i", "r"],
    })
    summary = summarize_release_forced_photometry(frame).set_index("Target")
    assert summary.loc["event-a", "release_forced_n_requested"] == 2
    assert summary.loc["event-a", "release_forced_n_measured"] == 1
    assert summary.loc["event-a", "release_forced_n_magnitudes"] == 1
    lightcurve = prepare_release_lightcurve_data(frame, "i")
    assert len(lightcurve) == 1
    assert lightcurve.iloc[0]["Magnitude"] == 19.2


def test_dia_flux_conversion_uses_ab_nanojansky_zero_point():
    magnitude, uncertainty = _nanojansky_to_magnitude([1.0, 10.0, -1.0], [0.1, 1.0, 0.1])
    assert magnitude[0] == 31.4
    assert round(magnitude[1], 4) == 28.9
    assert pd.isna(magnitude[2])
    assert round(uncertainty[0], 4) == 0.1086


def test_target_release_cache_upserts_without_losing_other_collections(tmp_path):
    first = pd.DataFrame({
        "Target": ["event-a"], "data_release": ["DP2"],
        "butler_collection": ["dp2"], "measurement_method": ["dia_forced_catalog"],
        "diaObjectId": [42], "visitId": [100], "detector": [5],
        "expMidptMJD": [60000.0], "band": ["i"], "magnitude": [19.7],
    })
    update_and_other_collection = pd.DataFrame({
        "Target": ["event-a", "event-a"], "data_release": ["DP2", "DP3"],
        "butler_collection": ["dp2", "dp3"],
        "measurement_method": ["dia_forced_catalog", "dia_forced_catalog"],
        "diaObjectId": [42, 42], "visitId": [100, 100], "detector": [5, 5],
        "expMidptMJD": [60000.0, 60000.0], "band": ["i", "i"],
        "magnitude": [19.5, 19.4],
    })
    save_target_release_photometry(first, tmp_path)
    save_target_release_photometry(update_and_other_collection, tmp_path)
    cached = load_target_release_photometry(tmp_path, "event-a")
    assert len(cached) == 2
    assert set(cached["butler_collection"]) == {"dp2", "dp3"}
    assert cached.loc[cached["butler_collection"] == "dp2", "magnitude"].item() == 19.5


class _FakeTapJob:
    def __init__(self, frame):
        self._frame = frame
        self.phase = "PENDING"

    def run(self):
        self.phase = "COMPLETED"

    def wait(self, phases):
        return None

    def raise_if_error(self):
        raise RuntimeError("Unexpected fake TAP error")

    def fetch_result(self):
        from astropy.table import Table

        class Result:
            def __init__(self, frame):
                self.frame = frame

            def to_table(self):
                return Table.from_pandas(self.frame)

        return Result(self._frame)


class _FakeTapService:
    def submit_job(self, query):
        if "FROM dp2.DiaObject" in query:
            return _FakeTapJob(pd.DataFrame({"diaObjectId": [42], "ra": [10.0], "dec": [-20.0]}))
        return _FakeTapJob(pd.DataFrame({
            "diaObjectId": [42], "visitId": [100], "detector": [5], "band": ["i"],
            "direct_flux_njy": [100.0], "direct_flux_err_njy": [10.0], "direct_flux_flag": [False],
            "difference_flux_njy": [3.0], "difference_flux_err_njy": [2.0], "difference_flux_flag": [False],
            "expMidptMJD": [60000.0],
        }))


def test_dp2_dia_catalog_rows_are_converted_to_plot_magnitudes():
    from data_release_config import get_data_release

    result = query_dia_forced_photometry(
        _targets().iloc[:1], tap_service=_FakeTapService(),
        data_release=get_data_release("DP2"), max_workers=1, verbose=False,
    )
    assert result.loc[0, "measurement_status"] == "measured"
    assert result.loc[0, "measurement_method"] == "dia_forced_catalog"
    assert result.loc[0, "band"] == "i"
    assert round(result.loc[0, "magnitude"], 1) == 26.4
