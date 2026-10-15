# Data and cache

The project separates persistent scientific data from run-specific products.
This avoids copying the same photometry and observations into every run while
keeping each result reproducible.

## Local source files

Small local catalogs and observatory inventories used as inputs are kept under
`data/`, grouped by source (`hsh_data/`, `js_data/`, `mop_data/`, and
`LaStBeRu_data/`). The directory is ignored by Git so private or changing input
files stay local. Jupyter checkpoint copies are removed; notebooks should read
the canonical file in the source directory. Downloaded and refreshable provider
caches belong under the single shared `outputs/cache/` tree. Run folders
contain products and provenance, not their own copies of reusable source data.

## Persistent registry

The default registry is:

```text
outputs/target_database/target_selection.sqlite
```

It stores normalized information across runs:

| Table | Contents |
|---|---|
| `targets` | Canonical target name, authoritative coordinates, source, and priority |
| `target_aliases` | Alternative names used by different sources |
| `source_target_records` | Latest source-specific snapshot and provenance |
| `survey_observations` | HSH, JS, or other follow-up image epochs |
| `photometry_points` | Normalized provider, follow-up, and reference photometry |
| `source_state` | File fingerprints and refresh state |
| `analysis_runs` | Configuration, status, and output path of completed runs |

The database is updated when a selected source contains new or changed data.
Source-specific fields remain associated with that source instead of being
silently merged into one ambiguous row.

## Raw and provider-specific caches

Large or source-native data remain outside SQLite when that is more practical:

```text
outputs/
├── cache/
│   ├── mop/{visibility,event_data,photometry}/
│   ├── ogle/photometry/
│   ├── opm/photometry/
│   ├── bhtom/photometry/
│   ├── rubin/{photometry,reference_catalog,visit_coverage,coadd_coverage}/
└── target_database/         # Normalized SQLite registry (persistent state)
```

Repository code should use `target_selection.cache_paths` for shared locations.
Cache keys and release fingerprints remain inside provider subdirectories when
results depend on a query or data release. All generated products stay under this same root, grouped by run or analysis
(e.g. `outputs/notebooks/` and `outputs/analyses/mop_dp2/`). Only reusable
source/query data belong in `outputs/cache/`. Changing an observing window
should not repeat a compatible coverage query.

The one-time cache consolidation compared all retained variants by their
filesystem modification timestamps and kept the newest copy for each cache key.
The timestamp of creation was unavailable from the filesystem. The audit at
`outputs/migration_latest_versions.csv` records each choice and the
removed source paths.

## One run directory

Each run directory contains a reproducible snapshot and derived products, not
another complete copy of the database:

```text
outputs/<date-range-and-observing-window>/  # root-level run products
├── analysis_config.json
├── manifest.json
├── product_index.json
├── tables/
├── visibility_plots/
├── monitoring_reports/
└── sky_plots/

outputs/target_reports/
└── <Target>_target_report.png
```

Planning-only runs are stored below `outputs/planning_only/`.

## Reuse and refresh

```toml
[cache]
reuse = true
refresh_target_providers = false
refresh_followup_surveys = false
refresh_reference_surveys = false
```

- `reuse = true` reuses compatible downloads and computations.
- A follow-up CSV is reimported automatically when its file size or modification
  time changes.
- A refresh switch forces that source role to be checked again.
- Product overwrite switches are independent from source refresh switches. A
  plot can be regenerated without redownloading all source data.

## Audit files

Inside a run, use:

- `tables/source_updates.csv` to see which selected sources imported or updated
  records;
- `tables/source_catalog.csv` to inspect target provenance by source;
- `analysis_config.json` to reproduce the exact analysis; and
- `product_index.json` to locate the recommended outputs that were created.
