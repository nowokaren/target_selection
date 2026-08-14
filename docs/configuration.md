# Configuration

The TOML file is ordered by how often settings normally change. Dates, active
sources, scientific cuts, and product switches are at the top. Source
definitions, cache policy, and implementation details are below them.

`configs/example.toml` is intentionally only lightly annotated. This page is
the complete keyword reference so the executable configuration remains easy to
scan.

## Run dates and output

```toml
[run]
name = ""
start_date = "2026-08-09"
end_date = "2026-08-12"
observatory = "El Leoncito"
output_dir = "outputs"
```

| Keyword | Type | Required/default | Meaning |
|---|---|---|---|
| `start_date` | ISO date string | Required | First evening/night evaluated |
| `end_date` | ISO date string | Defaults to `start_date` | Last evening/night evaluated, inclusive |
| `name` | String, default `""` | Optional | When non-empty, a safe version is prepended to the run-directory name, e.g. `august_2026-08-09_to_2026-08-12__obs_20-30_to_07-00`. |
| `observatory` | String | `"El Leoncito"` | Observatory profile used by visibility and provider queries |
| `output_dir` | Path string | `"outputs"` | Root for persistent data, caches, and run directories |

For one night, omit `end_date` or set it equal to `start_date`.

## Active sources

```toml
[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh"]
reference_surveys = ["rubin_dp2"]
```

Each value is a list of source-definition names from the corresponding section
later in the file. Empty lists are valid.

| Keyword | Role | Typical values |
|---|---|---|
| `target_providers` | Candidate/event sources | `[]`, `["mop"]`, `["mop", "my_targets"]` |
| `followup_surveys` | Telescope programs being evaluated/planned | `[]`, `["casleo_hsh"]`, `["casleo_hsh", "casleo_js"]` |
| `reference_surveys` | External contextual surveys | `[]`, `["rubin_dp2"]`, `["rubin_dp1", "rubin_dp2"]` |

There is no new global Boolean for every future source. Add one source
definition and select its name in the appropriate list.

## Scientific and visibility selection

```toml
[selection]
target_data_scope = "with_data"
maximum_current_magnitude = 18.5
minimum_altitude_deg = 40.0
minimum_observable_minutes = 90.0
time_step_minutes = 1
visibility_target_scope = "all_queried"
observing_windows = ["20:30", "07:00"]
```

| Keyword | Type/default | Allowed values and meaning |
|---|---|---|
| `target_data_scope` | String, default `"with_data"` | `"with_data"` retains only targets with photometry from an active target provider, imported images from an active follow-up survey, or normalized photometry from an active source. `"all"` disables this extra active-source cut. MOP-visible candidates with neither event parameters nor photometry remain diagnostic-only in `mop_candidates_without_data.csv`.  |
| `maximum_current_magnitude` | Float or omitted | Retain MOP events with `mag_now` at or below this faint limit. Missing magnitudes are retained. |
| `minimum_altitude_deg` | Float, default `40.0` | Range 0–90 degrees. Altitude threshold for locally observable time. |
| `minimum_observable_minutes` | Non-negative float, default `90.0` | Required duration satisfying astronomical night, allocation, and altitude simultaneously. |
| `time_step_minutes` | Positive integer, default `1` | Sampling interval for visibility curves. Smaller values are more precise and slower. |
| `visibility_target_scope` | String, default `"all_queried"` | Candidate pool: `"all_queried"` evaluates provider, follow-up, and user targets; `"mop_daily"` evaluates only targets returned by MOP for each night. In both cases, nightly plots always retain only rows passing the local altitude, astronomical-night, allocation-window, and duration cuts above. |
| `observing_windows` | Time pair or mapping | Local observing allocation. See formats below. |

### Observing-window formats

One interval for every requested night:

```toml
observing_windows = ["20:30", "07:00"]
```

A default with date-specific overrides can be represented as a TOML
subtable:

