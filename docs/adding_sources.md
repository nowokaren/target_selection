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

## Normalized columns

Target frames use `Target`, `RA_deg`, and `Dec_deg`. Observation inventories
use `Target` and `mjd`, with optional `band`, `exptime_s`, `usable`, `RA_deg`,
and `Dec_deg`. Photometry uses `Target`, `mjd`, optional `band`, and either
`magnitude`/`magnitude_error` or `flux`/`flux_error`.

Additional columns are retained as source metadata and provenance.

