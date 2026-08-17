# Python and CLI API

## Recommended Python task API

The preferred notebook API is task-oriented. Use normal Python variables in one
cell, and call only the steps needed for the current question.

```python
from target_selection import (
    collect_mop_targets,
    evaluate_visibility,
    query_lsst_coverage,
    compute_lsst_photometry,
    create_target_reports,
)

START_DATE = "2026-08-09"
END_DATE = "2026-08-12"
DATA_RELEASE = "DP2"
OBSERVATORY = "El Leoncito"

# 1. Candidate/event targets from MOP.
targets = collect_mop_targets(
    start_date=START_DATE,
    end_date=END_DATE,
    observatory=OBSERVATORY,
)

# 2. Local observability for a telescope schedule.
visibility = evaluate_visibility(
    targets,
    start_date=START_DATE,
    end_date=END_DATE,
    observatory=OBSERVATORY,
    observing_windows=["20:30", "07:00"],
    minimum_altitude_deg=40,
    minimum_observable_minutes=90,
)

# 3. Rubin/LSST coverage and optional photometry.
coverage = query_lsst_coverage(targets, data_release=DATA_RELEASE)
photometry = compute_lsst_photometry(
    targets,
    coverage,
    data_release=DATA_RELEASE,
    method="dia_forced_catalog",
)

# 4. Optional target dashboards.
create_target_reports(
    targets,
    coverage=coverage,
    release_photometry=photometry,
    data_release=DATA_RELEASE,
    output_dir="outputs/target_reports",
)
```

For convenience, the same task sequence can be run with one function while
still using explicit Python keyword arguments instead of a TOML config:

```python
from target_selection import run_target_selection_tasks

result = run_target_selection_tasks(
    start_date="2026-08-09",
    end_date="2026-08-12",
    observing_windows=["20:30", "07:00"],
    data_release="DP2",
    include_mop=True,
    compute_visibility=True,
    query_coverage=True,
    compute_photometry=True,
    photometry_method="dia_forced_catalog",
    make_visibility_plots=True,
    make_lightcurves_pdf=True,
)

display(result.targets.head())
display(result.visibility.head())
print(result.paths["run"])
```

This is now the recommended interactive style. `run_analysis(config)` remains
available for reproducible CLI/config-file runs, but it is no longer the only
high-level entry point.

## LSST/Rubin helpers

All task-level Rubin access goes through `target_selection.sources.lsst`. It
centralizes RSP TAP, Butler, coverage, coadd lookup, and release photometry
helpers. Other modules should import Rubin interactions from that module rather
than creating TAP/Butler clients directly.

Common wrappers are exposed at package level:

- `query_lsst_coverage(...)`
- `compute_lsst_photometry(...)`
- `save_lsst_photometry(...)`

## Config-file API

The config-file workflow is still useful for reproducible runs and CLI use:

```python
from target_selection import load_config, run_analysis

config = load_config("configs/my_run.toml")
result = run_analysis(config)
```

`run_analysis` also accepts the path directly:

```python
result = run_analysis("configs/my_run.toml")
```

The result contains one entry per Rubin/Data Release source, or a
`planning_only` entry when no Rubin source is selected:

```python
for source_name, run in result.runs.items():
    print(source_name, run.paths["run"])
    display(run.targets.head())

display(result.source_updates)
```

Useful attributes are:

- `result.config`: validated normalized configuration;
- `result.runs`: named `ReferenceRunResult` objects;
- `result.targets`: concatenated targets across all results; and
- `result.source_updates`: import/update counts for selected sources.

## CLI

```bash
# Validate without running.
target-selection validate-config --config configs/my_run.toml

# See available adapter types.
target-selection list-sources

# Run and print each output directory.
target-selection run --config configs/my_run.toml
```

The CLI uses the config-file workflow.

## Large coordinate catalogs

```python
from target_selection import (
    load_reference_catalog_config, run_reference_catalog,
    run_reference_catalog_preview, query_direct_coadd_coverage,
)

config = load_reference_catalog_config("configs/lastberu_dp2.toml")
# Small validation run; the full run omits n_targets.
result = run_reference_catalog_preview(config, n_targets=200)
# Full run: result = run_reference_catalog(config)
display(result.catalog.head())
```

The equivalent CLI command is:

```bash
target-selection catalog --config configs/lastberu_dp2.toml
```

`ReferenceCatalogResult` exposes `catalog`, `catalog_path`, `cutout_plan_path`,
`manifest_path`, `output_dir`, and the aggregate `summary`. TAP and Butler
clients can be supplied explicitly for tests or non-default RSP environments.
For staged runs, `query_direct_coadd_coverage` checks real coadd dataset
references with Butler; coadd-property maps are optional metadata and are not
used to decide whether a coadd exists.

## Advanced compatibility API

`target_selection_pipeline.run_target_selection` remains available for an
advanced single-Rubin-release analysis with explicit dependency injection:

```python
from target_selection_pipeline import run_target_selection

targets, paths = run_target_selection(
    start_date="2026-08-09",
    end_date="2026-08-12",
    data_release="DP2",
)
```

Prefer the task API for notebooks and new code. Prefer `run_analysis` only when
a TOML config/CLI run is the desired interface.
