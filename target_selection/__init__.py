"""Extensible target-selection and follow-up planning toolkit."""

from .config import AnalysisConfig, load_config
from .reference_catalog import (
    ReferenceCatalogConfig,
    ReferenceCatalogResult,
    load_reference_catalog_config,
    run_reference_catalog,
    run_reference_catalog_preview,
    run_reference_coverage_catalog,
    enrich_reference_catalog_coadds,
    generate_reference_catalog_cutouts,
)

__all__ = [
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
