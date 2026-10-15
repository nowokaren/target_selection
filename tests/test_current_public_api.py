from pathlib import Path

import target_selection
from target_selection.cache_paths import (
    CACHE_ROOT, OUTPUT_ROOT, PROJECT_ROOT, MOP_EVENT_CACHE, MOP_PHOTOMETRY_CACHE,
    RUBIN_PHOTOMETRY_CACHE, RUBIN_VISIT_CACHE, resolve_project_path,
)
from target_selection.planning_cli import main as plan_main


def test_public_exports_resolve():
    assert all(hasattr(target_selection, name) for name in target_selection.__all__)


def test_shared_cache_paths_are_canonical():
    assert OUTPUT_ROOT == PROJECT_ROOT / "outputs"
    assert CACHE_ROOT == OUTPUT_ROOT / "cache"
    assert OUTPUT_ROOT.is_absolute()
    assert resolve_project_path("outputs/test-run") == OUTPUT_ROOT / "test-run"
    assert MOP_EVENT_CACHE == CACHE_ROOT / "mop" / "event_data"
    assert MOP_PHOTOMETRY_CACHE == CACHE_ROOT / "mop" / "photometry"
    assert RUBIN_PHOTOMETRY_CACHE == CACHE_ROOT / "rubin" / "photometry"
    assert RUBIN_VISIT_CACHE == CACHE_ROOT / "rubin" / "visit_coverage"


def test_seasonal_plan_cli_requires_dates():
    import pytest

    with pytest.raises(SystemExit) as exc:
        plan_main([])
    assert exc.value.code == 2
