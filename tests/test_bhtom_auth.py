"""BHTOM authentication contract without contacting the remote service."""

import pytest

from target_selection.cli import main
from target_selection.credentials import get_credentials, set_credentials
from target_selection.data_source import data_source
from target_selection.sources.bhtom import BHTOMAdapter


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self.body = body if body is not None else {"token": "test-token"}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, response=None, responses=None):
        self.response = response or FakeResponse()
        self.responses = list(responses) if responses is not None else None
        self.headers = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0) if self.responses is not None else self.response


def test_bhtom_netrc_credentials_use_real_host(tmp_path):
    path = tmp_path / ".netrc"
    set_credentials("bhtom", "alice", "private", backend="netrc", netrc_path=path)
    assert 'machine "bh-tom2.astrouw.edu.pl"' in path.read_text()
    credentials = get_credentials("bhtom", backend="netrc", netrc_path=path)
    assert (credentials.username, credentials.password) == ("alice", "private")
    assert path.stat().st_mode & 0o777 == 0o600


def test_bhtom_netrc_round_trips_special_characters(tmp_path):
    path = tmp_path / ".netrc"
    password = 'with space\\slash"quote'
    set_credentials("bhtom", "alice", password, backend="netrc", netrc_path=path)
    assert get_credentials("bhtom", backend="netrc", netrc_path=path).password == password


def test_bhtom_login_exchanges_credentials_for_session_token():
    session = FakeSession()
    adapter = BHTOMAdapter(session=session)
    assert adapter.login(username="alice", password="private") is session
    url, kwargs = session.calls[0]
    assert url == "https://bh-tom2.astrouw.edu.pl/api/token-auth/"
    assert kwargs["json"] == {"username": "alice", "password": "private"}
    assert type(kwargs["auth"]).__name__ == "_NoAuth"
    assert session.headers["Authorization"] == "Token test-token"
    assert data_source.bhtom.kind == "tom"
    assert data_source.bhtom.supports("authenticated_session")
    assert data_source.bhtom.supports("targets")
    assert data_source.bhtom.supports("photometry")


def test_bhtom_request_auth_is_not_replaced_by_netrc(monkeypatch):
    import requests
    from target_selection.sources.bhtom import _NoAuth, _TokenAuth

    monkeypatch.setattr("requests.sessions.get_netrc_auth", lambda url: ("netrc-user", "netrc-secret"))
    session = requests.Session()
    login_request = session.prepare_request(requests.Request(
        "POST", "https://bh-tom2.astrouw.edu.pl/api/token-auth/", auth=_NoAuth(),
    ))
    target_request = session.prepare_request(requests.Request(
        "POST", "https://bh-tom2.astrouw.edu.pl/targets/getTargetList/",
        auth=_TokenAuth("Token synthetic"),
    ))
    assert "Authorization" not in login_request.headers
    assert target_request.headers["Authorization"] == "Token synthetic"


@pytest.mark.parametrize("response", [FakeResponse(401), FakeResponse(body={})])
def test_bhtom_login_rejects_failed_auth_without_setting_header(response):
    session = FakeSession(response)
    with pytest.raises(PermissionError):
        BHTOMAdapter(session=session).login(username="alice", password="private")
    assert "Authorization" not in session.headers


def test_bhtom_remote_auth_test_hides_token(monkeypatch, capsys):
    from target_selection.credentials import Credentials

    monkeypatch.setattr("target_selection.cli.get_credentials", lambda provider: Credentials("alice", "private", "environment"))
    monkeypatch.setattr("target_selection.sources.bhtom.BHTOMAdapter.login", lambda self, **kwargs: FakeSession())
    assert main(["auth", "test", "bhtom", "--remote"]) == 0
    output = capsys.readouterr().out
    assert "access verified" in output
    assert "private" not in output
    assert "test-token" not in output


def test_bhtom_target_pages_are_normalized_and_filtered():
    session = FakeSession(responses=[
        FakeResponse(body={"num_pages": 2, "data": [{
            "name": "A", "ra": 20, "dec": -10, "classification": "Microlensing Event",
            "mag_last": 17.2,
        }]}),
        FakeResponse(body={"num_pages": 2, "data": [{
            "name": "B", "ra": 30, "dec": -20, "classification": "Star",
            "mag_last": 19.5,
        }]}),
    ])
    session.headers["Authorization"] = "Token test-token"
    table = BHTOMAdapter(session=session).load_targets(filters={"category": "Microlensing Event"})
    assert table["target_id"].tolist() == ["A"]
    assert table.loc[0, "provider"] == "bhtom"
    assert table.loc[0, "magnitude_last"] == 17.2
    assert [call[1]["json"]["page"] for call in session.calls] == [1, 2]
    assert all(call[1]["json"]["classification"] == "Microlensing Event" for call in session.calls)
    assert all(type(call[1]["auth"]).__name__ == "_TokenAuth" for call in session.calls)


@pytest.mark.parametrize("classification, expected", [
    ("Star", "Star"), (None, None),
])
def test_bhtom_classification_can_be_changed_or_removed(classification, expected):
    session = FakeSession(response=FakeResponse(body={"num_pages": 1, "data": []}))
    session.headers["Authorization"] = "Token test-token"
    BHTOMAdapter(session=session).load_targets(classification=classification)
    payload = session.calls[0][1]["json"]
    assert payload.get("classification") == expected


def test_bhtom_cache_keeps_classifications_separate(tmp_path):
    session = FakeSession(responses=[
        FakeResponse(body={"num_pages": 1, "data": [{
            "name": "Lens", "ra": 20, "dec": -10, "classification": "Microlensing Event",
        }]}),
        FakeResponse(body={"num_pages": 1, "data": [{
            "name": "Star", "ra": 30, "dec": -20, "classification": "Star",
        }]}),
    ])
    session.headers["Authorization"] = "Token test-token"
    adapter = BHTOMAdapter(session=session)
    assert adapter.load_targets(cache_dir=tmp_path)["target_id"].tolist() == ["Lens"]
    assert adapter.load_targets(classification="Star", cache_dir=tmp_path)["target_id"].tolist() == ["Star"]
    assert adapter.load_targets(cache_dir=tmp_path)["target_id"].tolist() == ["Lens"]
    assert len(session.calls) == 2
    assert len(list(tmp_path.glob("targets_*.csv"))) == 2


def test_bhtom_photometry_preserves_mjd():
    import pandas as pd

    csv = "MJD;Magnitude;Error;Facility;Filter;Observer\n60200.5;18.2;0.1;Obs;I;Alice\n"
    session = FakeSession(response=FakeResponse(text=csv))
    session.headers["Authorization"] = "Token test-token"
    table = BHTOMAdapter(session=session).photometry(pd.DataFrame({"target_id": ["A", "A"]}))
    assert len(table) == 1
    assert table.loc[0, "epoch_mjd"] == 60200.5
    assert table.loc[0, "band"] == "I"
    assert bool(table.loc[0, "is_usable"])
    assert "epoch_hjd" not in table
    assert len(session.calls) == 1
