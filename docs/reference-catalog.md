# Large reference-catalog enrichment

Use this workflow when the input is a large coordinate list rather than a
date-based observing plan. It is designed for catalogs such as the 35,400-row
LaStBeRu watchlist and can be used with another catalog by changing column
names in the dedicated configuration.

## Run it

Place the local catalog at the path configured in
`configs/lastberu_dp2.toml`, then run:

```bash
target-selection catalog --config configs/lastberu_dp2.toml
```

The same operation is available from Python:

```python
from target_selection import load_reference_catalog_config, run_reference_catalog

config = load_reference_catalog_config("configs/lastberu_dp2.toml")
result = run_reference_catalog(config)
catalog = result.catalog
```

Use `notebooks/lastberu_reference_catalog.ipynb` to run the analysis
interactively or inspect the resulting tables, distributions, priorities, and
cutout grids.

## How it scales

The workflow does not issue one TAP query per target.

1. Consolidated HealSparse coadd-property maps are sampled at all coordinates
   using HTTP range reads of only the required coverage pixels.
2. Targets with a coadd are grouped into HEALPix tiles. Each tile downloads only
   nearby `VisitDetector` rows from TAP; exact detector-polygon matching and
   aggregation by target and band are performed locally.
3. Tiles and coadd samples are cached independently, and up to
   `tap_max_workers` tiles are processed concurrently.
4. Only the highest-priority targets within the configured cutout budget cause
   individual coadds to be retrieved from Butler.

With `visit_query_scope = "coadd"` (the default), positions without a coadd are
recorded with `visit_query_status = "skipped_no_coadd"`; use `"all"` when exact
VisitDetector coverage is required for every target. Rerunning an interrupted
job reuses completed tiles and map samples when the release and coordinates are
unchanged.

## Scientific columns

`reference_catalog.csv` has one row per input target and retains the original
catalog fields. Added columns include:

- `n_images_<band>` and `n_images_total`: individual detector images whose
  true detector polygon contains the target (nullable when a target was not
  queried because it had no coadd, or when its tile failed);
- visit-level mean/best seeing, mean/best 5-sigma limiting magnitude, exposure
  time, effective time, sky noise, zero point, PSF-star count, and first/last
  MJD, all separated by band;
- coadd-property-map values by band: 5-sigma PSF depth, PSF determinant radius,
  approximate PSF FWHM, accumulated exposure time, and sky noise;
- `has_coadd_<band>`, `coadd_n_bands`, and `coadd_bands`;
- normalized LaStBeRu grade and its source field; and
- the quality-score components, cutout eligibility, rank, selection, status,
  and output path.

The coadd PSF FWHM is derived from the property-map determinant radius using
`FWHM = 2.35482 × radius × pixel_scale`. The configured DP2 coadd pixel scale
is 0.2 arcsec/pixel. The original radius in pixels is retained.

## Cutout priority

Targets without a coadd are not cutout candidates. Grade order is strict:
with the default `cutout_grades = ["A", "B"]`, all grade-A targets are placed
before grade B. Within each grade, a transparent score combines:

- deeper coadd PSF magnitude limit (50%);
- smaller coadd PSF FWHM (35%); and
- more available coadd bands (15%).

These weights are configurable. The score is a resource-prioritization
heuristic, not a new lens classification. `max_cutouts` is the resource budget;
the manifest reports how many targets have coadds, how many meet the allowed
grades, how many were selected, and how many PNGs were generated.

Each target PNG is one multi-band grid. Every panel is exactly the configured
angular side length (20 arcsec by default), marks the catalog coordinate, and
reports the band image count, coadd depth, and PSF FWHM when available.

## Products

```text
outputs/catalog_enrichment/<name>/
├── reference_catalog.csv    # complete one-row-per-target dataset
├── cutout_plan.csv          # eligible targets in priority order
├── manifest.json            # configuration and counts
└── cutouts/
    └── <rank>_<target>_coadds.png
```

For a small test, use `run_reference_catalog_preview(config, n_targets=200)`
from Python or set `TRIAL_TARGET_LIMIT = 200` in the notebook. For the complete
35,400-row run, set `TRIAL_TARGET_LIMIT = None`, `RUN_CATALOG = True`, and keep
`tap_max_workers = 2` unless the RSP service permits a larger value. Progress is
shown by `tqdm` for VisitDetector tiles and cutouts.

The input LaStBeRu file is local scientific data and is not committed to Git.
Set `input_path` to any accessible CSV and configure its identifier, RA, Dec,
and optional metadata columns.
