# Catalog sources and adapters

The simple public API for catalog-like target sources is:

```python
from target_selection import target_io

catalog = target_io.load(
    "lastberu_watchlist",
    filters={"category": ["A", "B"]},
)
```

Configured sources are stored in `configs/sources/*.toml`. A source definition declares its adapter, path, input columns, category parser, and capabilities. The adapter owns source-specific reading and normalization; the rest of the package receives the canonical columns `target_id`, `ra_deg`, and `dec_deg`.

```python
target_io.list_sources()
target_io.describe_source("qso_extended")
target_io.list_adapters()
target_io.describe_adapter()
```

## Adding a catalog adapter

A new catalog format needs an adapter with a `load(definition, *, filters=None, verbose=True)` method returning the canonical target table. Register it with `target_io.register_adapter`, then add a source definition under `configs/sources/` that selects the adapter. The adapter should handle source access and normalization; reports and scientific selection stay in their respective modules.

OGLE and OPM already have provider interfaces under `target_selection.sources` and `target_selection.data_source`. Their public access examples are in `notebooks/tutorials/ogle_data_access.ipynb` and `notebooks/tutorials/opm_data_access.ipynb`.

`target_io.add_data` is reserved for optional data added after loading, such as photometry or event parameters obtained from another provider.

## HSH and JS inputs

`configs/sources/hsh_events.toml` maps `data/hsh_data/hsh_objects.csv` to canonical target columns. The current `data/js_data/js_inventory.csv` records observation history but has no target coordinates, so it is not configured as a target catalog. The JS/HSH meeting notebook reads that inventory directly and keeps its provenance explicit.
