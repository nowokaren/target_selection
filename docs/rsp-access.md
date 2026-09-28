# Rubin RSP access API

The collection-aware Rubin interface is implemented in `target_selection.rsp_access`, with `CollectionContext` as the main user-facing object. It keeps collection-specific TAP table names and Butler dataset types behind a small public API.

## Discover available products

```python
from target_selection import open_collection

collection = open_collection("DP2")
collection.list_tables()
collection.list_property_maps()
collection.list_images()
collection.describe()
```

## Main operations

The preferred style is method-based:

```python
collection = open_collection("DP2")
collection.list_tables()
collection.list_property_maps()
collection.list_images()
collection.describe()

collection.get_table(...)
collection.get_property_map_values(...)
collection.get_visit_coverage(...)
collection.get_coadd_coverage(...)
collection.get_coadd_cutouts(...)
collection.get_image(image_type="coadd", ...)
```

The module-level functions remain as compatibility wrappers. `get_image` accepts the semantic types `coadd`, `visit`, `raw_visit`, and `diff`; dataset names are resolved from the selected collection.

The complete walkthrough is available as a [Jupyter notebook](notebooks/rsp_access_examples.ipynb).

## Image terminology

The API distinguishes `coadd`, `visit_coverage`, and `calexp`. `visit_coverage` is table metadata, not an image. A `calexp` is a calibrated single-visit image and may not be available in every release or RSP deployment. Coadd cutouts are currently the supported generic image operation.
