import pandas as pd

from target_registry import TargetRegistry
from target_selection.config import AnalysisConfig, ProductSettings, SelectionSettings, SourceSpec
from target_selection.sources import AdapterRegistry, default_adapter_registry
from target_selection.workflow import AnalysisWorkflow, _merge_targets


def test_normalized_config_selects_sources_by_name():
    config = AnalysisConfig.from_mapping(
        {
            "run": {"name": "example", "start_date": "2026-08-01"},
            "sources": {
                "target_providers": ["mop"],
                "followup_surveys": [],
                "reference_surveys": ["dp1", "dp2"],
            },
            "target_providers": {"mop": {"adapter": "mop"}},
            "reference_surveys": {
                "dp1": {"adapter": "rubin", "data_release": "DP1"},
                "dp2": {"adapter": "rubin", "data_release": "DP2"},
            },
        }
    )
    assert config.target_providers == ("mop",)
    assert config.name == "example"
    assert config.followup_surveys == ()
    assert config.reference_surveys == ("dp1", "dp2")
    assert config.reference_specs["dp2"].options["data_release"] == "DP2"


def test_config_rejects_undefined_selected_source():
    config = AnalysisConfig(
        start_date="2026-08-01",
        target_providers=("unknown",),
        followup_surveys=(),
    )
    import pytest

    with pytest.raises(ValueError, match="Undefined target provider"):
        config.validate()


def test_default_adapter_registry_is_extensible():
    registry = default_adapter_registry()
    assert "mop" in registry.available("target_provider")
    assert "hsh" in registry.available("followup_survey")
    assert "rubin" in registry.available("reference_survey")

    registry.register("target_provider", "custom", lambda spec: {"name": spec.name})
    adapter = registry.create("target_provider", SourceSpec("external", "custom"))
    assert adapter == {"name": "external"}


def test_workflow_runs_multiple_reference_surveys(tmp_path):
    calls = []

    def runner(start_date, end_date, **kwargs):
        calls.append(kwargs["data_release"].name)
        run = tmp_path / kwargs["data_release"].name
        tables = run / "tables"
        tables.mkdir(parents=True)
        targets = pd.DataFrame(
            {
                "Target": ["OGLE-TEST"],
                "RA_deg": [270.0],
                "Dec_deg": [-30.0],
            }
        )
        return targets, {"run": run, "tables": tables}

    config = AnalysisConfig(
        start_date="2026-08-01",
        end_date="2026-08-02",
        output_dir=str(tmp_path),
        target_providers=("mop",),
        followup_surveys=(),
        reference_surveys=("dp1", "dp2"),
        provider_specs={"mop": SourceSpec("mop", "mop")},
        reference_specs={
            "dp1": SourceSpec("dp1", "rubin", options={"data_release": "DP1"}),
            "dp2": SourceSpec("dp2", "rubin", options={"data_release": "DP2"}),
        },
        products=ProductSettings(
            visibility_plots=False,
            sky_maps=False,
            monitoring_report=False,
        ),
    )
    result = AnalysisWorkflow(
        config,
        clients={"mop": object()},
        runner=runner,
    ).run()
    assert calls == ["DP1", "DP2"]
    assert set(result.runs) == {"dp1", "dp2"}
    assert (tmp_path / "DP1" / "analysis_config.json").exists()
    assert (tmp_path / "DP2" / "tables" / "source_updates.csv").exists()
    assert (tmp_path / "DP1" / "product_index.json").exists()


def test_registry_stores_source_records_photometry_and_runs(tmp_path):
    registry = TargetRegistry(tmp_path / "targets.sqlite")
    targets = pd.DataFrame(
        {
            "Target": ["OGLE-TEST"],
            "RA_deg": [270.0],
            "Dec_deg": [-30.0],
            "mag_now": [17.2],
        }
    )
    assert (
        registry.upsert_source_records(
            targets,
            source_name="mop",
            source_role="target_provider",
        )
        == 1
    )
    photometry = pd.DataFrame(
        {
            "Target": ["OGLE-TEST"],
            "mjd": [61000.0],
            "band": ["I"],
            "magnitude": [17.2],
            "magnitude_error": [0.03],
        }
    )
    assert (
        registry.import_photometry(
            "mop",
            photometry,
            source_role="target_provider",
        )
        == 1
    )
    registry.record_run(
        "test-run",
        {"start_date": "2026-08-01"},
        tmp_path / "run",
        status="complete",
    )
    catalog = registry.source_catalog()
    assert catalog.iloc[0]["source_name"] == "mop"
    assert catalog.iloc[0]["coordinate_source"] == "MOP"
    assert catalog.iloc[0]["coordinate_priority"] == 100
    assert "17.2" in catalog.iloc[0]["record_json"]


