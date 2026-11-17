from datetime import datetime, timedelta, timezone
import os

import pandas as pd

from target_selection.daily_cache import query_daily_frame


def test_daily_query_refreshes_once_then_reuses_the_result(tmp_path):
    calls = []

    def loader(refresh):
        calls.append(refresh)
        return pd.DataFrame({"value": [len(calls)]})

    first = query_daily_frame(tmp_path, "opm", "photometry", ["event-1"], loader, verbose=False)
    second = query_daily_frame(tmp_path, "opm", "photometry", ["event-1"], loader, verbose=False)

    assert calls == [True]
    assert first.equals(second)
    assert second.loc[0, "value"] == 1


def test_daily_query_refreshes_again_after_cache_is_from_yesterday(tmp_path):
    calls = []

    def loader(refresh):
        calls.append(refresh)
        return pd.DataFrame({"value": [len(calls)]})

    query_daily_frame(tmp_path, "bhtom", "targets", {"query": 1}, loader, verbose=False)
    cache = next((tmp_path / "daily_queries").rglob("*.csv"))
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).timestamp()
    os.utime(cache, (yesterday, yesterday))

    result = query_daily_frame(tmp_path, "bhtom", "targets", {"query": 1}, loader, verbose=False)

    assert calls == [True, True]
    assert result.loc[0, "value"] == 2
