"""Compatibility import for :mod:`target_selection.release_photometry`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.release_photometry")
_sys.modules[__name__] = _implementation
