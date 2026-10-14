# MOP target selection with Rubin coverage

This project combines candidate events from target providers, observing history from follow-up surveys, and contextual data from reference surveys to support follow-up planning. Current built-in sources include MOP, CASLEO/HSH, normalized CSV surveys such as CASLEO/JS, and Rubin Data Preview/Data Release collections. The modular `data_source` API also supports OGLE, OPM, and BHTOM. The v0.1 MOP + DP2 workflow remains available as a compatible low-level API.

## Recommended interface

Select active sources in `configs/example.toml`; the frequently changed dates, observatory, sources, and scientific cuts are at the top. Source paths, cache policy, and runtime details are grouped below.

```bash
target-selection validate-config --config configs/example.toml
target-selection list-sources
target-selection run --config configs/example.toml
```

For large coordinate catalogs that do not require dates or local visibility, use the dedicated enrichment workflow:

```bash
target-selection catalog --config configs/lastberu_dp2.toml
```

It performs chunked uploaded-table TAP cross-matches, queries Butler directly for coadd datasets at target positions, optionally samples Rubin coadd-property maps, and generates a resource-limited set of prioritized 20-arcsec cutout grids. See `docs/reference-catalog.md` and `notebooks/lastberu_reference_catalog.ipynb`.

For interactive work, the recommended entry point is the task notebook
[`notebooks/task_api_examples.ipynb`](notebooks/task_api_examples.ipynb). It
uses explicit Python variables and lets you run only the tasks you need. The
configuration workflow remains available for reproducible CLI runs:

```python
from target_selection import load_config, run_analysis

config = load_config("configs/example.toml")
result = run_analysis(config)
dp2_targets = result.runs["rubin_dp2"].targets
```

The three source roles are target providers (MOP and future OMP), follow-up surveys (CASLEO/HSH and CASLEO/JS), and reference surveys (Rubin DP1/DP2). See `docs/architecture.md`, `docs/adding_sources.md`, and `docs/products.md`.



## Project files

- `notebooks/task_api_examples.ipynb`: recommended task-by-task interactive entry point.
- `analyses/mop_dp2/`: notebooks, outputs and documentation for the complete MOP–DP2 analysis, including LSDB/HATS matching.
- `mop_lsst.ipynb`: configuration-driven compatibility notebook for complete runs.
- `target_selection/`: normalized configuration, source adapters, planning, reporting, and CLI modules.
- `configs/example.toml`: ordered configuration shared by CLI and compatibility notebook.
- `docs/`: architecture, extension, and product guides.
- `target_selection/mop_photometry.py`: MOP photometry loading, caching, and preparation.
- `target_selection/target_report.py`: graphical dashboard for each target.
- `target_selection/release_photometry.py`: Rubin light-curve retrieval and persistent per-target photometry cache.
- `target_selection/observatory_observations.py`: summaries of processed HSH observations by event and microlensing stage.
- `target_selection/observing_selection_summary.py`: visibility and event-stage planning summaries.
- `target_selection/visibility_plotter.py`: local nightly altitude, airmass, twilight, and Moon plots.
- `target_selection/data_release_config.py`: DP0.1, DP0.2, DP1, and DP2 configuration.

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

## Notebooks

For independent tasks, open `notebooks/task_api_examples.ipynb` and uncomment only
the sections required for the analysis. It demonstrates target collection,
source merging, visibility, Rubin coverage/photometry, sky maps, target reports,
and light-curve PDFs.

For a complete configuration-driven run, open `mop_lsst.ipynb`, edit
`configs/example.toml`, restart the kernel, and select **Run All**. This notebook
loads `AnalysisConfig`, calls `run_analysis(config)`, and displays the products
of the first selected reference survey. Additional reference-survey results
remain available in `analysis_result.runs`.

The first run may take time because it refreshes the configured providers and surveys; later runs reuse the persistent registry and provider-specific caches.

## Current planning and reporting APIs

Use `notebooks/task_api_examples.ipynb` for task-by-task analysis, or run the
configured workflow with `target-selection run --config configs/example.toml`.
The reusable implementations are in `target_selection/`; the old root-level
workflow API is no longer part of the package. See [API](docs/api.md) for the
supported Python entry points.

## Outputs

For DP2, one run produces:

```text
outputs/
├── mop_photometry/                     # One MOP photometry CSV per target
├── mop_event_cache/                    # MOP event-data cache
├── _cache/release_coverage/            # Shared TAP coverage cache across schedules
├── target_reports/                     # Shared individual target dashboards
│   ├── <Target>_target_report.png
│   └── report_versions.json            # Report cache control
└── YYYY-MM-DD_to_YYYY-MM-DD__obs_HH-MM_to_HH-MM/
    ├── manifest.json                   # Run configuration and metadata
    ├── plot_errors.csv                 # Isolated report errors
    ├── tables/
    │   ├── visible_targets_daily.csv   # Visibility by date
    │   ├── visible_summary.csv         # Visibility + MOP parameters
    │   ├── mop_candidates_without_data.csv # MOP-visible candidates excluded for missing event data
    │   ├── visibility_target_summary.csv # Per-target local visibility pass/fail summary
    │   ├── queried_targets.csv          # Complete source-labeled Rubin query list
    │   ├── coverage_raw.csv            # Rubin visit/detector rows
    │   ├── coverage_summary.csv        # Coverage and visits by band
    │   ├── observing_selection_summary.csv # Visibility-passing targets, stage coverage, and parameters
    │   ├── observing_selection_summary.png # Bright-to-faint observing planning table
    │   ├── release_forced_photometry.csv # Optional PSF fluxes, magnitudes, flags, and status
    │   ├── release_forced_photometry_metadata.json # Cache/version metadata
    │   ├── release_visit_centers.csv    # Optional cached background input
    │   ├── hsh_observation_summary.csv  # Optional HSH image, stage, and astrometry summary
    │   ├── combined_targets.csv        # Complete MOP + Rubin + optional HSH table
    │   ├── target_summary.csv          # Compact scientific summary
    │   └── target_summary.png          # Visual summary table
    ├── sky_plots/                      # Full-sky and bulge maps
    ├── monitoring_reports/
    │   └── lightcurves.pdf  # Optional MOP curves with DP2/HSH/JS epochs and t0 ± 2tE zooms
    └── visibility_plots/               # Automatically filtered nightly plots
        └── visibility_selection.csv    # Metrics, decisions, and rejection reasons
```

