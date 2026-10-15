"""Canonical paths for reusable downloaded and computed caches.

Run-specific tables, plots, and reports belong in their run output directory.
Reusable source data belongs under :data:`CACHE_ROOT` so separate runs share it.
Paths are anchored to this repository, independent of the notebook or shell working directory.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "outputs"
CACHE_ROOT = OUTPUT_ROOT / "cache"


def resolve_project_path(path: str | Path) -> Path:
    """Resolve a user path relative to the repository, never the notebook cwd."""
    value = Path(path).expanduser()
    return value if value.is_absolute() else PROJECT_ROOT / value


def output_path(*parts: str) -> Path:
    """Return an output path anchored at the repository output directory."""
    return OUTPUT_ROOT.joinpath(*parts)


def cache_path(*parts: str) -> Path:
    """Return a reusable cache path anchored at the single shared cache root."""
    return CACHE_ROOT.joinpath(*parts)

MOP_CACHE = CACHE_ROOT / "mop"
MOP_VISIBILITY_CACHE = MOP_CACHE / "visibility"
MOP_EVENT_CACHE = MOP_CACHE / "event_data"
MOP_PHOTOMETRY_CACHE = MOP_CACHE / "photometry"

OGLE_PHOTOMETRY_CACHE = CACHE_ROOT / "ogle" / "photometry"
OPM_PHOTOMETRY_CACHE = CACHE_ROOT / "opm" / "photometry"
BHTOM_PHOTOMETRY_CACHE = CACHE_ROOT / "bhtom" / "photometry"

RUBIN_CACHE = CACHE_ROOT / "rubin"
RUBIN_PHOTOMETRY_CACHE = RUBIN_CACHE / "photometry"
RUBIN_REFERENCE_CACHE = RUBIN_CACHE / "reference_catalog"
RUBIN_VISIT_CACHE = RUBIN_CACHE / "visit_coverage"
RUBIN_COADD_CACHE = RUBIN_CACHE / "coadd_coverage"
