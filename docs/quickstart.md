# Quick start

## 1. Install in the Rubin Science Platform

```bash
git clone https://github.com/nowokaren/target_selection.git
cd target_selection
python -m pip install -e .
```

The package installs `mop_api`. Rubin `lsst.*` packages are supplied by the RSP
environment and are not installed by pip.

## 2. Create a run configuration

Keep the example unchanged and make a working copy:

```bash
cp configs/example.toml configs/my_run.toml
```

At minimum, edit the dates and the active source lists near the top:

```toml
[run]
start_date = "2026-08-09"
end_date = "2026-08-12"
observatory = "El Leoncito"
output_dir = "outputs"

[sources]
target_providers = ["mop"]
followup_surveys = ["casleo_hsh"]
reference_surveys = ["rubin_dp2"]
```

For a single night, omit `end_date` or set it equal to `start_date`.

## 3. Set the observing cut

This example retains a target if it has at least 90 minutes at 40 degrees or
higher during both astronomical night and the allocated observing window:

```toml
[selection]
maximum_current_magnitude = 18.5
minimum_altitude_deg = 40.0
minimum_observable_minutes = 90.0
time_step_minutes = 1
observing_windows = ["20:30", "07:00"]
```

Targets without a current MOP magnitude are retained by the magnitude cut.
Targets with a magnitude greater than the limit are excluded from the analysis
products but remain recorded in the initial MOP audit table.

## 4. Select products

```toml
[products]
visibility_plots = true
sky_maps = true
monitoring_report = true
target_reports = false
reference_photometry = false
```

Individual target reports and reference photometry are expensive. Leave them
disabled for MOP/HSH planning, and enable them when coadd inspection or Rubin
photometry is needed.

## 5. Validate and run

```bash
target-selection validate-config --config configs/my_run.toml
target-selection run --config configs/my_run.toml
```

From Python or the notebook, the equivalent call is:

```python
from target_selection import load_config, run_analysis

config = load_config("configs/my_run.toml")
result = run_analysis(config)

print(result.runs.keys())
print(result.runs["rubin_dp2"].paths["run"])
```

`mop_lsst.ipynb` uses this same configuration and displays selected results. It
is an interactive front end, not a second implementation of the pipeline.

## 6. Find the result

Start with these files inside the printed run directory:

1. `product_index.json` — paths and descriptions of products created.
2. `analysis_config.json` — exact normalized configuration used.
3. `tables/combined_targets.csv` — complete enriched target result.
4. `tables/observing_selection_summary.png` — compact planning table.
5. `monitoring_reports/lightcurves.pdf` — light curves and temporal
   coverage, when enabled.

The first run downloads or imports source data. Compatible later runs reuse the
persistent registry and source-specific caches.
