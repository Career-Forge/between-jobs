"""What the error reporter is allowed to send: pure functions that clean an event.

The reporter (error_reporting.py) hands every event, transaction and breadcrumb to a
function in this module before it leaves the process. They take plain dicts and return
new ones; none of them touches the SDK, the network or any global state, so they are
tested directly with hostile inputs.

THIS IS A SAFETY NET, NOT A GUARANTEE. Three layers keep personal data out of the
reports, and only the first two are structural:

1. The SDK is configured to collect little (error_reporting.py): no request bodies, no
   local variables, no default PII, no log-line events, no outbound-HTTP breadcrumbs.
2. This module drops, by shape, whole parts of an event: the request's body, cookies,
   query string and every header outside a short allowlist; the user down to at most an
   opaque id; stack-frame variables, the process's command line and the machine's
   hostname; any value stored under a key whose name says it is a secret (token,
   password, key, authorization, cookie, email, phone, ip ...); and outbound-HTTP,
   database and subprocess breadcrumbs. The source lines shown around a stack frame are
   code read from a file, not data the program handled, so they are cut to a length and
   otherwise left alone.
3. Regex scrubbing of the free text that is left: exception messages, log messages,
   URLs, tags, extras. It reuses the log redactor's patterns (logging_setup.redact: JWTs,
   bearer tokens, OpenRouter/OpenAI/Anthropic-style `sk-` keys, Telegram bot tokens,
   Supabase and Google keys, `name=value` secrets, URL query strings and email
   addresses) plus a catch for a truncated JWT. REGEX SCRUBBING CANNOT BE PERFECT: it
   recognises the shapes of secrets that are known today, and nothing at all about a
   person's name, a resume sentence or a company a validation error happens to quote.

   Two further limits follow from that. An exception raised by a library that quotes the
   values involved in its message (a database client, a validation library, an HTTP
   client, an AI SDK, FastAPI's validation errors) has its message replaced outright, the
   same rule the JSON logs follow ("types, stack frames and allowlisted fields, never the
   free-text message"). So does an `ApiError`: its message is written for the person who
   sees it, and the clients of the resume engine and the PDF renderer build it from what
   those services answered. Every other exception keeps a scrubbed message cut to a few
   hundred characters. And free text is cut before it is scanned, so a hostile event
   cannot make the scan slow.

Fail closed: a function that cannot make sense of its input, or that raises, returns
None, and the SDK drops the event. An unscrubbed event never goes out.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .errors import ApiError
from .logging_setup import redact

logger = logging.getLogger(__name__)

FILTERED = "[Filtered]"

MAX_TEXT_CHARS = 1000
"""Longest string scanned for secrets; the rest is cut off first. The redactor's patterns
can take time quadratic in the length of a string, so a bound is what keeps a hostile
event cheap, and the event is scrubbed on the caller's thread (the event loop's)."""

MAX_TOTAL_CHARS = 40_000
"""Total characters scanned in one event; past it, strings are replaced, not scanned."""

MAX_NODES = 20_000
"""Dict entries and list items visited in one event; past it, the rest is dropped."""

MAX_DEPTH = 20
"""Deeper than a real event ever nests (a stack frame sits about eight levels down)."""

MAX_KEY_CHARS = 200
MAX_EXCEPTION_MESSAGE_CHARS = 300
MAX_USER_ID_CHARS = 64

# Headers that stay. Everything else -- Authorization, Cookie, Set-Cookie, apikey-style
# headers, X-Telegram-Bot-Api-Secret-Token, X-Forwarded-For and every other header a
# caller or a proxy might put an identity or a secret in -- is dropped by name.
_KEPT_HEADERS = frozenset(
    {"accept", "content-length", "content-type", "host", "origin", "user-agent", "x-request-id"}
)

# Whole words (a key is split on punctuation and camelCase: `apiKey` -> api, key) that make
# a key's value secret, and the endings that do the same for a compound written as one
# word (`accesstoken`, `useremail`). Whole words, not substrings, so `monkey` and
# `tokenizers` stay.
_SECRET_WORDS = frozenset(
    {
        "addr",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "email",
        "emails",
        "ip",
        "jwt",
        "key",
        "keys",
        "passwd",
        "password",
        "passwords",
        "phone",
        "phones",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)
_SECRET_SUFFIXES = ("apikey", "cookie", "email", "password", "phone", "secret", "token")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")

# A JWT the length cut sliced before its last segment still gives its claims away.
_PARTIAL_JWT = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}[A-Za-z0-9_.\-]*")
_URL_USERINFO = re.compile(r"(?i)^(https?://)[^/?#\s]*@")

# Modules whose exception messages quote the values involved (database rows, request
# input, provider replies), so the message is not sent at all. Libraries, and this API's own
# `ApiError`: its message is written for the person who sees it, and the clients of the
# resume engine and the PDF renderer build it from what those services answered.
_MESSAGE_OMITTED_MODULES = (
    "between_jobs.api.errors",
    "gotrue",
    "httpcore",
    "httpx",
    "openai",
    "postgrest",
    "pydantic",
    "pydantic_core",
    "realtime",
    "storage3",
    "supabase",
    "supabase_auth",
    "supabase_functions",
)
# Exception types that are omitted by name because their module also holds exceptions whose
# message is a fixed string of ours (FastAPI's `HTTPException`). FastAPI's validation errors
# quote the offending input, and a response that fails its declared model is an unhandled 500
# whose message is the response data.
_MESSAGE_OMITTED_TYPES = frozenset(
    ("fastapi.exceptions", name)
    for name in (
        "RequestValidationError",
        "ResponseValidationError",
        "ValidationException",
        "WebSocketRequestValidationError",
    )
)
MESSAGE_OMITTED = "[message omitted: it may quote user data]"

# Keys whose values are never kept: stack-frame variables, the process's command line.
_FILTERED_KEYS = frozenset({"sys.argv", "vars"})
# Event keys left out altogether: the machine's hostname can be a person's name on a
# developer's own computer, and on a platform it is an opaque container id nobody needs; and
# the SDK's own `_meta` annotations ("a value was removed here"), which describe fields this
# module has already removed or reshaped, and which the key-name filter below would only
# turn into a bare string Sentry ignores.
_DROPPED_EVENT_KEYS = frozenset({"server_name", "_meta"})

# The SDK's dynamic sampling context rides in `contexts.trace` until the SDK moves it into
# the envelope header, which happens after `before_send`. Its `public_key` is the DSN's
# public half (it is in the DSN and in every request's auth header, and is no secret), but
# the key-name filter reads "key" as a secret, so the context is rebuilt from this allowlist
# instead of walked: an invalid one is discarded by Sentry, and with it any sampling that
# depends on it.
_DSC_KEYS = frozenset(
    {
        "environment",
        "org_id",
        "public_key",
        "release",
        "sample_rand",
        "sample_rate",
        "sampled",
        "trace_id",
        "transaction",
    }
)
_PUBLIC_KEY = re.compile(r"[A-Za-z0-9]{8,64}")
MAX_DSC_VALUE_CHARS = 200

# The source lines shown around a stack frame. They are code read from a file, not data
# the program handled, and the secret patterns would only garble them (`token = <redacted>`),
# so they are cut to a length and otherwise left alone.
_SOURCE_LINE_KEYS = frozenset({"context_line", "post_context", "pre_context"})
MAX_SOURCE_LINE_CHARS = 300
MAX_SOURCE_LINES = 20

# Breadcrumbs the app has no use for and that carry URLs or command lines.
_DROPPED_BREADCRUMB_TYPES = frozenset({"http", "query"})
_DROPPED_BREADCRUMB_CATEGORIES = frozenset({"db", "httplib", "httpx", "query", "subprocess"})


class _Budget:
    __slots__ = ("chars", "nodes")

    def __init__(self) -> None:
        self.nodes = MAX_NODES
        self.chars = MAX_TOTAL_CHARS


def is_secret_key(key: str) -> bool:
    """Whether a value stored under `key` is treated as secret, whatever it holds."""
    # NFKC first, so fullwidth and other compatibility lookalikes of ASCII letters count.
    spaced = _CAMEL_BOUNDARY.sub(" ", unicodedata.normalize("NFKC", key))
    words = [w for w in _NOT_ALNUM.split(spaced.lower()) if w]
    return any(w in _SECRET_WORDS or w.endswith(_SECRET_SUFFIXES) for w in words)


def scrub_text(text: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    """`text` with the shapes of known secrets, URL query strings and email addresses
    replaced. Cut to `limit` characters BEFORE it is scanned. Not exhaustive: see the
    module docstring."""
    if len(text) > limit:
        text = text[:limit] + "...[cut]"
    return _PARTIAL_JWT.sub("<jwt-redacted>", redact(text))


def scrub_url(url: str) -> str:
    """A URL without credentials, query string or fragment."""
    url = url.split("#", 1)[0].split("?", 1)[0]
    return scrub_text(_URL_USERINFO.sub(r"\1", url))


def _scrub_string(text: str, budget: _Budget) -> str:
    if budget.chars <= 0:
        return FILTERED
    budget.chars -= min(len(text), MAX_TEXT_CHARS)
    return scrub_text(text)


def _source_lines(value: Any) -> Any:
    if isinstance(value, str):
        return value[:MAX_SOURCE_LINE_CHARS]
    if isinstance(value, list | tuple):
        return [
            line[:MAX_SOURCE_LINE_CHARS]
            for line in value[:MAX_SOURCE_LINES]
            if isinstance(line, str)
        ]
    return FILTERED


def _log_dropped(what: str, failure: BaseException) -> None:
    """Says that something was dropped because it could not be scrubbed -- the type of the
    failure only, never its message or the event."""
    logger.warning(
        "an error report was dropped because it could not be scrubbed",
        extra={
            "ctx": {
                "what": what,
                "error_type": f"{type(failure).__module__}.{type(failure).__qualname__}",
            }
        },
    )


def _walk(value: Any, budget: _Budget, depth: int, key: str | None = None) -> Any:
    """Copies `value` with secrets removed. Anything that is not plain JSON data
    (bytes, objects) becomes `[Filtered]`, and a structure larger or deeper than the
    bounds is cut."""
    if budget.nodes <= 0:
        return FILTERED
    budget.nodes -= 1
    if key is not None:
        if key in _FILTERED_KEYS or is_secret_key(key):
            return FILTERED
        if key in _SOURCE_LINE_KEYS:
            return _source_lines(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _scrub_string(value, budget)
    if depth >= MAX_DEPTH:
        return FILTERED
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            if budget.nodes <= 0 or budget.chars <= 0:
                break
            if not isinstance(raw_key, str):
                continue
            budget.chars -= min(len(raw_key), MAX_KEY_CHARS)
            out[scrub_text(raw_key, limit=MAX_KEY_CHARS)] = _walk(item, budget, depth + 1, raw_key)
        return out
    if isinstance(value, list | tuple | set | frozenset):
        items: list[Any] = []
        for item in value:
            if budget.nodes <= 0:
                break
            items.append(_walk(item, budget, depth + 1))
        return items
    return FILTERED


def _scrub_dsc(dsc: Any) -> dict[str, str]:
    """The SDK's dynamic sampling context cut down to the allowlisted keys, each a short
    string run through `scrub_text`; a public key that is not a plain token is dropped."""
    if not isinstance(dsc, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, value in dsc.items():
        if name not in _DSC_KEYS or not isinstance(value, str) or not value:
            continue
        if name == "public_key" and _PUBLIC_KEY.fullmatch(value) is None:
            continue
        out[name] = scrub_text(value, limit=MAX_DSC_VALUE_CHARS)
    return out


def _scrub_contexts(contexts: Any, budget: _Budget) -> Any:
    """`contexts` walked like any other value, except the trace context's dynamic sampling
    context, which is rebuilt by `_scrub_dsc` (see `_DSC_KEYS`)."""
    trace = contexts.get("trace") if isinstance(contexts, Mapping) else None
    if not isinstance(trace, Mapping) or "dynamic_sampling_context" not in trace:
        return _walk(contexts, budget, 1, "contexts")
    dsc = _scrub_dsc(trace["dynamic_sampling_context"])
    rest = {
        **contexts,
        "trace": {k: v for k, v in trace.items() if k != "dynamic_sampling_context"},
    }
    out = _walk(rest, budget, 1, "contexts")
    if dsc and isinstance(out, dict) and isinstance(out.get("trace"), dict):
        out["trace"]["dynamic_sampling_context"] = dsc
    return out


def _scrub_headers(headers: Any) -> dict[str, str]:
    pairs: list[tuple[Any, Any]] = []
    if isinstance(headers, Mapping):
        pairs = list(headers.items())
    elif isinstance(headers, list | tuple):
        pairs = [(p[0], p[1]) for p in headers if isinstance(p, list | tuple) and len(p) == 2]
    kept: dict[str, str] = {}
    for name, value in pairs[:200]:
        if isinstance(name, str) and name.lower() in _KEPT_HEADERS and isinstance(value, str):
            kept[name] = scrub_text(value, limit=500)
    return kept


def _scrub_request(request: Any) -> dict[str, Any] | None:
    """Only the method, the URL without its query string, and the allowlisted headers.
    The body (`data`), cookies, the query string and the WSGI-style `env` (it carries the
    caller's address) are never kept."""
    if not isinstance(request, Mapping):
        return None
    out: dict[str, Any] = {}
    url = request.get("url")
    if isinstance(url, str):
        out["url"] = scrub_url(url[:1000])
    method = request.get("method")
    if isinstance(method, str) and method.isalpha() and len(method) <= 16:
        out["method"] = method.upper()
    if "headers" in request:
        out["headers"] = _scrub_headers(request["headers"])
    return out


def _scrub_user(user: Any) -> dict[str, str] | None:
    """At most an opaque id: never an email, a username or an address."""
    if not isinstance(user, Mapping):
        return None
    raw = user.get("id")
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str) or not raw or len(raw) > MAX_USER_ID_CHARS:
        return None
    if "@" in raw or scrub_text(raw) != raw:
        return None
    return {"id": raw}


