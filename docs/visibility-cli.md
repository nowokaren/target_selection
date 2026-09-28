# Terminal visibility reports

This command is the lightweight workflow for making a visibility PDF after
manually selecting targets. It reuses the same astronomical calculations and
plot style as the full pipeline.

## Interactive mode

From the repository root:

If you are elsewhere, use the absolute path /home/karennowo/target_selection/data/hsh_data/hsh_objects.csv.

```bash
target-selection visibility \
  --targets data/hsh_data/hsh_objects.csv \
  --start-date 2026-09-20 \
  --end-date 2026-09-25 \
  --observatory "El Leoncito" \
  --minimum-altitude 40 \
  --minimum-minutes 90 \
  --observing-window 20:30 06:00 \
  --output outputs/my_visibility
```

The command asks for an optional substring first. Then it shows a checkbox list:

- Arrow keys: move.
- Space: select or deselect.
- Enter: confirm and generate.
- Mouse click: select or deselect in terminals with mouse support.

If the interactive UI is unavailable:

```bash
python -m pip install -e '.[interactive]'
```

The optional dependency is `prompt_toolkit`.

## Reproducible mode

Use explicit names:

```bash
target-selection visibility \
  --targets data/hsh_data/hsh_objects.csv \
  --names OGLE-2025-BLG-0451,OGLE-2025-BLG-1121 \
  --start-date 2026-09-20 \
  --end-date 2026-09-25 \
  --non-interactive \
  --output outputs/reproducible_visibility
```

Or create `inputs/selected_targets.csv`:

```csv
Target
OGLE-2025-BLG-0451
OGLE-2025-BLG-1121
```

and use:

```bash
target-selection visibility \
  --targets data/hsh_data/hsh_objects.csv \
  --selection-file inputs/selected_targets.csv \
  --start-date 2026-09-20 \
  --end-date 2026-09-25 \
  --non-interactive
```

Use `--match OGLE-2025` to restrict the candidates shown by the interactive
selector. Use `--only-visible` if the PDF should omit selected targets that
do not meet the configured cut.

## Outputs

The output directory contains:

- `selected_targets.csv`: the exact manually selected input.
- `visibility_selection.csv`: one row per target and night, with altitude and
  observable-time metrics.
- `visibility_summary.csv`: one row per target.
- `visibility_config.json`: dates, observatory, cuts, and selection mode.
- `nightly_plots/*.png`: one plot per night.
- `visibility_report.pdf`: the compiled report.

The default behavior preserves the observer's selection in the plots. The
visibility thresholds are reported in the CSV and in the plot annotation; they
are not used to silently discard targets unless `--only-visible` is supplied.
