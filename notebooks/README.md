# Notebooks

All project notebooks live under this directory, grouped by purpose. Start with the workflow or tutorial that matches the task; the specialized MOP–DP2 analysis notebooks are grouped together.

## Analysis

| Notebook | Purpose |
|---|---|
| `analysis/priority_assessment.ipynb` | Inspect per-target event-stage evidence and compare switchable priority criteria with the shared evaluator |
| `analysis/dp2_dia_lightcurve_validation.ipynb` | Exploratory DiaObject matches and forced-source light curves |
| `analysis/mop_dp2/00_mop_snapshot_and_enrichment.ipynb` | Snapshot and enrich the MOP event catalog |
| `analysis/mop_dp2/01_mop_dp2_coverage_and_reports.ipynb` | MOP–DP2 coverage, quality, matches, and reports |
| `analysis/mop_dp2/02_lsdb_hats_mop_dp2_comparison.ipynb` | Independent spatial matching with LSDB/HATS |
| `analysis/mop_dp2/03_explore_mop_dp2_matches.ipynb` | Explore saved MOP–DP2 match tables without repeating remote queries |

## Tutorials

| Notebook | Purpose |
|---|---|
| `tutorials/catalog_loading_and_selection.ipynb` | Configured catalog sources, filters, and enrichment |
| `tutorials/task_api_examples.ipynb` | Small examples of the public task API |
| `tutorials/ogle_data_access.ipynb` | OGLE EWS catalog and photometry access |
| `tutorials/opm_data_access.ipynb` | Public and authenticated OPM access |
| `tutorials/lsst_coverage_and_products.ipynb` | Rubin collection matching and products |
| `tutorials/rsp_access_examples.ipynb` | Rubin RSP access helpers |
| `tutorials/mop_lsst.ipynb` | Interactive guide to the wider target-selection API |

## Workflows

| Notebook | Purpose |
|---|---|
| `workflows/tom_survey_visibility_lightcurves.ipynb` | TOM/local-survey candidates, configurable visibility cut, tables, and inline light curves; no Rubin/LSST calls |
| `workflows/seasonal_observing_plan.ipynb` | End-to-end seasonal plan with TOM photometry, visibility, and priority assessment |
| `workflows/hsh_daily_target_selection.ipynb` | Daily HSH feasibility and priority assessment |

## Visualization and reports

| Notebook | Purpose |
|---|---|
| `visualization/hsh_dp2_lightcurves_and_reports.ipynb` | HSH batch light curves and DP2 reports |
| `visualization/js_hsh_meeting_lightcurves.ipynb` | Meeting report from MOP/OGLE curves and JS/HSH observation epochs |
| `visualization/casleo_lsst_presentation.ipynb` | CASLEO observed targets with TOM photometry and selected DP2 light curves |

Historical notebooks live under `notebooks/archive/`; they preserve earlier methods or results and are not current execution guides. Generated run products remain under the single repository-level `outputs/` directory. See [`docs/how-it-works.md`](../docs/how-it-works.md) for the seasonal workflow and [`analyses/mop_dp2/README.md`](../analyses/mop_dp2/README.md) for the MOP–DP2 study.
