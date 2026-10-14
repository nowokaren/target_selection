# Priority assessment contract

`evaluate_priority` in `target_selection.prioritization` is the single method
for scientific categorization and ordering. It produces one row per candidate
and does not collapse unlike evidence into a weighted score. The category is
decision support; the observing team makes the final selection.

## `priority_assessment.csv`

One row per `target_id`, ordered by category and then by the category's
documented ranking metric. Required decision fields:

```text
target_id, priority_category, priority_rank, category_rank,
assessment_status, priority_reason, event_stage,
rise_significance_sigma, expected_delta_mag, detectable_delta_mag,
n_photometry, n_casleo_observations, n_casleo_photometry,
n_casleo_coverage_points, casleo_phases,
event_model_parameters_available, t0_hjd, tE_days,
days_since_external_photometry, last_external_survey,
external_median_cadence_days, external_stale_threshold_days, stale_external_data,
magnitude_assessment, n_visible_nights, best_observable_minutes
```

Photometry-derived stage counts, model parameters, visibility summaries,
instrument magnitude-limit flags, and source metadata are included when
available. `REVISAR` means evidence is insufficient for a defensible category;
it is retained in the assessment and excluded from the schedule candidate
list until reviewed.

## Categories and ordering

The current order is `NUEVO-RISE`, `NUEVO-PEAK`, `CONTINUIDAD`,
`DIAGNÓSTICO`, `OPORTUNISTA`, `REVISAR`, `DESCARTAR`. Within categories:

- `NUEVO-RISE`: rise significance, highest first;
- `NUEVO-PEAK`: absolute time from the modeled peak, closest first;
- `CONTINUIDAD`: predicted change divided by detectable change, highest first;
- `DIAGNÓSTICO`: time since external photometry, longest gap first;
- `OPORTUNISTA`: latest measured magnitude, brightest first;
- `REVISAR`: rise significance, highest first;
- `DESCARTAR`: stable target-ID order for audit.

These are first-pass rules in `PriorityConfig`; the configuration and
thresholds are written to `priority_config.json` with the table.
External data are marked stale only when the gap exceeds both a 30-day
minimum window and the configured cadence-based threshold. Survey, source, and
provider labels are coalesced per measurement so sparse columns from combined
providers do not merge unrelated survey cadences.

Visibility and brightness are feasibility fields. The nightly visibility
table remains the source of per-night windows and is joined by target and
night when producing a schedule. It does not alter the scientific category.

## Evidence sources

Priority products may reference, rather than duplicate:

- `targets_from_TOMs.csv` for candidate metadata and event parameters;
- `observations.csv` for normalized survey/provider photometry;
- HSH/JS phase-coverage summaries for previous CASLEO observations;
- `visibility_by_night.csv` and `visibility_summary.csv` for observability;
- LSST DiaObject/DiaSource products for matched Rubin photometry.
Targets with no usable photometry row (valid epoch and magnitude) are omitted
from `priority_assessment.csv` and written to
`priority_discarded_no_photometry.csv` with a `discard_reason` field. A CASLEO
image inventory alone does not count as photometry.
