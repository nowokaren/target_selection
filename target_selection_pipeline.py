"""Backward-compatible import shim for the packaged target-selection backend.

New code should import task functions from :mod:`target_selection` and source
adapters from :mod:`target_selection.sources`. This module remains so existing
notebooks and scripts using ``target_selection_pipeline`` continue to work.
"""

from target_selection import backend as _backend

# Include private compatibility helpers as well as public functions because
# older notebooks imported a few underscore-prefixed utilities directly.
globals().update({
    name: value
    for name, value in vars(_backend).items()
    if not name.startswith("__")
})
