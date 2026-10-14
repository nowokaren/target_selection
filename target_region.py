"""Compatibility import for :mod:`target_selection.target_region`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.target_region")
_sys.modules[__name__] = _implementation