The run directory includes the default observing window. A schedule with per-night overrides uses `__obs_<default>_varied-<hash>`; the full schedule remains in `manifest.json`. Other Data Releases add a directory named after the release. The persistent local registry is stored at `outputs/target_database/target_selection.sqlite`; it tracks target identities, HSH/JS epochs, and daily provider refresh state. Raw MOP, Rubin, and local-survey source files remain in their respective caches rather than being duplicated in the database. The release-coverage cache is shared, so changing only the observing schedule does not repeat compatible TAP coverage queries.

### Which table should I use?

- Exact targets sent to the Rubin coverage query, including their source and visibility-pass flag: `queried_targets.csv`.
- Per-target local visibility metrics, current MOP magnitude, maximum observable time, pass/fail decision, and rejection reason: `visibility_target_summary.csv`. By default it covers the complete queried union (MOP-visible, configured registered-survey targets, and optional user targets).
- Per-night visibility metrics and decisions: `visibility_plots/visibility_selection.csv`; use `observable_minutes` and `mag_now` to reproduce each nightly legend label.
- Final target list and all properties: `combined_targets.csv`.
- HSH-only observation, stage, and astrometry/geometry-quality summary: `hsh_observation_summary.csv`.
- Visits by filter, MOP points, `t_E`, `t_0`, and `u_0`: `target_summary.csv`.
- Bright-to-faint targets that pass the visibility filter, including best visible interval and stage-resolved MOP/HSH/Data Release coverage: `observing_selection_summary.csv` and `.png`. MOP exposure durations are not published by the source, so its stage cells are point counts; Data Release time uses the profile's nominal unique-visit exposure.
- Unaggregated visit/detector rows: `coverage_raw.csv`.
- Complete MOP photometry: `outputs/mop_photometry/<Target>.csv` (the former `outputs/photometry` cache is migrated lazily when used).
- Complete optional Rubin forced photometry: `tables/release_forced_photometry.csv`.

The `n_visits_<filter>` counts represent unique `visitId` values. An individual report is created only when at least one coadd contains the target position. Reports display deep coadds in the image panels. When enabled, the lower light-curve panel includes forced PSF measurements on those coadds; the coadd images themselves remain in the image panels.

## Resuming an interrupted run

Keep `reuse_cache=True` and every `OVERWRITE_*` option `False`, then rerun the same configured workflow call. Completed MOP, TAP-coverage, forced-photometry, and target-report stages are reused independently, even if the interruption occurred before the final manifest was written. Existing PNG reports and confirmed no-coadd targets are skipped; the run continues with the remaining reports. When the observing schedule changes, compatible TAP coverage and target-report PNGs are reused from the date-only run or shared cache.

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


## Interactive visibility reports

The `visibility` command lets you choose a subset of targets and generate nightly
altitude/visibility plots plus a PDF from the terminal:

```bash
target-selection visibility \
  --targets data/hsh_data/hsh_objects.csv \
  --start-date 2026-09-20 \
  --end-date 2026-09-25 \
  --observatory "El Leoncito" \
  --minimum-altitude 40 \
  --minimum-minutes 90 \
  --observing-window 20:30 06:00 \
  --output outputs/my_visibility
```

The command first asks for a text fragment, then opens a multi-selection list.
Use the arrow keys to move, Space to select/deselect, Enter to confirm, or click
with the mouse in a compatible terminal. Install the optional interactive extra
if needed:

```bash
python -m pip install -e '.[interactive]'
```

For a fully reproducible run without the selector:

```bash
target-selection visibility \
  --targets data/hsh_data/hsh_objects.csv \
  --names OGLE-2025-GD-0006,OGLE-2025-BLG-0451 \
  --start-date 2026-09-20 \
  --end-date 2026-09-25 \
  --non-interactive \
  --output outputs/selected_visibility
```

Alternatively, pass a CSV containing one `Target` column with
`--selection-file`. Add `--match OGLE-2025` to pre-filter the interactive list.
By default the report plots the selected targets on every requested night, even
if they do not pass the visibility cut. Add `--only-visible` to plot only the
target/night rows that pass the configured altitude and minimum-time criteria.

Each run writes `selected_targets.csv`, `visibility_selection.csv`,
`visibility_summary.csv`, `visibility_config.json`, individual nightly PNGs,
and `visibility_report.pdf`.
