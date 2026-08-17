# Architecture

The architecture separates **data-source definitions**, **data access**,
**task execution**, **persistence**, and **products**. This allows a new survey,
event aggregator, local inventory, or catalog to be added without adding
another provider-specific argument to the main function.

## Core vocabulary

| Term | Meaning | Example |
|---|---|---|
| Source kind | What the source is | `survey`, `event_aggregator`, `user_target_list` |
| Run usage | How a source is used in one run | candidate input, planning telescope, context survey |
| Source definition | One named source entry | `sources.rubin_dp2`, `sources.mop` |
| Adapter | Python boundary that translates one source into normalized operations and tables | MOP adapter, HSH CSV adapter, Rubin adapter |
| Capability | Data offered by a source | `targets`, `coverage`, `photometry`, `images` |
| Registry | Persistent SQLite catalog shared across runs | `TargetRegistry` |
| Task | One explicit operation called from Python | `query_lsst_coverage`, `evaluate_visibility` |
| Product | A run-specific table, plot, PDF, or manifest | `product_index.json` |

### Source kinds and run usage

The core model is not `reference_survey` versus `followup_survey`. Those labels
describe how a survey is used in a specific run. The stable definition is:

```text
DataSource = source kind + access mode + capabilities
Run usage  = why this source is selected for this run
```

| Source kind | Meaning | Examples |
|---|---|---|
| `survey` | Observing survey or observing program. It may be wide-field, alert-based, follow-up, or archival. | Rubin/LSST, ZTF, Gaia, OGLE, CASLEO/HSH, CASLEO/JS |
| `event_aggregator` | System that aggregates or publishes candidate events from one or more surveys. | MOP, future OMP |
| `user_target_list` | Local list of targets to analyze. | CSV with name, RA, Dec |
| `local_inventory` | Local image/observation inventory for a survey. | HSH `image_collection_astro.csv`, future JS inventory |
| `catalog` | Static external catalog used for matching or enrichment. | LaStBeRu |

A source can expose one or more capabilities: `targets`, `event_parameters`,
`coverage`, `epochs`, `photometry`, `objects`, `images`, `coadds`, `cutouts`,
and `astrometry`. A source can be accessed by a Python API, HTTP request,
TAP/Butler, local CSV, local files, or a database.

Run usage is selected separately. For example, Rubin DP2 can be a context
survey, a photometry source, and an image source. HSH can be a planning survey
and an observed-epoch source. MOP can be a candidate input and event-parameter
source.

### Coordinate authority

Coordinates are normalized independently from target membership. An event may enter the union from MOP visibility, an HSH/JS observation inventory, or a user list, but a successful MOP event-page lookup is the authoritative position for that event. The adapter preserves MOP's sexagesimal strings and full floating-point ICRS values; the merge and registry use an explicit priority so adapter order cannot replace them with a lower-authority coordinate.

The built-in precedence is MOP event page (100), MOP visibility table (90), user target list (80), other provider/reference coordinates (50), and follow-up survey coordinates (10). HSH `CRVAL1`/`CRVAL2` are not target coordinates at all: they remain observation-level `pointing_ra_deg`/`pointing_dec_deg` metadata. Legacy registry coordinates of unknown provenance are ignored until an authoritative source resolves them. Run tables expose the selected source, priority, original MOP strings, and any corrected angular offset. All geometry, TAP, Butler, visibility, and photometry operations use the unrounded canonical floats.

## What an adapter is

An adapter is a small translation layer between a source-specific API, CSV, or
collection and the normalized workflow. It owns source-specific knowledge such
as:

- how to create or receive a client;
- how to query or read source data;
- how to normalize names, coordinates, epochs, and measurements;
- how to use source-specific cache state; and
- which source-specific options are required.

An adapter does **not** decide the global observing workflow or write every
product. That remains the responsibility of `AnalysisWorkflow` and the
scientific backends.

### Source specification

Every configured source becomes a `SourceSpec`:

| Field | Purpose |
|---|---|
| `name` | Stable name used in active source lists and result provenance |
| `adapter` | Registered implementation type, such as `mop`, `hsh`, `csv`, or `rubin` |
| `enabled` | Whether the definition is allowed to be selected |
| `label` | Human-readable display name |
| `capabilities` | Descriptive list of data the source offers |
| `options` | Remaining provider-specific settings, such as a path or Data Release |

