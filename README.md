# Target selection and follow-up planning

This repository collects microlensing candidates and observing history, retrieves survey photometry and Rubin coverage, evaluates visibility and scientific priority, and produces seasonal planning products.

## Current entry points

- TOM and local-survey visibility workflow, with inline candidate tables and light curves: [`notebooks/workflows/tom_survey_visibility_lightcurves.ipynb`](notebooks/workflows/tom_survey_visibility_lightcurves.ipynb).

- Seasonal planning: `target-selection-plan --start-date YYYY-MM-DD --end-date YYYY-MM-DD` or `python -m target_selection.planning_cli ...`.
- Reproducible Rubin reference-catalog enrichment: `target-selection catalog --config configs/lastberu_dp2.toml`.
- Small individual tasks: functions under `target_selection.tasks` and source-specific APIs under `target_selection.sources`.
- Interactive nightly visibility: `target-selection visibility` (prompts for the targets and observing window).

The plan uses the shared evidence-based evaluator in `target_selection.prioritization`. It writes its tables, plots and PDF to the selected output directory. Provider downloads and reusable query results are stored centrally under `outputs/cache/`, with canonical paths defined in `target_selection.cache_paths`.

## Project layout

- `target_selection/`: supported Python package and command-line tools.
- `notebooks/`: current observing guides and scientific notebooks; see its README for their distinct purposes.
- `analyses/mop_dp2/`: MOP–DP2 study notes and its analysis environment; the notebooks are under `notebooks/analysis/mop_dp2/`.
- `configs/`: source and Rubin run configuration.
- `data/`: local source inputs and observing inventories.
- `outputs/cache/`: shared reusable provider/cache data (ignored by Git).
- `outputs/`: generated run products and reports (ignored by Git).
- `docs/`: current API, planning, cache and operations documentation.

## Install without Rubin/LSST

```bash
git clone --branch v0.2.2 --depth 1 https://github.com/nowokaren/target_selection.git
cd target_selection
python -m pip install -e '.[credentials]'
python -m pip install jupyterlab
```

Open `notebooks/workflows/tom_survey_visibility_lightcurves.ipynb` in Jupyter to load TOM
and local-survey candidates, apply the visibility cut, and display the visible
target light curves. Copy `configs/local_hsh.toml` and set paths for the local
HSH image inventory, event-coordinate catalog, and (optionally) its photometry
CSV. Local survey files are user data and are not included in the Git clone.
For the full setup, including TOM logins and column maps for new surveys, follow
the [TOM and local-survey quickstart](docs/quickstart_toms_surveys.md).

Rubin/LSST is optional. On a machine without its client packages, Rubin-only
requests print a warning and return no Rubin products; TOM, local-survey,
visibility, and plotting workflows remain available. TOMs that require login
still need their own account credentials. For setup steps, see the [TOM and
local-survey quickstart](docs/quickstart_toms_surveys.md), including HSH and
generic CSV column mappings.

To combine TOM candidates with HSH image epochs and a local HSH photometry CSV,
copy `configs/local_hsh.toml`, set the input file paths and current dates, and
store each TOM account as described in the quickstart. Validate and run:

```bash
target-selection validate-config --config configs/local_hsh.toml
target-selection plan --config configs/local_hsh.toml --with-photometry --with-lightcurves
```

Or launch the interactive workflow with
`jupyter lab notebooks/workflows/tom_survey_visibility_lightcurves.ipynb`.

`photometry_path` can be a filename beside `inventory_path`. Configure
nonstandard headers with `photometry_column_map`. BHTOM accepts
`classification`, `query`, `max_pages`, and `base_url`; OPM accepts
`minimum_altitude_deg`, `observing_windows`, `sample_minutes`, `base_url`, and
`timeout`.
Configure credentials with `target-selection auth set bhtom` or
`target-selection auth set opm` when needed.

For development checks, install `python -m pip install -e ".[test]"` and run
`pytest`. Unit tests use local fixtures and do not make remote queries.
