"""OPM (Galactic Science OPM) target and photometry adapter."""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
import json
import re
from urllib.parse import urlparse, urlunparse

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time

from ..contracts import normalize_targets, validate_targets
from ..credentials import get_credentials


OPM_URL = "https://lensingtom.zah.uni-heidelberg.de"
RANKED_TARGETS_PATH = "/ranked-microlensing-targets/"
NEW_ACTIVE_TARGETS_PATH = "/new_and_active_targets"
PUBLIC_RANKING_PATH = RANKED_TARGETS_PATH
TARGET_API_PATH = "/api/targets/"


def _split_latest_magnitude(value: object) -> tuple[float | None, str | None]:
    text = "" if pd.isna(value) else str(value).strip()
    match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*\(([^)]+)\)", text)
    if not match:
        return None, None
    return float(match.group(1)), match.group(2).strip()


def _split_coordinates(value: object) -> tuple[float | None, float | None]:
    text = "" if pd.isna(value) else str(value).strip()
    values = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    return (float(values[0]), float(values[1])) if len(values) >= 2 else (None, None)


class OPMAdapter:
    """Access public OPM rankings and authenticated OPM pages."""

    name = "opm"
    kind = "tom"
    features = ("targets", "ranking", "new_active_targets", "region_search", "photometry", "authenticated_session")

    def __init__(self, *, base_url: str = OPM_URL, session=None, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = float(timeout)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "features": list(self.features),
            "public_ranking": f"{self.base_url}{PUBLIC_RANKING_PATH}",
            "new_and_active": f"{self.base_url}{NEW_ACTIVE_TARGETS_PATH}",
            "target_api": f"{self.base_url}{TARGET_API_PATH}",
            "region_filter": "cone_search=RA,Dec,radius_deg over the scheduled sky region",
            "private_data": "requires an authenticated OPM account",
        }

    def supports(self, feature: str) -> bool:
        return str(feature).lower() in self.features

    def ranking(self) -> pd.DataFrame:
        response = self.session.get(f"{self.base_url}{RANKED_TARGETS_PATH}", timeout=self.timeout)
        response.raise_for_status()
        page_links = {}
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            match = re.fullmatch(r"/targets/(\d+)/?", anchor["href"])
            label = anchor.get_text(" ", strip=True).split()
            if match and label:
                page_links[label[0]] = {
                    "page_id": int(match.group(1)),
                    "url": f"{self.base_url}{match.group(0)}",
                }
        table = pd.read_html(StringIO(response.text))[0]
        table = table.rename(columns={
            "Targetname Broker links": "target_id",
            "Latest Mag (Band)": "latest_mag_band",
            "RA (deg), Dec (deg)": "coordinates",
            "Probability-based Rescaled Average": "opm_probability_score",
            "Days Since Created": "opm_days_since_created",
            "Updated": "opm_updated",
        })
        # The first HTML column contains the target name followed by broker
        # labels/links. Keep the target name as the stable identifier and
        # preserve the original combined column separately.
        table["opm_target_links"] = table["target_id"]
        table["target_id"] = table["target_id"].astype(str).str.split().str[0]
        table["opm_target_page_id"] = table["target_id"].map(
            lambda target_id: page_links.get(str(target_id), {}).get("page_id")
        )
        table["opm_target_url"] = table["target_id"].map(
            lambda target_id: page_links.get(str(target_id), {}).get("url")
        )
        magnitude = table["latest_mag_band"].map(_split_latest_magnitude)
        coordinates = table["coordinates"].map(_split_coordinates)
        table["magnitude_last"] = magnitude.map(lambda item: item[0])
        table["band_last"] = magnitude.map(lambda item: item[1])
        table["ra_deg"] = coordinates.map(lambda item: item[0])
        table["dec_deg"] = coordinates.map(lambda item: item[1])
        table["provider"] = self.name
        table["category"] = "public_ranking"
        table["opm_candidate_list"] = "ranked_microlensing"
        table["opm_ranked_microlensing"] = True
        return validate_targets(normalize_targets(table, provider=self.name))

    def new_and_active_targets(self) -> pd.DataFrame:
        """Load OPM's current new/active list and enrich its entries with coordinates."""
        self.login()
        response = self.session.get(f"{self.base_url}{NEW_ACTIVE_TARGETS_PATH}", timeout=self.timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_ids = {}
        for anchor in soup.find_all("a", href=True):
            match = re.fullmatch(r"/targets/(\d+)/?", anchor["href"])
            label = anchor.get_text(" ", strip=True)
            if match and label:
                page_ids[label] = int(match.group(1))
        tables = pd.read_html(StringIO(response.text))
        if not tables:
            return pd.DataFrame()
        table = tables[0].rename(columns={
            "Targetname Broker links": "opm_target_links",
            "Probability-based Rescaled Average": "opm_probability_score",
            "Days Since Created": "opm_days_since_created",
            "Updated": "opm_updated",
        })
        table["target_id"] = table["opm_target_links"].astype(str).str.split().str[0]
        table["opm_target_page_id"] = table["target_id"].map(page_ids)
        table["opm_target_url"] = table["opm_target_page_id"].map(
            lambda page_id: f"{self.base_url}/targets/{int(page_id)}/" if pd.notna(page_id) else ""
        )
        table["provider"] = self.name
        table["category"] = "new_and_active"
        table["opm_candidate_list"] = "new_and_active"
        table["opm_new_active"] = True

        enriched = []
        page_ids = table["opm_target_page_id"].dropna().astype(int).unique()
        for index, page_id in enumerate(page_ids, start=1):
            try:
                page = self.session.get(f"{self.base_url}/targets/{page_id}/", timeout=self.timeout)
                page.raise_for_status()
                detail = BeautifulSoup(page.text, "html.parser")
                fields = {
                    dt.get_text(" ", strip=True): dt.find_next_sibling("dd")
                    for dt in detail.find_all("dt") if dt.find_next_sibling("dd") is not None
                }
                row = {"opm_target_page_id": page_id}
                for label, column in (("RA", "ra_deg"), ("Dec", "dec_deg")):
                    value = fields.get(label)
                    match = re.search(r"\(([-+]?\d+(?:\.\d+)?)°\)", value.get_text(" ", strip=True)) if value else None
                    row[column] = float(match.group(1)) if match else np.nan
                class_values = [
                    f"{label}: {value.get_text(' ', strip=True)}"
                    for label, value in fields.items() if label.startswith("Class ")
                ]
                row["opm_classification"] = "; ".join(class_values)
                names = fields.get("Names")
                row["opm_aliases"] = "; ".join(
                    dict.fromkeys(names.get_text(" ", strip=True).split())
                ) if names else ""
                enriched.append(row)
            except requests.RequestException as exc:
                print(f"[opm] Could not read new/active target page {page_id}: {exc}", flush=True)
                enriched.append({"opm_target_page_id": page_id, "ra_deg": np.nan, "dec_deg": np.nan})
            if len(page_ids) >= 10 and index % 10 == 0:
                print(f"[opm] Read {index}/{len(page_ids)} new/active target pages.", flush=True)
        if enriched:
            table = table.merge(pd.DataFrame(enriched), on="opm_target_page_id", how="left")
        table = table.dropna(subset=["ra_deg", "dec_deg"])
        return validate_targets(normalize_targets(table, provider=self.name))

    def public_candidate_targets(self) -> pd.DataFrame:
        """Refresh and combine OPM's ranked and new/active candidate lists."""
        ranked = self.ranking()
        active = self.new_and_active_targets()
        sources = [frame for frame in (ranked, active) if frame is not None and not frame.empty]
        if not sources:
            return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg", "provider"])
        combined = pd.concat(sources, ignore_index=True, sort=False)
        rows = []
        for _, group in combined.groupby("target_id", sort=False):
            row = group.iloc[0].copy()
            row["opm_candidate_list"] = ",".join(sorted(set(
                value.strip()
                for value in group["opm_candidate_list"].dropna().astype(str).str.split(",").explode()
                if value.strip()
            )))
            for column in group.columns:
                values = group[column].dropna()
                if (column not in row or pd.isna(row[column]) or str(row[column]).strip() in {"", "nan"}) and not values.empty:
                    row[column] = values.iloc[0]
            rows.append(row)
        return pd.DataFrame(rows).reset_index(drop=True)

    @staticmethod
    def visible_region_cones(
        *, start_date: str, end_date: str | None = None,
        observatory: str = "El Leoncito", minimum_altitude_deg: float = 30.0,
        observing_windows=None, sample_minutes: int = 60,
    ) -> list[dict[str, float]]:
        """Cover every zenith-centered visibility cap with overlapping OPM cones.

        The cone union is a conservative search area. The planning pipeline
        applies its exact darkness, altitude, duration, and window criteria
        after retrieving these targets.
        """
        from ..visibility_plotter import _clock_time, get_observatory, resolve_observing_windows

        if not 0 <= minimum_altitude_deg < 90:
            raise ValueError("minimum_altitude_deg must be in [0, 90).")
        if sample_minutes < 1:
            raise ValueError("sample_minutes must be positive.")
        location, timezone, _ = get_observatory(observatory)
        dates = pd.date_range(start_date, end_date or start_date, freq="D")
        cones: dict[tuple[float, float], dict[str, float]] = {}
        # Sidereal rotation is about 15.04 degrees per hour. Expanding each
        # sampled visibility cap by half a step covers the sky between samples.
        radius = 90.0 - minimum_altitude_deg + 15.05 * sample_minutes / 120 + 0.1
        for night in dates:
            night_string = night.date().isoformat()
            for start_clock, end_clock in resolve_observing_windows(night_string, observing_windows):
                start = _clock_time(night_string, start_clock, timezone)
                end = _clock_time(night_string, end_clock, timezone)
                if end <= start:
                    end += pd.Timedelta(days=1)
                samples = pd.date_range(start, end, freq=f"{sample_minutes}min")
                if len(samples) == 0 or samples[-1] != end:
                    samples = samples.append(pd.DatetimeIndex([end]))
                times = Time(samples.to_pydatetime())
                frame = AltAz(obstime=times, location=location)
                zenith = SkyCoord(
                    az=np.zeros(len(samples)) * u.deg,
                    alt=np.full(len(samples), 90.0) * u.deg,
                    frame=frame,
                ).icrs
                for ra, dec in zip(zenith.ra.deg, zenith.dec.deg):
                    key = (round(float(ra) % 360, 5), round(float(dec), 5))
                    cones[key] = {"ra_deg": key[0], "dec_deg": key[1], "radius_deg": radius}
        return list(cones.values())

    def _query_cone(self, ra_deg: float, dec_deg: float, radius_deg: float) -> list[dict]:
        url = f"{self.base_url}{TARGET_API_PATH}"
        params = {"cone_search": f"{ra_deg:.5f},{dec_deg:.5f},{radius_deg:.5f}", "limit": 100}
        records, visited = [], set()
        while url and url not in visited:
            visited.add(url)
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                raise ValueError("OPM target API returned an unexpected response.")
            records.extend(page["results"])
            next_url = page.get("next")
            params = None
            if next_url:
                parsed = urlparse(next_url)
                if parsed.hostname != urlparse(self.base_url).hostname:
                    raise ValueError("OPM target API pagination returned an unexpected host.")
                url = urlunparse(parsed._replace(scheme=urlparse(self.base_url).scheme))
            else:
                url = None
        if url in visited:
            raise RuntimeError("OPM target API pagination repeated a page URL.")
        return records

    def targets_in_visible_region(
        self, *, start_date: str, end_date: str | None = None,
        observatory: str = "El Leoncito", minimum_altitude_deg: float = 30.0,
        observing_windows=None, sample_minutes: int = 60,
    ) -> pd.DataFrame:
        """Query all OPM targets within the scheduled sky region."""
        self.login()
        cones = self.visible_region_cones(
            start_date=start_date, end_date=end_date, observatory=observatory,
            minimum_altitude_deg=minimum_altitude_deg,
            observing_windows=observing_windows, sample_minutes=sample_minutes,
        )
        records = []
        for index, cone in enumerate(cones, start=1):
            records.extend(self._query_cone(**cone))
            if len(cones) > 1 and index % 10 == 0:
                print(f"[opm] Queried {index}/{len(cones)} visibility-region cones.", flush=True)
        if not records:
            return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg", "provider"])

        table = pd.DataFrame(records)
        if not {"id", "name", "ra", "dec"}.issubset(table.columns):
            raise ValueError("OPM target API omitted required id, name, ra or dec fields.")
        table = table.drop_duplicates("id", keep="first").copy()
        table["target_id"] = table["name"].astype(str)
        table["ra_deg"] = pd.to_numeric(table["ra"], errors="coerce")
        table["dec_deg"] = pd.to_numeric(table["dec"], errors="coerce")
        table["opm_target_page_id"] = table["id"].astype(int)
        table["opm_target_url"] = table["opm_target_page_id"].map(
            lambda page_id: f"{self.base_url}/targets/{page_id}/"
        )
        table["provider"] = self.name
        table["category"] = "visible_region_catalog"
        table["opm_search_method"] = "overlapping_cone_search"
        table["opm_query_start_date"] = start_date
        table["opm_query_end_date"] = end_date or start_date
        table["opm_query_observatory"] = observatory
        table["opm_query_minimum_altitude_deg"] = minimum_altitude_deg
        table["opm_query_observing_windows"] = str(observing_windows)
        table["opm_query_cone_sample_minutes"] = sample_minutes
        table["opm_query_cone_count"] = len(cones)
        table = table.dropna(subset=["target_id", "ra_deg", "dec_deg"])
        table = table.drop_duplicates("target_id", keep="first").reset_index(drop=True)
        return validate_targets(normalize_targets(table, provider=self.name))

    def load_targets(
        self, *, filters: Mapping[str, object] | None = None,
        start_date: str | None = None, end_date: str | None = None,
        observatory: str = "El Leoncito", minimum_altitude_deg: float = 30.0,
        observing_windows=None, sample_minutes: int = 60, **_,
    ) -> pd.DataFrame:
        # The planner needs both the regional catalog and OPM's curated lists.
        regional = self.targets_in_visible_region(
            start_date=start_date, end_date=end_date, observatory=observatory,
            minimum_altitude_deg=minimum_altitude_deg,
            observing_windows=observing_windows, sample_minutes=sample_minutes,
        ) if start_date else pd.DataFrame()
        listed = self.public_candidate_targets()
        table = pd.concat([regional, listed], ignore_index=True, sort=False)
        if not table.empty:
            sources = table.groupby("target_id", sort=False)["opm_candidate_list"].agg(
                lambda values: ",".join(sorted({
                    item.strip()
                    for value in values.dropna().astype(str)
                    for item in value.split(",") if item.strip()
                }))
            ) if "opm_candidate_list" in table else None
            table = table.groupby("target_id", sort=False).first().reset_index()
            if sources is not None:
                table["opm_candidate_list"] = table["target_id"].map(sources).fillna("")
        if filters:
            from .catalog_adapters import _apply_filters
            table = _apply_filters(table, filters, category_column="category", magnitude_column="magnitude_last", source_name=self.name)
        return table

    @staticmethod
    def _parse_photometry_page(html: str) -> pd.DataFrame:
        """Parse the Plotly photometry figure embedded in one target page."""
        figure = None
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", id="photometry_data"):
            text = script.string or script.get_text()
            if not text or not text.lstrip().startswith('{"data"'):
                continue
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("data"), list):
                traces = candidate["data"]
                if any("x" in trace and "y" in trace for trace in traces if isinstance(trace, dict)):
                    figure = candidate
                    break
        if figure is None:
            return pd.DataFrame()

        rows = []
        for trace in figure["data"]:
            if not isinstance(trace, dict):
                continue
            dataset_id = str(trace.get("name", "")).strip()
            if not dataset_id or "non-detection" in dataset_id.lower():
                continue
            epochs = trace.get("x", []) or []
            magnitudes = trace.get("y", []) or []
            errors = (trace.get("error_y") or {}).get("array", []) or []
            for index, (epoch, magnitude) in enumerate(zip(epochs, magnitudes)):
                value = pd.to_numeric(pd.Series([magnitude]), errors="coerce").iloc[0]
                if pd.isna(value):
                    continue
                timestamp = pd.to_datetime(epoch, utc=True, errors="coerce")
                if pd.isna(timestamp):
                    epoch_hjd = pd.to_numeric(pd.Series([epoch]), errors="coerce").iloc[0]
                    observation_time = pd.NaT
                else:
                    epoch_hjd = timestamp.timestamp() / 86400.0 + 2440587.5
                    observation_time = timestamp
                error = errors[index] if index < len(errors) else np.nan
                rows.append({
                    "epoch_hjd": epoch_hjd,
                    "observation_time": observation_time,
                    "magnitude": float(value),
                    "magnitude_err": pd.to_numeric(pd.Series([error]), errors="coerce").iloc[0],
                    "dataset_id": dataset_id,
                    "telescope": dataset_id.rsplit("_", 1)[0] if "_" in dataset_id else dataset_id,
                    "band": dataset_id.rsplit("_", 1)[-1],
                })
        return pd.DataFrame(rows)

    def photometry(
        self,
        targets: pd.DataFrame,
        *,
        errors: str = "ignore",
        verbose: bool = True,
    ) -> pd.DataFrame:
        """Download and normalize photometry from selected OPM target pages."""
        if "target_id" not in targets or "opm_target_page_id" not in targets:
            raise ValueError("OPM photometry requires target_id and opm_target_page_id columns.")
        self.login()
        rows = []
        work = targets[["target_id", "opm_target_page_id"]].dropna(subset=["opm_target_page_id"]).drop_duplicates()
        for target_id, page_id in work.itertuples(index=False):
            try:
                response = self.session.get(f"{self.base_url}/targets/{int(page_id)}/?tab=photometry", timeout=self.timeout)
                response.raise_for_status()
                parsed = self._parse_photometry_page(response.text)
                if not parsed.empty:
                    parsed.insert(0, "target_id", str(target_id))
                    parsed["provider"] = self.name
                    rows.append(parsed)
            except Exception:
                if errors == "raise":
                    raise
                if verbose:
                    print(f"OPM photometry unavailable for {target_id} (page {page_id}).")
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
            columns=["target_id", "epoch_hjd", "observation_time", "magnitude", "magnitude_err", "dataset_id", "telescope", "band", "provider"]
        )

    def login(self, *, username: str | None = None, password: str | None = None) -> requests.Session:
        """Authenticate without persisting credentials.

        Credentials default to ``TARGET_SELECTION_OPM_USERNAME`` and
        ``TARGET_SELECTION_OPM_PASSWORD``. The password is never written to a
        config file or returned by this method.
        """
        stored = None
        if not username or not password:
            stored = get_credentials("opm", interactive=False)
        username = username or (stored.username if stored else None)
        password = password or (stored.password if stored else None)
        if not username or not password:
            stored = get_credentials("opm", backend="prompt", interactive=True)
            username, password = stored.username, stored.password
        login_url = f"{self.base_url}/accounts/login/"
        page = self.session.get(login_url, timeout=self.timeout)
        page.raise_for_status()
        token = re.search(r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)', page.text)
        # OPM's Django login form names the account field ``login``.
        payload = {"login": username, "password": password}
        if token:
            payload["csrfmiddlewaretoken"] = token.group(1)
        result = self.session.post(login_url, data=payload, headers={"Referer": login_url}, timeout=self.timeout)
        result.raise_for_status()
        if "/accounts/login" in result.url:
            raise PermissionError("OPM login did not succeed; check credentials or account permissions.")
        return self.session

    def private_targets(self, **kwargs) -> pd.DataFrame:
        self.login(**kwargs)
        raise NotImplementedError("OPM private target-table parsing is intentionally pending inspection with an authenticated account.")
