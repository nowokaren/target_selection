"""Compatibility import for :mod:`target_selection.target_report`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.target_report")
_sys.modules[__name__] = _implementation
