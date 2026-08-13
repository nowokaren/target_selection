import pandas as pd

from data_release_config import get_data_release
from target_selection_pipeline import (
    create_run_structure,
    plot_sky_dual_metric,
    query_release_visit_centers,
    run_target_selection,
    save_target_reports,
    summarize_release_coverage,
    _apply_authoritative_mop_coordinates,
    _filter_invalid_mop_magnitudes,
    _visibility_daily_targets,
)


def test_run_directory_includes_observing_schedule(tmp_path):
    simple = create_run_structure(
        tmp_path, "2026-08-09", "2026-08-12", "DP2",
        observing_windows=("02:00", "07:00"),
    )
    assert simple["run"].name == "2026-08-09_to_2026-08-12__obs_02-00_to_07-00"

    varied = create_run_structure(
        tmp_path, "2026-08-09", "2026-08-12", "DP2",
        observing_windows={"default": ("18:00", "06:00"), "2026-08-10": ("21:00", "03:00")},
    )
    assert varied["run"].name.startswith("2026-08-09_to_2026-08-12__obs_18-00_to_06-00_varied-")


def test_dp2_profile_is_available():
    release = get_data_release("DP2")
    assert release.name == "DP2"
    assert release.tap_visit_table
    assert release.coadd_dataset_types == ("deep_coadd",)
    assert release.photometry_method == "coadd_forced"


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
    assert signature.parameters["generate_release_photometry"].default is False
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
        self.queries = []

    def submit_job(self, query):
        self.query = query
        self.queries.append(query)
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


def test_target_report_cache_can_track_forced_photometry_mode(tmp_path):
    import matplotlib.pyplot as plt

    targets = pd.DataFrame({"Target": ["event"], "forced": [False]})
    coverage = pd.DataFrame({
        "Target": ["event"], "visitId": [1], "detector": [2],
        "expMidptMJD": [60000.0], "band": ["i"],
    })
    calls = []

    def plotter(row, rows):
        calls.append((row["Target"], len(rows)))
        return plt.figure()

    def version(row):
        return "test:forced" if row["forced"] else "test:coverage_only"

    save_target_reports(
        targets, plotter, tmp_path, coverage, verbose=False, version_for_target=version,
    )
    save_target_reports(
        targets, plotter, tmp_path, coverage, verbose=False, version_for_target=version,
    )
    assert calls == [("event", 1)]

    forced_targets = targets.assign(forced=True)
    save_target_reports(
        forced_targets, plotter, tmp_path, coverage, verbose=False, version_for_target=version,
    )
    assert calls == [("event", 1), ("event", 1)]


def test_target_report_cache_migrates_matching_reports(tmp_path):
    import matplotlib.pyplot as plt

    targets = pd.DataFrame({"Target": ["event"]})
    coverage = pd.DataFrame({
        "Target": ["event"], "visitId": [1], "detector": [2],
        "expMidptMJD": [60000.0], "band": ["i"],
    })
    old_reports = tmp_path / "old"
    new_reports = tmp_path / "new"
    calls = []

    def plotter(row, rows):
        calls.append(row["Target"])
        return plt.figure()

    save_target_reports(targets, plotter, old_reports, coverage, verbose=False)
    save_target_reports(
        targets, plotter, new_reports, coverage, verbose=False,
        reuse_reports_from=old_reports,
    )
    assert calls == ["event"]
    assert (new_reports / "event_target_report.png").exists()


def test_target_report_cache_remembers_no_coadd_results(tmp_path):
    targets = pd.DataFrame({"Target": ["no-coadd"]})
    coverage = pd.DataFrame({
        "Target": ["no-coadd"], "visitId": [1], "detector": [2],
        "expMidptMJD": [60000.0], "band": ["i"],
    })
    calls = []

    def plotter(row, rows):
        calls.append((row["Target"], len(rows)))
        return None

    save_target_reports(targets, plotter, tmp_path, coverage, verbose=False)
    save_target_reports(targets, plotter, tmp_path, coverage, verbose=False)
    assert calls == [("no-coadd", 1)]
    assert (tmp_path / "report_status.json").exists()

    save_target_reports(targets, plotter, tmp_path, coverage, verbose=False, overwrite=True)
    assert calls == [("no-coadd", 1), ("no-coadd", 1)]


class _FakeMop:
    def visible_targets(self, **kwargs):
        return pd.DataFrame({
            "Target": ["visible-event", "invalid-zero-magnitude"],
            "RA_deg": [10.0, 11.0], "Dec_deg": [-20.0, -21.0],
            "mag_now": [18.0, 0.0],
            "observation_date": ["2026-08-01", "2026-08-01"],
        })

    def visibility_summary(self, *, daily_targets, **kwargs):
        return daily_targets.assign(
            mop_t_0_hjd="2460000.0", mop_t_e_days="20.0", mop_u_0="0.1",
            mop_parameters_status="available", n_visible_nights=1,
            mop_ra="00:40:29.630", mop_dec="-20:07:24.44",
            mop_ra_deg=10.1234567890123, mop_dec_deg=-20.1234567890123,
            mop_coordinate_source="target_page",
        )

    def enrich_microlensing_parameters(self, targets, **kwargs):
        return targets.assign(
            mop_t_0_hjd="2460000.0", mop_t_e_days="20.0", mop_u_0="0.1",
            mop_parameters_status="available",
            mop_ra="17:58:09.830", mop_dec="-19:58:40.80",
            mop_ra_deg=269.54095833333326, mop_dec_deg=-19.978,
            mop_coordinate_source="target_page",
        )


