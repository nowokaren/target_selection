# Target Selection

Target Selection combines candidate microlensing events, previous follow-up
observations, and optional reference-survey data to answer a practical
question: **which targets are worth observing from a given telescope and time
allocation?**

The normal workflow is:

```text
target providers + follow-up history
                 ↓
        common target list
                 ↓
  local visibility and scientific cuts
                 ↓
 optional reference-survey context
                 ↓
 tables, visibility plots, light curves, and reports
```

## Source roles

| Role | What it contributes | Current examples |
|---|---|---|
| Target provider | Candidates, event parameters, and possibly photometry | MOP; user CSV; future OMP |
| Follow-up survey | Targets and observations from the program being planned | CASLEO/HSH; CASLEO/JS |
| Reference survey | External visits, images, or photometry used as context | Rubin DP1 or DP2 |

Sources are selected as lists in one TOML file. Adding DP2, removing Rubin
entirely, or enabling a future provider does not require another keyword on the
main function.

## Smallest complete example

```bash
cp configs/example.toml configs/my_run.toml
target-selection validate-config --config configs/my_run.toml
target-selection run --config configs/my_run.toml
```

The command prints the run directory. Open its `product_index.json` first: it
lists the most useful products that actually exist for that run.

## Where to go next

- [Quick start](quickstart.md): install, configure, and run the tool.
- [Usage modes](usage-modes.md): choose MOP, HSH/JS, Rubin, or a combination.
- [Data and cache](data-and-cache.md): understand what is persistent and what
  belongs only to one run.
- [Products](products.md): find every recommended table, plot, and PDF.
- [Architecture proposal](architecture_restructure_proposal.md): proposed source-capability and task-based refactor; the PDF version is [target_selection_architecture_proposal.pdf](target_selection_architecture_proposal.pdf).
