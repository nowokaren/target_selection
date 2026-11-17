import pandas as pd

from target_selection.config import load_config
from target_selection.planning_cli import _load_local_surveys, _plot_layers


def test_configured_hsh_inventory_and_neighbor_photometry_make_lightcurve(tmp_path):
    survey_dir = tmp_path / "data" / "HSH"
    survey_dir.mkdir(parents=True)
    pd.DataFrame([{
        "object": "OGLE-2026-BLG-0001", "objname": "OGLE-2026-BLG-0001",
        "imagetyp": "object", "astromet": "yes", "obj_stat": "OK",
        "clmatch": True, "mjd-obs": 61000.0, "filter": "(5) I",
        "exptime": 300, "airmass": 1.2, "filename": "hsh_001.fits",
    }]).to_csv(survey_dir / "image_inventory.csv", index=False)
    pd.DataFrame([{
        "object": "OGLE-2026-BLG-0001", "MJD-OBS": 61000.1, "filter": "I",
        "MAG": 17.2, "MAG_ERR": 0.04,
    }]).to_csv(survey_dir / "photometry.csv", index=False)

    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = config_dir / "run.toml"
    config_path.write_text(f'''[run]
start_date = "2026-10-16"
output_dir = "{tmp_path / "outputs"}"

[sources]
target_providers = []
followup_surveys = ["hsh"]
reference_surveys = []

[followup_surveys.hsh]
adapter = "hsh"
provider_name = "HSH"
inventory_path = "../data/HSH/image_inventory.csv"
photometry_path = "photometry.csv"
''', encoding="utf-8")

    config = load_config(config_path)
    targets = pd.DataFrame({"target_id": ["OGLE-2026-BLG-0001"]})
    epochs, photometry = _load_local_surveys(config, targets, verbose=False)

    assert epochs["provider"].tolist() == ["HSH"]
    assert photometry["magnitude"].tolist() == [17.2]
    assert photometry["provider"].tolist() == ["hsh"]

    from target_selection.report import create_lightcurve_plots
    paths = create_lightcurve_plots(
        targets,
        output_dir=tmp_path / "plots",
        observatory_epochs=epochs,
        observatory_photometry=photometry,
        layers=("hsh",),
        overwrite=True,
        verbose=False,
    )
    assert len(paths) == 1 and paths[0].exists()


def test_generic_csv_survey_column_maps_reach_lightcurve_plot(tmp_path):
    survey_dir = tmp_path / "data" / "SurveyX"
    survey_dir.mkdir(parents=True)
    pd.DataFrame([{"event": "OGLE-2026-BLG-0002", "when": 61001.0,
                   "exposure": 300}]).to_csv(survey_dir / "epochs.csv", index=False)
    pd.DataFrame([{"event": "OGLE-2026-BLG-0002", "when": 61001.1,
                   "mag": 17.4, "err": 0.05, "filter": "I"}]).to_csv(
                       survey_dir / "photometry.csv", index=False)
    config_path = tmp_path / "run.toml"
    config_path.write_text(f'''[run]
start_date = "2026-10-16"
output_dir = "{tmp_path / "outputs"}"

[sources]
target_providers = []
followup_surveys = ["survey_x"]
reference_surveys = []

[followup_surveys.survey_x]
adapter = "csv"
provider_name = "SurveyX"
inventory_path = "{survey_dir / "epochs.csv"}"
photometry_path = "{survey_dir / "photometry.csv"}"

[followup_surveys.survey_x.inventory_column_map]
event = "Target"
when = "mjd"
exposure = "exptime_s"

[followup_surveys.survey_x.photometry_column_map]
event = "Target"
when = "mjd"
mag = "magnitude"
err = "magnitude_error"
filter = "band"
''', encoding="utf-8")

    config = load_config(config_path)
    targets = pd.DataFrame({"target_id": ["OGLE-2026-BLG-0002"]})
    epochs, photometry = _load_local_surveys(config, targets, verbose=False)
    assert epochs["provider"].tolist() == ["SURVEYX"]
    assert photometry["magnitude"].tolist() == [17.4]

    from target_selection.report import create_lightcurve_plots
    layers = _plot_layers([], config)
    assert "surveyx" in layers
    paths = create_lightcurve_plots(
        targets, output_dir=tmp_path / "plots", observatory_epochs=epochs,
        observatory_photometry=photometry, layers=layers,
        overwrite=True, verbose=False,
    )
    assert len(paths) == 1 and paths[0].exists()
