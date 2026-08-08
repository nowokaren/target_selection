import pandas as pd

from monitoring_report import create_monitoring_report, plot_monitoring_lightcurve


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
