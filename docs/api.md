# Python and CLI API

## Recommended Python API

```python
from target_selection import load_config, run_analysis

config = load_config("configs/my_run.toml")
result = run_analysis(config)
```

`run_analysis` also accepts the path directly:

```python
result = run_analysis("configs/my_run.toml")
```

The result contains one entry per reference survey, or a `planning_only` entry
when no reference survey is selected:

```python
for source_name, run in result.runs.items():
    print(source_name, run.paths["run"])
    display(run.targets.head())

display(result.source_updates)
```

Useful attributes are:

- `result.config`: validated normalized configuration;
- `result.runs`: named `ReferenceRunResult` objects;
- `result.targets`: concatenated targets across all reference results; and
- `result.source_updates`: import/update counts for selected sources.

## CLI

```bash
# Validate without running.
target-selection validate-config --config configs/my_run.toml

# See available adapter types.
target-selection list-sources

# Run and print each output directory.
target-selection run --config configs/my_run.toml
```

The CLI and notebook call the same Python workflow and use the same TOML file.

## Advanced low-level API

`target_selection_pipeline.run_target_selection` remains available for an
advanced single-Rubin-release analysis with explicit dependency injection:

```python
from target_selection_pipeline import run_target_selection

targets, paths = run_target_selection(
    start_date="2026-08-09",
    end_date="2026-08-12",
    data_release="DP2",
)
```

Prefer `run_analysis` for normal work. It provides normalized source roles,
multiple reference surveys, planning-only operation, persistent registry
updates, configuration snapshots, and product indexing.
