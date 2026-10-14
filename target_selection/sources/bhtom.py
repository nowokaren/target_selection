"""Read-only BHTOM2 target and photometry access.

Tokens stay in the HTTP session and are not persisted. The implementation
follows the published BHTOM2 API and server response formats.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
from io import StringIO
import json
from pathlib import Path

import pandas as pd
import requests

from ..contracts import normalize_targets, validate_targets
from ..credentials import get_credentials
from .catalog_adapters import _apply_filters


BHTOM_URL = "https://bh-tom2.astrouw.edu.pl"
TARGET_LIST_PATH = "/targets/getTargetList/"
PHOTOMETRY_PATH = "/targets/download-photometry/"
TARGET_QUERY_FIELDS = frozenset({
    "name", "type", "raMin", "raMax", "decMin", "decMax", "importanceMin",
    "importanceMax", "classification", "targetGroup", "coneSearchTarget",
    "coneSearchRaDecRadius", "priority", "galacticLatMin", "galacticLatMax",
    "galacticLonMin", "galacticLonMax", "description", "sunSeparationMin",
    "sunSeparationMax", "lastMagMin", "lastMagMax", "hasOptical",
    "hasInfrared", "hasRadio", "hasXray", "hasGamma", "hasPolarimetry",
})
PHOTOMETRY_COLUMNS = (
    "target_id", "provider", "epoch_mjd", "magnitude", "magnitude_err",
    "facility", "band", "observer", "is_usable",
)


class _NoAuth(requests.auth.AuthBase):
    """Prevent requests from substituting matching netrc Basic credentials."""

    def __call__(self, request):
        request.headers.pop("Authorization", None)
        return request


class _TokenAuth(requests.auth.AuthBase):
    def __init__(self, authorization: str) -> None:
        self.authorization = authorization

    def __call__(self, request):
        request.headers["Authorization"] = self.authorization
        return request


class BHTOMAdapter:
    """Access authenticated BHTOM2 targets and magnitude photometry."""

    name = "bhtom"
    kind = "tom"
    features = ("targets", "photometry", "authenticated_session")

    def __init__(self, *, base_url: str = BHTOM_URL, session=None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session if session is not None else requests.Session()
        self.timeout = float(timeout)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "features": list(self.features),
            "base_url": self.base_url,
            "private_data": "requires an authenticated BHTOM account",
            "photometry_time_scale": "MJD as returned by BHTOM",
        }

    def login(self, *, username: str | None = None, password: str | None = None) -> requests.Session:
        """Exchange username/password for a token without persisting either one.

        Credentials are resolved through the shared keyring, environment, or
        netrc backends. This method never prompts, so it also works in jobs.
        """
        if not username or not password:
            stored = get_credentials("bhtom")
            username = username or (stored.username if stored else None)
            password = password or (stored.password if stored else None)
        if not username or not password:
            raise RuntimeError(
                "BHTOM credentials are missing. Run 'target-selection auth set bhtom' "
                "or set TARGET_SELECTION_BHTOM_USERNAME and TARGET_SELECTION_BHTOM_PASSWORD."
            )

        response = self.session.post(
            f"{self.base_url}/api/token-auth/",
            json={"username": username, "password": password},
            headers={"Accept": "application/json"},
            auth=_NoAuth(),
            timeout=self.timeout,
        )
        if response.status_code in (400, 401, 403):
            raise PermissionError("BHTOM authentication failed; check credentials and account access.")
        response.raise_for_status()
        try:
            token = response.json().get("token")
        except (ValueError, AttributeError) as exc:
            raise RuntimeError("BHTOM authentication returned an invalid response.") from exc
        if not isinstance(token, str) or not token:
            raise PermissionError("BHTOM authentication returned no token.")
        self.session.headers.update({"Authorization": f"Token {token}"})
        return self.session

    def _post(self, path: str, payload: Mapping[str, object]) -> requests.Response:
        if "Authorization" not in self.session.headers:
            self.login()
        response = self.session.post(
            f"{self.base_url}{path}", json=dict(payload),
            headers={"Accept": "application/json"},
            auth=_TokenAuth(self.session.headers["Authorization"]),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def load_targets(
        self,
        *,
        classification: str | None = "Microlensing Event",
        query: Mapping[str, object] | None = None,
        filters: Mapping[str, object] | None = None,
        max_pages: int | None = None,
        cache_dir: str | Path | None = None,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """Read BHTOM's target pages and return canonical target columns.

        ``classification`` defaults to microlensing events. Set it to another
        BHTOM classification or to ``None`` for all classes. ``query`` uses
        BHTOM's documented server-side parameter names and, when it contains
        ``classification``, takes precedence over the dedicated argument;
        ``filters`` applies the local category/magnitude filters afterward.
        ``max_pages`` can bound exploratory calls to the first N pages.
        """
        query = dict(query or {})
        if "classification" not in query:
            query["classification"] = classification
        if query["classification"] is None:
            query.pop("classification")
        elif not isinstance(query["classification"], str) or not query["classification"].strip():
            raise ValueError("classification must be a nonempty string or None.")
        unknown = set(query) - TARGET_QUERY_FIELDS
        if unknown:
            raise ValueError(f"Unknown BHTOM target query fields: {sorted(unknown)}")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be at least 1.")

        cache_path = None
        if cache_dir is not None:
            cache_root = Path(cache_dir)
            cache_root.mkdir(parents=True, exist_ok=True)
            cache_key = hashlib.sha256(json.dumps({"query": query, "max_pages": max_pages}, sort_keys=True, default=str).encode()).hexdigest()[:16]
            cache_path = cache_root / f"targets_{cache_key}.csv"
            if cache_path.exists() and not refresh:
                table = pd.read_csv(cache_path)
                if filters:
                    table = _apply_filters(
                        table, filters, category_column="category",
                        magnitude_column="magnitude_last", source_name=self.name,
                    )
                return validate_targets(table.reset_index(drop=True))

        rows: list[dict[str, object]] = []
        page = 1
        while True:
            response = self._post(TARGET_LIST_PATH, {**query, "page": page})
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError("BHTOM target list returned an unexpected response.")
            if not all(isinstance(item, dict) for item in payload["data"]):
                raise ValueError("BHTOM target list contains invalid records.")
            rows.extend(payload["data"])
            num_pages = int(payload.get("num_pages", page))
            if page >= num_pages or (max_pages is not None and page >= max_pages):
                break
            page += 1

        if not rows:
            return pd.DataFrame(columns=["target_id", "ra_deg", "dec_deg", "provider", "category"])
        table = pd.DataFrame(rows).rename(columns={
            "name": "target_id", "ra": "ra_deg", "dec": "dec_deg",
            "classification": "category", "mag_last": "magnitude_last",
        })
        table = normalize_targets(table, provider=self.name)
        if cache_path is not None:
            table.to_csv(cache_path, index=False)
        if filters:
            table = _apply_filters(
                table, filters, category_column="category",
                magnitude_column="magnitude_last", source_name=self.name,
            )
        return validate_targets(table.reset_index(drop=True))

    def photometry(self, targets: pd.DataFrame, *, errors: str = "raise") -> pd.DataFrame:
        """Download magnitude measurements for selected targets in native MJD."""
        if "target_id" not in targets:
            raise ValueError("BHTOM photometry requires a target_id column.")
        if errors not in ("raise", "ignore"):
            raise ValueError("errors must be 'raise' or 'ignore'.")
        tables = []
        for target_id in targets["target_id"].dropna().astype(str).drop_duplicates():
            try:
                response = self._post(PHOTOMETRY_PATH, {"name": target_id})
                table = pd.read_csv(StringIO(response.text), sep=";")
                required = {"MJD", "Magnitude", "Error", "Facility", "Filter", "Observer"}
                if not required.issubset(table.columns):
                    raise ValueError(f"BHTOM photometry has unexpected columns for {target_id!r}.")
                table = table.rename(columns={
                    "MJD": "epoch_mjd", "Magnitude": "magnitude",
                    "Error": "magnitude_err", "Facility": "facility",
                    "Filter": "band", "Observer": "observer",
                })
                table.insert(0, "target_id", target_id)
                table.insert(1, "provider", self.name)
                for column in ("epoch_mjd", "magnitude", "magnitude_err"):
                    table[column] = pd.to_numeric(table[column], errors="coerce")
                table["is_usable"] = (
                    table[["epoch_mjd", "magnitude", "magnitude_err"]].notna().all(axis=1)
                    & table["magnitude_err"].gt(0)
                )
                tables.append(table.loc[:, PHOTOMETRY_COLUMNS])
            except (requests.RequestException, ValueError):
                if errors == "raise":
                    raise
        return pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=PHOTOMETRY_COLUMNS)