def test_workflow_can_run_without_a_reference_survey(tmp_path):
    class StaticProvider:
        def __init__(self, spec):
            self.spec = spec

        def client(self, context):
            return None

        def collect_targets(self, context):
            return pd.DataFrame(
                {
                    "Target": ["OGLE-TEST"],
                    "RA_deg": [270.0],
                    "Dec_deg": [-30.0],
                    "target_source": [self.spec.name],
                }
            )

    adapters = AdapterRegistry()
    adapters.register("target_provider", "static", StaticProvider)
    config = AnalysisConfig(
        start_date="2026-08-01",
        output_dir=str(tmp_path),
        target_providers=("manual",),
        followup_surveys=(),
        reference_surveys=(),
        provider_specs={"manual": SourceSpec("manual", "static")},
        selection=SelectionSettings(
            target_data_scope="all", minimum_altitude_deg=0,
            minimum_observable_minutes=1, time_step_minutes=60,
        ),
        reference_specs={},
        products=ProductSettings(
            visibility_plots=False,
            sky_maps=True,
            monitoring_report=False,
            observing_selection_summary=False,
        ),
    )
    result = AnalysisWorkflow(config, adapters=adapters).run()
    assert set(result.runs) == {"planning_only"}
    assert list(result.targets["Target"]) == ["OGLE-TEST"]
    assert (result.runs["planning_only"].paths["tables"] / "visibility.csv").exists()
    assert (result.runs["planning_only"].paths["sky_plots"] / "sky_by_mag_and_hsh_points.png").exists()
    assert (result.runs["planning_only"].paths["run"] / "product_index.json").exists()



def test_csv_adapters_accept_configured_column_maps(tmp_path):
    from target_selection.sources.adapters import CsvFollowupSurvey, CsvTargetProvider
    from target_selection.sources.base import SourceContext

    target_path = tmp_path / "provider_targets.csv"
    pd.DataFrame({
        "event_id": ["OGLE-TEST"], "longitude": [270.0], "latitude": [-30.0],
    }).to_csv(target_path, index=False)
    target_spec = SourceSpec(
        "provider", "csv", options={
            "path": str(target_path),
            "column_map": {"event_id": "Target", "longitude": "RA_deg", "latitude": "Dec_deg"},
        },
    )
    targets = CsvTargetProvider(target_spec).collect_targets(None)
    assert targets.loc[0, ["Target", "RA_deg", "Dec_deg"]].tolist() == ["OGLE-TEST", 270.0, -30.0]

    inventory = tmp_path / "followup_inventory.csv"
    pd.DataFrame({
        "event": ["OGLE-TEST"], "epoch_mjd": [61000.0],
        "passband": ["I"], "seconds": [300.0],
    }).to_csv(inventory, index=False)
    photometry = tmp_path / "followup_photometry.csv"
    pd.DataFrame({
        "event": ["OGLE-TEST"], "utc_time": ["2026-08-01T02:00:00Z"],
        "passband": ["I"], "instrumental_mag": [17.1], "sigma_mag": [0.04],
    }).to_csv(photometry, index=False)
    survey_spec = SourceSpec(
        "survey", "csv", options={
            "provider_name": "TEST",
            "inventory_path": str(inventory),
            "photometry_path": str(photometry),
            "inventory_column_map": {
                "event": "Target", "epoch_mjd": "mjd", "passband": "band", "seconds": "exptime_s",
            },
            "photometry_column_map": {
                "event": "Target", "utc_time": "Timestamp", "passband": "band",
                "instrumental_mag": "magnitude", "sigma_mag": "magnitude_error",
            },
        },
    )
    config = AnalysisConfig(
        start_date="2026-08-01", target_providers=(), followup_surveys=("survey",),
        reference_surveys=(), followup_specs={"survey": survey_spec}, reference_specs={},
    )
    database = TargetRegistry(tmp_path / "targets.sqlite")
    context = SourceContext(config, database, tmp_path)
    assert CsvFollowupSurvey(survey_spec).import_observations(context) == 2
    assert len(database.observation_epochs(pd.DataFrame({"Target": ["OGLE-TEST"]}), providers=("TEST",))) == 1
    assert len(database.photometry(pd.DataFrame({"Target": ["OGLE-TEST"]}), sources=("TEST",))) == 1

