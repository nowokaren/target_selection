# Provider interface

A provider is an adapter that exposes one external or local data source with
normalized outputs. A TOM and a survey use the same interface; their `kind`
metadata distinguishes their role.

Examples:

```text
MOP, OPM, BHTOM  -> kind: tom
HSH, JS, OGLE    -> kind: survey
LSST/Rubin       -> kind: survey
```

The public handle can therefore be simple:

```python
data_source.mop
data_source.hsh
data_source.ogle
data_source.bhtom
data_source.lsst(collection="DP2")
```

## Features

Each provider declares a short list of supported features, for example:

```python
data_source.hsh.features
# ["targets", "photometry", "epochs"]
```

The public query is capability-oriented but uses the shorter name `features`:

```python
data_source.hsh.supports("photometry")
data_source.hsh.photometry(targets, band="I")
data_source.hsh.epochs(targets)
```

Typical features are:

```text
targets, parameters, epochs, photometry, visibility,
images, property_maps, coverage
```

`cutout` is not a provider feature or image kind. It is an operation applied
to an image returned by `images`.

## Normalized methods

Providers should implement only the methods their features support:

```python
provider.load_targets(filters=...)
provider.parameters(targets=...)
provider.epochs(targets=...)
provider.photometry(targets=..., band=...)
provider.visible_targets(...)
```

Each method returns a normalized table and preserves `provider` provenance.
Unsupported features should fail clearly or be reported by `supports()`; they
should not silently return an empty table.
