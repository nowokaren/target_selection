"""Local survey inventory tasks."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from target_registry import TargetRegistry


def import_hsh_inventory(
    inventory_path: str | Path,
    *,
    database_path: str | Path = "outputs/target_database/target_selection.sqlite",
    force: bool = False,
    verbose: bool = True,
) -> int:
    """Import the HSH astrometric image inventory into the registry."""
    if verbose:
        print(f"[surveys] Importing HSH inventory: {inventory_path}", flush=True)
    database = TargetRegistry(database_path)
    count = database.import_hsh_catalog(inventory_path, force=force)
    if verbose:
        print(f"[surveys] HSH records imported/updated: {count}", flush=True)
    return count


def observed_targets(
    *,
    providers: tuple[str, ...] = ("HSH",),
    database_path: str | Path = "outputs/target_database/target_selection.sqlite",
    verbose: bool = True,
) -> pd.DataFrame:
    """Return targets observed by local surveys already imported in the registry."""
    database = TargetRegistry(database_path)
    result = database.observed_targets(tuple(provider.upper() for provider in providers))
    if verbose:
        print(f"[surveys] Previously observed targets: {len(result)}", flush=True)
    return result
