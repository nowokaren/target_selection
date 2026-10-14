"""Compact target-region classification from standard event designations."""

from __future__ import annotations

import re


_REGION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("BLG", re.compile(r"(?:^|[-_])BLG(?:[-_]|$)", re.IGNORECASE)),
    ("GD", re.compile(r"(?:^|[-_])(?:GD|DG)(?:[-_]|$)", re.IGNORECASE)),
    ("LMC", re.compile(r"(?:^|[-_])LMC(?:[-_]|$)", re.IGNORECASE)),
    ("SMC", re.compile(r"(?:^|[-_])SMC(?:[-_]|$)", re.IGNORECASE)),
    (
        "XGAL",
        re.compile(r"(?:^|[-_])(?:M31|M33|M83|NGC\d*|IC\d+)(?:[-_]|$)", re.IGNORECASE),
    ),
)


def classify_target_region(target_name: object) -> str:
    """Return a compact region code inferred from an event designation.

    ``GD`` includes both ``GD`` and legacy ``DG`` Galactic-disk labels.
    ``XGAL`` is restricted to unambiguous named-galaxy designations; all
    other targets remain ``UNK`` rather than receiving a speculative label.
    """
    name = str(target_name).strip()
    for code, pattern in _REGION_PATTERNS:
        if pattern.search(name):
            return code
    return "UNK"
