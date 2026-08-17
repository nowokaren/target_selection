# MOP target selection with Rubin coverage

This project combines candidate events from target providers, observing history from follow-up surveys, and contextual data from reference surveys to support follow-up planning. Current built-in sources include MOP, CASLEO/HSH, normalized CSV surveys such as CASLEO/JS, and Rubin Data Preview/Data Release collections. The v0.1 MOP + DP2 workflow remains available as a compatible low-level API.

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
- `mop_lsst.ipynb`: configuration-driven compatibility notebook for complete runs.
- `target_selection/`: normalized configuration, adapters, tasks, backend, workflow, and CLI.
- `target_selection/backend.py`: packaged Rubin-oriented backend used by the compatibility workflow.
- `target_selection_pipeline.py`: small compatibility shim for older imports.
- `configs/example.toml`: ordered configuration shared by CLI and compatibility notebook.
- `docs/`: architecture, extension, and product guides.
- `mop_photometry.py`: MOP photometry loading, caching, and preparation.
- `target_report.py`: graphical dashboard for each target.
- `release_photometry.py`: Rubin light-curve retrieval and persistent per-target photometry cache.
- `observatory_observations.py`: summaries of processed HSH observations by event and microlensing stage.
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

## Legacy low-level API

`run_target_selection(...)` remains available for advanced single-Rubin-release calls and dependency injection. To regenerate only one dashboard from an existing run and store it in a stable shared folder:

```python
from target_selection_pipeline import regenerate_target_report_from_run

report_path = regenerate_target_report_from_run(
    "outputs/2026-08-14_to_2026-08-15__obs_20-30_to_07-00",
    "OGLE-2025-BLG-1121",
    data_release="DP2",
    photometry_method="dia_forced_catalog",
    refresh_photometry=True,
)
print(report_path)
```

The helper reuses the run tables and persistent MOP/Rubin caches, and writes
`outputs/target_reports/<Target>_target_report.png`. Normal runs with
`target_reports = true` use the same shared folder and update existing PNGs.

