"""Shared env-var helpers, and how the API refuses to start on a bad configuration."""

from __future__ import annotations

import logging
import os
import re
from typing import NoReturn

logger = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    """The environment is wrong, so the API will not start. The message names the
    setting, never its value."""


def refuse(message: str) -> NoReturn:
    """Says why the API cannot start -- as one short log line of its own -- and raises.

    Why a line of its own: uvicorn reports a lifespan that fails as one log record
    holding the whole traceback, ten-odd frames of Starlette deep, and the log
    formatter caps a record at 4,000 characters. The reason is the LAST line of that
    traceback, so on a platform that shows only the logs it is exactly what gets cut
    off, leaving "Traceback ..." and no hint which setting is wrong. The message names
    the variable, never its value, so it is safe to log (and still goes through the
    redactor like everything else).
    """
    logger.critical("the API cannot start: %s", message)
    raise ConfigurationError(message)


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        refuse(f"missing required env var {name}")
    return value


def optional_env(name: str) -> str | None:
    """The variable's value, or None when it is unset or empty -- the two read
    the same, since a blank `KEY=` line in a .env file is how "not configured"
    usually looks."""
    return os.environ.get(name) or None


def strict_on_off(name: str, *, default: bool) -> bool:
    """A switch read as exactly `on` or `off` (any case, surrounding spaces ignored); unset or
    blank is `default`. Anything else stops the API from starting (`refuse`), naming the setting
    and never its value.

    Strict on purpose, for a switch that guards something: the `DISABLE_*` flags read any
    non-empty value as true, so a setting spelled `=false` or `=0` there means the opposite of
    what it says. For a switch whose "on" is the safe state, or whose "off" skips a legal check,
    that reading is a trap, so no spelling other than the two words is guessed at."""
    raw = (os.environ.get(name) or "").strip().lower()
    if raw == "":
        return default
    if raw == "on":
        return True
    if raw == "off":
        return False
    refuse(f"{name} must be 'on' or 'off'")


_CANONICAL_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def optional_uuid_set(name: str) -> frozenset[str] | None:
    """A comma-separated list of user ids (UUIDs, any case, spaces around an entry ignored),
    returned lower-cased -- or None when the variable is unset or blank, which means "no list".

    Every entry must be a canonical UUID (36 characters, hex and hyphens), and an empty entry
    (`a,,b`, a trailing comma, a lone `,`) is not an entry. Anything else stops the API from
    starting (`refuse`), naming the setting and never its value: for a list that opens a
    feature to chosen people, a typo must not quietly turn into "everyone" or into "nobody"."""
    raw = optional_env(name)
    if raw is None or raw.strip() == "":
        return None
    entries = [entry.strip() for entry in raw.split(",")]
    if not all(_CANONICAL_UUID.fullmatch(entry) for entry in entries):
        refuse(f"{name} must be a comma-separated list of user ids (UUIDs), with no empty entries")
    return frozenset(entry.lower() for entry in entries)
