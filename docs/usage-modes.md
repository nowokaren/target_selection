# Ways to use the package

## Seasonal planning

Use `target-selection-plan` or `python -m target_selection.planning_cli` to collect current targets, photometry (optional), visibility and evidence-based priority, then generate the selected-target list and seasonal report. Its implementation is in `target_selection.planning`, `target_selection.prioritization` and `target_selection.planning_cli`.

## Notebook or individual task

Use `notebooks/seasonal_observing_plan.ipynb` for an interactive seasonal plan, or `notebooks/task_api_examples.ipynb` to run selected collection and reporting tasks. Reusable task functions are grouped under `target_selection.tasks`.

## Rubin reference catalog

Use `target-selection catalog --config ...` for large coordinate-list matching and coadd-property enrichment. This is a distinct command implemented by `target_selection.reference_catalog`.

These entry points share provider integrations and the central cache.
