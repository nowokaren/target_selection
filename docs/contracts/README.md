# Normalized data contracts

This directory defines the normalized contracts used to exchange data between
providers, target selection, planning and reports. The contracts are a public
interface: provider-specific adapters may use different column names or
formats internally, but they must return these normalized fields before their
data are combined with another provider.

The initial contract version is **v0.1**. It is intentionally small and
supports missing optional data. A missing value means that the provider did
not supply that measurement; it does not mean that the target was rejected.

## Recommended reading order

1. [Targets](targets.md)
2. [Observations](observations.md)
3. [Visibility](visibility.md)
4. [Coverage](coverage.md)
5. [Priority](priority.md)
6. [Provider interface](provider-interface.md)

## Core principles

- Every table has one documented row grain.
- Every external measurement keeps its `provider`.
- Units and time systems are explicit in column names or metadata.
- Optional values are null rather than fabricated.
- A provider may add extra columns, but must not redefine canonical columns.
- Reports are derived products; they are not additional sources of truth.

