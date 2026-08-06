# MOP target selection with Rubin coverage

This project cross-matches visible MOP targets with a Rubin Data Preview/Data Release and generates tables, sky maps, and graphical reports for each target. The currently validated run uses **DP2**; the DP0.1, DP0.2, and DP1 profiles may require collection or table adjustments for a specific RSP deployment.

## Project files

- `mop_lsst.ipynb`: ordered entry point for an interactive run.
- `target_selection_pipeline.py`: orchestration, queries, caching, tables, and sky maps.
- `photometry.py`: MOP photometry loading, caching, and preparation.
- `target_report.py`: graphical dashboard for each target.
- `visibility_plotter.py`: local nightly altitude, airmass, twilight, and Moon plots.
- `data_release_config.py`: DP0.1, DP0.2, DP1, and DP2 configuration.

The notebook only configures the run, calls installed functions, and displays the generated products.

## Requirements

1. An account with access to the Rubin Science Platform (RSP) and the selected Data Release.
2. Run the notebook inside the RSP scientific environment.
3. Clone and install this repository in the RSP environment:

```bash
git clone https://github.com/nowokaren/target_selection.git
cd target_selection
python -m pip install -e .
```

The installation automatically downloads the tested `mop_api` version from GitHub. The `lsst.*` libraries are supplied by the RSP environment and are not installed with pip.

## Quick start

Open `mop_lsst.ipynb` and edit the **Configuration** cell:

```python
DATA_RELEASE_NAME = "DP2"  # DP0.1, DP0.2, DP1, or DP2
START_DATE = "2026-08-01"
END_DATE = "2026-08-15"
OUTPUT_DIR = Path("outputs")
OBSERVATORY = "El Leoncito"

SKY_MARKER_ENCODING = "split_color"  # or "color_size"
SHOW_COVERAGE_BACKGROUND = False
COVERAGE_RESOLUTION = 19
```

Restart the kernel and select **Run All**. The main function is `run_target_selection(...)`; it automatically creates the MOP, TAP, and Butler clients and uses the standard report generator. The first run may take time because it queries MOP, TAP, and Butler; later runs reuse caches.

- `max_workers=4`: query concurrency.
- `reuse_cache=True`: reuse previous downloads and queries.
- `overwrite_target_plots=False`: keep current reports.
- `target_plotter=False`: skip individual reports.
- `sky_marker_encoding="split_color"`: encode magnitude and visits with two colored marker halves and two color bars.
- `sky_marker_encoding="color_size"`: encode magnitude with color and total visits with marker size.
- `show_coverage_background=True`: query and display a muted, low-resolution visit-density layer for the selected Data Release.
- `coverage_resolution=19`: control the coarse background grid; 19 approximately matches one LSSTCam field of view per cell.
- `generate_visibility_plots=True`: create one local visibility plot per requested night.
- `visibility_minimum_altitude=30`: altitude used to count observable astronomical-night time.
- `visibility_minimum_peak_altitude=50`: minimum peak altitude reached during astronomical night.
- `visibility_minimum_night_fraction=0.50`: minimum fraction of astronomical night above the altitude threshold.
- `visibility_selection_rule="all"`: require both criteria; use `"either"` to accept either criterion.
- `visibility_time_step_minutes=10`: temporal sampling of visibility curves.
- `overwrite_visibility_plots=False`: reuse existing nightly PNG files.

The release-wide visit-center query is executed only when the background is enabled. Its result is cached as `release_visit_centers.csv`, so later runs do not query the complete release again. The layer is an approximate visualization of total visit density, not an exact detector-footprint map.

The same run can be launched from Python without manually building the connections:

```python
from target_selection_pipeline import run_target_selection

combined, paths = run_target_selection(
    start_date="2026-08-01",
    end_date="2026-08-15",
    data_release="DP2",
)
```

For tests or advanced configurations, explicitly pass `mop`, `tap_service`, `butler`, or a custom `target_plotter`.


