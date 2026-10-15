# How the seasonal plan works

The seasonal planner combines current candidate information with available follow-up and reference data, then evaluates scientific priority and observing feasibility.

## Stages

1. **Load candidates.** `target_selection.planning.load_candidates` collects events from configured TOM providers and can reuse shared cache data.
2. **Enrich event data.** OGLE event parameters and optional provider photometry are attached to the candidate table.
3. **Evaluate visibility.** The planner computes when each target is observable at the selected observatory, including altitude and duration metrics.
4. **Evaluate priority.** `target_selection.prioritization.evaluate_priority` assigns evidence-based categories and ranks. Visibility and magnitude are feasibility inputs; they do not replace scientific classification.
5. **Write products.** The planner saves candidate and selected-target tables, visibility plots, priority summaries, optional light curves and Rubin cutouts, and a PDF.

## Run products

A seasonal run can include:

- `targets_loaded.csv` and `targets_after_magnitude.csv`;
- `observations.csv` when photometry is enabled;
- `selected_targets.csv`;
- visibility and priority tables/plots;
- `lightcurves/` when `--with-lightcurves` is used;
- Rubin coverage and `cutouts/` when `--with-lsst-cutouts` is used;
- `seasonal_observing_plan.pdf`.

Run products are saved to `--output-dir`. Reusable downloads and query results are shared under `outputs/cache/`.
