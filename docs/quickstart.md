# Quickstart

## Seasonal observing plan

Install the package and test dependencies, then run the planning CLI:

```bash
python -m pip install -e ".[test]"
target-selection-plan --start-date 2026-10-14 --end-date 2026-10-31 \
  --with-photometry --with-lightcurves --output-dir outputs/seasonal_plan
```

The CLI collects candidates from registered TOMs (or a selected subset with `--providers`), obtains event parameters, calculates visibility, evaluates scientific priority and writes selected targets, tables, plots and a PDF. Add `--with-lsst-cutouts` to request Rubin images. Use `--help` to see all options.

For a focused plan spanning a few nights, add `--photometry-scope visible` to fetch light curves only for candidates visible during the requested dates. The full candidate list remains in the visibility and priority products.

Reusable downloads and query results live in the shared `outputs/cache/` tree. Products from this run live in `outputs/seasonal_plan/`. See [data and cache](data-and-cache.md) for the layout.

## Run individual tasks

For interactive work, use `notebooks/task_api_examples.ipynb` or call the functions in `target_selection.tasks` directly. The seasonal notebook is `notebooks/seasonal_observing_plan.ipynb`. See the [notebook inventory](usage-notebooks.md).

## Rubin catalog enrichment

For a large coordinate catalog, use the separate reference-catalog command:

```bash
target-selection catalog --config configs/lastberu_dp2.toml
```

See [reference catalog](reference-catalog.md) for prerequisites and output details.
