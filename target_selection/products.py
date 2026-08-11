"""Discoverable product index for each analysis run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


def write_product_index(
    paths: Mapping[str, Path],
    *,
    reference_survey: str,
) -> Path:
    """Write a compact index containing only products that exist."""
    run = Path(paths["run"])
    tables = Path(paths["tables"])
    candidates = {
        "configuration": (
            run / "analysis_config.json",
            "Exact normalized configuration used for the run",
        ),
        "targets": (
            tables / "combined_targets.csv",
            "Complete enriched target result",
        ),
        "planning_targets": (
            tables / "targets.csv",
            "Complete target result for a planning-only run",
        ),
        "visibility_summary": (
            tables / "visibility_target_summary.csv",
            "Per-target local visibility decision",
        ),
        "visibility_details": (
            Path(paths.get("visibility_plots", run / "visibility_plots"))
            / "visibility_selection.csv",
            "Per-target and per-night visibility metrics",
        ),
        "planning_visibility": (
            tables / "visibility.csv",
            "Per-target and per-night planning-only visibility metrics",
        ),
        "observing_selection": (
            tables / "observing_selection_summary.csv",
            "Compact observing-planning table",
        ),
        "source_catalog": (
            tables / "source_catalog.csv",
            "Targets grouped by provider or survey provenance",
        ),
        "source_updates": (
            tables / "source_updates.csv",
            "Sources imported or refreshed for this run",
        ),
        "monitoring_report": (
            Path(paths.get("monitoring_reports", run / "monitoring_reports"))
            / "monitoring_lightcurves.pdf",
            "Provider light curves and follow-up/reference temporal coverage",
        ),
    }
    products = {
        name: {
            "path": str(path.relative_to(run)),
            "description": description,
        }
        for name, (path, description) in candidates.items()
        if path.exists()
    }
    payload = {
        "reference_survey": reference_survey,
        "run_directory": str(run),
        "recommended_products": products,
        "additional_diagnostics": "See the tables and plot directories for backend diagnostics.",
    }
    destination = run / "product_index.json"
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return destination
