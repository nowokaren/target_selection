"""Command-line interface for reproducible target-selection runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from target_selection.config import load_config
from target_selection.reference_catalog import (
    load_reference_catalog_config,
    run_reference_catalog,
)
from target_selection.sources import default_adapter_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="target-selection")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run an analysis from TOML or JSON")
    run.add_argument("--config", required=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list-sources":
        print(json.dumps(default_adapter_registry().available(), indent=2))
        return 0
    if args.command == "catalog":
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
    config = load_config(args.config)
    from target_selection.workflow import run_analysis

    result = run_analysis(config)
    for name, run in result.runs.items():
        print(f"{name}: {run.paths['run']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
