# Notebooks

All active general notebooks live here. Their names describe independent tasks; there is no required sequence or parallel `v2` collection. Execute only the cells needed for the question at hand. Provider, Rubin TAP, and Butler calls may require credentials or an RSP session and can generate large products under `outputs/`.

| Notebook | Purpose |
|---|---|
| `catalog_loading_and_selection.ipynb` | Configured catalog sources, filters, and explicit enrichment |
| `task_api_examples.ipynb` | Independent target, visibility, Rubin, and report tasks |
| `lsst_coverage_and_products.ipynb` | Collection API for DP2 coverage, properties, cutouts, and reports |
| `ogle_data_access.ipynb` | OGLE EWS source inspection and normalized photometry |
| `opm_data_access.ipynb` | Public OPM ranking, authenticated pages, and source-format inspection |
| `hsh_daily_target_selection.ipynb` | Daily HSH feasibility and priority assessment |
| `seasonal_observing_plan.ipynb` | Seasonal plan: visibility first, then TOM photometry and latest I-band telescope limits; priority labels only order the final table |
| `tom_survey_visibility_lightcurves.ipynb` | TOM/local-survey candidates, configurable visibility cut and nightly table, inline visibility and light-curve plots; no Rubin/LSST access |
| `priority_assessment.ipynb` | Inspect per-target event-stage logic beside light curves, toggle one priority criterion per cell, compare intermediate decisions with the current implementation; uses saved run data without network queries |
| `hsh_dp2_lightcurves_and_reports.ipynb` | HSH batch coverage, DP2 DIA photometry, and per-target reports |
| `dp2_dia_lightcurve_validation.ipynb` | Exploratory DiaObject match diagnostics and forced-source curves |
| `js_hsh_meeting_lightcurves.ipynb` | Meeting report from MOP curves and JS/HSH observation epochs |
| `casleo_lsst_presentation.ipynb` | CASLEO observed targets with TOM photometry: choose DP2 matches and plot published Source/ForcedSource/DIA catalog curves |

The MOP–DP2 study stays in `analyses/mop_dp2/notebooks/`. Historical notebooks recovered from checkpoints are documented in `archive/README.md`; they are not current workflows.

The seasonal notebook's inputs, function calls, filter gates, and output products are mapped in [`docs/how-it-works.md`](../docs/how-it-works.md).
