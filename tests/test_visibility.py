import pandas as pd

from visibility_plotter import (
    evaluate_nightly_visibility,
    get_observatory,
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
    assert {"peak_altitude_deg", "observable_night_fraction", "observable_minutes"} <= set(evaluated)
    selected = select_nightly_targets(
        targets, "2026-08-01", minimum_altitude=40,
        minimum_observable_minutes=90, time_step_minutes=30,
    )
    assert selected["Target"].tolist() == ["Bulge"]


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