```toml
[selection.observing_windows]
default = ["20:30", "07:00"]
"2026-08-10" = ["22:00", "03:30"]
"2026-08-11" = []
```

The ISO date identifies the evening that begins the observing night. A window
may cross midnight. An empty list means no allocated time for that night.

## Products

```toml
[products]
visibility_plots = true
observing_selection_summary = true
sky_maps = true
monitoring_report = true
target_reports = false
target_report_scope = "visibility_selected"
reference_photometry = false
coverage_background = false
marker_encoding = "split_color"
```

| Keyword | Type/default | Allowed values and effect |
|---|---|---|
| `visibility_plots` | Boolean, `true` | Write filtered nightly visibility PNGs. |
| `observing_selection_summary` | Boolean, `true` | Write the bright-to-faint visual planning table with visibility, microlensing parameters, and stage-resolved MOP/HSH/reference coverage. |
| `sky_maps` | Boolean, `true` | Write full and bulge-zoom maps in reference-survey mode. |
| `monitoring_report` | Boolean, `true` | Write the multipage light-curve and temporal-coverage PDF. Without a reference survey it includes MOP and configured follow-up layers only. |
| `target_reports` | Boolean, `false` | Write individual coadd dashboards. This is an expensive stage. |
| `target_report_scope` | String, `"visibility_selected"` | `"visibility_selected"` or `"all_queried"`. |
| `reference_photometry` | Boolean, `false` | Enable reference-survey forced photometry. This may be expensive. |
| `reference_photometry_targets` | List or omitted | Named target subset. Omit while photometry is enabled to request all eligible targets. |
| `coverage_background` | Boolean, `false` | Add muted low-resolution visit-density context to sky maps. |
| `marker_encoding` | String, `"split_color"` | `"split_color"`: magnitude and visits use marker halves. `"color_size"`: magnitude uses color and visits use size. |
| `monitoring_layers` | List of strings | Select normalized monitoring layers described below. |

Recommended normalized monitoring layers are:

| Layer | Meaning |
|---|---|
| `target_provider_photometry` | Provider light curve, currently mapped to MOP photometry |
| `reference_epochs` | Epoch markers from the active reference survey |
| `followup_surveys` | All selected HSH/JS-style follow-up layers |

Product availability also depends on mode. Planning-only mode intentionally
skips TAP and Butler. It can still generate local visibility, the observing-selection table, and the monitoring PDF; reference-survey products add sky context, coadds, and reference photometry. See [Products](products.md).
photometry. See [Products](products.md).

## Common source-definition fields

```toml
[target_providers.mop]
adapter = "mop"
label = "MOP"
capabilities = ["targets", "parameters", "photometry"]
enabled = true
```

| Keyword | Required/default | Meaning |
|---|---|---|
| `adapter` | Required in normal definitions | Registered implementation type used to read the source |
| `enabled` | `true` | A disabled definition may remain in the file but cannot be selected |
| `label` | Source name | Human-readable name for interfaces and provenance |
| `capabilities` | Empty list | Descriptive source metadata; currently not a product switch |

Every additional keyword in a source definition becomes an adapter-specific
option.

## Built-in target-provider adapters

### MOP

```toml
[target_providers.mop]
adapter = "mop"
label = "MOP"
capabilities = ["targets", "parameters", "photometry"]
enrich = true
```

`enrich = true` requests the parameter and photometry-enriched MOP summary.
Client injection is available through the Python API; otherwise the default MOP
client is created automatically.

### CSV target provider

```toml
[target_providers.my_targets]
adapter = "csv"
label = "Observer target list"
path = "inputs/targets.csv"
```

The file requires `Target`, `RA_deg`, and `Dec_deg`. Common lowercase aliases
such as `target`, `name`, `ra`, and `dec` are normalized automatically. For
other headers, use `column_map`, whose keys are input headers and values are
canonical names:

```toml
column_map = { event_id = "Target", ra_icrs = "RA_deg", dec_icrs = "Dec_deg" }
```

## Built-in follow-up adapters

