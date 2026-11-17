# Priority assessment contract

`evaluate_priority` in `target_selection.prioritization` is the single method
for scientific categorization and ordering. It produces one row per candidate
and does not collapse unlike evidence into a weighted score. The category is
decision support; the observing team makes the final selection.

`classify_event_stage(t0_hjd, tE_days, now_hjd, config)` calculates phase from
`τ = (now_hjd − t0_hjd) / tE_days` and the configured `tE` boundaries; it does
not use photometry. `build_priority_evidence` calculates per-target measurements and diagnostics
without assigning categories, and retains targets with no usable photometry.
`evaluate_priority` then omits targets without usable photometry and applies the
named criteria in order; the first matching rule sets the category. Criteria
are `criterion_ended_event`, `criterion_supported_event`,
`criterion_post_baseline`, `criterion_casleo_continuity`,
`criterion_casleo_stale_photometry`, `criterion_casleo_missing_uncertainty`,
`criterion_casleo_observing_question`, `criterion_casleo_no_added_information`,
`criterion_rising_event`, `criterion_peak_event`, `criterion_stale_photometry`,
`criterion_declining_event`, `criterion_unknown_event_stage`, and
`criterion_fallback`. `PriorityConfig` exposes the thresholds and windows they
use. The offline `notebooks/priority_assessment.ipynb` has one switchable cell
per criterion, shows its evidence and decision for every target, and ends with
a comparison to `evaluate_priority` as currently implemented.

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
available. `REVISAR` means evidence is insufficient for a defensible category.
It remains in the assessment; no priority category excludes a visible event
from the schedule candidate list. Visibility controls inclusion, and priority
only affects ordering.

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

The human-facing seasonal priority table currently omits the provisional
`Rank` and `Order` columns while the prioritizer is being evaluated. The
underlying assessment CSV retains rank/category fields for diagnostics and
existing consumers; the assessment notebook hides both rank fields and shows
the category and evidence needed to review each decision.

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
