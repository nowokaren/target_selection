import matplotlib.image as mpimg
from pathlib import Path
import pandas as pd

from visibility_plotter import (
    evaluate_nightly_visibility,
    get_observatory,
    observing_window_mask,
    _visibility_line_style,
    _visibility_source_group,
    _visibility_target_label,
    _split_visibility_targets,
    plot_nightly_visibility,
    plot_visibility_sequence,
    save_nightly_visibility_plots,
    save_selected_visibility_plots,
    select_nightly_targets,
)


def test_el_leoncito_coordinates():
    location, timezone, name = get_observatory("El Leoncito")
    assert name == "El Leoncito"
    assert abs(location.lat.degree + 31.798527) < 1e-5
    assert abs(location.lon.degree + 69.295583) < 1e-5
    assert timezone.key == "America/Argentina/San_Juan"


def test_plot_nightly_visibility(tmp_path):
    targets = pd.DataFrame({
        "Target": ["A", "B"], "RA_deg": [266.4, 270.0], "Dec_deg": [-29.0, -30.0],
    })
    output = tmp_path / "night.png"
    plot_nightly_visibility(targets, "2026-08-01", output, time_step_minutes=30)
    assert output.exists() and output.stat().st_size > 0


def test_nightly_plot_height_scales_with_target_count(tmp_path):
    small = pd.DataFrame({
        "Target": ["small-1", "small-2"],
        "RA_deg": [266.4, 270.0], "Dec_deg": [-29.0, -30.0],
    })
    count = 55
    large = pd.DataFrame({
        "Target": [f"target-{index:02d}" for index in range(count)],
        "RA_deg": [266.4 + 0.01 * index for index in range(count)],
        "Dec_deg": [-29.0] * count,
    })
    small_path = tmp_path / "small.png"
    large_path = tmp_path / "large.png"

    plot_nightly_visibility(small, "2026-08-01", small_path, time_step_minutes=60)
    plot_nightly_visibility(large, "2026-08-01", large_path, time_step_minutes=60)

    assert mpimg.imread(large_path).shape[0] > 2 * mpimg.imread(small_path).shape[0]


def test_save_one_plot_per_date_and_reuse(tmp_path):
    daily = pd.DataFrame({
        "observation_date": ["2026-08-01", "2026-08-02"],
        "Target": ["A", "B"], "RA_deg": [266.4, 270.0], "Dec_deg": [-29.0, -30.0],
    })
    paths = save_nightly_visibility_plots(
        daily, "2026-08-01", "2026-08-02", tmp_path, time_step_minutes=60, verbose=False,
    )
    assert len(paths) == 2 and all(path.exists() for path in paths)
    mtimes = [path.stat().st_mtime_ns for path in paths]
    save_nightly_visibility_plots(
        daily, "2026-08-01", "2026-08-02", tmp_path, time_step_minutes=60, verbose=False,
    )
    assert [path.stat().st_mtime_ns for path in paths] == mtimes


def test_visibility_metrics_and_configurable_selection():
    targets = pd.DataFrame({
        "Target": ["Bulge", "North"],
        "RA_deg": [266.4, 266.4],
        "Dec_deg": [-29.0, 70.0],
    })
    evaluated = evaluate_nightly_visibility(
        targets, "2026-08-01", minimum_altitude=30, time_step_minutes=30,
    )
    assert {"peak_altitude_deg", "observable_night_fraction", "observable_minutes", "observable_start_local", "observable_end_local"} <= set(evaluated)
    selected = select_nightly_targets(
        targets, "2026-08-01", minimum_altitude=40,
        minimum_observable_minutes=90, time_step_minutes=30,
    )
    assert selected["Target"].tolist() == ["Bulge"]



def test_allocated_observing_windows_limit_metrics_and_support_overrides():
    targets = pd.DataFrame({
        "Target": ["Bulge"], "RA_deg": [266.4], "Dec_deg": [-29.0],
    })
    full = evaluate_nightly_visibility(
        targets, "2026-08-01", minimum_altitude=30, time_step_minutes=30,
    )
    unavailable = evaluate_nightly_visibility(
        targets, "2026-08-01", minimum_altitude=30, time_step_minutes=30,
        observing_windows={"default": []},
    )
    assert full.loc[0, "allocated_astronomical_minutes"] > 0
    assert unavailable.loc[0, "allocated_astronomical_minutes"] == 0
    assert unavailable.loc[0, "observable_minutes"] == 0
    assert unavailable.loc[0, "observing_windows"] == "none"

    _, timezone, _ = get_observatory("El Leoncito")
    local_times = pd.date_range("2026-08-01 18:00", periods=25, freq="30min", tz=timezone)
    mask, windows = observing_window_mask(
        local_times, "2026-08-01", timezone,
        {"default": ("20:00", "02:00"), "2026-08-01": [("21:00", "23:00")]},
    )
    assert windows == [("21:00", "23:00")]
    assert mask.sum() == 5


