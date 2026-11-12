"""Shared env-var helpers, and how the API refuses to start on a bad configuration."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from functools import lru_cache
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


_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")
# re.ASCII keeps IGNORECASE from folding the Kelvin sign, the long s and the dotless i onto k, s and
# i, which would let a letter the operator never wrote through as another one.
_ORIGIN_SHAPE = re.compile(
    r"(?P<scheme>https?)://(?:\[(?P<v6>[0-9a-f:]+)\]|(?P<host>[a-z0-9._-]+))(?::(?P<port>[0-9]{1,5}))?",
    re.IGNORECASE | re.ASCII,
)
_HOST_LABEL = re.compile(r"[a-z0-9_-]{1,63}", re.IGNORECASE | re.ASCII)
_NUMERIC_LABEL = re.compile(r"[0-9]+|0x[0-9a-f]*", re.IGNORECASE | re.ASCII)
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_DEFAULT_PORTS = {"https": 443, "http": 80}


def _host_problem(host: str) -> str | None:
    """Why `host` is not a host name or IPv4 address as a browser writes it, or None. A name is
    labels of letters, digits, hyphens and underscores (1 to 63 each, 253 in all) and may end in
    one dot, which a browser keeps in the origin it sends. A host whose last label is a number is
    an IPv4 address to a browser (127.1 and 0x7f.1 both mean 127.0.0.1): only the canonical
    dotted form, without a trailing dot, is what it sends."""
    name = host[:-1] if host.endswith(".") else host
    labels = name.split(".")
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        try:
            if name == host and str(ipaddress.IPv4Address(host)) == host:
                return None
        except ValueError:
            pass
        return "an IPv4 address must be written as four decimal numbers"
    if len(name) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        return "not a valid host name"
    return None


def _ipv6_text(address: ipaddress.IPv6Address) -> str:
    """The address as a browser writes an IPv6 host: lower-case hexadecimal groups without leading
    zeros, the first longest run of two or more zero groups written as "::". Built here, not taken
    from `ipaddress`, whose text for an IPv4-mapped address differs between Python versions."""
    groups = [(address.packed[i] << 8) | address.packed[i + 1] for i in range(0, 16, 2)]
    start, length, i = -1, 0, 0
    while i < 8:
        if groups[i] != 0:
            i += 1
            continue
        j = i
        while j < 8 and groups[j] == 0:
            j += 1
        if j - i > length:
            start, length = i, j - i
        i = j
    if length < 2:
        return ":".join(f"{group:x}" for group in groups)
    head = ":".join(f"{group:x}" for group in groups[:start])
    tail = ":".join(f"{group:x}" for group in groups[start + length :])
    return f"{head}::{tail}"


def _origin_problem(entry: str) -> str | None:
    """Why `entry` is not an origin as a browser writes it in an `Origin` header, or None."""
    if (
        entry == ""
        or not entry.isascii()
        or any(ch.isspace() or not ch.isprintable() for ch in entry)
    ):
        return "empty, or whitespace, a control character or a non-ASCII character"
    if _EXTENSION_ORIGIN.fullmatch(entry):
        return None
    shape = _ORIGIN_SHAPE.fullmatch(entry)
    if shape is None:
        return "not an origin: a scheme, a host and an optional port, with no path"
    if shape["port"] is not None and not 1 <= int(shape["port"]) <= 65535:
        return "a port outside 1 to 65535"
    if shape["v6"] is not None:
        try:
            address = ipaddress.IPv6Address(shape["v6"])
        except ValueError:
            return "not a valid IPv6 address"
        if _ipv6_text(address) != shape["v6"].lower():
            return "an IPv6 address must be written in its compressed form"
        loopback = shape["v6"] == "::1"
    else:
        problem = _host_problem(shape["host"])
        if problem is not None:
            return problem
        loopback = shape["host"].lower() in _LOOPBACK_HOSTS
    if shape["scheme"].lower() == "http" and not loopback:
        return "http is allowed for localhost only"
    return None


def _normalised_origin(entry: str) -> str:
    """The origin the browser will send for `entry`: lower case, without a default port."""
    if _EXTENSION_ORIGIN.fullmatch(entry):
        return entry
    shape = _ORIGIN_SHAPE.fullmatch(entry)
    assert shape is not None  # `_origin_problem` has accepted it
    scheme = shape["scheme"].lower()
    host = f"[{shape['v6'].lower()}]" if shape["v6"] is not None else shape["host"].lower()
    port = None if shape["port"] is None else int(shape["port"])
    shown = "" if port is None or port == _DEFAULT_PORTS[scheme] else f":{port}"
    return f"{scheme}://{host}{shown}"


@lru_cache(maxsize=16)
def _parse_cors_origins(raw: str) -> frozenset[str]:
    entries = [entry.strip() for entry in raw.split(",")]
    if any(_origin_problem(entry) is not None for entry in entries):
        refuse(
            "CORS_ALLOWED_ORIGINS must be a comma-separated list of origins (https://host[:port], "
            "http://localhost[:port] (or 127.0.0.1, [::1]), or chrome-extension://<32-letter id>), "
            "each written the way a browser sends it: no path, no quotes, no wildcard, no empty "
            "entry"
        )
    return frozenset(_normalised_origin(entry) for entry in entries)


def cors_allowed_origins() -> frozenset[str] | None:
    """The origins a browser page may read this API's responses from (CORS_ALLOWED_ORIGINS), or
    None when it is unset or blank, which means any origin (the setting's absence is warned
    about at boot, and a self-hoster who has not set it keeps working).

    Every entry must be an origin exactly as a browser sends it in the `Origin` header: `https`
    and a host with an optional port, `http` only for localhost, 127.0.0.1 or [::1], or a browser
    extension's `chrome-extension://` and its 32-letter id. Anything that could never match (a
    path, a trailing slash, a wildcard, an internationalised host not written in its `xn--` form) or
    that is a mistake (credentials, an empty entry) stops the API from starting (`refuse`),
    naming the setting and never its value. Entries are compared in lower case without a
    default port. The lifespan reads this once at boot; the CORS middleware reads it again on
    every request, which is cheap, so a value can never be used without having passed the check."""
    raw = optional_env("CORS_ALLOWED_ORIGINS")
    if raw is None or raw.strip() == "":
        return None
    return _parse_cors_origins(raw)