- `max_workers=4`: query concurrency.
- `reuse_cache=True`: reuse previous downloads and queries.
- MOP candidates with `mag_now <= 0` are discarded before visibility selection, TAP, Butler, and report stages because this is an invalid current-magnitude value.
- `overwrite_target_plots=False`: keep current reports and resume an interrupted report stage; set `True` only to regenerate every report.
- `GENERATE_TARGET_REPORTS=False`: skip individual target dashboard PNGs when only aggregate MOP/HSH products are needed; set `True` to enable them.
- `target_plotter=False`: skip individual reports.
- `target_report_scope="all_queried"`: generate reports for every Rubin-query target; set `"visibility_selected"` to generate them only for targets that pass the local visibility filter on at least one requested night. PNGs are written to the shared `target_reports/` folder.
- `target_data_scope="with_data"`: default analysis mode; keep only targets with MOP photometry, active-survey images, or active-source photometry. Set `"all"` to disable this extra active-source cut; MOP candidates with no event parameters and no photometry remain diagnostic-only.
- `target_names=[...]`: optional explicit subset. When set, only these targets are retained from MOP, follow-up surveys, and user target lists.
- `visibility_target_scope="all_queried"`: choose the candidate pool for nightly visibility: every queried target on every requested night; set `"mop_daily"` to use only MOP candidates returned for each night. Both scopes are subsequently filtered by the local visibility cuts, so a separate `"visibility_only"` scope would be redundant.
- `previously_observed_providers=("HSH", "JS")`: choose which registered local surveys contribute previously observed targets; use a subset such as `("HSH",)`.
- `additional_targets=...`: add a pandas DataFrame or CSV path with `Target`, `RA_deg`, and `Dec_deg`; these targets join the Rubin query and, by default, nightly visibility evaluation.
- `sky_marker_encoding="split_color"`: encode magnitude and visits with two colored marker halves and two color bars.
- `sky_marker_encoding="color_size"`: encode magnitude with color and total visits with marker size.
- `show_coverage_background=True`: query and display a muted, low-resolution visit-density layer for the selected Data Release.
- `coverage_resolution=19`: control the coarse background grid; 19 approximately matches one LSSTCam field of view per cell.
- `generate_visibility_plots=True`: create one local visibility plot per requested night. If visibility plots, observing-selection summary, sky maps, and `target_report_scope="visibility_selected"` are all disabled, local visibility is skipped entirely.
- `visibility_minimum_altitude=40`: minimum altitude, approximately equivalent to airmass below 1.5.
- `visibility_minimum_observable_minutes=90`: required time above the altitude threshold during allocated astronomical night.
- `visibility_time_step_minutes=1`: temporal sampling of visibility curves.
- `visibility_observing_windows=None`: local assigned time, as one `("HH:MM", "HH:MM")` interval for all nights or ISO-date overrides; it limits both the visibility selection and shaded plots.
- `overwrite_visibility_plots=False`: reuse existing nightly PNG files. A changed visibility configuration, including allocated time, regenerates them automatically.
- `generate_release_photometry=False`: opt in to Rubin light-curve retrieval.
- `release_photometry_targets=[...]`: limit retrieval to named targets; `None` queries every selected target.
- `overwrite_release_photometry=False`: reuse completed per-target caches; set `True` to refresh them.
- `hsh_image_catalog=...`: optionally summarize previously processed HSH images for the selected targets.
- `max_current_magnitude=...`: optionally exclude MOP targets brighter than the configured faint-limit value (for example `18.5` keeps `mag_now <= 18.5`). Targets without a MOP magnitude are retained; excluded candidates are recorded in `tables/mop_targets_initial.csv` with `passes_magnitude_cut=False`.
- `hsh_peak_half_width_t_e=0.3` and `hsh_event_half_width_t_e=2.0`: configurable phase boundaries in Einstein-time units.
- `include_previously_observed=True`: add all targets already stored from HSH/JS to the Rubin coverage query, even when MOP does not mark them visible in this date range.
- `show_queried_targets=True`: print the complete source-labeled list sent to the Rubin coverage query.
- `generate_monitoring_report=True`: write a multipage monitoring PDF.
- `monitoring_report_plots_per_page=3`: number of light curves per PDF page.
- `monitoring_layers=("mop_photometry", "release_epochs", "hsh", "js")`: choose any subset of the default MOP light curve, selected-Data-Release epochs, HSH, and JS layers. HSH/JS shade all imported image epochs by default; darker daily coverage indicates more accumulated exposure time. When multiple epoch-only sources are shown, each receives a separate vertical lane. They automatically become photometric points when normalized local photometry is supplied.

When an HSH catalogue is supplied, the pipeline uses science images with successful astrometry (`imagetyp=object`, `astromet=yes`). It reports image counts and exposure time by filter, target-in-frame and catalogue-match status, airmass statistics, target-in-frame, catalogue-match, and usable-image fractions, and counts in pre-baseline, rise, peak, fall, and post-baseline phases. This is explicitly an astrometry/geometry quality summary: a photometric-quality metric will require target photometry, FWHM, and flux uncertainties. The local `hsh_data/` directory is ignored by Git.

Future JS data can be inserted through `TargetRegistry.import_survey_observations("JS", frame)`, where `frame` has at least `Target` and `mjd`; optional normalized fields include `band`, `exptime_s`, `usable`, `RA_deg`, and `Dec_deg`. Its epochs then enter both the target union and the monitoring PDF automatically, regardless of the `usable` flag. The lower-level `create_monitoring_report(..., observatory_photometry=...)` also accepts normalized HSH/JS photometry (`Target`, `provider`, `mjd` or `Timestamp`, magnitude, optional error and band); when present, points replace that provider's epoch shading.

Reference photometry is selectable per Rubin collection with `photometry_method` in the reference-survey definition. `coadd_forced` (the DP2 default) runs Rubin `ForcedMeasurementTask` with the `base_PsfFlux` plugin at each target coordinate on every available deep coadd. This provides one PSF measurement per target and coadd band without requiring a DIA detection or unavailable individual calibrated visit images. `dia_forced_catalog` instead matches each target to the nearest unambiguous `DiaObject` and retrieves its published `ForcedSourceOnDiaObject` time series through TAP. In either mode, the resulting points are added to `lightcurves.pdf`, target reports, and `tables/release_forced_photometry.csv`; per-target cache files are stored in `outputs/rubin_photometry/<Target>.csv`.

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

### Coordinate provenance and precision

