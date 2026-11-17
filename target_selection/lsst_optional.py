"""Small availability check for optional Rubin Science Platform features."""

from __future__ import annotations

import importlib.util


def lsst_available(*, operation: str, verbose: bool = True) -> bool:
    """Return whether Rubin client packages are installed, warning if not."""
    try:
        available = all(
            importlib.util.find_spec(module) is not None
            for module in ("lsst.rsp", "lsst.daf.butler")
        )
    except (ImportError, ModuleNotFoundError, ValueError):
        available = False
    if not available and verbose:
        print(
            f"[rubin] {operation} is unavailable: the LSST/Rubin client packages "
            "are not installed in this Python environment. Other target-selection "
            "features can still be used.",
            flush=True,
        )
    return available