The automatic visibility plots contain only targets passing the configurable peak-altitude and observable-night-fraction criteria. To make plots from a manually reviewed final selection without applying another filter:

```python
from visibility_plotter import (
    plot_selected_visibility,
    plot_visibility_sequence,
    save_selected_visibility_plots,
)

plot_selected_visibility(selected_for_one_night, "2026-08-05", "final_visibility.png")
save_selected_visibility_plots(selected_for_all_nights, "final_visibility_plots")

# Stack all selected nights chronologically; the extension chooses PDF or PNG.
plot_visibility_sequence(
    selected_for_all_nights,
    "visibility_sequence.pdf",
    x_reference_every=4,
)
```

For explicit selection outside the pipeline, use `select_nightly_targets(...)`. Set `return_all=True` to retain rejected targets and their reasons. `plot_visibility_sequence(...)` shares the 18:00–06:00 axis, orders panels chronologically, repeats time labels every `x_reference_every` nights, and writes PDF by default when no extension is supplied.

## Outputs

For DP2, one run produces:

```text
outputs/
├── photometry/                         # One MOP photometry CSV per target
├── mop_event_cache/                    # MOP event-data cache
└── YYYY-MM-DD_to_YYYY-MM-DD/
    ├── manifest.json                   # Run configuration and metadata
    ├── plot_errors.csv                 # Isolated report errors
    ├── tables/
    │   ├── visible_targets_daily.csv   # Visibility by date
    │   ├── visible_summary.csv         # Visibility + MOP parameters
    │   ├── coverage_raw.csv            # Rubin visit/detector rows
    │   ├── coverage_summary.csv        # Coverage and visits by band
    │   ├── release_visit_centers.csv    # Optional cached background input
    │   ├── combined_targets.csv        # Complete MOP + Rubin table
    │   ├── target_summary.csv          # Compact scientific summary
    │   └── target_summary.png          # Visual summary table
    ├── sky_plots/                      # Full-sky and bulge maps
    ├── visibility_plots/               # Automatically filtered nightly plots
    │   └── visibility_selection.csv    # Metrics, decisions, and rejection reasons
    └── targets/
        ├── <Target>_target_report.png  # Individual report
        └── report_versions.json        # Report cache control
```

Other Data Releases add a directory named after the release.

### Which table should I use?

- Final target list and all properties: `combined_targets.csv`.
- Visits by filter, MOP points, `t_E`, `t_0`, and `u_0`: `target_summary.csv`.
- Unaggregated visit/detector rows: `coverage_raw.csv`.
- Complete photometry: `outputs/photometry/<Target>.csv`.

The `n_visits_<filter>` counts represent unique `visitId` values. An individual report is created only when at least one coadd contains the target position. Reports currently display coadds, not individual exposures.

## Repeating or refreshing a run

- Same configuration: `reuse_cache=True`.
- Force fresh data: `reuse_cache=False`.
- Rebuild every PNG: `overwrite_target_plots=True`.
- After editing code, restart the kernel or rerun the imports cell.

## Sharing the project

This project belongs in a Git repository separate from `mop_api`. The `.gitignore` excludes outputs, caches, and checkpoints. To share a specific result, archive only the corresponding run directory.

The dependency in `pyproject.toml` pins `mop_api` to the tested commit `28f8f87`. To adopt a newer API version, update that hash explicitly and rerun the tests.

## Development and tests

```bash
python -m pip install -e ".[test]"
pytest
```

GitHub Actions runs these tests on every push and pull request. Unit tests do not require Rubin access; a full notebook run requires the RSP.

## Future development

The proposed observability filtering, scientific prioritization, JS/HSH exposure models, and nightly scheduling workflow are documented in [ROADMAP.md](ROADMAP.md). These Target Selection 2.0 ideas are intentionally separated from the validated version 1 pipeline until their scientific and operational criteria are agreed upon.
