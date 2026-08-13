import pandas as pd

from target_registry import TargetRegistry


def test_registry_imports_hsh_and_returns_microlensing_targets(tmp_path):
    catalogue = pd.DataFrame({
        "filename": ["event_i_wcs.fits", "field_i_wcs.fits"],
        "object": ["OGLE-2024-DG-0016", "M83"],
        "imagetyp": ["object", "object"],
        "astromet": ["yes", "yes"],
        "obj_stat": ["OK", "OK"],
        "clmatch": [True, True],
        "mjd-obs": [60000.0, 60001.0],
        "filter": ["(5) I", "(5) I"],
        "exptime": [300.0, 60.0],
        "airmass": [1.2, 1.3],
        "crval1": [270.0, 200.0],
        "crval2": [-30.0, -20.0],
    })
    source = tmp_path / "hsh.csv"
    catalogue.to_csv(source, index=False)
    registry = TargetRegistry(tmp_path / "targets.sqlite")

    assert registry.import_hsh_catalog(source) == 2
    assert registry.import_hsh_catalog(source) == 0
    targets = registry.observed_targets()
    assert list(targets["Target"]) == ["OGLE-2024-DG-0016"]
    assert pd.isna(targets.iloc[0]["RA_deg"])
    assert pd.isna(targets.iloc[0]["Dec_deg"])
    assert pd.isna(targets.iloc[0]["coordinate_source"])
    epochs = registry.observation_epochs(pd.DataFrame({"Target": ["OGLE-2024-GD-0016"]}))
    assert len(epochs) == 1
    assert epochs.iloc[0]["provider"] == "HSH"


def test_registry_accepts_normalized_js_observations(tmp_path):
    registry = TargetRegistry(tmp_path / "targets.sqlite")
    observations = pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0001"], "mjd": [60500.2],
        "band": ["I"], "exptime_s": [600.0], "usable": [True],
        "RA_deg": [270.0], "Dec_deg": [-30.0],
    })
    assert registry.import_survey_observations("JS", observations, source_id="js-2025") == 1
    targets = registry.observed_targets()
    assert targets.iloc[0]["observatory_providers"] == "JS"


def test_mop_coordinates_cannot_be_overwritten_by_survey_pointings(tmp_path):
    registry = TargetRegistry(tmp_path / "targets.sqlite")
    observations = pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0451"], "mjd": [60500.2],
        "RA_deg": [269.563841528], "Dec_deg": [-19.998130225],
    })
    registry.import_survey_observations("HSH", observations, source_id="hsh")
    registry.register_targets(pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0451"],
        "RA_deg": [269.54095833333326], "Dec_deg": [-19.978],
    }), provider="MOP")
    registry.import_survey_observations(
        "JS", observations.assign(RA_deg=270.0, Dec_deg=-20.0, mjd=60501.0),
        source_id="js",
    )

    target = registry.observed_targets().iloc[0]
    assert target["RA_deg"] == 269.54095833333326
    assert target["Dec_deg"] == -19.978
    assert target["coordinate_source"] == "MOP"
    assert target["coordinate_priority"] == 100
