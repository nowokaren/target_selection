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
) -> int:
    """Import the HSH astrometric image inventory into the registry."""
    database = TargetRegistry(database_path)
    return database.import_hsh_catalog(inventory_path, force=force)


def observed_targets(
    *,
    providers: tuple[str, ...] = ("HSH",),
    database_path: str | Path = "outputs/target_database/target_selection.sqlite",
) -> pd.DataFrame:
    """Return targets observed by local surveys already imported in the registry."""
    database = TargetRegistry(database_path)
    return database.observed_targets(tuple(provider.upper() for provider in providers))
