"""Built-in adapters for current providers and surveys."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_release_config import get_data_release
from target_selection.config import SourceSpec
from target_selection.sources.base import SourceContext
from target_selection.sources.normalization import (
    OBSERVATION_ALIASES,
    PHOTOMETRY_ALIASES,
    TARGET_ALIASES,
    normalize_columns,
)


def _required_path(spec: SourceSpec, key: str = "path") -> Path:
    value = spec.options.get(key)
    if not value:
        raise ValueError(f"Source {spec.name!r} requires the {key!r} option")
    path = Path(str(value)).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Source {spec.name!r} file does not exist: {path}")
    return path


def _normalize_targets(
    data: pd.DataFrame,
    source_name: str,
    *,
    column_map: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Normalize a target table and attach its coordinate provenance."""
    normalized = normalize_columns(
        data,
        aliases=TARGET_ALIASES,
        column_map=column_map,
        source_name=source_name,
        data_kind="Target",
    )
    required = {"Target", "RA_deg", "Dec_deg"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(
            f"Target source {source_name!r} is missing columns: {sorted(missing)}"
        )
    normalized["Target"] = normalized["Target"].astype(str).str.strip()
    normalized["RA_deg"] = pd.to_numeric(normalized["RA_deg"], errors="coerce")
    normalized["Dec_deg"] = pd.to_numeric(normalized["Dec_deg"], errors="coerce")
    normalized["target_source"] = source_name
    normalized["coordinate_source"] = source_name
    normalized["coordinate_priority"] = 80
    return (
        normalized.loc[
            normalized["Target"].ne("")
            & normalized["RA_deg"].notna()
            & normalized["Dec_deg"].notna()
        ]
        .drop_duplicates("Target", keep="first")
        .reset_index(drop=True)
    )


def _import_photometry_if_configured(
    spec: SourceSpec,
    provider_name: str,
    context: SourceContext,
) -> int:
    """Import an optional follow-up photometry CSV using its declared map."""
    value = spec.options.get("photometry_path")
    if not value:
        return 0
    path = Path(str(value)).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Photometry file does not exist for {spec.name!r}: {path}"
        )
    stat = path.stat()
    source_id = f"photometry:{path.resolve()}"
    fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
    if (
        context.config.cache.reuse
        and not context.config.cache.refresh_followup_surveys
        and context.database.source_is_current(provider_name, source_id, fingerprint)
    ):
        return 0
    data = normalize_columns(
        pd.read_csv(path),
        aliases=PHOTOMETRY_ALIASES,
        column_map=spec.options.get("photometry_column_map"),
        source_name=spec.name,
        data_kind="Photometry",
    )
    if "mjd" not in data and "Timestamp" in data:
        timestamps = pd.to_datetime(data["Timestamp"], errors="coerce", utc=True)
        data["mjd"] = timestamps.map(
            lambda value: value.to_julian_date() - 2_400_000.5
            if pd.notna(value)
            else None
        )
    count = context.database.import_photometry(
        provider_name,
        data,
        source_role="followup_survey",
    )
    context.database.mark_source(provider_name, source_id, fingerprint)
    return count


class MopTargetProvider:
    """MOP target, event-parameter, and photometry adapter."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec

    def client(self, context: SourceContext) -> Any:
        if self.spec.name in context.clients:
            return context.clients[self.spec.name]
        if "mop" in context.clients:
            return context.clients["mop"]
        from target_selection_pipeline import _create_mop_client

        return _create_mop_client()

    def collect_targets(self, context: SourceContext) -> pd.DataFrame:
        client = self.client(context)
        daily = client.visible_targets(
            observatory=context.config.observatory,
            start_date=context.config.start_date,
            end_date=context.config.resolved_end_date,
            sort_by_mag=False,
        )
        from target_selection_pipeline import _apply_authoritative_mop_coordinates

        if not bool(self.spec.options.get("enrich", True)):
            targets = _normalize_targets(daily, self.spec.name)
            targets["coordinate_source"] = "MOP visibility table"
            targets["coordinate_priority"] = 90
            return targets
        targets = client.visibility_summary(
            observatory=context.config.observatory,
            start_date=context.config.start_date,
            end_date=context.config.resolved_end_date,
            include_microlensing_parameters=True,
            parameter_errors="ignore",
            daily_targets=daily,
            parameter_max_workers=context.config.runtime.max_workers,
            parameter_cache_dir=context.cache_dir / "mop_event_cache",
            photometry_dir=context.cache_dir / "mop_photometry",
            refresh_parameters=context.config.cache.refresh_target_providers,
        ).assign(target_source=self.spec.name, is_mop_visible_in_run=True)
        return _apply_authoritative_mop_coordinates(targets)


class CsvTargetProvider:
    """User or external-provider target list stored as a CSV file."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec

    def client(self, context: SourceContext) -> None:
        return None

    def collect_targets(self, context: SourceContext) -> pd.DataFrame:
        return _normalize_targets(
            pd.read_csv(_required_path(self.spec)),
            self.spec.name,
            column_map=self.spec.options.get("column_map"),
        )


class HshFollowupSurvey:
    """CASLEO/HSH survey adapter for the native astrometric image inventory."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec
        self.provider_name = str(spec.options.get("provider_name", "HSH")).upper()

    @property
    def inventory_path(self) -> Path:
        return _required_path(self.spec, "inventory_path")

    def import_observations(self, context: SourceContext) -> int:
        observations = context.database.import_hsh_catalog(
            self.inventory_path,
            force=context.config.cache.refresh_followup_surveys,
        )
        photometry = _import_photometry_if_configured(
            self.spec,
            self.provider_name,
            context,
        )
        return observations + photometry

    def observed_targets(self, context: SourceContext) -> pd.DataFrame:
        return context.database.observed_targets((self.provider_name,))


class CsvFollowupSurvey:
    """CSV follow-up observation inventory for JS or future surveys."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec
        self.provider_name = str(spec.options.get("provider_name", spec.name)).upper()

    @property
    def inventory_path(self) -> Path:
        return _required_path(self.spec, "inventory_path")

    def import_observations(self, context: SourceContext) -> int:
        path = self.inventory_path
        stat = path.stat()
        source_id = str(path.resolve())
        fingerprint = f"{stat.st_mtime_ns}:{stat.st_size}"
        current = (
            context.config.cache.reuse
            and not context.config.cache.refresh_followup_surveys
            and context.database.source_is_current(
                self.provider_name, source_id, fingerprint
            )
        )
        if current:
            observations = 0
        else:
            data = normalize_columns(
                pd.read_csv(path),
                aliases=OBSERVATION_ALIASES,
                column_map=self.spec.options.get("inventory_column_map"),
                source_name=self.spec.name,
                data_kind="Observation",
            )
            observations = context.database.import_survey_observations(
                self.provider_name,
                data,
                source_id=source_id,
            )
            context.database.mark_source(self.provider_name, source_id, fingerprint)
        photometry = _import_photometry_if_configured(
            self.spec,
            self.provider_name,
            context,
        )
        return observations + photometry

    def observed_targets(self, context: SourceContext) -> pd.DataFrame:
        return context.database.observed_targets((self.provider_name,))


class RubinReferenceSurvey:
    """Rubin Data Preview/Data Release adapter."""

    def __init__(self, spec: SourceSpec):
        self.spec = spec

    def data_release(self):
        release_name = self.spec.options.get(
            "data_release", self.spec.options.get("collection")
        )
        if not release_name:
            raise ValueError(
                f"Reference survey {self.spec.name!r} requires data_release or collection"
            )
        return get_data_release(str(release_name))
