# Data and cache

The project separates persistent scientific data from run-specific products.
This avoids copying the same photometry and observations into every run while
keeping each result reproducible.

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
├── mop_photometry/          # Complete per-target MOP photometry CSV files
├── mop_event_cache/         # MOP event and parameter cache
├── rubin_photometry/        # Persistent per-target Rubin photometry CSV files
├── _cache/                  # Shared Rubin/TAP query cache
└── target_database/         # Normalized SQLite registry
```

These caches are shared across compatible date ranges and observing schedules.
Changing only the observing window should not repeat a compatible coverage
query.

## One run directory

Each run directory contains a reproducible snapshot and derived products, not
another complete copy of the database:

```text
outputs/<date-range-and-observing-window>/
├── analysis_config.json
├── manifest.json
├── product_index.json
├── tables/
├── visibility_plots/
├── monitoring_reports/
├── sky_plots/
└── targets/
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
