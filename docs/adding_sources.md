# Adding a source

New sources are added through adapters rather than new arguments on the main
function.

## Choose the semantic role

- Use `target_provider` when the source proposes candidate events.
- Use `followup_survey` when the source represents a telescope observing
  program whose history or future plan is being evaluated.
- Use `reference_survey` when the source enriches candidates with external
  coverage, images, catalogues, or photometry.

## Implement the smallest interface

Interfaces live in `target_selection.sources.base`.

```python
class OmpTargetProvider:
    def __init__(self, spec):
        self.spec = spec

    def client(self, context):
        return OmpClient(**self.spec.options)

    def collect_targets(self, context):
        return self.client(context).targets(
            start_date=context.config.start_date,
            end_date=context.config.resolved_end_date,
        )
```

Register it without changing the workflow:

```python
registry = default_adapter_registry()
registry.register("target_provider", "omp", OmpTargetProvider)
result = run_analysis(config, adapters=registry)
```

Then declare and select it in TOML:

```toml
[sources]
target_providers = ["mop", "omp"]

[target_providers.omp]
adapter = "omp"
endpoint = "https://example.invalid/api"
```

Provider-specific settings belong in that source definition. They do not
become global keywords.

## Normalized columns and configurable maps

All adapters translate external column names into a small internal schema.
The rest of the workflow, database, tables, and plots use **only** these
canonical names; a provider's native headers never propagate through the
pipeline as requirements.

| Data type | Required canonical columns | Common optional columns |
|---|---|---|
| Targets | `Target`, `RA_deg`, `Dec_deg` | `mag_now`, source parameters, provenance |
| Observation inventory | `Target`, `mjd` | `band`, `exptime_s`, `usable`, `RA_deg`, `Dec_deg`, `observation_id` |
| Photometry | `Target`, `mjd` | `band`, `magnitude`, `magnitude_error`, `flux`, `flux_error`, `point_id` |

The CSV adapters recognize common aliases automatically: for example,
`target`/`name`/`object` for `Target`, `ra`/`dec` for coordinates, `filter`
for `band`, and `mag` for `magnitude`.

When a source uses different headers, declare an exact input-to-canonical map
inside its source definition. The map is local to that source; no global option
or code change is needed.

```toml
[target_providers.omp_export]
adapter = "csv"
path = "inputs/omp_targets.csv"

[target_providers.omp_export.column_map]
event_identifier = "Target"
ra_icrs_degrees = "RA_deg"
dec_icrs_degrees = "Dec_deg"

[followup_surveys.casleo_js]
adapter = "csv"
provider_name = "JS"
inventory_path = "inputs/js_inventory.csv"
photometry_path = "inputs/js_photometry.csv"

[followup_surveys.casleo_js.inventory_column_map]
event_identifier = "Target"
observation_mjd = "mjd"
filter_name = "band"
exposure_seconds = "exptime_s"

[followup_surveys.casleo_js.photometry_column_map]
event_identifier = "Target"
utc_timestamp = "Timestamp"
filter_name = "band"
calibrated_mag = "magnitude"
calibrated_mag_error = "magnitude_error"
```

The maps have the form `input_column = "canonical_column"`. They are
validated before import: a missing input header, an unsupported canonical name,
or an ambiguous overwrite raises a clear error. `Timestamp` is accepted for
photometry and is converted internally to MJD.

The native HSH adapter is intentionally different: it reads the established
HSH astrometric inventory format directly, including its `objname` convention
and WCS-pointing metadata. Use the generic `csv` adapter for a survey whose
schema should be supplied through `inventory_column_map`.
