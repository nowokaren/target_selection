"""Compatibility import for :mod:`target_selection.data_release_config`."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("target_selection.data_release_config")
_sys.modules[__name__] = _implementation