def test_partial_night_plots_are_regenerated_when_schedule_changes(tmp_path):
    daily = pd.DataFrame({
        "observation_date": ["2026-08-01"],
        "Target": ["A"], "RA_deg": [266.4], "Dec_deg": [-29.0],
    })
    paths = save_nightly_visibility_plots(
        daily, "2026-08-01", "2026-08-01", tmp_path,
        time_step_minutes=60, observing_windows=("20:00", "02:00"), verbose=False,
    )
    first_mtime = paths[0].stat().st_mtime_ns
    save_nightly_visibility_plots(
        daily, "2026-08-01", "2026-08-01", tmp_path,
        time_step_minutes=60, observing_windows=("21:00", "02:00"), verbose=False,
    )
    assert paths[0].stat().st_mtime_ns > first_mtime

def test_observable_minutes_validation():
    targets = pd.DataFrame({"Target": ["A"], "RA_deg": [266.4], "Dec_deg": [-29.0]})
    try:
        select_nightly_targets(targets, "2026-08-01", minimum_observable_minutes=0)
    except ValueError as error:
        assert "minimum_observable_minutes" in str(error)
    else:
        raise AssertionError("Zero observable minutes were accepted")


def test_save_final_selected_plots(tmp_path):
    selected = pd.DataFrame({
        "observation_date": ["2026-08-01", "2026-08-02"],
        "Target": ["A", "B"], "RA_deg": [266.4, 270.0], "Dec_deg": [-29.0, -30.0],
    })
    paths = save_selected_visibility_plots(
        selected, tmp_path, time_step_minutes=60, verbose=False,
    )
    assert len(paths) == 2 and all(path.exists() for path in paths)


def test_visibility_split_groups_sources_and_limits_parts_to_twenty():
    mop_count = 25
    hsh_count = 23
    targets = pd.DataFrame({
        "Target": [f"mop-{index}" for index in range(mop_count)]
        + [f"hsh-{index}" for index in range(hsh_count)],
        "RA_deg": [266.4] * (mop_count + hsh_count),
        "Dec_deg": [-29.0] * (mop_count + hsh_count),
        "is_mop_visible_in_run": [True] * mop_count + [False] * hsh_count,
        "is_previously_observed": [False] * mop_count + [True] * hsh_count,
    })

    parts = _split_visibility_targets(targets, max_targets_per_plot=20)

    assert [group for group, _ in parts] == [
        "MOP-only", "MOP-only", "HSH/JS-observed", "HSH/JS-observed",
    ]
    assert [len(part) for _, part in parts] == [20, 5, 20, 3]
    assert _visibility_source_group(parts[0][1].iloc[0]) == "MOP-only"
    assert _visibility_source_group(parts[2][1].iloc[0]) == "HSH/JS-observed"
    assert _visibility_line_style(parts[0][1].iloc[0], show_source_styles=True) == "-"
    assert _visibility_line_style(parts[2][1].iloc[0], show_source_styles=True) == "--"
    assert _visibility_line_style(parts[2][1].iloc[0], show_source_styles=False) == "-"


def test_nightly_visibility_writes_source_aware_parts(tmp_path, monkeypatch):
    import visibility_plotter as module

    count = 24
    daily = pd.DataFrame({
        "observation_date": ["2026-08-01"] * count,
        "Target": [f"target-{index}" for index in range(count)],
        "RA_deg": [266.4] * count, "Dec_deg": [-29.0] * count,
        "is_mop_visible_in_run": [True] * 12 + [False] * 12,
        "is_previously_observed": [False] * 12 + [True] * 12,
    })
    selection = daily.assign(selected_for_visibility=True)
    calls = []

    def fake_plot(targets, night, output_path, **kwargs):
        calls.append((len(targets), kwargs.get("part_label")))
        Path(output_path).touch()

    monkeypatch.setattr(module, "plot_nightly_visibility", fake_plot)
    legacy = tmp_path / "2026-08-01_visibility.png"
    legacy.touch()

    paths = save_nightly_visibility_plots(
        daily, "2026-08-01", "2026-08-01", tmp_path,
        selection=selection, time_step_minutes=60, verbose=False,
    )

    assert len(paths) == 2
    assert all(path.exists() for path in paths)
    assert all("_part_" in path.name for path in paths)
    assert not legacy.exists()
    assert calls == [
        (12, "MOP-only — part 1/2"),
        (12, "HSH/JS-observed — part 2/2"),
    ]


def test_plot_visibility_sequence_pdf_and_format_override(tmp_path):
    selected = pd.DataFrame({
        "observation_date": ["2026-08-01", "2026-08-01", "2026-08-02"],
        "Target": ["A", "B", "A"],
        "RA_deg": [266.4, 270.0, 266.4],
        "Dec_deg": [-29.0, -30.0, -29.0],
    })
    pdf_path = plot_visibility_sequence(
        selected, tmp_path / "sequence", time_step_minutes=60, x_reference_every=1,
    )
    assert pdf_path.suffix == ".pdf"
    assert pdf_path.exists() and pdf_path.stat().st_size > 0

    png_path = plot_visibility_sequence(
        selected, tmp_path / "sequence.pdf", output_format="png",
        time_step_minutes=60, x_reference_every=2,
    )
    assert png_path.suffix == ".png"
    assert png_path.exists() and png_path.stat().st_size > 0


def test_visibility_legend_label_includes_hours_and_current_magnitude():
    row = pd.Series({"Target": "event", "mag_now": 18.37})
    assert _visibility_target_label(row, 125) == "event (2.1 h, mag=18.4)"
