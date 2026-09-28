# `targets.csv`

`targets.csv` is the canonical candidate table for one analysis run. It has
one row per target and contains the information available after the initial
filters have been applied.

The targets in this file are **preselected candidates**, not the final
scientific observing decision. The final choice remains with the user after
reviewing tables, plots and reports.

## Required columns

| Column | Type | Meaning |
|---|---|---|
| `target_id` | string | Stable identifier within the analysis. |
| `ra_deg` | float | Right ascension in ICRS degrees. |
| `dec_deg` | float | Declination in ICRS degrees. |
| `provider` | string | Provider that supplied the target record. |

## Common optional columns

| Column | Type | Meaning |
|---|---|---|
| `category` | string | Provider-defined target class or status. |
| `t0_hjd` | float | Microlensing peak time, HJD. |
| `t0_hjd_err` | float | Uncertainty on `t0_hjd`, days. |
| `tE_days` | float | Einstein timescale, days. |
| `tE_days_err` | float | Uncertainty on `tE_days`, days. |
| `u0` | float | Impact parameter. |
| `u0_err` | float | Uncertainty on `u0`. |
| `magnitude_last` | float | Last measured magnitude. |
| `magnitude_last_err` | float | Uncertainty on `magnitude_last`. |
| `epoch_last_hjd` | float | Epoch of `magnitude_last`, HJD. |
| `band_last` | string | Band of `magnitude_last`. |

`magnitude_now` is reserved for a modeled or current estimate. It must not be
used as a synonym for the last measured magnitude.

## Selection semantics

The table may contain columns such as `passes_magnitude` or
`passes_visibility`, but those are decisions made by the analysis and must be
documented in the run configuration. They do not replace the original target
information.

If a parameter is supplied by more than one provider, the canonical column
must have one documented precedence rule and the provider-specific values
should be preserved in separate names or a provenance table.

