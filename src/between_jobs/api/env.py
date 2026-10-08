"""Shared env-var helpers, and how the API refuses to start on a bad configuration."""

from __future__ import annotations

import logging
import os
import re
from typing import NoReturn
from urllib.parse import urlsplit

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


def https_url_or_refuse(name: str, value: str) -> str:
    """`value` as the https address it is, without a trailing slash, or `refuse`: the API does
    not start, and the message names the setting `name` and never the value (an address in a
    setting can carry credentials, which is one of the things refused).

    Only what is safe to put in front of a person and to append a path to is accepted: the
    `https` scheme and a host, optionally a port and a path, and nothing else -- no user name
    or password, no query string, no fragment, no whitespace, control or invisible character
    (a zero-width space or a direction mark pasted in from a chat or a document would cut the
    link the bot prints, with nothing to see), and no backslash. `urlsplit` is lenient (it reads
    "https:host" as a path), so the shape is checked on the string as written too."""
    problem = _url_problem(value)
    if problem is not None:
        refuse(f"{name} must be an https URL with a host, and no credentials, query or fragment")
    return value.rstrip("/")


def is_plain_https_url(value: str) -> bool:
    """Whether `value` passes the same check `https_url_or_refuse` applies, for a setting that
    is only cosmetic and so is ignored (with a warning) rather than stopping the API."""
    return _url_problem(value) is None


def is_https_link(value: str) -> bool:
    """Whether `value` is an https address that is only ever shown as a link, as written: the
    check of `is_plain_https_url` except that a query string is allowed (an install link is
    `.../oauth2/authorize?client_id=...`). Nothing is appended to such a value, so the reason
    a query is refused elsewhere (a path added after it would land inside it) does not apply;
    credentials, a fragment, whitespace and invisible characters are still refused."""
    return _url_problem(value, allow_query=True) is None


def _url_problem(value: str, *, allow_query: bool = False) -> str | None:
    if not value.lower().startswith("https://"):
        return "not https"
    # `isprintable` is False for every control, format (zero-width, direction, soft hyphen, byte
    # order mark), separator, private-use and unassigned character, and for DEL; ordinary letters
    # of any script (an internationalised host or path) pass. A plain space is printable, so it is
    # refused by `isspace`.
    if any(ch.isspace() or not ch.isprintable() or ch == "\\" for ch in value):
        return "whitespace, a control or invisible character, or a backslash"
    if "#" in value or ("?" in value and not allow_query):
        return "a query or fragment"
    try:
        parts = urlsplit(value)
        _port = parts.port  # raises ValueError for a port that is not a number in range
    except ValueError:
        return "not a valid address"
    if not parts.hostname:
        return "no host"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return "credentials"
    return None


def web_app_url() -> str | None:
    """The public address of the web app (WEB_APP_URL), or None when it is unset or empty.
    The bot uses it to link to the website's pages from a chat. A bad value stops the API from
    starting (the lifespan reads this once at boot); it is read again, cheaply, wherever a link
    is built, so a value can never be used without having passed the check."""
    raw = (optional_env("WEB_APP_URL") or "").strip()
    if raw == "":
        return None
    return https_url_or_refuse("WEB_APP_URL", raw)
