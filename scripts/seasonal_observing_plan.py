#!/usr/bin/env python
"""Compatibility entry point for the installed seasonal planning command."""

from target_selection.planning_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
