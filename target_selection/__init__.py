"""Extensible target-selection and follow-up planning toolkit."""

from .config import AnalysisConfig, load_config

from .tasks import (
    collect_mop_targets,
    load_target_list,
    merge_targets,
    evaluate_visibility,
    save_visibility_plots,
    make_visibility_sequence,
    query_lsst_coverage,
    compute_lsst_photometry,
    save_lsst_photometry,
    create_target_report,
    create_target_reports,
    create_lightcurves_report,
    run_target_selection_tasks,
    import_hsh_inventory,
    observed_targets,
)

from .reference_catalog import (
    ReferenceCatalogConfig,
    ReferenceCatalogResult,
    load_reference_catalog_config,
    run_reference_catalog,
    run_reference_catalog_preview,
    run_reference_coverage_catalog,
    query_direct_coadd_coverage,
    enrich_reference_catalog_coadds,
    generate_reference_catalog_cutouts,
)

__all__ = [
    "observed_targets",
    "import_hsh_inventory",
    "run_target_selection_tasks",
    "create_lightcurves_report",
    "create_target_reports",
    "create_target_report",
    "save_lsst_photometry",
    "compute_lsst_photometry",
    "query_lsst_coverage",
    "make_visibility_sequence",
    "save_visibility_plots",
    "evaluate_visibility",
    "merge_targets",
    "load_target_list",
    "collect_mop_targets",
    "AnalysisConfig",
    "AnalysisResult",
    "ReferenceCatalogConfig",
    "ReferenceCatalogResult",
    "load_config",
    "load_reference_catalog_config",
    "run_analysis",
    "run_reference_catalog",
    "run_reference_catalog_preview",
    "run_reference_coverage_catalog",
    "query_direct_coadd_coverage",
    "enrich_reference_catalog_coadds",
    "generate_reference_catalog_cutouts",
]


def __getattr__(name: str):
    # Keep catalog-only and configuration-only commands usable when optional
    # root-level workflow modules are not installed in the current environment.
    if name in {"AnalysisResult", "run_analysis"}:
        from .workflow import AnalysisResult, run_analysis

        return {"AnalysisResult": AnalysisResult, "run_analysis": run_analysis}[name]
    raise AttributeError(name)
