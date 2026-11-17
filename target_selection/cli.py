"""Command-line interface for reproducible target-selection runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from target_selection.config import load_config
from target_selection.reference_catalog import (
    load_reference_catalog_config,
    run_reference_catalog,
)
from target_selection.sources import default_adapter_registry
from target_selection.credentials import get_credentials, remove_credentials, set_credentials


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="target-selection")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-config", help="Validate a configuration file"
    )
    validate.add_argument("--config", required=True)
    catalog = commands.add_parser(
        "catalog",
        help="Enrich a large coordinate catalog with Rubin visits and coadd properties",
    )
    catalog.add_argument("--config", required=True)
    commands.add_parser("list-sources", help="List installed source adapters")
    commands.add_parser("visibility", help="Interactively select targets and make visibility PDF")
    auth = commands.add_parser("auth", help="Store and test provider credentials")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_set = auth_commands.add_parser("set", help="Store provider credentials")
    auth_set.add_argument("provider")
    auth_set.add_argument("--backend", choices=("keyring", "netrc"), default="keyring")
    auth_test = auth_commands.add_parser("test", help="Check whether credentials can be resolved")
    auth_test.add_argument("provider")
    auth_test.add_argument("--remote", action="store_true", help="Verify BHTOM credentials with its token endpoint")
    auth_remove = auth_commands.add_parser("remove", help="Remove keyring credentials")
    auth_remove.add_argument("provider")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "visibility":
        from .visibility_cli import main as visibility_main
        return visibility_main(raw_argv[1:])
    if raw_argv and raw_argv[0] == "plan":
        from .planning_cli import main as planning_main
        return planning_main(raw_argv[1:])
    args = build_parser().parse_args(raw_argv)
    if args.command == "list-sources":
        print(json.dumps(default_adapter_registry().available(), indent=2))
        return 0
    if args.command == "auth":
        if args.auth_command == "set":
            from getpass import getpass
            username = input(f"{args.provider} username: ")
            password = getpass(f"{args.provider} password: ")
            try:
                set_credentials(args.provider, username, password, backend=args.backend)
            except RuntimeError as exc:
                print(f"Credential storage failed: {exc}", file=sys.stderr)
                return 2
            print(f"Credentials stored using {args.backend}.")
            return 0
        if args.auth_command == "test":
            credentials = get_credentials(args.provider)
            if credentials is None:
                print(f"No credentials found for {args.provider}.")
                return 1
            if args.remote:
                if args.provider.lower() != "bhtom":
                    print("Remote verification is currently available only for bhtom.", file=sys.stderr)
                    return 2
                from requests import RequestException
                from target_selection.sources.bhtom import BHTOMAdapter
                try:
                    BHTOMAdapter().login(username=credentials.username, password=credentials.password)
                except (PermissionError, RuntimeError, RequestException) as exc:
                    print(f"BHTOM access check failed: {exc}", file=sys.stderr)
                    return 1
                print("BHTOM access verified; authentication token received.")
                return 0
            print(f"Credentials found for {args.provider} using {credentials.backend}.")
            return 0
        if args.auth_command == "remove":
            remove_credentials(args.provider)
            print(f"Keyring credentials removed for {args.provider}.")
            return 0
    if args.command == "catalog":
        from target_selection.lsst_optional import lsst_available
        if not lsst_available(operation="Rubin catalog enrichment"):
            return 2
        config = load_reference_catalog_config(args.config)
        result = run_reference_catalog(config)
        print(result.catalog_path)
        return 0
    if args.command == "validate-config":
        raw_config = Path(args.config).read_text(encoding="utf-8")
        if "[catalog]" in raw_config:
            load_reference_catalog_config(args.config)
        else:
            load_config(args.config)
        print(f"Configuration is valid: {args.config}")
        return 0
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
