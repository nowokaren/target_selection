# Usage modes

The same `run_analysis` call supports different questions. Change the source
lists and product switches; do not change the workflow code.

## At a glance

| Question | Target providers | Follow-up surveys | Reference surveys | Main products |
|---|---|---|---|---|
| Plan HSH observations using MOP and DP2 context | MOP | HSH | DP2 | Visibility, planning table, monitoring PDF, sky maps; optional target reports |
| Plan tonight without Rubin | MOP | HSH or JS | None | Visibility decisions and nightly plots |
| Revisit only previously observed events | None | HSH or JS | Optional | Local visibility plus survey history; reference products if selected |
| Inspect a user target list | CSV | Optional | Optional | Same products for the supplied list |
| Compare Rubin releases | Any | Any | DP1 and DP2 | One enriched result and output directory per release |

## MOP + HSH + DP2

Use this for the complete planning and reference-context analysis:

```toml
[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh"]
reference_surveys = ["rubin_dp2"]
```

Recommended products are the observing-selection summary, nightly visibility
plots, monitoring PDF, and sky maps. Enable `target_reports` only when the
per-target coadd dashboard is useful.

## MOP + HSH planning, without Rubin

Use an empty reference list:

```toml
[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh"]
reference_surveys = []
```

This produces a `planning_only` result with target and visibility tables plus
nightly visibility plots. It does not start TAP or Butler queries.

## Previously observed HSH targets only

```toml
[sources]
target_providers = []
followup_surveys = ["casleo_hsh"]
reference_surveys = []
```

The target union comes from the HSH inventory. This is useful for checking
which previously monitored events are observable again.

## User-supplied target list

Define a CSV provider and select it:

```toml
[sources]
target_providers = ["my_targets"]
followup_surveys = []
reference_surveys = []

[target_providers.my_targets]
adapter = "csv"
label = "My target list"
path = "inputs/my_targets.csv"
```

The CSV requires `Target`, `RA_deg`, and `Dec_deg`. Common lowercase aliases
such as `target`, `ra`, and `dec` are normalized automatically.

## Compare more than one Rubin release

```toml
[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh"]
reference_surveys = ["rubin_dp1", "rubin_dp2"]

[reference_surveys.rubin_dp1]
adapter = "rubin"
label = "Rubin DP1"
data_release = "DP1"

[reference_surveys.rubin_dp2]
adapter = "rubin"
label = "Rubin DP2"
data_release = "DP2"
```

The returned `AnalysisResult.runs` contains both entries. Each release keeps a
separate enriched result and output directory while using the same target
selection inputs.

## Add JS observations or photometry

Enable the existing normalized CSV adapter:

```toml
[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh", "casleo_js"]
reference_surveys = []

[followup_surveys.casleo_js]
adapter = "csv"
label = "CASLEO/JS"
provider_name = "JS"
inventory_path = "js_data/observations.csv"
photometry_path = "js_data/photometry.csv"
```

The observation inventory requires `Target` and `mjd`; optional normalized
columns include `band`, `exptime_s`, `RA_deg`, and `Dec_deg`. Photometry accepts
`Target`, an epoch, and either magnitude or flux columns.

## Choose products independently

For a fast selection-only run:

```toml
[products]
visibility_plots = true
sky_maps = false
monitoring_report = false
target_reports = false
reference_photometry = false
```

For detailed DP2 target inspection, enable `target_reports` and optionally
`reference_photometry`. These are the slowest product stages.
