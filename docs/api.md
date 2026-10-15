# API reference

## Seasonal plan

The supported end-to-end seasonal interface is `target-selection-plan`:

```bash
target-selection-plan \
  --start-date 2026-10-14 \
  --end-date 2026-10-31 \
  --with-photometry \
  --with-lightcurves \
  --output-dir outputs/seasonal_plan
```

It is implemented in `target_selection.planning_cli`. Its main steps are:

1. `load_candidates` retrieves candidates from the selected TOM providers.
2. `add_ogle_event_parameters` adds available event parameters.
3. `collect_photometry` loads provider light curves when requested.
4. `save_visibility_products` calculates date and time visibility.
5. `evaluate_priority` assigns evidence-based categories and ranks.
6. The CLI writes selected targets, tables, plots and a seasonal PDF.

Use `target-selection-plan --help` for all options. `--providers` accepts a comma-separated list; `--minimum-altitude-deg`, `--minimum-visible-minutes`, and `--magnitude-max` control observing feasibility filters.

For a short date window, `--photometry-scope visible` downloads photometry only for candidates that pass the requested visibility thresholds on at least one night. All candidates still appear in the visibility and priority tables, but those outside the photometry scope lack newly retrieved light curves and may have less complete priority evidence. The default `all` requests photometry for every candidate. The effective arguments are saved in `run_config.json`.

## Individual task APIs

For notebook or script workflows, import the required functions from `target_selection.tasks`:

- `tasks.targets`: load, collect, merge and restrict target lists.
- `tasks.visibility`: evaluate visibility and save visibility plots/sequences.
- `tasks.lsst`: query Rubin coverage and compute or save photometry.
- `tasks.surveys`: import HSH inventories and inspect observed targets.
- `tasks.reports`: create target reports and light-curve products.

Provider integrations are implemented under `target_selection.sources`; shared planning functions are in `target_selection.planning`.

## Rubin reference-catalog command

For a large coordinate list use:

```bash
target-selection catalog --config configs/lastberu_dp2.toml
```

This is a distinct operation implemented in `target_selection.reference_catalog`. See [Reference catalog](reference-catalog.md).

## CLI commands

- `target-selection validate-config --config PATH`
- `target-selection list-sources`
- `target-selection catalog --config PATH`
- `target-selection visibility` for the interactive nightly visibility tool
- `target-selection-plan ...` for seasonal planning
