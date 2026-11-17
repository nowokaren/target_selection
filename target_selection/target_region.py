"""Compact target-region classification from standard event designations."""

from __future__ import annotations

import re


_REGION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BLG", re.compile(r"(?:^|[-_])BLG(?:[-_]|$)", re.IGNORECASE)),
    ("GD", re.compile(r"(?:^|[-_])(?:GD|DG)(?:[-_]|$)", re.IGNORECASE)),
    ("LMC", re.compile(r"(?:^|[-_])LMC(?:[-_]|$)", re.IGNORECASE)),
    ("SMC", re.compile(r"(?:^|[-_])SMC(?:[-_]|$)", re.IGNORECASE)),
)


def classify_target_region(target_name: object) -> str:
    """Return a compact region code inferred from an event designation.

    ``GD`` includes both ``GD`` and legacy ``DG`` Galactic-disk labels.
    Designations without one of the supported region codes are labeled
    ``OTHER``; coordinate-based fields are assigned separately by the
    seasonal priority table.
    """
    name = str(target_name).strip()
    for code, pattern in _REGION_PATTERNS:
        if pattern.search(name):
            return code
    return "OTHER"