def test_pipeline_queries_visible_and_previously_observed_targets(tmp_path):
    hsh = pd.DataFrame({
        "filename": ["old_i_wcs.fits"], "object": ["OGLE-2025-BLG-0001"],
        "imagetyp": ["object"], "astromet": ["yes"], "obj_stat": ["OK"],
        "clmatch": [True], "mjd-obs": [60000.0], "filter": ["(5) I"],
        "exptime": [300.0], "airmass": [1.2],
        "crval1": [269.563841528], "crval2": [-19.998130225],
    })
    hsh_path = tmp_path / "hsh.csv"
    hsh.to_csv(hsh_path, index=False)
    tap = _FakeTapService(pd.DataFrame({
        "visitId": [1], "expMidptMJD": [60000.0], "band": ["i"],
        "detector": [2], "seeing": [0.8], "magLim": [24.0],
    }))

    additional = pd.DataFrame({
        "Target": ["manual-event"], "RA_deg": [266.4], "Dec_deg": [-29.0],
    })
    combined, paths = run_target_selection(
        "2026-08-01", data_release="DP2", root_dir=tmp_path / "outputs",
        mop=_FakeMop(), tap_service=tap, target_plotter=False,
        generate_visibility_plots=False, hsh_image_catalog=hsh_path,
        additional_targets=additional, visibility_time_step_minutes=60,
        max_workers=1, verbose=False,
    )

    assert set(combined["Target"]) == {"visible-event", "OGLE-2025-BLG-0001", "manual-event"}
    assert "invalid-zero-magnitude" not in set(combined["Target"])
    assert "invalid-zero-magnitude" not in set(pd.read_csv(paths["tables"] / "visible_targets_daily.csv")["Target"])
    indexed = combined.set_index("Target")
    assert indexed.loc["OGLE-2025-BLG-0001", "is_previously_observed"]
    assert indexed.loc["visible-event", "RA_deg"] == 10.1234567890123
    assert indexed.loc["visible-event", "Dec_deg"] == -20.1234567890123
    assert indexed.loc["OGLE-2025-BLG-0001", "RA_deg"] == 269.54095833333326
    assert indexed.loc["OGLE-2025-BLG-0001", "Dec_deg"] == -19.978
    assert indexed.loc["OGLE-2025-BLG-0001", "coordinate_source"] == "MOP target page"
    assert any("269.54095833333326" in query and "-19.978" in query for query in tap.queries)
    assert not any("269.563841528" in query for query in tap.queries)
    assert (paths["tables"] / "analysis_targets.csv").exists()
    visibility_summary = pd.read_csv(paths["tables"] / "visibility_target_summary.csv")
    assert {"Target", "mag_now", "passes_visibility_filter", "max_observable_minutes"} <= set(visibility_summary)
    assert set(visibility_summary["Target"]) == {"visible-event", "OGLE-2025-BLG-0001", "manual-event"}
    assert visibility_summary.set_index("Target").loc["visible-event", "mag_now"] == 18.0
    queried = pd.read_csv(paths["tables"] / "queried_targets.csv")
    assert set(queried["Target"]) == {"visible-event", "OGLE-2025-BLG-0001", "manual-event"}
    assert set(queried["query_source"]) == {"MOP visible", "Previously observed", "User supplied"}
    assert queried.set_index("Target").loc["manual-event", "is_user_supplied"]
    assert (paths["tables"] / "coverage_targets.csv").exists()
    observing_summary = pd.read_csv(paths["tables"] / "observing_selection_summary.csv")
    assert {"Target", "mag_now", "visible_hours", "visible_from", "visible_to"} <= set(observing_summary)
    assert (paths["tables"] / "observing_selection_summary.png").exists()


def test_magnitude_cut_discards_faint_targets_but_keeps_missing_values():
    targets = pd.DataFrame({"Target": ["bright", "faint", "unknown"], "mag_now": [16.0, 19.0, None]})
    filtered, excluded = _filter_invalid_mop_magnitudes(targets, max_current_magnitude=18.0)
    assert filtered["Target"].tolist() == ["bright", "unknown"]
    assert excluded == 1


def test_mop_page_coordinates_override_pointing_without_rounding():
    targets = pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0451"],
        "RA_deg": [269.563841528], "Dec_deg": [-19.998130225],
        "mop_ra": ["17:58:09.830"], "mop_dec": ["-19:58:40.80"],
        "mop_ra_deg": [269.54095833333326], "mop_dec_deg": [-19.978],
    })

    corrected = _apply_authoritative_mop_coordinates(targets).iloc[0]

    assert corrected["RA_deg"] == 269.54095833333326
    assert corrected["Dec_deg"] == -19.978
    assert corrected["coordinate_source"] == "MOP target page"
    assert corrected["coordinate_was_overridden"]
    assert abs(corrected["coordinate_offset_arcsec"] - 106.0432) < 0.001


def test_mop_daily_visibility_uses_enriched_page_coordinates():
    daily = pd.DataFrame({
        "Target": ["OGLE-TEST"], "RA_deg": [10.0], "Dec_deg": [-20.0],
        "observation_date": ["2026-08-01"],
    })
    enriched = pd.DataFrame({
        "Target": ["OGLE-TEST"],
        "RA_deg": [10.1234567890123], "Dec_deg": [-20.1234567890123],
        "mag_now": [17.2],
    })

    result = _visibility_daily_targets(
        enriched, "2026-08-01", "2026-08-01", scope="mop_daily", mop_daily=daily,
    ).iloc[0]

    assert result["RA_deg"] == 10.1234567890123
    assert result["Dec_deg"] == -20.1234567890123
    assert result["mag_now"] == 17.2
