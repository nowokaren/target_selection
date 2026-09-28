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

## Adding a new API

A new API should be implemented as a catalog adapter with a `load` method returning the canonical target table. Register it without changing `target_io.load`:

```python
class OgleAdapter:
    capabilities = ("targets", "coordinates", "photometry")

    def load(self, definition, *, filters=None, verbose=True):
        # Query OGLE, normalize its identifiers/coordinates, and apply
        # source-side filters before returning the canonical table.
        ...

from target_selection import target_io
target_io.register_adapter(
    "ogle", OgleAdapter,
    capabilities=("targets", "coordinates", "photometry"),
)
```

Then define `configs/sources/ogle.toml` with `adapter = "ogle"` and provider-specific options. The adapter should not generate reports or decide the global workflow; those are separate matching and product functions.

`target_io.add_data` is reserved for optional data added after loading, such as photometry or event parameters obtained from another provider.

## Current HSH and JS definitions

The v2 microlensing notebook declares both `hsh_events` and `js_events`, but loads only sources that are ready:

- `hsh_events` is ready and reads `data/hsh_data/hsh_objects.csv`. Its canonical mapping is `object → target_id`, `ra_deg → ra_deg` and `dec_deg → dec_deg`. The current file contains 49 targets.
- `js_events` is a template only. It expects `data/js_data/observations.csv`, but that file is not present in this workspace. The notebook reports it as not ready and skips it; once the file exists, rerunning the notebook will include it automatically.

When the JS inventory is available, update `configs/sources/js_events.toml` if its path or column names differ, then validate it with:

```python
from target_selection import target_io

print(target_io.describe_source("js_events"))
js = target_io.load("js_events")
print(js.shape)
print(js[["target_id", "ra_deg", "dec_deg"]].head())
```

The notebook checks three conditions before loading a source: its TOML definition exists, it is enabled, and its input path exists. This prevents a configured-but-missing JS file from being treated as a valid survey.