def _message_omitted(module: Any) -> bool:
    if not isinstance(module, str):
        return False
    return any(module == m or module.startswith(m + ".") for m in _MESSAGE_OMITTED_MODULES)


def _type_omitted(item: dict[str, Any]) -> bool:
    module, name = item.get("module"), item.get("type")
    if not isinstance(module, str) or not isinstance(name, str):
        return False
    return (module, name) in _MESSAGE_OMITTED_TYPES


def _limit_exception_messages(exception: Any) -> Any:
    """Applies the exception-message policy to an already scrubbed `exception` block."""
    values = exception.get("values") if isinstance(exception, dict) else exception
    if not isinstance(values, list):
        return exception
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("value"), str):
            continue
        if _message_omitted(item.get("module")) or _type_omitted(item):
            item["value"] = MESSAGE_OMITTED
        elif len(item["value"]) > MAX_EXCEPTION_MESSAGE_CHARS:
            item["value"] = item["value"][:MAX_EXCEPTION_MESSAGE_CHARS] + "..."
    return exception


def _scrub_breadcrumb_list(container: Any, budget: _Budget) -> Any:
    values = container.get("values") if isinstance(container, Mapping) else container
    if not isinstance(values, list | tuple):
        return None
    crumbs = []
    for crumb in values:
        cleaned = _scrub_breadcrumb(crumb, budget)
        if cleaned is not None:
            crumbs.append(cleaned)
    return {"values": crumbs} if isinstance(container, Mapping) else crumbs


