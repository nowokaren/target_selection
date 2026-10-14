from target_selection.sources.ogle import _event_paths


def test_ogle_ews_paths_use_event_year_and_field():
    blg = _event_paths("OGLE-2026-BLG-0888")
    dg = _event_paths("OGLE-2024-DG-0016")

    assert blg["photometry_url"] == "https://www.astrouw.edu.pl/ogle/ogle4/ews/2026/blg-0888/phot.dat"
    assert blg["event_url"] == "https://ogle.astrouw.edu.pl/ogle4/ews/2026/blg-0888.html"
    assert dg["photometry_url"] == "https://www.astrouw.edu.pl/ogle/ogle4/ews/2024/dg-0016/phot.dat"
    assert dg["event_url"] == "https://ogle.astrouw.edu.pl/ogle4/ews/2024/dg-0016.html"
from target_selection.sources.ogle import _event_paths


def test_ogle_lmc_microlensing_id_uses_lmc_optical_depth_release():
    paths = _event_paths("OGLE-LMC-06")
    assert "/LMC_OPTICAL_DEPTH/phot/OGLE-LMC-06.dat" in paths["photometry_url"]
