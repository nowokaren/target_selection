# MOP–DP2 analysis

This directory contains the analysis notebooks and products for comparing the
complete MOP event catalog with Rubin DP2.

## Notebooks

- `notebooks/analysis/mop_dp2/00_mop_snapshot_and_enrichment.ipynb`,
  `notebooks/analysis/mop_dp2/01_mop_dp2_coverage_and_reports.ipynb` and
  `notebooks/analysis/mop_dp2/03_explore_mop_dp2_matches.ipynb` build the MOP–DP2 coverage and
  report products.
- `notebooks/analysis/mop_dp2/02_lsdb_hats_mop_dp2_comparison.ipynb` matches the MOP targets
  against DP2 HATS catalogs with the reusable `target_selection` API.

MOP–DP2 run tables are written under `outputs/mop_dp2_all/`. LSDB/HATS
products and retained reports live under `outputs/analyses/mop_dp2/`. Both are
under the single root `outputs/`, are generated locally, and remain outside Git.

## Environment

Run the notebooks from the repository root so imports resolve to the local
`target_selection` package. Create an analysis environment here when needed:

```bash
python -m venv analyses/mop_dp2/.venv
analyses/mop_dp2/.venv/bin/python -m pip install -r analyses/mop_dp2/requirements-lsdb.txt
```

The previous home-directory environment is not part of the project and is not
linked from this analysis.

The pre-package helper implementation is retained for provenance in
`archive/mop_dp2_legacy.py`; notebooks use the maintained package modules.

## Reusable package layers

- `target_selection.sources`: load and normalize MOP, QSO, LaStBeRu and other
  input catalogs.
- `target_selection.catalog_matching`: generic LSDB/HATS spatial matching.
- `target_selection.catalog_products`: semantic DP2 products such as
  `object`, `dia_object` and `dia_source`.
- `target_selection.collection_access` and `reference_catalog`: visit/coadd
  coverage and image-product queries.
- `target_selection.quality`: per-band image/coadd quality flags.
- `target_selection.plotting`: reusable diagnostic plots.

Forced-source products are not spatially matched independently. They are
retrieved by the parent `objectId` or `diaObjectId` after the spatial match.
