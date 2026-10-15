# Target selection and follow-up planning

This repository collects microlensing candidates and observing history, retrieves survey photometry and Rubin coverage, evaluates visibility and scientific priority, and produces seasonal planning products.

## Current entry points

- Seasonal planning: `target-selection-plan --start-date YYYY-MM-DD --end-date YYYY-MM-DD` or `python -m target_selection.planning_cli ...`.
- Reproducible Rubin reference-catalog enrichment: `target-selection catalog --config configs/lastberu_dp2.toml`.
- Small individual tasks: functions under `target_selection.tasks` and source-specific APIs under `target_selection.sources`.
- Interactive nightly visibility: `target-selection visibility` (prompts for the targets and observing window).

Example seasonal run:

```bash
target-selection-plan \
  --start-date 2026-10-14 \
  --end-date 2026-10-31 \
  --with-photometry \
  --with-lightcurves \
  --output-dir outputs/seasonal_plan
```

The plan uses the shared evidence-based evaluator in `target_selection.prioritization`. It writes its tables, plots and PDF to the selected output directory. Provider downloads and reusable query results are stored centrally under `outputs/cache/`, with canonical paths defined in `target_selection.cache_paths`.

## Project layout

- `target_selection/`: supported Python package and command-line tools.
- `notebooks/`: current observing guides and scientific notebooks; see its README for their distinct purposes.
- `analyses/mop_dp2/`: MOP–Rubin analysis notebooks and products.
- `configs/`: source and Rubin run configuration.
- `data/`: local source inputs and observing inventories.
- `outputs/cache/`: shared reusable provider/cache data (ignored by Git).
- `outputs/`: generated run products and reports (ignored by Git).
- `docs/`: current API, planning, cache and operations documentation.

## Setup and tests

```bash
python -m pip install -e ".[test]"
pytest
```

Rubin queries and some provider integrations require the corresponding RSP access or credentials. Unit tests use local fixtures and do not run remote queries.