### CASLEO/HSH

```toml
[followup_surveys.casleo_hsh]
adapter = "hsh"
label = "CASLEO/HSH"
provider_name = "HSH"
inventory_path = "hsh_data/image_collection_astro.csv"
```

`inventory_path` points to the HSH astrometric image inventory. An optional
`photometry_path` may point to normalized target photometry.

### Normalized CSV survey, such as JS

```toml
[followup_surveys.casleo_js]
adapter = "csv"
label = "CASLEO/JS"
provider_name = "JS"
inventory_path = "js_data/observations.csv"
photometry_path = "js_data/photometry.csv"
```

Observation rows require `Target` and `mjd`; optional columns include `band`,
`exptime_s`, `usable`, `RA_deg`, and `Dec_deg`. Photometry requires an epoch
and either magnitude or flux data. Use `inventory_column_map` and
`photometry_column_map` for nonstandard CSV headers. See
[Adding a source](adding_sources.md#normalized-columns-and-configurable-maps)
for the complete canonical schemas and examples.

## Built-in reference adapter

```toml
[reference_surveys.rubin_dp2]
adapter = "rubin"
label = "Rubin DP2"
data_release = "DP2"
```

`data_release` selects a profile supported by `data_release_config.py`, such as
`DP0.1`, `DP0.2`, `DP1`, or `DP2`. Multiple definitions may use the same
adapter with different releases.

## Cache policy

```toml
[cache]
reuse = true
database_path = "target_database/target_selection.sqlite"
refresh_target_providers = false
refresh_followup_surveys = false
```

| Keyword | Type/default | Meaning |
|---|---|---|
| `reuse` | Boolean, `true` | Reuse compatible native caches, registry records, queries, and products. |
| `database_path` | Path string | Persistent SQLite path, relative to `output_dir` unless absolute. |
| `refresh_target_providers` | Boolean, `false` | Force supported provider parameter/photometry refresh behavior. |
| `refresh_followup_surveys` | Boolean, `false` | Force reimport of selected follow-up inventories and photometry. |
| `refresh_reference_surveys` | Boolean, `false` | Reserved role-level setting; current Rubin refresh is controlled by reuse and reference-photometry overwrite settings. |

File-backed follow-up adapters also compare fingerprints. A changed input CSV
is imported even when reuse is enabled.

## Runtime and overwrite details

```toml
[runtime]
max_workers = 4
verbose = true
overwrite_visibility_plots = false
overwrite_target_reports = false
overwrite_reference_photometry = false
monitoring_plots_per_page = 3
coverage_resolution = 19
```

| Keyword | Type/default | Meaning |
|---|---|---|
| `max_workers` | Positive integer, `4` | Bounded concurrency for supported provider and coverage operations. |
| `verbose` | Boolean, `true` | Print stage progress, source-labeled query targets, and product paths. |
| `overwrite_visibility_plots` | Boolean, `false` | Regenerate nightly plots instead of reusing compatible files. |
| `overwrite_target_reports` | Boolean, `false` | Regenerate individual target dashboards. |
| `overwrite_reference_photometry` | Boolean, `false` | Repeat reference photometry instead of reusing completed per-target data. |
| `monitoring_plots_per_page` | Positive integer, `3` | Light curves per monitoring PDF page. |
| `coverage_resolution` | Positive integer, `19` | Coarse visit-density grid resolution; larger values use smaller cells. |

Refresh controls govern source data. Overwrite controls govern derived products.
They are intentionally separate so a plot can be remade without downloading
all source data again.

## What `validate-config` guarantees

Always validate an edited file before a long run:

```bash
target-selection validate-config --config configs/my_run.toml
```

This confirms TOML/JSON parsing, selected source references, enabled status,
duplicates, and basic numeric ranges. It does **not** contact MOP, TAP, or
Butler and does not open configured CSV files. Adapter, file, credential, and
remote-service checks occur when the analysis starts. See
[Architecture: What validation means](architecture.md#what-validation-means).
