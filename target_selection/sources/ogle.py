"""Public OGLE-IV Early Warning System adapter.

The adapter uses the public EWS event table and the per-event ``phot.dat``
files. It deliberately treats EWS model parameters as preliminary metadata;
they are not silently promoted to a final microlensing fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
from pathlib import Path
import re
from urllib.parse import urljoin

import pandas as pd
import requests

from ..contracts import normalize_observations, normalize_targets, validate_observations, validate_targets


EWS_URL = "https://ogle.astrouw.edu.pl/ogle4/ews/ews.html"
ARCHIVE_ROOT = "https://www.astrouw.edu.pl/ogle/ogle4/ews"
LMC_OPTICAL_DEPTH_ROOT = "https://www.astrouw.edu.pl/ogle/ogle4/LMC_OPTICAL_DEPTH"


def _session(session=None):
    return session or requests.Session()


def _sexagesimal_ra(value: str) -> float:
    hours, minutes, seconds = (float(item) for item in str(value).strip().split(":"))
    return 15.0 * (hours + minutes / 60.0 + seconds / 3600.0)


def _sexagesimal_dec(value: str) -> float:
    text = str(value).strip()
    sign = -1.0 if text.startswith("-") else 1.0
    degrees, minutes, seconds = (float(item) for item in text.lstrip("+-").split(":"))
    return sign * (degrees + minutes / 60.0 + seconds / 3600.0)


def _event_paths(event_id: str) -> dict[str, str]:
    event = str(event_id).strip()
    parts = event.split("-")
    lmc_match = re.fullmatch(r"OGLE-LMC-(\d{2})", event, flags=re.IGNORECASE)
    if lmc_match:
        # OGLE-LMC-04..19 are from the LMC optical-depth release, not EWS.
        number = int(lmc_match.group(1))
        if 4 <= number <= 19:
            return {
                "event_url": "https://www.astrouw.edu.pl/ogle/ogle4/LMC_OPTICAL_DEPTH/",
                "photometry_url": f"{LMC_OPTICAL_DEPTH_ROOT}/phot/{event.upper()}.dat",
                "archive_url": f"{LMC_OPTICAL_DEPTH_ROOT}/phot.tar.gz",
                "finding_chart_url": "",
            }
    if len(parts) != 4 or parts[0].upper() != "OGLE" or not parts[1].isdigit():
        raise ValueError(f"Expected OGLE-YYYY-FIELD-NNNN event ID, got {event!r}")
    year = parts[1]
    slug = f"{parts[2].lower()}-{parts[3]}"
    base = f"{ARCHIVE_ROOT}/{year}/{slug}"
    return {
        "event_url": f"https://ogle.astrouw.edu.pl/ogle4/ews/{year}/{slug}.html",
        "photometry_url": f"{base}/phot.dat",
        "archive_url": f"{base}.tar.gz",
        "finding_chart_url": f"{base}/fchart.fts",
    }


class OGLEAdapter:
    """Access OGLE EWS event metadata and public I-band photometry."""

    name = "ogle"
    kind = "survey"
    features = ("targets", "parameters", "epochs", "photometry")

    def __init__(self, *, ews_url: str = EWS_URL, session=None, timeout: float = 60.0) -> None:
        self.ews_url = ews_url
        self.session = _session(session)
        self.timeout = float(timeout)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "features": list(self.features),
            "catalog_url": self.ews_url,
            "photometry": "OGLE EWS public I-band phot.dat files",
            "quality_note": "EWS parameters and photometry are preliminary/provisional.",
        }

    def supports(self, feature: str) -> bool:
        return str(feature).lower() in self.features

    def load_targets(
        self,
        *,
        filters: Mapping[str, object] | None = None,
        season: int | str | None = None,
        magnitude_range: tuple[float | None, float | None] | None = None,
        ra_range: tuple[float, float] | None = None,
        dec_range: tuple[float, float] | None = None,
        **_,
    ) -> pd.DataFrame:
        response = self.session.get(self.ews_url, timeout=self.timeout)
        response.raise_for_status()
        text = response.content.decode("latin-1")
        table = pd.read_html(StringIO(text))[0]
        table = table.rename(columns={
            "Event": "target_id",
            "RA (J2000)": "ra_deg",
            "Dec (J2000)": "dec_deg",
            "Tmax (HJD)": "t0_hjd",
            "tau": "tE_days",
            "Umin": "u0",
            "Ibl": "magnitude_baseline_I",
            "I0": "magnitude_lens_I",
            "Star No": "ogle_star_id",
        })
        table = table.loc[table["target_id"].notna()].copy()
        # The EWS table omits the OGLE prefix used by TOM IDs and event URLs.
        table["target_id"] = table["target_id"].astype(str).str.strip().str.replace(
            r"^(\d{4}-[A-Za-z]+-\d+)$", r"OGLE-\1", regex=True,
        )
        if season is not None:
            prefix = f"OGLE-{int(season)}-"
            table = table.loc[table["target_id"].astype(str).str.startswith(prefix)].copy()
        table["ra_deg"] = table["ra_deg"].map(_sexagesimal_ra)
        table["dec_deg"] = table["dec_deg"].map(_sexagesimal_dec)
        table["provider"] = self.name
        table["category"] = "ews_candidate"
        paths = table["target_id"].map(_event_paths)
        for column in ("event_url", "photometry_url", "archive_url", "finding_chart_url"):
            table[column] = paths.map(lambda item, key=column: item[key])
        table = normalize_targets(table, provider=self.name)
        if filters:
            from .catalog_adapters import _apply_filters
            table = _apply_filters(table, filters, category_column="category", magnitude_column="magnitude_baseline_I", source_name=self.name)
        if magnitude_range is not None:
            lower, upper = magnitude_range
            magnitude = pd.to_numeric(table["magnitude_baseline_I"], errors="coerce")
            if lower is not None:
                table = table.loc[magnitude.ge(float(lower))].copy()
            if upper is not None:
                table = table.loc[magnitude.le(float(upper))].copy()
        if ra_range is not None:
            ra_min, ra_max = (float(value) for value in ra_range)
            ra = pd.to_numeric(table["ra_deg"], errors="coerce")
            ra_mask = ra.ge(ra_min) & ra.le(ra_max) if ra_min <= ra_max else ra.ge(ra_min) | ra.le(ra_max)
            table = table.loc[ra_mask].copy()
        if dec_range is not None:
            dec_min, dec_max = (float(value) for value in dec_range)
            dec = pd.to_numeric(table["dec_deg"], errors="coerce")
            table = table.loc[dec.ge(dec_min) & dec.le(dec_max)].copy()
        return validate_targets(table)

    def photometry(self, targets: pd.DataFrame, *, cache_dir: str | Path | None = None, refresh: bool = False, errors: str = "raise", **_) -> pd.DataFrame:
        """Download OGLE EWS ``phot.dat`` rows as normalized observations."""
        cache = Path(cache_dir) if cache_dir is not None else None
        if cache is not None:
            cache.mkdir(parents=True, exist_ok=True)
        records: list[pd.DataFrame] = []
        for target in targets.itertuples(index=False):
            target_id = str(getattr(target, "target_id"))
            url = getattr(target, "photometry_url", None) or _event_paths(target_id)["photometry_url"]
            filename = cache / f"{target_id.replace('/', '_')}.phot.dat" if cache is not None else None
            try:
                if filename is not None and filename.exists() and not refresh:
                    raw = filename.read_text(encoding="latin-1")
                else:
                    response = self.session.get(url, timeout=self.timeout)
                    response.raise_for_status()
                    raw = response.content.decode("latin-1")
                    if filename is not None:
                        filename.write_text(raw, encoding="latin-1")
                rows = pd.read_csv(StringIO(raw), sep=r"\s+", header=None, names=["epoch_hjd", "magnitude", "magnitude_err", "seeing_arcsec", "sky_background"])
                rows["target_id"] = target_id
                rows["provider"] = self.name
                rows["band"] = "I"
                rows["observation_id"] = [f"OGLE:{target_id}:{index}" for index in range(len(rows))]
                rows["is_usable"] = rows[["epoch_hjd", "magnitude", "magnitude_err"]].notna().all(axis=1) & rows["magnitude_err"].gt(0)
                records.append(rows)
            except Exception:
                if errors == "raise":
                    raise
        if not records:
            return pd.DataFrame(columns=["target_id", "provider", "epoch_hjd", "band", "observation_id", "magnitude", "magnitude_err", "is_usable"])
        return validate_observations(normalize_observations(pd.concat(records, ignore_index=True), provider=self.name))

    epochs = photometry
