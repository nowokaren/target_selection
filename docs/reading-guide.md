# How to read the codebase

This guide gives the recommended order for understanding the current package and seasonal planning workflow.

## 1. Start with seasonal planning

Read [How the seasonal plan works](how-it-works.md) and [Current architecture](architecture.md). They describe candidate loading, photometry, visibility, priority assessment, and generated products.

## 2. Learn the normalized contracts

Before reading provider implementations, read the [data contracts](contracts/README.md).
They define the row grain, canonical column names, units, missing-value rules
and provenance requirements for targets, observations, visibility, coverage
and priority products.

In particular, `targets.csv` contains preselected candidates, not the final
scientific observing decision. `observations.csv` combines epochs with any
available photometry, while visibility and priority remain separate derived
tables.

## 3. Learn the public entry points

Read the [API reference](api.md), then inspect `target_selection/planning_cli.py`, `target_selection/planning.py`, `target_selection/prioritization.py`, and the relevant modules under `target_selection/tasks/`.

## 4. Understand sources and adapters

Read [Catalog sources and adapters](source-adapters.md), then inspect:

- `configs/sources/*.toml`: paths, column mappings, parsers and capabilities;
- `target_selection/sources/catalog_adapters.py`: adapter protocol and registry;
- `target_selection/collection_access.py`: `CollectionContext`, the object-oriented entry point for one Rubin collection.

Use `target_io.list_sources()`, `target_io.describe_source()`, `target_io.list_adapters()` and `target_io.describe_adapter()` while exploring.

## 5. Follow the relevant example

The notebooks are independent; choose the one that matches the task. Start with `notebooks/workflows/seasonal_observing_plan.ipynb` for the complete observing workflow, `notebooks/tutorials/task_api_examples.ipynb` for individual calls, or `notebooks/tutorials/catalog_loading_and_selection.ipynb` for configured catalogs. The [notebook guide](usage-notebooks.md) lists the source-specific and diagnostic examples.

## 6. Read the implementation behind each operation

For visibility, use the public module `target_selection.visibility`: `evaluate`, `plots` and `sequence`. Its implementation is currently delegated to `target_selection/tasks/visibility.py`.

For Rubin access, inspect `target_selection/rsp_access.py` and `CollectionContext`. These methods select the appropriate dataset/table for the chosen release and pass the query to RSP/Butler.

For photometry, inspect `target_selection/sources/lsst.py` and the light-curve helpers in `target_selection/tasks/reports.py`.

## 7. Reference-catalog analysis

For a large coordinate list, inspect `target_selection/reference_catalog.py` and its dedicated [guide](reference-catalog.md).

## 8. Verify results and provenance

For every analysis, inspect the source configuration, selected-target table, cache paths, output tables, plots, cutouts and report. Preserve source, provider, collection and configuration information with the results.
