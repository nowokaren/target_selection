# How it works

This page explains the scientific and operational workflow. For class
contracts, adapter resolution, validation layers, and module ownership, see
[Architecture](architecture.md).

## Complete user workflow

```text
configs/my_run.toml
        │
        ▼
load and validate AnalysisConfig
        │
        ▼
resolve selected sources by role
        │
        ├── target providers ──► candidates, event data, photometry
        │                         (MOP, user CSV, future OMP)
        │
        └── follow-up surveys ─► observed targets, epochs, photometry
                                  (CASLEO/HSH, CASLEO/JS)
        │
        ▼
canonical target union + source provenance
        │
        ▼
local visibility for every requested night
        │
        ├── astronomical night
        ├── allocated observing window
        ├── minimum altitude
        └── minimum observable duration
        │
        ▼
apply scientific and feasibility selection
        │
        ▼
Are reference surveys selected?
        │
        ├── no ──► planning-only result
        │           ├── target table
        │           ├── visibility decisions
        │           └── nightly visibility plots
        │
        └── yes ─► one independent enrichment run per reference survey
                    ├── TAP visit/detector coverage
                    ├── Butler deep-coadd lookup
                    ├── optional forced photometry
                    ├── summary and planning tables
                    ├── optional sky and visibility plots
                    ├── optional monitoring PDF
                    └── optional target dashboards
        │
        ▼
update persistent registry + write run snapshot and product index
        │
        ▼
AnalysisResult
```

## 1. Load one declarative configuration

`load_config()` reads TOML or JSON and creates a validated `AnalysisConfig`.
The configuration says **what** should participate in the analysis:

- dates, observatory, and allocated time;
- selected source names grouped by semantic role;
- scientific and local-visibility cuts;
- requested products; and
- cache and runtime policy.

It does not contain provider-specific branching logic. A source name points to
a source definition, and its `adapter` determines how that source is read.

## 2. Import selected sources

Target providers propose candidates. Follow-up surveys contribute targets that
have already been observed, even when those targets are absent from the current
provider response. Every source is normalized to shared names such as
`Target`, `RA_deg`, `Dec_deg`, and `mjd` while retaining its provenance and
additional metadata.

The workflow imports follow-up observations before building the target union.
This makes the persistent HSH/JS history immediately available to selection,
stage summaries, monitoring plots, and subsequent runs.

## 3. Build the canonical target union

The union contains:

- candidates returned by selected target providers;
- targets already present in selected follow-up surveys; and
- targets from optional user CSV providers.

Names are canonicalized to reduce duplicates, aliases remain traceable, and
`input_sources` records which sources contributed each target.

### Provider visibility is not local visibility

- **MOP-visible** means MOP returned the event for the requested date or date
  range.
- **Locally visible** means this project calculated that the event satisfies
  the telescope-specific observing criterion.

A previously observed HSH event may be locally visible even when MOP did not
return it. Conversely, an event returned by MOP may fail the local criterion.

## 4. Calculate local observing feasibility

For every target and night, the visibility layer evaluates the altitude curve
on the configured time grid. Observable minutes are counted only where all of
the following overlap:

1. astronomical night;
2. the assigned local observing window; and
3. altitude at or above `minimum_altitude_deg`.

The default selection requires at least 90 minutes at 40 degrees or higher.
The result stores observable duration, best interval, maximum altitude,
pass/fail status, and rejection reasons. Plots are a visualization of these
stored decisions, not the only record of them.

## 5. Add reference-survey context when requested

Each selected reference survey enriches the same target selection
independently. For the current Rubin adapter, the validated backend:

1. uses TAP to retrieve nearby visit/detector rows, including epoch, band,
   seeing, and limiting magnitude;
2. uses Butler to locate deep coadds whose footprint contains each target; and
3. optionally measures forced PSF photometry at the exact target coordinate on
   those coadds.

The Early DP2 workflow uses deep coadds for image panels and optional forced
photometry. It does not download or display individual calibrated visit
exposures.

With two selected reference surveys, for example DP1 and DP2,
`AnalysisResult.runs` contains two named results and two output directories.
The sources are not silently merged because their coverage and products have
different meanings.

## 6. Generate only configured products

Visibility plots, sky maps, monitoring light curves, target dashboards, and
reference photometry are independent product switches where the selected mode
supports them. Expensive target reports and reference photometry can remain
disabled for a fast planning run.

Every completed result writes:

- `analysis_config.json`: exact normalized configuration;
- `source_updates.csv` and `source_catalog.csv`: update and provenance audit;
- target and visibility result tables; and
- `product_index.json`: recommended products that actually exist.

## 7. Persist reusable scientific data

The SQLite registry stores canonical identities, source records, follow-up
epochs, normalized photometry, refresh state, and completed-run metadata.
Provider-native downloads and large query results may remain in specialized
caches. A run directory stores reproducible snapshots and derived products,
not another complete copy of the database.

See [Data and cache](data-and-cache.md) for ownership and refresh behavior and
[Products](products.md) for the tables, plots, and PDFs produced by each mode.
