"""Public visibility analysis API.

The facade accepts the canonical v2 target columns and keeps the legacy
columns needed internally by the established visibility implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from .tasks.visibility import (
    evaluate_visibility,
    make_visibility_sequence,
    save_visibility_plots,
)
from visibility_plotter import summarize_visibility_selection


def _legacy_targets(targets: pd.DataFrame) -> pd.DataFrame:
    """Add legacy visibility aliases without changing the caller's table."""
    work = targets.copy()
    aliases = {
        "target_id": "Target",
        "ra_deg": "RA_deg",
        "dec_deg": "Dec_deg",
    }
    for canonical, legacy in aliases.items():
        if legacy not in work and canonical in work:
            work[legacy] = work[canonical]
    return work


def _canonical_selection(selection: pd.DataFrame) -> pd.DataFrame:
    """Restore canonical aliases in a visibility result."""
    result = selection.copy()
    aliases = {
        "Target": "target_id",
        "RA_deg": "ra_deg",
        "Dec_deg": "dec_deg",
    }
    for legacy, canonical in aliases.items():
        if canonical not in result and legacy in result:
            result[canonical] = result[legacy]
    return result


def evaluate(targets: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Evaluate target visibility for dates, observatory and constraints."""
    result = evaluate_visibility(_legacy_targets(targets), **kwargs)
    return _canonical_selection(result)


def summary(visibility_selection: pd.DataFrame) -> pd.DataFrame:
    """Return one visibility summary row per target."""
    result = summarize_visibility_selection(_legacy_targets(visibility_selection))
    return _canonical_selection(result)


def plots(targets: pd.DataFrame, *, output_dir: str | Path, **kwargs) -> list[Path]:
    """Write nightly visibility plots for a target table."""
    selection = kwargs.get("selection")
    if selection is not None:
        kwargs["selection"] = _legacy_targets(selection)
    return save_visibility_plots(_legacy_targets(targets), output_dir=output_dir, **kwargs)


def sequence(visibility_selection: pd.DataFrame, output_path: str | Path, **kwargs) -> Path:
    """Create a stacked visibility sequence from an evaluated selection."""
    return make_visibility_sequence(_legacy_targets(visibility_selection), output_path, **kwargs)


__all__ = ["evaluate", "summary", "plots", "sequence"]
