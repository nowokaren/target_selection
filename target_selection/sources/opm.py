"""OPM (Galactic Science OPM) adapter.

The public probability ranking is available without credentials. Individual
targets and their private observations require an authenticated OPM session;
the adapter exposes a login scaffold but does not store credentials.
"""

from __future__ import annotations

from collections.abc import Mapping
from io import StringIO
import os
import re

import pandas as pd
import requests

from ..contracts import normalize_targets, validate_targets


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

    def login(self, *, username: str | None = None, password: str | None = None) -> requests.Session:
        """Authenticate without persisting credentials.

        Credentials default to ``TARGET_SELECTION_OPM_USERNAME`` and
        ``TARGET_SELECTION_OPM_PASSWORD``. The password is never written to a
        config file or returned by this method.
        """
        username = username or os.getenv("TARGET_SELECTION_OPM_USERNAME")
        password = password or os.getenv("TARGET_SELECTION_OPM_PASSWORD")
        if not username or not password:
            raise ValueError("OPM login requires username/password arguments or TARGET_SELECTION_OPM_* environment variables.")
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
