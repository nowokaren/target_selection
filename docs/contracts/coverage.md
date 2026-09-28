# Coverage contracts

## `coverage.csv`

`coverage.csv` is the normalized coverage table for image and catalog
products. It has one row per target, provider/collection, product kind and
band.

| Column | Type | Meaning |
|---|---|---|
| `target_id` | string | Target identifier. |
| `provider` | string | Data provider, for example `lsst`. |
| `collection` | string | Collection or data release. |
| `image_kind` | string | `individual_raw`, `individual`, `coadd` or `diff`. |
| `band` | string | Image band. |
| `n_images` | integer | Number of matching products. |
| `has_coverage` | boolean | At least one product is registered. |
| `has_valid_metrics` | boolean | Required metrics are available and valid. |
| `has_finite_pixels` | boolean/null | Product image contains finite science pixels when checked. |

Optional property columns include:

```text
maglim, psf_fwhm_arcsec, exposure_time_s, sky_noise
```

The image kind is deliberately simple for users. Collection-specific dataset
types such as `calexp`, `deepCoadd` or `deepDiff` are internal adapter details.

## Coverage properties

Properties may be requested as part of coverage:

```python
coverage = lsst.coverage(
    targets,
    image_kinds=("individual", "coadd"),
    properties=("maglim", "psf_fwhm", "exposure_time", "sky_noise"),
)
```

They may also be queried independently for specialized analyses. In either
case, properties are sampled at the target position from the relevant
property map; they are not measurements extracted from the target cutout.

## `stage_coverage.csv`

This table has one row per `target_id + provider + stage` and summarizes the
number of usable observations and exposure time in each microlensing stage:

```text
target_id, provider, stage, n_points, exposure_time_s
```

The detailed observations remain in `observations.csv`; this is a derived
summary for planning and reports.

