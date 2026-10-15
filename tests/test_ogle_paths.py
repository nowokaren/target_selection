import pandas as pd

from target_selection.sources.ogle import OGLEAdapter, _event_paths


def test_ogle_ews_paths_use_event_year_and_field():
    blg = _event_paths("OGLE-2026-BLG-0888")
    dg = _event_paths("OGLE-2024-DG-0016")

    assert blg["photometry_url"] == "https://www.astrouw.edu.pl/ogle/ogle4/ews/2026/blg-0888/phot.dat"
    assert blg["event_url"] == "https://ogle.astrouw.edu.pl/ogle4/ews/2026/blg-0888.html"
    assert dg["photometry_url"] == "https://www.astrouw.edu.pl/ogle/ogle4/ews/2024/dg-0016/phot.dat"
    assert dg["event_url"] == "https://ogle.astrouw.edu.pl/ogle4/ews/2024/dg-0016.html"
def test_ogle_lmc_microlensing_id_uses_lmc_optical_depth_release():
    paths = _event_paths("OGLE-LMC-06")
    assert "/LMC_OPTICAL_DEPTH/phot/OGLE-LMC-06.dat" in paths["photometry_url"]


def test_ews_table_ids_are_canonical_before_season_filter_and_urls():
    raw = pd.DataFrame({
        "Event": ["2026-BLG-0001", "2025-BLG-0002"],
        "RA (J2000)": ["17:00:00", "18:00:00"],
        "Dec (J2000)": ["-29:00:00", "-28:00:00"],
        "Tmax (HJD)": [2461000.0, 2460900.0],
        "tau": [20.0, 30.0],
        "Umin": [0.1, 0.2],
        "Ibl": [17.0, 18.0],
    })

    class Session:
        def get(self, *_args, **_kwargs):
            return self

        def raise_for_status(self):
            pass

        @property
        def content(self):
            return raw.to_html(index=False).encode("latin-1")

    events = OGLEAdapter(session=Session()).load_targets(season=2026)
    assert events["target_id"].tolist() == ["OGLE-2026-BLG-0001"]
    assert events["photometry_url"].iloc[0].endswith("/2026/blg-0001/phot.dat")
