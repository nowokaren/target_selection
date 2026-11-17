import pandas as pd


def test_rubin_handle_warns_and_returns_empty_without_lsst(monkeypatch, capsys):
    from target_selection.data_source import RubinHandle
    import target_selection.lsst_optional as optional

    monkeypatch.setattr(optional, "lsst_available", lambda **kwargs: (print("[rubin] unavailable"), False)[1])
    result = RubinHandle().coverage(pd.DataFrame({"target_id": ["event"]}))

    assert result.empty
    assert "[rubin] unavailable" in capsys.readouterr().out


def test_lsst_coverage_task_warns_and_skips_without_lsst(monkeypatch, capsys):
    import target_selection.tasks.lsst as task

    monkeypatch.setattr(task, "lsst_available", lambda **kwargs: False)
    result = task.query_lsst_coverage(pd.DataFrame(), verbose=True)

    assert result.empty
    assert "Target" in result.columns
