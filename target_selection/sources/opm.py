"""OPM (Galactic Science OPM) adapter.

The public probability ranking is available without credentials. Individual
targets and their private observations require an authenticated OPM session;
the adapter exposes a login scaffold but does not store credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
import json
import os
import re

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..contracts import normalize_targets, validate_targets
from ..credentials import get_credentials


OPM_URL = "https://lensingtom.zah.uni-heidelberg.de"
PUBLIC_RANKING_PATH = "/custom_code/prob_list.html"


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
    features = ("targets", "ranking", "authenticated_session")

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
            "private_data": "requires an authenticated OPM account",
        }

    def supports(self, feature: str) -> bool:
        return str(feature).lower() in self.features

    def ranking(self) -> pd.DataFrame:
        response = self.session.get(f"{self.base_url}{PUBLIC_RANKING_PATH}", timeout=self.timeout)
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
        return validate_targets(normalize_targets(table, provider=self.name))

    def load_targets(self, *, filters: Mapping[str, object] | None = None, **_) -> pd.DataFrame:
        table = self.ranking()
        if filters:
            from .catalog_adapters import _apply_filters
            table = _apply_filters(table, filters, category_column="category", magnitude_column="magnitude_last", source_name=self.name)
        return table

    @staticmethod
    def _parse_photometry_page(html: str) -> pd.DataFrame:
        """Parse the Plotly photometry figure embedded in one target page."""
        figure = None
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
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
        payload = {"username": username, "password": password}
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
