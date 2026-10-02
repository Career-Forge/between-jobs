"""Shared env-var helpers, and how the API refuses to start on a bad configuration."""

from __future__ import annotations

import logging
import os
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
