"""Filesystem layout helpers for target-selection runs.

The path convention is intentionally independent from the scientific tasks so
that notebooks can create or inspect a run without importing the legacy
pipeline backend.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from data_release_config import DataReleaseConfig, get_data_release


def safe_name(value: object) -> str:
    """Convert a user or target name into a safe path component."""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return text or "unknown_target"


def observing_window_slug(observing_windows=None) -> str:
    """Build a stable, readable directory component for an observing schedule."""
    def pair_label(value) -> str | None:
        if isinstance(value, tuple) and len(value) == 2 and all(isinstance(item, str) for item in value):
            return f"{value[0].replace(':', '-')}_to_{value[1].replace(':', '-')}"
        if isinstance(value, list):
            if len(value) == 2 and all(isinstance(item, str) for item in value):
                return f"{value[0].replace(':', '-')}_to_{value[1].replace(':', '-')}"
            pairs = []
            for item in value:
                if not isinstance(item, (tuple, list)) or len(item) != 2:
                    return None
                pairs.append(f"{str(item[0]).replace(':', '-')}_to_{str(item[1]).replace(':', '-')}")
            return "_and_".join(pairs) if pairs else "none"
        return None

    if observing_windows is None:
        schedule = "18-00_to_06-00"
    elif isinstance(observing_windows, dict):
        date_overrides = {key: value for key, value in observing_windows.items() if key != "default"}
        default_schedule = pair_label(observing_windows.get("default"))
        if not date_overrides and default_schedule is not None:
            schedule = default_schedule
        else:
            prefix = default_schedule or "custom"
            canonical = json.dumps(observing_windows, default=str, sort_keys=True, separators=(",", ":"))
            schedule = f"{prefix}_varied-{hashlib.sha256(canonical.encode()).hexdigest()[:8]}"
    else:
        schedule = pair_label(observing_windows)
        if schedule is None:
            canonical = json.dumps(observing_windows, default=str, sort_keys=True, separators=(",", ":"))
            schedule = f"custom-{hashlib.sha256(canonical.encode()).hexdigest()[:8]}"
    return safe_name(f"obs_{schedule}")


def create_run_structure(
    root_dir: str | Path,
    start_date: str,
    end_date: str,
    data_release: str | DataReleaseConfig = "DP2",
    observing_windows=None,
    run_name: str = "",
) -> dict[str, Path]:
    """Create and return the folders used by one analysis run."""
    date_label = start_date if start_date == end_date else f"{start_date}_to_{end_date}"
    label = f"{date_label}__{observing_window_slug(observing_windows)}"
    if str(run_name).strip():
        label = f"{safe_name(str(run_name))}_{label}"
    release = get_data_release(data_release)
    base_dir = Path(root_dir) if release.name == "DP2" else Path(root_dir) / safe_name(release.name)
    run_dir = base_dir / label
    paths = {
        "run": run_dir,
        "tables": run_dir / "tables",
        "sky_plots": run_dir / "sky_plots",
        "visibility_plots": run_dir / "visibility_plots",
        # Light-curve PNGs are persistent products shared by runs; the run PDF
        # lives directly in the run directory.
        "lightcurves": base_dir / "lightcurves",
        "monitoring_reports": run_dir,  # compatibility alias; no extra folder
        "target_reports": base_dir / "target_reports",
        # Compatibility aliases for callers using the pre-task API.
        "targets": base_dir / "target_reports",
        "targets_visibility_selected": base_dir / "target_reports",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths
