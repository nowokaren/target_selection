# Archived material

This directory is reserved for historical snapshots that are not part of the
active Python package. Generated run outputs, notebook checkpoints, Python
bytecode, test caches, and the MkDocs `site/` directory are intentionally not
archived or versioned; they can be regenerated and are ignored by Git.

The active source layout is documented in `docs/architecture.md`. The root
`target_selection_pipeline.py` file remains as a compatibility shim, while the
implementation is in `target_selection/backend.py`.
