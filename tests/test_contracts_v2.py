import pandas as pd
import pytest

from target_selection.contracts import ContractError, normalize_observations, normalize_targets, validate_targets
from target_selection.credentials import get_credentials, set_credentials
from target_selection.data_source import data_source


def test_target_aliases_use_one_category_field():
    table = normalize_targets(
        pd.DataFrame({"Target": ["a"], "RA": [10.0], "DEC": [-20.0], "Status": ["active"]}),
        provider="mop",
    )
    result = validate_targets(table)
    assert result.loc[0, "category"] == "active"
    assert result.loc[0, "provider"] == "mop"


def test_target_contract_rejects_duplicates():
    table = pd.DataFrame({"target_id": ["a", "a"], "ra_deg": [10, 10], "dec_deg": [1, 1], "provider": ["x", "x"]})
    with pytest.raises(ContractError):
        validate_targets(table)


def test_observation_aliases_are_normalized():
    table = normalize_observations(pd.DataFrame({"Target": ["a"], "source": ["hsh"], "HJD": [2.4e6], "filter": ["I"], "magnitude_error": [0.1]}))
    assert list(table[["target_id", "provider", "epoch_hjd", "band", "magnitude_err"]].columns) == ["target_id", "provider", "epoch_hjd", "band", "magnitude_err"]


def test_public_provider_registry_is_lazy():
    assert data_source.mop.kind == "tom"
    assert data_source.hsh.kind == "survey"
    assert data_source.lsst.supports("coverage")


def test_credentials_can_use_a_private_netrc(tmp_path):
    path = tmp_path / ".netrc"
    set_credentials("opm", "alice", "secret", backend="netrc", netrc_path=path)
    assert "machine lensingtom.zah.uni-heidelberg.de" in path.read_text()
    credentials = get_credentials("opm", backend="netrc", netrc_path=path)
    assert credentials.username == "alice"
    assert credentials.password == "secret"
