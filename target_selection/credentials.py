"""Provider credential storage with portable and non-interactive fallbacks.

The preferred backend is the operating-system credential store through the
optional ``keyring`` package. Environment variables and ``.netrc`` are useful
for headless servers and batch jobs. Secrets are never written by this module
to the repository or to a notebook.
"""

from __future__ import annotations

from dataclasses import dataclass
from getpass import getpass
import netrc as netrc_module
import os
from pathlib import Path
from typing import Literal


Backend = Literal["auto", "keyring", "env", "netrc", "prompt"]
KEYRING_SERVICE = "target-selection"
NETRC_HOSTS = {"opm": "lensingtom.zah.uni-heidelberg.de"}


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    backend: str


def _env_names(provider: str) -> tuple[str, str]:
    prefix = "TARGET_SELECTION_" + str(provider).upper()
    return f"{prefix}_USERNAME", f"{prefix}_PASSWORD"


def _netrc_machine(provider: str) -> str:
    return NETRC_HOSTS.get(str(provider).strip().lower(), str(provider).strip().lower())


def _keyring_module():
    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Keyring support is not installed. Install target-selection[credentials].") from exc
    return keyring


def _get_keyring(provider: str) -> Credentials | None:
    keyring = _keyring_module()
    username = keyring.get_password(f"{KEYRING_SERVICE}/username", provider)
    if not username:
        return None
    password = keyring.get_password(KEYRING_SERVICE, f"{provider}:{username}")
    if not password:
        return None
    return Credentials(username, password, "keyring")


def _get_env(provider: str) -> Credentials | None:
    username_name, password_name = _env_names(provider)
    username, password = os.getenv(username_name), os.getenv(password_name)
    if username and password:
        return Credentials(username, password, "environment")
    return None


def _get_netrc(provider: str, path: str | Path | None = None) -> Credentials | None:
    netrc_path = Path(path).expanduser() if path else Path.home() / ".netrc"
    if not netrc_path.exists():
        return None
    try:
        authenticators = netrc_module.netrc(str(netrc_path)).authenticators(_netrc_machine(provider))
    except (OSError, netrc_module.NetrcParseError):
        return None
    if not authenticators:
        return None
    login, _, password = authenticators
    if login and password:
        return Credentials(login, password, "netrc")
    return None


def get_credentials(provider: str, *, backend: Backend = "auto", interactive: bool = False, netrc_path: str | Path | None = None) -> Credentials | None:
    """Resolve credentials without exposing the password in normal output."""
    provider = str(provider).strip().lower()
    if backend in ("auto", "keyring"):
        try:
            credentials = _get_keyring(provider)
        except RuntimeError:
            credentials = None
        if credentials:
            return credentials
        if backend == "keyring":
            return None
    if backend in ("auto", "env"):
        credentials = _get_env(provider)
        if credentials:
            return credentials
        if backend == "env":
            return None
    if backend in ("auto", "netrc"):
        credentials = _get_netrc(provider, path=netrc_path)
        if credentials:
            return credentials
        if backend == "netrc":
            return None
    if backend == "prompt" or interactive:
        username = input(f"{provider} username: ")
        password = getpass(f"{provider} password: ")
        return Credentials(username, password, "prompt")
    return None


def set_credentials(provider: str, username: str, password: str, *, backend: Literal["keyring", "netrc"] = "keyring", netrc_path: str | Path | None = None) -> None:
    """Persist credentials in a user-controlled backend."""
    provider = str(provider).strip().lower()
    if not username or not password:
        raise ValueError("Username and password cannot be empty.")
    if backend == "keyring":
        keyring = _keyring_module()
        keyring.set_password(f"{KEYRING_SERVICE}/username", provider, username)
        keyring.set_password(KEYRING_SERVICE, f"{provider}:{username}", password)
        return
    path = Path(netrc_path).expanduser() if netrc_path else Path.home() / ".netrc"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\nmachine {_netrc_machine(provider)}\nlogin {username}\npassword {password}\n")
    os.chmod(path, 0o600)


def remove_credentials(provider: str, *, username: str | None = None) -> None:
    """Remove keyring credentials for one provider when keyring is available."""
    keyring = _keyring_module()
    provider = str(provider).strip().lower()
    username = username or keyring.get_password(f"{KEYRING_SERVICE}/username", provider)
    if username:
        keyring.delete_password(KEYRING_SERVICE, f"{provider}:{username}")
        keyring.delete_password(f"{KEYRING_SERVICE}/username", provider)
