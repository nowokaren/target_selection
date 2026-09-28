# Priority contracts

Priority tables are derived decision-support products. They do not encode the
user's final scientific selection.

## `priority_nightly.csv`

One row per `target_id + night`. It should contain only priority information
not already present in `visibility_nightly.csv`:

```text
target_id, night, priority_score, priority_class, priority_reason
```

If a report needs visibility fields, it joins this table with
`visibility_nightly.csv` on `target_id` and `night`; it should not copy the
same fields into both files.

## `priority_season.csv`

One row per target, aggregating the nightly decision-support information:

```text
target_id, observable_nights, total_visible_minutes,
stage_coverage_summary, priority_score, priority_class, priority_reason
```

The exact score formula is configuration, not part of the normalized provider
contract. The run must save the formula, thresholds and input providers.

## Scientific evidence

Priority products may reference, rather than duplicate:

- `targets.csv` for target parameters;
- `observations.csv` for photometry and epochs;
- `stage_coverage.csv` for event-stage coverage;
- `coverage.csv` for LSST products;
- `visibility_nightly.csv` for observability.

