# Target Selection 2.0 roadmap

This document records proposed features that require scientific and operational validation before implementation. Version 1 remains focused on assembling MOP and Rubin information and producing diagnostic products.

## Goal

Turn the current reports into an auditable nightly decision workflow:

1. determine which events are technically observable;
2. rank observable events by scientific value and urgency;
3. let an observer accept, reject, or pin targets;
4. plot only the final selection for each night;
5. build an editable observing plan for JS or HSH.

Hard feasibility rules must remain separate from the scientific priority score. Every rejection and score contribution should be stored in the output tables.

## Nightly observability filter

Proposed API:

```python
selected = select_nightly_targets(
    targets,
    night="2026-08-05",
    telescope="JS",
    minimum_altitude=30,
    minimum_observable_minutes=60,
    maximum_airmass=2.0,
    minimum_moon_separation=30,
)
plot_selected_visibility(selected, night="2026-08-05")
```

Recommended hard criteria:

- target is inside telescope-specific altitude and hour-angle limits;
- airmass remains below the chosen maximum for a minimum continuous duration;
- the usable interval overlaps astronomical night;
- Moon separation and lunar illumination are acceptable;
- the requested signal-to-noise ratio is achievable without saturation and within the safe exposure limit;
- coordinates, finding information, camera, filters, and target field are valid and operational.

Defaults must come from telescope profiles, not universal constants. The result should include `observable`, `rejection_reasons`, usable start/end and transit times, maximum altitude, minimum airmass, observable minutes, Moon separation, and estimated I/V exposures.

## Scientific priority

Only feasible targets should be ranked. Proposed score components:

- **event phase and urgency:** newly rising events, events approaching `t_0`, and rapidly changing light curves;
- **anomaly opportunity:** deviations from a point-lens model or cadence gaps during the rise or peak;
- **phase coverage:** missing baseline, rise, peak, decline, or post-event measurements;
- **network coverage:** targets not currently monitored by another MOP telescope;
- **brightness and precision:** useful precision without saturation or unsafe exposures;
- **visibility quality:** longer windows, higher altitude, lower airmass, larger Moon separation, and transit during the allocated night;
- **scientific value:** high-magnification events, unusual timescales, finite-source/parallax candidates, MOP priority, and manual flags;
- **recency and cadence:** time since the last useful measurement relative to the requested cadence;
- **color information:** missing or stale V-band measurements when an I-band light curve exists;
- **field quality:** crowding, blending, bright neighbors, comparison stars, and acquisition reliability;
- **instrument history:** previous seeing, background, tracking, saturation, and uncertainty for the field.

Start with an explicit weighted sum with named contributions, configurable weights, and a manual override. Consider expected information gain only after the simpler score has been evaluated against past observing decisions.

## JS and HSH sensitivity models

Published specifications are useful metadata, but useful limiting magnitude should be calibrated from the program's reduced images. JS has a 2.15 m primary, while HSH has a 0.6096 m primary and an SBIG STL-1001E camera; aperture alone does not predict useful crowded-field microlensing precision.

For each historical exposure, collect:

- telescope, camera, filter, exposure time, binning, and date;
- target magnitude and measured signal-to-noise ratio or magnitude uncertainty;
- sky background, seeing, airmass, Moon illumination/separation, transparency, and zeropoint;
- peak counts/saturation, ellipticity or trail length, and tracking failures.

Fit an empirical model predicting photometric uncertainty, saturation risk, and tracking risk. It should recommend the shortest exposure that reaches the requested precision, initially capped at the operational limits supplied by the team: 300 s for HSH and 600 s for JS. These caps are safety inputs, not guarantees; exposures should be shortened when saturation or tracking history requires it.

Until enough calibrated images exist, use a conservative observer-approved lookup table instead of inferring limiting magnitude from aperture alone.

## Filter and cadence policy

A configurable initial policy could:

- observe primarily in I;
- insert V at the start/end of a block, every configurable number of I exposures, or when color coverage is missing;
- use higher cadence for rising, anomalous, or near-peak events;
- use lower cadence for baseline or declining events;
- split long integrations when cosmic rays, saturation, or tracking make one exposure risky;
- include acquisition, readout, filter-change, focus, and slew overheads.

Filter policy should be independent of target ranking so either can change without altering the other.

## Nightly scheduler

Proposed API:

```python
plan = build_observing_plan(
    selected_targets,
    night="2026-08-05",
    telescope="HSH",
    filters=("I", "V"),
)
```

The first scheduler can be transparent and greedy: choose the highest-priority due target satisfying current constraints, accounting for exposure, readout, filter changes, slews, cadence, and remaining visibility. Mixed-integer optimization can be evaluated later.

Suggested outputs:

- `nightly_candidates.csv`: feasibility, rejection reasons, and score components;
- `selected_targets.csv`: automatic selection plus manual overrides;
- `observing_plan.csv`: ordered time, target, filter, exposure, repeats, cadence, expected airmass, and notes;
- `observing_plan.png`: timeline with twilight and altitude constraints;
- a visibility PNG containing only final selected targets;
- `plan_summary.json`: configuration, telescope profile, model versions, and warnings.

Plans must remain editable and be recomputed when weather, anomalies, telescope status, or completed observations change.

## Validation required before automation

1. Agree on JS/HSH altitude, hour-angle, tracking, saturation, and operational limits with CASLEO observers.
2. Define coverage of baseline, rise, peak, and decline.
3. Identify a reliable source for current MOP-network observing status.
4. Assemble historical JS/HSH metadata and photometric measurements.
5. Replay past nights and compare results with expert choices.
6. Keep human approval mandatory and record overrides.

## Additional criteria to evaluate

- cloud, wind, humidity, seeing forecast, and safe shutdown margin;
- anomaly alerts and target-of-opportunity status;
- probability that `t_0` or caustic features occur during the night, including uncertainty;
- coverage from other longitudes and the value of filling network gaps;
- twilight background, extinction, and differential refraction by filter;
- detector defects, calibration requirements, focus changes, and field acquisition;
- slew distance, meridian constraints, dome limits, and observing overheads;
- minimum allocations among science categories when desired.
