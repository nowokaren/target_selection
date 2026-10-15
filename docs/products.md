# Planning products

The seasonal planning CLI writes products to the directory passed with `--output-dir` (default `outputs/seasonal_plan`).

## Core products

| Product | Purpose |
|---|---|
| `run_config.json` | Effective CLI arguments and provider selection for this run. |
| `targets_loaded.csv` | Candidate list after provider loading and event-parameter enrichment. |
| `observations.csv` | Collected photometry when `--with-photometry` is enabled. |
| `targets_after_magnitude.csv` | Candidate list after the optional magnitude limit. |
| `selected_targets.csv` | Feasible targets in schedulable priority categories, ordered by priority. |
| `visibility/` | Visibility summaries and plots for the requested date range. |
| `tables/priority_assessment.csv` | Evidence fields, category and rank for evaluated targets. |
| `seasonal_observing_plan.pdf` | Combined plan with summary tables, visibility and optional light curves/cutouts. |

Optional products include per-target light-curve plots (`--with-lightcurves`), Rubin coverage and image cutouts (`--with-lsst-cutouts`), and a sky map of selected targets.

When `--with-photometry` is enabled, `targets_loaded.csv` includes `photometry_requested` to show which candidates were within the requested photometry scope. A successful request does not guarantee measurements from a remote provider; inspect `observations.csv` for actual coverage.

For detailed field definitions and criteria, see [How it works](how-it-works.md) and `target_selection/prioritization.py`.

## Reference-catalog products

The separate `target-selection catalog` command writes catalog-enrichment tables, coverage summaries, and optional cutout products. See [Reference catalog](reference-catalog.md).
