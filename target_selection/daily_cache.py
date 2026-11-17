"""Daily refresh wrapper for downloaded tables outside Rubin collections."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Callable

import pandas as pd
from pandas.errors import EmptyDataError


def query_daily_frame(
    cache_root: str | Path,
    provider: str,
    dataset: str,
    request: object,
    loader: Callable[[bool], pd.DataFrame],
    *,
    verbose: bool = True,
) -> pd.DataFrame:
    """Refresh a provider query once per UTC day, then reuse its daily result.

    ``loader`` receives ``True`` on a refresh day so it can bypass its own
    provider cache. A request hash keeps different target sets and query
    parameters independent. Rubin collection access intentionally does not
    use this wrapper.
    """
    provider_key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(provider)).strip("._")
    dataset_key = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(dataset)).strip("._")
    payload = json.dumps(request, sort_keys=True, default=str, separators=(",", ":"))
    key = hashlib.sha256(payload.encode()).hexdigest()[:20]
    path = Path(cache_root) / "daily_queries" / provider_key / dataset_key / f"{key}.csv"
    today = datetime.now(timezone.utc).date()
    if path.exists():
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date()
        if modified == today:
            if verbose:
                print(f"[cache] {provider_key}/{dataset_key}: reused today's query", flush=True)
            try:
                return pd.read_csv(path)
            except EmptyDataError:
                metadata = path.with_suffix(".json")
                columns = json.loads(metadata.read_text()).get("columns", []) if metadata.exists() else []
                return pd.DataFrame(columns=columns)

    result = loader(True)
    result = result if result is not None else pd.DataFrame()
    path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(path, index=False)
    path.with_suffix(".json").write_text(json.dumps({"columns": list(result.columns)}))
    return result