def test_csv_followup_adapter_imports_and_reuses_photometry(tmp_path):
    from target_selection.sources.adapters import CsvFollowupSurvey
    from target_selection.sources.base import SourceContext

    inventory = tmp_path / "js_observations.csv"
    pd.DataFrame(
        {
            "Target": ["OGLE-TEST"],
            "mjd": [61000.0],
            "RA_deg": [270.0],
            "Dec_deg": [-30.0],
            "band": ["I"],
            "exptime_s": [300.0],
        }
    ).to_csv(inventory, index=False)
    photometry = tmp_path / "js_photometry.csv"
    pd.DataFrame(
        {
            "Target": ["OGLE-TEST"],
            "Timestamp": ["2026-08-01T02:00:00Z"],
            "Filter": ["I"],
            "Magnitude": [17.1],
            "Error": [0.04],
        }
    ).to_csv(photometry, index=False)
    spec = SourceSpec(
        "casleo_js",
        "csv",
        options={
            "provider_name": "JS",
            "inventory_path": str(inventory),
            "photometry_path": str(photometry),
        },
    )
    config = AnalysisConfig(
        start_date="2026-08-01",
        target_providers=(),
        followup_surveys=("casleo_js",),
        reference_surveys=(),
        followup_specs={"casleo_js": spec},
        reference_specs={},
    )
    database = TargetRegistry(tmp_path / "targets.sqlite")
    context = SourceContext(config, database, tmp_path)
    adapter = CsvFollowupSurvey(spec)

    assert adapter.import_observations(context) == 2
    assert adapter.import_observations(context) == 0
    assert list(adapter.observed_targets(context)["Target"]) == ["OGLE-TEST"]


def test_merge_targets_prefers_authoritative_mop_page_coordinates():
    pointing = pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0451"],
        "RA_deg": [269.563841528], "Dec_deg": [-19.998130225],
        "coordinate_source": ["HSH"], "coordinate_priority": [10],
        "target_source": ["casleo_hsh"],
    })
    mop = pd.DataFrame({
        "Target": ["OGLE-2025-BLG-0451"],
        "RA_deg": [269.54095833333326], "Dec_deg": [-19.978],
        "mop_ra": ["17:58:09.830"], "mop_dec": ["-19:58:40.80"],
        "coordinate_source": ["MOP target page"],
        "coordinate_priority": [100], "target_source": ["mop"],
    })

    merged = _merge_targets([pointing, mop]).iloc[0]

    assert merged["RA_deg"] == 269.54095833333326
    assert merged["Dec_deg"] == -19.978
    assert merged["coordinate_source"] == "MOP target page"
    assert merged["input_sources"] == "casleo_hsh,mop"


def test_mop_adapter_promotes_event_page_coordinates(tmp_path):
    from target_selection.sources.adapters import MopTargetProvider
    from target_selection.sources.base import SourceContext

    class FakeMop:
        def visible_targets(self, **kwargs):
            return pd.DataFrame({
                "Target": ["OGLE-2025-BLG-0451"],
                "RA_deg": [269.563841528], "Dec_deg": [-19.998130225],
                "observation_date": ["2026-08-01"],
            })

        def visibility_summary(self, *, daily_targets, **kwargs):
            return daily_targets.assign(
                mop_ra="17:58:09.830", mop_dec="-19:58:40.80",
                mop_ra_deg=269.54095833333326, mop_dec_deg=-19.978,
                mop_coordinate_source="target_page",
            )

    config = AnalysisConfig(
        start_date="2026-08-01", output_dir=str(tmp_path),
        target_providers=("mop",), followup_surveys=(), reference_surveys=(),
        provider_specs={"mop": SourceSpec("mop", "mop")}, reference_specs={},
    )
    context = SourceContext(
        config, TargetRegistry(tmp_path / "targets.sqlite"), tmp_path,
        clients={"mop": FakeMop()},
    )

    target = MopTargetProvider(config.provider_specs["mop"]).collect_targets(context).iloc[0]

    assert target["RA_deg"] == 269.54095833333326
    assert target["Dec_deg"] == -19.978
    assert target["coordinate_source"] == "MOP target page"
    assert target["coordinate_priority"] == 100
