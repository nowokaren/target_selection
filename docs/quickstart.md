# Quick start

## 1. Install in the Rubin Science Platform

```bash
git clone https://github.com/nowokaren/target_selection.git
cd target_selection
python -m pip install -e .
```

The package installs `mop_api`. Rubin `lsst.*` packages are supplied by the RSP
environment and are not installed by pip.

## 2. Run from Python variables

For interactive work, use the task API directly from a notebook cell:

```python
from target_selection import run_target_selection_tasks

START_DATE = "2026-08-09"
END_DATE = "2026-08-12"
OBSERVING_WINDOWS = ["20:30", "07:00"]

result = run_target_selection_tasks(
    start_date=START_DATE,
    end_date=END_DATE,
    observing_windows=OBSERVING_WINDOWS,
    data_release="DP2",
    include_mop=True,
    compute_visibility=True,
    query_coverage=True,
    compute_photometry=False,
    make_visibility_plots=True,
    make_lightcurves_pdf=True,
    make_target_reports=False,
)

display(result.targets.head())
display(result.visibility.head())
print(result.paths["run"])
```

The same steps can be called one by one when you need more control:

```python
from target_selection import collect_mop_targets, evaluate_visibility, query_lsst_coverage

targets = collect_mop_targets(start_date=START_DATE, end_date=END_DATE)
visibility = evaluate_visibility(targets, start_date=START_DATE, end_date=END_DATE)
coverage = query_lsst_coverage(targets, data_release="DP2")
```

## 3. Set the observing cut

This example retains a target if it has at least 90 minutes at 40 degrees or
higher during both astronomical night and the allocated observing window:

```python
visibility = evaluate_visibility(
    targets,
    start_date="2026-08-09",
    end_date="2026-08-12",
    observatory="El Leoncito",
    observing_windows=["20:30", "07:00"],
    minimum_altitude_deg=40,
    minimum_observable_minutes=90,
    time_step_minutes=1,
)
```

## 4. Add Rubin/LSST information

```python
from target_selection import query_lsst_coverage, compute_lsst_photometry

coverage = query_lsst_coverage(targets, data_release="DP2", max_workers=4)
photometry = compute_lsst_photometry(
    targets,
    coverage,
    data_release="DP2",
    method="dia_forced_catalog",  # or "coadd_forced"
)
```

## 5. Create products only when needed

```python
from target_selection import create_lightcurves_report, create_target_reports

create_lightcurves_report(
    targets,
    output_path="outputs/lightcurves.pdf",
    release_photometry=photometry,
    coverage=coverage,
    data_release="DP2",
)

create_target_reports(
    targets,
    coverage=coverage,
    release_photometry=photometry,
    data_release="DP2",
    output_dir="outputs/target_reports",
)
```

Individual target reports and Rubin photometry are expensive. Leave them off
unless you specifically need coadd inspection or Rubin light curves.

## 6. Config-file and CLI mode

For reproducible command-line runs, keep using a TOML file:

```bash
cp configs/example.toml configs/my_run.toml
target-selection validate-config --config configs/my_run.toml
target-selection run --config configs/my_run.toml
```

From Python:

```python
from target_selection import load_config, run_analysis

result = run_analysis(load_config("configs/my_run.toml"))
```

## 7. Find the result

Start with these files inside the printed run directory:

1. `tables/targets.csv` or `tables/combined_targets.csv` — target table used by the run.
2. `visibility_plots/visibility_selection.csv` — target-night visibility decisions.
3. `tables/lsst_coverage.csv` — Rubin visit/detector coverage when queried.
4. `tables/lsst_photometry.csv` — Rubin photometry when requested.
5. `monitoring_reports/lightcurves.pdf` — light curves and temporal coverage, when enabled.

The first run downloads or imports source data. Compatible later runs reuse the
persistent registry and source-specific caches.
