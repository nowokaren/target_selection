# `observations.csv`

`observations.csv` is the common observation and photometry table. It has one
row per target, provider and observation epoch. It combines epochs with any
photometric information available for those epochs.

The table is intentionally in a row-oriented format: a provider can add a new
epoch or band without adding new columns.

## Required columns

| Column | Type | Meaning |
|---|---|---|
| `target_id` | string | Target identifier matching `targets.csv`. |
| `provider` | string | Survey or TOM supplying the observation. |
| `epoch_hjd` | float | Observation time in HJD. |
| `band` | string | Provider/filter band name. |

At least one stable observation identifier should be supplied whenever the
provider exposes one:

| Column | Type | Meaning |
|---|---|---|
| `observation_id` | string | Provider observation or exposure identifier. |

## Optional measurement columns

| Column | Type | Meaning |
|---|---|---|
| `magnitude` | float | Measured magnitude. |
| `magnitude_err` | float | Magnitude uncertainty. |
| `flux` | float | Measured flux in provider units. |
| `flux_err` | float | Flux uncertainty in provider units. |
| `exposure_time_s` | float | Exposure time in seconds. |
| `is_usable` | boolean | Provider-specific quality selection passed. |
| `quality_flag` | string/integer | Original or normalized quality flag. |

Magnitude and flux may coexist when a provider supplies both. A conversion
must not silently overwrite the original measurement.

`is_usable` means that the provider's documented quality criteria passed. It
does not guarantee that the target is detected in the image. It is not the
same field as the target-level `category` (which may originate from a TOM
status, a survey grade or another classification). A provider may calculate
`is_usable` from its own quality flags; it is not restricted to TOMs.

## Missing values and duplicates

Missing photometry is represented by null values. An epoch-only observation is
valid and may have all measurement columns null. Duplicate rows are allowed
only when they have different `observation_id` values or represent distinct
measurements; adapters must document how provider duplicates are removed.
