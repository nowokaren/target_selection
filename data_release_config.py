"""Release-specific names for Rubin RSP target-selection runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class DataReleaseConfig:
    """Names that vary between Rubin data releases.

    Canonical output names remain fixed, so the rest of the pipeline does not
    need release-specific conditionals. Profiles may be replaced or registered
    by users when an RSP deployment exposes a different table version.
    """

    name: str
    rsp_instance: str
    butler_repo: str
    butler_collections: str | tuple[str, ...] | None
    tap_visit_table: str
    visit_columns: Mapping[str, str]
    coadd_dataset_types: tuple[str, ...]
    coadd_spatial_where: str
    calexp_dataset_types: tuple[str, ...] = ("calexp",)
    calexp_data_id_keys: Mapping[str, str] = field(
        default_factory=lambda: {"visitId": "visit", "detector": "detector"}
    )
    photometry_method: str = "calexp_forced"
    tap_dia_object_table: str | None = None
    tap_dia_forced_source_table: str | None = None
    notes: str = ""

    def visit_select(self, alias: str = "vd") -> str:
        required = ("visitId", "expMidptMJD", "band", "detector", "seeing", "magLim")
        missing = [name for name in required if name not in self.visit_columns]
        if missing:
            raise ValueError(f"{self.name}: missing TAP aliases: {missing}")
        return ", ".join(
            f"{alias}.{self.visit_columns[name]} AS {name}" for name in required
        )

    def calexp_data_id(self, visit_id: int, detector: int) -> dict[str, int]:
        """Return the Butler data ID for one calibrated single-detector exposure."""
        return {
            self.calexp_data_id_keys["visitId"]: int(visit_id),
            self.calexp_data_id_keys["detector"]: int(detector),
        }

    @property
    def tap_ra(self) -> str:
        return self.visit_columns["ra"]

    @property
    def tap_dec(self) -> str:
        return self.visit_columns["dec"]


_DP2_COLUMNS = {
    "visitId": "visitId", "expMidptMJD": "expMidptMJD", "band": "band",
    "detector": "detector", "seeing": "seeing", "magLim": "magLim",
    "ra": "ra", "dec": "dec",
}
_CCD_VISIT_COLUMNS = {
    "visitId": "visitId", "expMidptMJD": "expMidptMJD", "band": "band",
    "detector": "detector", "seeing": "seeing", "magLim": "magLim",
    "ra": "s_ra", "dec": "s_dec",
}

RELEASES: dict[str, DataReleaseConfig] = {
    "DP0.1": DataReleaseConfig(
        name="DP0.1", rsp_instance="dp01", butler_repo="dp01",
        butler_collections="2.2i/runs/DP0.1",
        tap_visit_table="dp01_dc2_catalogs.CcdVisit",
        visit_columns=_CCD_VISIT_COLUMNS, coadd_dataset_types=("deepCoadd",),
        coadd_spatial_where="patch.region OVERLAPS POINT(:ra,:dec)",
        notes="Legacy DC2 release; availability depends on the RSP deployment.",
    ),
    "DP0.2": DataReleaseConfig(
        name="DP0.2", rsp_instance="dp02", butler_repo="dp02",
        butler_collections="2.2i/runs/DP0.2",
        tap_visit_table="dp02_dc2_catalogs.CcdVisit",
        visit_columns=_CCD_VISIT_COLUMNS, coadd_dataset_types=("deepCoadd",),
        coadd_spatial_where="patch.region OVERLAPS POINT(:ra,:dec)",
    ),
    "DP1": DataReleaseConfig(
        name="DP1", rsp_instance="dp1", butler_repo="dp1",
        butler_collections=None, tap_visit_table="dp1.CcdVisit",
        visit_columns=_CCD_VISIT_COLUMNS,
        coadd_dataset_types=("deep_coadd", "deepCoadd"),
        coadd_spatial_where="patch.region OVERLAPS POINT(:ra,:dec)",
        notes="Collection defaults can differ by RSP deployment; override when needed.",
    ),
    "DP2": DataReleaseConfig(
        name="DP2", rsp_instance="dp2", butler_repo="dp2",
        butler_collections="dp2", tap_visit_table="dp2.VisitDetector",
        visit_columns=_DP2_COLUMNS, coadd_dataset_types=("deep_coadd",),
        coadd_spatial_where="patch.region OVERLAPS POINT(:ra,:dec)",
        photometry_method="coadd_forced",
        tap_dia_object_table="dp2.DiaObject",
        tap_dia_forced_source_table="dp2.ForcedSourceOnDiaObject",
        notes=("Early DP2 publishes deep coadds but not calibrated individual visit images; "
               "target photometry is therefore measured on the coadds."),
    ),
}


def get_data_release(name: str | DataReleaseConfig = "DP2") -> DataReleaseConfig:
    """Return a profile by case-insensitive name, or pass custom profiles through."""
    if isinstance(name, DataReleaseConfig):
        return name
    normalized = str(name).strip().upper().replace("DP0_", "DP0.")
    for key, profile in RELEASES.items():
        if key.upper() == normalized:
            return profile
    choices = ", ".join(RELEASES)
    raise ValueError(f"Unknown release {name!r}. Options: {choices}")


def register_data_release(profile: DataReleaseConfig) -> None:
    """Register or replace a profile for a specific RSP deployment."""
    RELEASES[profile.name] = profile
