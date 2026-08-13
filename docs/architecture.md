# Architecture

The architecture separates **scientific roles**, **data access**,
**orchestration**, **persistence**, and **products**. This allows a new target
provider or follow-up survey to be added without adding another provider-
specific argument to the main function.

## Core vocabulary

| Term | Meaning | Example |
|---|---|---|
| Source role | Why a source participates in planning | `target_provider` |
| Source definition | One named configuration entry | `target_providers.mop` |
| Adapter | Python boundary that translates one source into normalized operations and tables | `MopTargetProvider` |
| Capability | Descriptive metadata about data offered by a source | `targets`, `photometry` |
| Registry | Persistent SQLite catalog shared across runs | `TargetRegistry` |
| Reference run | One target union enriched by one reference survey | `rubin_dp2` |
| Product | A run-specific table, plot, PDF, or manifest | `product_index.json` |

### The three source roles

| Role | Purpose | Built-in adapter types | Current examples |
|---|---|---|---|
| Target provider | Supplies candidate events, parameters, or provider photometry | `mop`, `csv` | MOP, user lists, future OMP |
| Follow-up survey | Represents the observing program being evaluated or planned | `hsh`, `csv` | CASLEO/HSH, CASLEO/JS |
| Reference survey | Adds external coverage, images, catalogues, or photometry | `rubin` | Rubin DP1, DP2 |

A role is semantic, not a file format. MOP remains a target provider even
though it also supplies event parameters and photometry. HSH remains a
follow-up survey even after photometry is added.

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

`capabilities` are currently descriptive metadata. They document and expose
intent but do not dynamically replace adapter methods or enable products.

### Adapter contracts

The protocols in `target_selection.sources.base` define the smallest interface
for each role:

| Role | Required operations | Normalized result |
|---|---|---|
| Target provider | `client(context)`, `collect_targets(context)` | Target table with `Target`, `RA_deg`, `Dec_deg` |
| Follow-up survey | `import_observations(context)`, `observed_targets(context)` | Imported epochs and observed target table |
| Reference survey | `data_release()` | Backend-specific release profile |

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
validated `run_target_selection` backend, which already owns MOP enrichment,
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
| `target_selection.sources.adapters` | Built-in MOP, CSV, HSH, and Rubin translations |
| `target_selection.workflow` | Source orchestration, branch selection, persistence coordination, result objects |
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

- `target_selection.run_analysis`: recommended source-neutral API.
- `target-selection run --config ...`: reproducible CLI using the same API.
- `mop_lsst.ipynb`: interactive configuration, execution, and product review.
- `target_selection_pipeline.run_target_selection`: compatible low-level API
  for one Rubin release and advanced dependency injection.

New user workflows should start with `run_analysis`. New source integrations
should implement an adapter rather than add provider-specific conditions to the
workflow. See [Adding a source](adding_sources.md).
