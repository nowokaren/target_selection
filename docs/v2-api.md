# Version 2 API guide

The API is organized by the operation being performed. A normal analysis uses
small functions with explicit inputs and outputs rather than one large
pipeline function.

## Load targets

```python
from target_selection import target_io

catalog = target_io.load(
    "lastberu_watchlist",
    filters={"category": ["A", "B"]},
)
```

The configured source returns a canonical table containing at least:

```text
target_id, ra_deg, dec_deg, source
```

If a category is configured, it also contains `category`. Source columns are
preserved. `load` applies filters it can evaluate from the source data and
returns only the selected targets.

For an unregistered local file:

```python
catalog = target_io.load(
    "path/to/catalog.csv",
    source_name="my_catalog",
    id_column="object_id",
    ra_column="ra",
    dec_column="dec",
    category_column="status",
    category_parser="identity",
    filters={"category": [1]},
)
```

Available source definitions and adapter capabilities can be inspected with:

```python
target_io.list_sources()
target_io.describe_source("qso_extended")
target_io.list_adapters()
target_io.describe_adapter()
```

## MOP event enrichment

For an existing HSH/JS event list, use `target_io.add_data` with the MOP loader. This preserves events that MOP does not know and adds only the fields returned by MOP.

```python
from target_selection import target_io
from target_selection.sources.mop import load_event_data

def mop_loader(catalog, fields):
    return load_event_data(catalog, fields=fields, cache_dir="outputs/cache/mop_event_data")

events = target_io.add_data(
    events, provider="MOP",
    fields=("magnitude", "event_duration_days", "event_peak_hjd", "u0"),
    provider_options={"cache_dir": "outputs/cache/mop_event_data"},
)
```

The provider uses the existing `MOPClient.enrich_microlensing_parameters` method, caches one record per target, and maps MOP-specific names such as `mop_mag_now` to the canonical v2 field `magnitude`.

## Add optional data

`add_data` is optional. It adds explicitly retrieved fields to an existing
canonical table; it does not silently query an external service.

```python
catalog = target_io.add_data(
    catalog,
    photometry_table,
    provider="mop",
    fields=["magnitude_i", "magnitude_i_error"],
)
```

## Visibility

The current visibility task evaluates target/night rows using an observatory,
dates, altitude threshold, observing window, and minimum observable duration.

```python
from target_selection.tasks.visibility import evaluate_visibility

selection = evaluate_visibility(
    catalog,
    start_date="2026-10-01",
    end_date="2026-12-31",
    observatory="CASLEO",
    minimum_altitude_deg=30,
    minimum_observable_minutes=90,
)
```

The result records the visibility decision and metrics for each target/night.
The planned v2 visibility namespace will expose the same operations without
changing the scientific result format.

## Rubin matching

Use one `CollectionContext` for all Rubin operations. This avoids passing the
collection, Butler and TAP clients through several wrappers.

```python
from target_selection import open_collection

collection = open_collection("DP2")
coverage = collection.get_visit_coverage(catalog, bands=tuple("ugrizy"))
coverage = collection.get_coadd_coverage(coverage, bands=tuple("ugrizy"))
positions = coverage[["target_id", "ra_deg", "dec_deg"]]
properties = collection.get_property_map_values(
    positions,
    properties=("maglim", "psf_size"),
    bands=tuple("ugrizy"),
)
coverage = coverage.merge(properties, on="target_id", how="left")
```

Visit properties are aggregated from individual visit/detector matches.

## Products

```python
from target_selection import report

report.match_summary(coverage, output_dir, category_column="category")
report.property_distributions(coverage, output_dir, category_column="category")
report.sky_maps(coverage, output_dir)
collection.get_image(coverage, image_type="coadd", fits_dir=fits_dir, output_dir=png_dir)
report.main(coverage, fits_dir=fits_dir, asset_dir=output_dir, output_path=pdf_path)
```

The PDF includes only targets for which at least one FITS cutout is available.
Targets with `has_coadd=True` but no locally generated FITS remain in the
coverage tables and are reported as missing products, not as empty target
pages.
