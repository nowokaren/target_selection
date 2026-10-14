# Typical seasonal HSH/JS workflow

This is the recurring workflow to use when a new observing season approaches.
Use `notebooks/v2/02_seasonal_visibility_selection.ipynb` for visibility alone.
For the complete HSH/JS priority assessment and seasonal plan, use
`notebooks/v2/11_seasonal_observing_plan.ipynb` (see [Usage notebooks](usage-notebooks.md)).

## Flow

The seasonal HSH/JS path is one branch of the integrated workflow shown in
[Version 2 architecture](v2-architecture.md). The complete diagram is available
as [PNG](assets/v2_diagrams/v2_target_selection_workflow.png) or
[PDF](assets/v2_diagrams/v2_target_selection_workflow.pdf).

## Minimal seasonal configuration

```python
SOURCE = "lastberu_watchlist"  # or qso_extended, MOP, HSH, JS, etc.
OBSERVATORY = "CASLEO"
START_DATE = "2026-10-01"
END_DATE = "2026-12-31"
MIN_ALTITUDE_DEG = 30.0
MIN_VISIBLE_MINUTES = 90.0
```

## Recommended execution

```python
from target_selection import target_io
from target_selection.tasks.visibility import evaluate_visibility

# Load only the candidate population needed for this season.
targets = target_io.load(SOURCE, filters=SOURCE_FILTERS)

visibility = evaluate_visibility(
    targets,
    start_date=START_DATE,
    end_date=END_DATE,
    observatory=OBSERVATORY,
    minimum_altitude_deg=MIN_ALTITUDE_DEG,
    minimum_observable_minutes=MIN_VISIBLE_MINUTES,
    output_path="outputs/season/visibility.csv",
)

season_targets = visibility.loc[
    visibility["selected_for_visibility"].astype(bool)
].copy()
```

The next survey-specific step should add the HSH/JS observation inventory and
photometry through an adapter or `add_data`. It should not duplicate the
visibility calculation or Rubin matching logic.

After TOM and survey photometry is collected, call the single
`target_selection.prioritization.evaluate_priority` function with the target
table, normalized observations, and visibility summary. It returns one
category/rank assessment per candidate; visibility and magnitude remain
separate schedule-feasibility evidence. The [priority contract](contracts/priority.md)
defines categories, metrics and output fields.

Before assessment, `add_ogle_event_parameters` fills missing OGLE `t0`, `tE`,
and `u0` values from the OGLE EWS event table while preserving parameters
already supplied by a TOM. Rows without usable model parameters remain in the
table with `event_stage=unknown` and an explicit review reason.

## Outputs to preserve

At minimum, save:

- the source and filter configuration;
- the input and selected target tables;
- per-target/per-night visibility decisions;
- rejection reasons;
- HSH/JS observation history;
- the final prioritized observing list;
- optional Rubin coverage and coadd summaries;
- cache provenance and adapter versions.

This makes it possible to reproduce why a target was selected or rejected in a
specific observing season.