`capabilities` document which tasks a source can support. The new task API uses
that concept directly, while the older config workflow still maps capabilities
through compatibility adapters.

### Adapter contracts

The current compatibility protocols in `target_selection.sources.base` still
exist for config-file runs. The executable backend now lives in
`target_selection.backend`; the root-level `target_selection_pipeline.py` is only
a compatibility shim for older notebooks. New task modules import the packaged
backend, source adapters, and product helpers directly. The refactor target is
capability-oriented protocols:

| Capability protocol | Required operation type | Normalized result |
|---|---|---|
| `TargetProvider` | collect target rows | `Target`, `RA_deg`, `Dec_deg`, provenance |
| `EventDataProvider` | enrich event parameters | `t_0`, `t_E`, `u_0`, `mag_now`, priority |
| `CoverageProvider` | query coverage/epochs | visits/images by target and band |
| `PhotometryProvider` | load/query light curves | normalized photometry table |
| `ObjectCatalogProvider` | match catalog objects | counterpart ID and separation |
| `ImageProvider` / `CutoutProvider` | find images/coadds/cutouts | image metadata and PNG/FITS products |

`SourceContext` gives adapters the validated configuration, persistent
registry, shared cache directory, and optionally injected clients. Client
injection keeps tests offline and allows advanced callers to reuse existing
connections.

### Adapter resolution

```text
active source name
        │
        ▼
SourceSpec from the matching role section
        │
        ├── role = followup_survey
        └── adapter = "hsh"
        │
        ▼
AdapterRegistry.create(role, spec)
        │
        ▼
HshFollowupSurvey(spec)
```

The adapter registry is keyed by both role and adapter name. Therefore a `csv`
target provider and a `csv` follow-up survey can use different contracts while
sharing a familiar configuration style.

## What validation means

Validation happens in layers. A successful early layer does not imply that a
remote service or every input file is available.

| Layer | When | What it checks | What it does not check |
|---|---|---|---|
| Parse and construction | `load_config()` | Valid TOML/JSON, required construction fields, compatible value shapes | Network, credentials, source contents |
| Configuration validation | `AnalysisConfig.validate()` and `validate-config` | Selected names are defined and enabled, no duplicate active names, altitude/duration/time-step/worker ranges | File existence, adapter registration, TAP/Butler access |
| Adapter resolution | Start of `run_analysis()` | Adapter type exists for the requested role | Remote availability or scientific completeness |
| Source validation | Adapter import/query | Required paths, required normalized columns, readable source response | Whether measurements are scientifically high quality |
| Scientific backend validation | Visibility or reference processing | Supported scope/encoding, release profile, query and image compatibility | Future data changes after the run |
| Product audit | End of a result | Configuration snapshot, source update audit, existing recommended products | Scientific interpretation by the observer |

`target-selection validate-config` intentionally performs no remote requests.
It is safe and fast, but “configuration is valid” means the declarative
structure is internally consistent—not that RSP credentials, TAP, Butler, MOP,
or local CSV files have all been tested.

## Complete internal flow

```text
run_analysis(config_or_path)
        │
        ├── load_config() when a path was supplied
        └── AnalysisConfig.validate()
        │
        ▼
AnalysisWorkflow.run()
        │
        ├── open/migrate TargetRegistry
        ├── create SourceContext
        │
        ├── for each follow-up survey
        │     ├── resolve adapter
        │     ├── import observations and optional photometry
        │     └── obtain previously observed targets
        │
        ├── for each target provider
        │     ├── resolve adapter
        │     ├── retain primary MOP client for the validated backend
        │     └── collect additional provider targets
        │
        ├── canonicalize and merge additional + observed targets
        │
        ├── no reference surveys?
        │     │
        │     ├── yes: planning-only branch
        │     │     ├── collect provider targets directly
        │     │     ├── build target-night rows
        │     │     ├── calculate and summarize local visibility
        │     │     └── write planning tables and optional nightly plots
        │     │
        │     └── no: for each reference adapter
        │           ├── resolve DataReleaseConfig
        │           ├── call validated run_target_selection backend
        │           │     ├── MOP visible targets, parameters, photometry
        │           │     ├── merge additional/follow-up targets
        │           │     ├── local visibility and magnitude cuts
        │           │     ├── TAP coverage
        │           │     ├── Butler coadds
        │           │     ├── optional reference photometry
        │           │     └── configured products
        │           ├── import normalized reference photometry
        │           └── store ReferenceRunResult
        │
        ├── write analysis_config.json
        ├── write source_updates.csv and source_catalog.csv
        ├── write product_index.json
        └── record completed analysis run in SQLite
        │
        ▼
AnalysisResult
        ├── .runs[name] -> ReferenceRunResult
        ├── .targets -> one result or concatenated multi-reference view
        ├── .source_updates
        └── .config
```

