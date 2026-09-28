# How to read the codebase

This guide gives the recommended order for understanding the current modular version. Older notebooks and large orchestration modules remain useful as historical references, but they are not the first place to start.

## 1. Start with the scientific workflow

Read the [integrated workflow diagram](v2-architecture.md) first. It shows four stages: configure sources and capabilities; load and select targets; choose analysis capabilities; and write the requested products.

The key idea is that loading targets and producing reports are separate decisions. The same selected catalog can be used for visibility, Rubin matching, photometry, or several analyses.

## 2. Learn the public entry points

Read [Version 2 API](v2-api.md), then inspect these modules in order:

1. `target_selection/target_io.py`: load configured sources and add explicit optional data.
2. `CollectionContext` methods: request visit/coadd coverage, property-map values and images for one collection.
3. `target_selection/report.py`: create summaries, distributions, sky maps and the combined PDF.

The public API is intentionally small. Source-specific queries belong to adapters or collection access. The older `match.py` and `images.py` wrappers are compatibility helpers, not required concepts for new notebooks.

## 3. Understand sources and adapters

Read [Catalog sources and adapters](source-adapters.md), then inspect:

- `configs/sources/*.toml`: paths, column mappings, parsers and capabilities;
- `target_selection/sources/catalog_adapters.py`: adapter protocol and registry;
- `target_selection/collection_access.py`: `CollectionContext`, the object-oriented entry point for one Rubin collection.

Use `target_io.list_sources()`, `target_io.describe_source()`, `target_io.list_adapters()` and `target_io.describe_adapter()` while exploring.

## 4. Follow one complete example

Run the notebooks in this order:

1. `notebooks/v2/01_catalog_loading_and_selection.ipynb`
2. `notebooks/v2/02_seasonal_visibility_selection.ipynb`
3. `notebooks/v2/03_lsst_matching_and_products.ipynb`
4. `notebooks/v2/04_photometry_and_lightcurves.ipynb`
5. `notebooks/v2/05_developer_add_an_adapter.ipynb`
6. `notebooks/v2/06_microlensing_followup_prioritization.ipynb`

The first three cover the core HSH/JS seasonal workflow. The fourth covers the photometry branch, and the fifth shows where a provider such as OGLE belongs.

## 5. Read the implementation behind each operation

For visibility, use the public module `target_selection.visibility`: `evaluate`, `plots` and `sequence`. Its implementation is currently delegated to `target_selection/tasks/visibility.py`.

For Rubin access, inspect `target_selection/rsp_access.py` and `CollectionContext`. These methods select the appropriate dataset/table for the chosen release and pass the query to RSP/Butler.

For photometry, inspect `target_selection/sources/lsst.py` and the light-curve helpers in `target_selection/tasks/reports.py`.

## 6. Read historical implementation last

When reproducing or auditing an older analysis, inspect:

- `reference_catalog.py`: reference-catalog coverage, coadd-property sampling and prioritized cutouts;
- `workflow.py`: former large analysis coordinator;
- `backend.py`: older end-to-end target-selection backend;
- `tasks/pipeline.py`: legacy task orchestration;
- `notebooks/*.ipynb`: historical analyses and generated products.

These contain useful mature logic, but new source-specific branches should start from the public API.

## 7. Verify results and provenance

For every analysis, inspect the source configuration, selected-target table, cache paths, output tables, plots, cutouts and report. Preserve source, provider, collection and configuration information with the results.
