"""Compatibility import for :mod:`target_selection.observatory_observations`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.observatory_observations")
_sys.modules[__name__] = _implementation
