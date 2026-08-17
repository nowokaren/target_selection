"""Task-level public API for target_selection.

These functions are the preferred way to use the package from notebooks and
scripts when direct Python variables are more convenient than a full config
file.
"""

from .targets import collect_mop_targets, load_target_list, merge_targets, restrict_targets
from .surveys import import_hsh_inventory, observed_targets
from .visibility import evaluate_visibility, save_visibility_plots, make_visibility_sequence
from .lsst import query_lsst_coverage, compute_lsst_photometry, save_lsst_photometry
from .reports import create_target_report, create_target_reports, create_lightcurves_report
from .pipeline import run_target_selection_tasks

__all__ = [
    "observed_targets",
    "import_hsh_inventory",
    "collect_mop_targets",
    "load_target_list",
    "merge_targets",
    "restrict_targets",
    "evaluate_visibility",
    "save_visibility_plots",
    "make_visibility_sequence",
    "query_lsst_coverage",
    "compute_lsst_photometry",
    "save_lsst_photometry",
    "create_target_report",
    "create_target_reports",
    "create_lightcurves_report",
    "run_target_selection_tasks",
]
