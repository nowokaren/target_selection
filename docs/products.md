# Products and where to find them

Every run has a `product_index.json`. Open it first: it contains only products
that exist and gives a short description and relative path for each one.

## Products by usage mode

| Product | Planning only, without a reference survey | With Rubin DP1/DP2 | Required switch |
|---|---:|---:|---|
| Configuration and source audit | Yes | Yes | Always |
| Final target table | Yes | Yes | Always |
| Local visibility metrics | Yes | Yes | Always |
| Nightly visibility PNGs | Optional | Optional | `visibility_plots = true` |
| Observing-selection table and PNG | Optional | Optional | `observing_selection_summary = true` |
| Monitoring light-curve PDF | Optional | Optional | `monitoring_report = true` |
| Sky maps | No | Optional | `sky_maps = true` |
| Individual coadd dashboard PNGs | No | Optional | `target_reports = true` |
| Reference forced photometry | No | Optional | `reference_photometry = true` |

Planning-only mode intentionally avoids TAP and Butler. It is the fastest way
to evaluate local visibility, produce the MOP/follow-up observing-selection table,
and write the monitoring PDF. Select a Rubin reference survey when sky context,
coadds, Rubin epochs, or Rubin photometry are required.

## Recommended files

| File | What to use it for |
|---|---|
| `analysis_config.json` | Reproduce the exact run |
| `tables/combined_targets.csv` | Complete enriched target result for a reference-survey run |
| `tables/targets.csv` | Complete target result for a planning-only run |
| `tables/visibility_target_summary.csv` | One local pass/fail summary per target |
| `visibility_plots/visibility_selection.csv` | Per-target, per-night visibility metrics and rejection reasons |
| `tables/visibility.csv` | Planning-only per-target, per-night visibility metrics |
| `tables/observing_selection_summary.csv` and `.png` | Bright-to-faint observing-planning view with event-stage coverage |
| `monitoring_reports/lightcurves.pdf` | Provider light curves plus follow-up and reference temporal coverage |
| `tables/source_catalog.csv` | Target provenance by provider or survey |
| `tables/source_updates.csv` | Sources imported or refreshed in this run |
| `tables/mop_candidates_without_data.csv` | MOP-visible candidates excluded from analysis because neither event parameters nor photometry are available |
| `tables/targets_without_selected_data.csv` | Candidates removed when `target_data_scope = "with_data"`, with the source-data counts and reason |

## Plot and report contents

### Nightly visibility plots

Each PNG shows altitude curves and a binned altitude strip for targets that pass
the configured local cut. Twilight, Moon altitude, observing allocation, target
magnitude, and observable duration are included where available.

### Visibility-sequence PDF

`visibility_sequence.pdf` compiles the already-generated nightly visibility PNGs rather than recomputing curves. Each PDF page is one observing date, retains every source-aware part, and includes a date box; the embedded PNG titles identify the target group and part.

### Observing-selection summary

This compact table contains locally visible targets sorted by current MOP magnitude. Alongside `mag now`, `Last mag` gives the latest valid preferred MOP measurement (OGLE_I first, then G, otherwise the most recent available filter), and `Last filter` identifies the filter of that selected measurement. Standard labels are compacted (for example, `OGLE_I` becomes `I`); other long names are shortened for legibility.

`Region` is a compact designation-based classification: `BLG` (Galactic bulge),
`GD` (Galactic disk; includes legacy `DG`), `LMC`, `SMC`, `XGAL` (an
unambiguous named external galaxy), or `UNK` when the event name does not
provide a reliable classification.

### Monitoring light curves

The PDF shows provider photometry and configurable temporal-coverage lanes for
Rubin and follow-up surveys. A second panel is centered on `t_0 ± 2 t_E` when
those MOP parameters are available. The main panel always extends through two calendar months after the report is created.

### Sky maps

The full-sky and Galactic-bulge maps show only targets that pass the local visibility filter, with position, current magnitude,
and visit count. A muted low-resolution reference-coverage background is
optional.

### Individual target reports

Each PNG contains deep-coadd and zoom panels by band, visit statistics,
microlensing parameters, and a light curve with reference and follow-up epochs.
A report is not created if no selected coadd contains the target.

## Python access

Every reference-survey run returns a named entry in `AnalysisResult.runs`:

```python
result = run_analysis("configs/example.toml")
dp2 = result.runs["rubin_dp2"]
targets = dp2.targets
print(dp2.paths["run"])
```

For multiple reference surveys, iterate over `result.runs`. Detailed diagnostic
tables from the Rubin backend remain available inside each run, but the files
above are the recommended entry points.

The persistent cross-run catalog is stored at the configured
`cache.database_path`, normally
`outputs/target_database/target_selection.sqlite`. See
[Data and cache](data-and-cache.md) before moving or deleting it.