MOP event-page coordinates are authoritative. The pipeline preserves the original sexagesimal values in `mop_ra` and `mop_dec`, stores their full floating-point ICRS conversion in `mop_ra_deg` and `mop_dec_deg`, and copies those values without rounding into the canonical `RA_deg` and `Dec_deg` used by visibility, TAP, Butler, and photometry calculations. `coordinate_source`, `coordinate_priority`, `coordinate_offset_arcsec`, and `coordinate_was_overridden` make coordinate replacement auditable in the run tables.

HSH `CRVAL1`/`CRVAL2` values are image WCS reference points, not event coordinates. They are retained only as `pointing_ra_deg` and `pointing_dec_deg` observation metadata and can never replace a MOP position. Caches and report versions include coordinates, so upgrading to this release automatically invalidates products calculated at a different position. Coordinate-copy notebook cells display three decimal places in sexagesimal notation; calculations always use the unrounded numeric values.

### Partial-night allocations

Use `visibility_observing_windows` when only part of a night is assigned. The ISO date identifies the **evening on which the night begins**; times before noon belong to the following calendar day in the observatory's local timezone. The configuration is evaluated once per night and its mask is reused for every target.

```python
allocated_time = {
    "default": ("18:00", "06:00"),
    "2026-08-10": ("21:30", "03:00"),
    "2026-08-11": [("18:00", "23:00"), ("02:00", "06:00")],
    "2026-08-12": [],  # No time allocated.
}

combined, paths = run_target_selection(
    start_date="2026-08-09",
    end_date="2026-08-12",
    data_release="DP2",
    visibility_observing_windows=allocated_time,
)
```

A simpler `visibility_observing_windows=("20:00", "02:00")` applies the same interval to every night. Target acceptance requires the configured time above the altitude limit **inside both the allocated interval and astronomical night**. The availability mask is shaded in the nightly and sequence plots, and `visibility_selection.csv` records the resolved windows and available astronomical minutes. MOP's upstream daily visibility query is intentionally unchanged; this setting controls local observing feasibility and the visibility products.

When only the schedule changes, set `OVERWRITE_VISIBILITY_PLOTS=True` while leaving the other overwrite settings `False`, then rerun the main notebook call. It creates the corresponding schedule-named directory and regenerates visibility products while reusing compatible MOP, TAP, Butler, and report caches.

The automatic visibility plots contain only targets passing the configurable altitude and observable-time criteria. To make plots from a manually reviewed final selection without applying another filter:

When more than 20 targets pass on one night, the automatic and manually selected nightly plots are split into numbered PNGs with at most 20 targets each. They are grouped first as `MOP-only`, then `HSH/JS-observed`, so the output names and titles make the source clear. If two source groups must appear in a single plot, MOP-only curves are solid and HSH/JS-observed curves are dashed; these styles are explained in the legend. Pass `max_targets_per_plot=...` to either save function to change the limit.

```python
from visibility_plotter import (
    plot_selected_visibility,
    compile_visibility_plot_pages,
    save_selected_visibility_plots,
)

plot_selected_visibility(
    selected_for_one_night, "2026-08-05", "final_visibility.png",
    observing_windows=allocated_time,
)
save_selected_visibility_plots(
    selected_for_all_nights, "final_visibility_plots",
    observing_windows=allocated_time,
)

# Compile the already-generated nightly PNGs, preserving source-aware parts.
compile_visibility_plot_pages(
    "final_visibility_plots",
    "visibility_sequence.pdf",
)
```

For explicit selection outside the pipeline, use `select_nightly_targets(...)`. Set `return_all=True` to retain rejected targets and their reasons. `compile_visibility_plot_pages(...)` creates one PDF page per observing date from the existing nightly PNGs. It preserves every source-aware part and adds a date box to each page.

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

Keep `reuse_cache=True` and every `OVERWRITE_*` option `False`, then rerun the same `run_target_selection(...)` call. Completed MOP, TAP-coverage, forced-photometry, and target-report stages are reused independently, even if the interruption occurred before the final manifest was written. Existing PNG reports and confirmed no-coadd targets are skipped; the run continues with the remaining reports. When the observing schedule changes, compatible TAP coverage and target-report PNGs are reused from the date-only run or shared cache.

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
