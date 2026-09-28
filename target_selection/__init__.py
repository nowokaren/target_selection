"""Extensible target-selection and follow-up planning toolkit."""

from .config import AnalysisConfig, load_config
from . import report, target_io, visibility
from .target_io import describe_adapter, describe_source, list_adapters, list_sources
from .products import plot_sky_dual_metric
from .reporting import numeric_series, plot_collection_distributions, write_collection_summaries
from .rsp_access import (
    describe, get_coadd_coverage, get_coadd_cutouts, get_image, get_property_map_values,
    get_table, get_visit_coverage, list_images, list_property_maps, list_tables,
)


from .collection_access import (
    CollectionContext,
    open_collection,
    load_collection_coverage,
    load_collection_coadds,
    build_cutout_pdf,
    build_collection_report_pdf,
    generate_collection_cutouts,
)

from .tasks import (
    collect_mop_targets,
    load_target_list,
    merge_targets,
    restrict_targets,
    evaluate_visibility,
    save_visibility_plots,
    make_visibility_sequence,
    query_lsst_coverage,
    compute_lsst_photometry,
    save_lsst_photometry,
    create_target_report,
    create_target_reports,
    create_lightcurve_plots,
    create_lightcurves_report,
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
    "create_lightcurves_report",
    "create_target_reports",
    "create_lightcurve_plots",
    "create_target_report",
    "save_lsst_photometry",
    "compute_lsst_photometry",
    "query_lsst_coverage",
    "make_visibility_sequence",
    "save_visibility_plots",
    "evaluate_visibility",
    "merge_targets",
    "restrict_targets",
    "load_target_list",
    "collect_mop_targets",
    "plot_sky_dual_metric",
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
    "CollectionContext",
    "open_collection",
    "load_collection_coverage",
    "load_collection_coadds",
    "build_cutout_pdf",
    "build_collection_report_pdf",
    "generate_collection_cutouts",
    "numeric_series",
    "write_collection_summaries",
    "plot_collection_distributions",
    "get_property_map_values",
    "get_table",
    "get_visit_coverage",
    "get_coadd_coverage",
    "get_coadd_cutouts",
    "get_image",
    "list_tables",
    "list_property_maps",
    "list_images",
    "describe",
    "list_sources",
    "describe_source",
    "list_adapters",
    "describe_adapter",
    "target_io",
    "report",
    "visibility",
]

