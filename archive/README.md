# Archived material

This directory is reserved for historical snapshots that are not part of the
active Python package. Generated run outputs, notebook checkpoints, Python
bytecode, test caches, and the MkDocs `site/` directory are intentionally not
archived or versioned; they can be regenerated and are ignored by Git.

The active source layout is documented in `docs/architecture.md`. Reusable
application modules live in `target_selection/`; root-level modules with the
same public names are compatibility imports for existing notebooks and scripts.
The historical MOP–DP2 helper is kept under `analyses/mop_dp2/archive/`.

Two otherwise unrepresented notebooks were recovered from Jupyter checkpoints during the notebook consolidation. `microlensing_followup_prioritization_legacy.ipynb` explores an older score-based prioritization; the current method is the shared evidence evaluator. `hsh_daily_target_selection_legacy.ipynb` preserves embedded results from an earlier daily workflow. Neither is a current execution guide.
