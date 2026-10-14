"""Compatibility import for :mod:`target_selection.visibility_plotter`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.visibility_plotter")
_sys.modules[__name__] = _implementation
