"""Shared env-var helper."""

from __future__ import annotations

import os


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required env var {name}")
    return value


def optional_env(name: str) -> str | None:
    """The variable's value, or None when it is unset or empty -- the two read
    the same, since a blank `KEY=` line in a .env file is how "not configured"
    usually looks."""
    return os.environ.get(name) or None
