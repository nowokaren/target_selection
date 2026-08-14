import pandas as pd
import monitoring_report

from monitoring_report import (
    _microlensing_zoom_limits, _monitoring_future_xmax, create_monitoring_report, plot_monitoring_lightcurve,
)


def test_monitoring_report_writes_pdf(tmp_path):
    targets = pd.DataFrame({
        "Target": ["event"], "is_mop_visible_in_run": [True],
        "is_previously_observed": [True], "mop_t_e_days": [20.0],
        "mop_t_0_hjd": [2460000.0],
    })
    photometry = pd.DataFrame({
        "Timestamp": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "Filter": ["OGLE_I", "OGLE_I"], "Magnitude": [18.0, 17.9],
        "Error": [0.02, 0.02],
    })
    coverage = pd.DataFrame({"Target": ["event"], "expMidptMJD": [60310.0]})
    epochs = pd.DataFrame({
        "Target": ["event", "event"], "provider": ["HSH", "JS"],
        "mjd": [60311.0, 60312.0], "usable": [True, True],
    })
    output = tmp_path / "report.pdf"
    result = create_monitoring_report(
        targets, output, mop=None, mop_photometry_dir=tmp_path,
        lsst_coverage=coverage, observatory_epochs=epochs,
        photometry_loader=lambda _: photometry,
    )
    assert output.exists() and output.stat().st_size > 0
    assert result["n_targets"] == 1



def test_monitoring_report_sorts_targets_by_current_magnitude(tmp_path, monkeypatch):
    targets = pd.DataFrame({
        "Target": ["faint", "unknown", "bright", "zero"],
        "mag_now": [18.4, float("nan"), 14.2, 0.0],
    })
    plotted: list[str] = []

    def capture_target(target, *_args, **kwargs):
        plotted.append(str(target["Target"]))
        return kwargs["ax"]

    monkeypatch.setattr(monitoring_report, "plot_monitoring_lightcurve", capture_target)
    create_monitoring_report(
        targets, tmp_path / "ordered.pdf", mop=None, mop_photometry_dir=tmp_path,
        layers=(), plots_per_page=4,
    )
    assert plotted == ["bright", "faint", "unknown", "zero"]

def test_monitoring_layers_can_use_local_photometry_or_epoch_markers():
    import matplotlib.pyplot as plt

    target = pd.Series({"Target": "event"})
    local_photometry = pd.DataFrame({
        "provider": ["HSH"], "mjd": [60311.0], "Magnitude": [17.8],
        "Error": [0.03], "band": ["I"],
    })
    figure, axis = plt.subplots()
    plot_monitoring_lightcurve(
        target, pd.DataFrame(), ax=axis, layers=("hsh",),
        observatory_photometry=local_photometry,
    )
    _, labels = axis.get_legend_handles_labels()
    assert any("HSH photometry" in label for label in labels)
    plt.close(figure)

    figure, axis = plt.subplots()
    epochs = pd.DataFrame({"provider": ["JS", "JS"], "mjd": [60312.0, 60312.1],
                           "exptime_s": [300.0, 300.0], "usable": [True, False]})
    plot_monitoring_lightcurve(target, pd.DataFrame(), ax=axis, layers=("js",), observatory_epochs=epochs)
    labels = [item.get_text() for item in axis.get_legend().get_texts()]
    assert any("JS images (N=2, 0.2 h)" in label for label in labels)
    plt.close(figure)



def test_temporal_coverage_lanes_are_separated_and_include_nonusable_images():
    import matplotlib.pyplot as plt

    target = pd.Series({"Target": "event"})
    coverage = pd.DataFrame({"expMidptMJD": [60310.0]})
    epochs = pd.DataFrame({
        "provider": ["HSH", "HSH"], "mjd": [60311.0, 60311.1],
        "exptime_s": [300.0, 300.0], "usable": [True, False],
    })
    figure, axis = plt.subplots()
    plot_monitoring_lightcurve(
        target, pd.DataFrame(), ax=axis, lsst_coverage=coverage,
        observatory_epochs=epochs, layers=("release_epochs", "hsh"),
    )
    assert [(patch.get_y(), patch.get_height()) for patch in axis.patches] == [
        (0.5, 0.5), (0.0, 0.5),
    ]
    labels = [item.get_text() for item in axis.get_legend().get_texts()]
    assert any("HSH images (N=2, 0.2 h)" in label for label in labels)
    plt.close(figure)



def test_microlensing_zoom_uses_hjd_and_nominal_mop_values():
    target = pd.Series({
        "mop_t_0_hjd": "2460000.0±0.2", "mop_t_e_days": "20.0±1.0",
    })
    limits = _microlensing_zoom_limits(target)
    assert limits is not None
    assert limits[1] - limits[0] == 80.0
    assert _microlensing_zoom_limits(pd.Series({"mop_t_0_hjd": "unknown"})) is None


def test_monitoring_plot_extends_to_two_calendar_months_after_creation():
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    future_xmax = _monitoring_future_xmax("2026-08-13T12:00:00Z")
    expected = mdates.date2num(pd.Timestamp("2026-10-13T12:00:00"))
    assert future_xmax == expected

    figure, axis = plt.subplots()
    plot_monitoring_lightcurve(
        pd.Series({"Target": "event"}), pd.DataFrame(), ax=axis,
        layers=(), future_xmax=future_xmax,
    )
    assert axis.get_xlim()[1] == expected
    plt.close(figure)
