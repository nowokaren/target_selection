import pandas as pd

from photometry import prepare_lightcurve_data, select_lightcurve_filters


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