def _scrub_breadcrumb(crumb: Any, budget: _Budget) -> dict[str, Any] | None:
    if not isinstance(crumb, Mapping):
        return None
    if (
        crumb.get("type") in _DROPPED_BREADCRUMB_TYPES
        or crumb.get("category") in _DROPPED_BREADCRUMB_CATEGORIES
    ):
        return None
    cleaned = _walk(crumb, budget, 0)
    return cleaned if isinstance(cleaned, dict) else None


def scrub_event(event: Any, hint: Any = None) -> dict[str, Any] | None:
    """The event (or transaction) with everything described in the module docstring
    removed, as a new dict; None when `event` is not a dict or could not be scrubbed,
    which makes the SDK drop it. Never raises."""
    del hint
    try:
        return _scrub_event(event)
    except Exception as failure:
        _log_dropped("event", failure)
        return None


def _scrub_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, Mapping):
        return None
    budget = _Budget()
    out: dict[str, Any] = {}
    for key, value in event.items():
        if not isinstance(key, str) or key in _DROPPED_EVENT_KEYS:
            continue
        if key == "request":
            request = _scrub_request(value)
            if request is not None:
                out[key] = request
        elif key == "user":
            user = _scrub_user(value)
            if user is not None:
                out[key] = user
        elif key == "breadcrumbs":
            crumbs = _scrub_breadcrumb_list(value, budget)
            if crumbs is not None:
                out[key] = crumbs
        elif key == "exception":
            out[key] = _limit_exception_messages(_walk(value, budget, 1))
        elif key == "contexts":
            out[key] = _scrub_contexts(value, budget)
        else:
            out[key] = _walk(value, budget, 1, key)
    return out


def scrub_breadcrumb(crumb: Any, hint: Any = None) -> dict[str, Any] | None:
    """The breadcrumb cleaned, or None to drop it (outbound HTTP, database and
    subprocess crumbs are always dropped; so is anything that is not a dict)."""
    del hint
    try:
        return _scrub_breadcrumb(crumb, _Budget())
    except Exception as failure:
        _log_dropped("breadcrumb", failure)
        return None


def is_client_error(hint: Any) -> bool:
    """True for an event about an `ApiError` the API deliberately answered with a 4xx:
    the caller was told, nothing is broken, so nothing is reported."""
    exc_info = hint.get("exc_info") if isinstance(hint, Mapping) else None
    error = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) == 3 else None
    return isinstance(error, ApiError) and error.status_code < 500


def before_send(event: Any, hint: Any) -> dict[str, Any] | None:
    """The SDK's `before_send` (and `before_send_transaction`): drops what is not worth
    reporting, then scrubs. Never raises."""
    try:
        if is_client_error(hint):
            return None
    except Exception as failure:
        _log_dropped("event", failure)
        return None
    return scrub_event(event, hint)
