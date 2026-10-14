"""Verified TAP visit-column profiles for individual-image releases."""

from target_selection.data_release_config import get_data_release


def test_dp0_2_visit_query_uses_available_columns():
    release = get_data_release("DP0.2")
    assert release.tap_ra == "ra"
    assert release.tap_dec == "decl"
    assert "vd.seeing * NULL AS magLim" in release.visit_select()


def test_dp1_visit_query_uses_available_columns():
    release = get_data_release("DP1")
    assert release.tap_ra == "ra"
    assert release.tap_dec == "dec"
    assert "vd.magLim AS magLim" in release.visit_select()


def test_dp0_1_profile_did_not_change():
    release = get_data_release("DP0.1")
    assert release.tap_ra == "s_ra"
    assert release.tap_dec == "s_dec"