### Why MOP follows two paths

In planning-only mode the MOP adapter collects provider targets directly. In a
reference-survey run, the primary MOP client is passed into the existing
validated `target_selection.backend.run_target_selection` backend, which already owns MOP enrichment,
photometry cache handling, magnitude cuts, and the Rubin-oriented product
sequence. Other providers and follow-up targets enter that backend through the
normalized additional-target union.

This is a deliberate compatibility boundary during the architecture refactor,
not a distinction users need to encode in their configuration.

## Persistence and data ownership

```text
source-native data                     normalized persistent data
──────────────────                     ──────────────────────────
MOP pages and photometry CSVs ─────┐
Rubin coverage/query caches ───────┼──► TargetRegistry (SQLite)
HSH/JS user inventories ───────────┘      ├── target identities and aliases
                                           ├── latest source records
                                           ├── follow-up epochs
                                           ├── normalized photometry
                                           ├── source refresh state
                                           └── analysis-run records
                                                      │
                                                      ▼
                                           run-specific snapshots/products
```

Raw and provider-native caches are not forced into SQLite when their native
representation is more efficient. The registry contains normalized data needed
across analyses. Run directories contain the exact configuration and derived
view used for one decision.

## Module ownership

| Module | Responsibility |
|---|---|
| `target_selection.config` | Typed source-neutral configuration and structural validation |
| `target_selection.sources.base` | Adapter protocols and shared context |
| `target_selection.sources.registry` | Adapter registration and resolution |
| `target_selection.sources.adapters` | Compatibility adapters for config-file runs |
| `target_selection.sources.lsst` | Central LSST/Rubin TAP, Butler, coverage, coadd, and photometry helpers |
| `target_selection.tasks` | Preferred notebook-level task API |
| `target_selection.workflow` | Config-file orchestration, branch selection, persistence coordination, result objects |
| `target_selection.products` | Compact discovery index for products that exist |
| `target_registry` | SQLite schema, migrations, normalized imports, provenance, and cross-run queries |
| `target_selection_pipeline` | Validated single-Rubin-release scientific backend and aggregate products |
| `visibility_plotter` | Visibility physics, feasibility selection, nightly and sequence plots |
| `release_photometry` | Reference-survey photometry measurement and cache |
| `target_report`, `monitoring_report` | Individual and aggregate scientific visualization |

## Failure and resume behavior

- Source imports use fingerprints or provider-specific caches where available.
- The persistent registry is updated incrementally.
- Reference coverage, photometry, and target reports have independent reuse or
  overwrite controls.
- A failed product does not redefine source provenance or configuration.
- Repeating the same configuration with reuse enabled resumes compatible work.

The configuration snapshot and `product_index.json` make a completed result
discoverable; the registry and native caches make it reusable.

## Public and compatibility boundaries

- `target_selection.tasks` and package-level task functions: recommended
  notebook API for interactive work with Python variables.
- `target_selection.run_target_selection_tasks`: convenience task orchestrator.
- `target_selection.run_analysis`: config-file API for reproducible CLI runs.
- `target-selection run --config ...`: command-line entry point.
- `target_selection.sources.lsst`: only task-level import location for
  LSST/Rubin TAP, Butler, coverage, coadd, and photometry helpers.
- `target_selection_pipeline.run_target_selection`: compatibility backend for
  existing one-Rubin-release workflows.

New notebook workflows should start with task functions. New source
integrations should implement a source/capability adapter rather than add
provider-specific conditions to the workflow. See [Adding a source](adding_sources.md).
