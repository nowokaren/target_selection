# Visibility contracts

Visibility is represented at two grains. Detailed information is stored per
target and night; seasonal information is an aggregation and should not
replace the detailed table.

## `visibility_nightly.csv`

One row per `target_id + night`.

| Column | Type | Meaning |
|---|---|---|
| `target_id` | string | Target identifier. |
| `night` | date/string | Local observing night. |
| `observatory` | string | Observatory used for the calculation. |
| `is_visible` | boolean | Target passes the configured visibility cuts. |
| `visible_from_local` | datetime/string | Start of the accepted local interval. |
| `visible_to_local` | datetime/string | End of the accepted local interval. |
| `visible_minutes` | float | Duration of the accepted interval, minutes. |
| `max_altitude_deg` | float | Maximum altitude during the interval. |
| `visibility_provider` | string | Provider or `local` calculation used. |

`local` means local civil or observatory time as specified by the run
configuration. The timezone and observing-window convention must be saved in
that configuration.

## `visibility_season.csv`

One row per target, derived from `visibility_nightly`:

```text
target_id, observable_nights, total_visible_minutes,
best_night, best_visible_from_local, best_visible_to_local
```

The best-night fields are summaries, not replacements for the nightly rows.

## Provider versus local visibility

A provider may expose its own visibility query, for example:

```python
data_source.mop.visible_targets(
    observatory="El Leoncito",
    start=season_start,
    end=season_end,
)
```

The common fallback is:

```python
planning.visibility(
    targets,
    observatory="El Leoncito",
    start=season_start,
    end=season_end,
)
```

Both outputs must be normalized to the same table. A visibility plot must be
available regardless of whether the result came from a provider or the local
calculation.

