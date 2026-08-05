"""Shared env-var helper."""

from __future__ import annotations

import os


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required env var {name}")
    return value
