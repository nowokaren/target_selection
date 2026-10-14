"""Compatibility import for :mod:`target_selection.mop_photometry`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.mop_photometry")
_sys.modules[__name__] = _implementation
