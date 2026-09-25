"""Server logging: one JSON object per line on stdout.

The rule is that logs carry ids, counts, error codes and exception types --
never request bodies, BYOK secrets, resume or job-description text, or email
contents. Three things hold it:

- Code logs only safe fields (`extra={"ctx": {...}}` with ids and counts).
- Exceptions are written as their types, stack frames and a few allowlisted
  fields (an ApiError code, a Postgres SQLSTATE, an HTTP status and host) --
  never their free-text message, which for database, provider and validation
  errors can quote the values involved.
- `redact` scrubs key-shaped strings, URL query strings and email addresses
  from everything written, as a safety net under the first two.

Third-party libraries log at WARNING and above only: httpx would otherwise
print every outbound URL (the Telegram bot token is part of Telegram's), and
the openai SDK prints whole prompts at DEBUG. LOG_LEVEL governs this app's own
loggers.

`request_id_var` carries the id of the request being served, set by the
middleware in app.py and stamped onto every line logged while it runs.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_NOT_ALNUM_BEFORE = r"(?<![A-Za-z0-9])"
_SECRET_NAMES = (
    r"(?:api[_-]?key|apikey|app[_-]?key|x-api-key|rapidapi-key|x-rapidapi-key|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|token|client[_-]?secret|secret(?:[_-]?\d+)?|password|"
    r"authorization(?:-key)?)"
)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # LLM and search provider keys: OpenRouter/OpenAI/Anthropic (sk-...), Firecrawl
    # (fc-...), xAI (xai-...), Tavily (tvly-...), Groq (gsk_...), Google OAuth
    # client secrets (GOCSPX-...).
    (re.compile(_NOT_ALNUM_BEFORE + r"sk-[A-Za-z0-9_\-]{8,}"), "sk-<redacted>"),
    (re.compile(_NOT_ALNUM_BEFORE + r"(?:fc|xai|tvly(?:-dev)?)-[A-Za-z0-9_\-]{16,}"), "<key>"),
    (re.compile(_NOT_ALNUM_BEFORE + r"gsk_[A-Za-z0-9]{20,}"), "<key>"),
    (re.compile(_NOT_ALNUM_BEFORE + r"GOCSPX-[A-Za-z0-9_\-]{20,}"), "<key>"),
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._\-~+/]+=*"), "<auth-redacted>"),
    # JWTs (Supabase access tokens, legacy service-role keys).
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        "<jwt-redacted>",
    ),
    # Supabase secret and publishable API keys.
    (re.compile(r"\bsb_(?:secret|publishable)_[A-Za-z0-9_\-]{8,}"), "sb_<redacted>"),
    # Telegram bot tokens, including inside `https://api.telegram.org/bot<token>/...`.
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_\-]{30,}"), "<telegram-token-redacted>"),
    # Google API keys and OAuth tokens.
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), "<google-key-redacted>"),
    (re.compile(r"\bya29\.[0-9A-Za-z_\-.]{20,}"), "<google-token-redacted>"),
    (re.compile(r"\b1//[0-9A-Za-z_\-]{20,}"), "<google-token-redacted>"),
    # name=value / "name": "value" / 'name': 'value' pairs whose name says secret.
    (
        re.compile(
            r"(?i)" + _NOT_ALNUM_BEFORE + "(" + _SECRET_NAMES + r")([\"']?\s*[:=]\s*[\"']?)"
            r"[^\s\"'&,;}]+"
        ),
        r"\1\2<redacted>",
    ),
    # Query strings: some providers take keys as query parameters, and OAuth
    # callbacks carry codes there.
    (re.compile(r"(https?://[^\s?#\"']+)\?[^\s#\"']*"), r"\1?<query-redacted>"),
    # Email addresses.
    (
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
        "<email>",
    ),
)

_MAX_TEXT = 4000
_MAX_TRACEBACK = 12000

# Libraries whose INFO/DEBUG output carries URLs, headers or request bodies.
_QUIET_LIBRARIES = (
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "hpack",
    "h2",
    "openai",
    "websockets",
    "realtime",
    "postgrest",
    "gotrue",
    "supabase",
    "storage3",
    "asyncio",
)
_LEVELS = {name: getattr(logging, name) for name in ("DEBUG", "INFO", "WARNING", "ERROR")}


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _strip_query(path: str) -> str:
    return path.split("?", 1)[0] + ("?<query-redacted>" if "?" in path else "")


def _safe_detail(exc: BaseException) -> str:
    """The parts of an exception that are safe to log. Never `str(exc)`.
    Attribute access can itself raise (httpx's `.request` does when unset),
    so every probe is guarded."""

    def request_host() -> Any:
        return getattr(getattr(getattr(exc, "request", None), "url", None), "host", None)

    getters: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("code", lambda: getattr(exc, "code", None)),
        ("status", lambda: getattr(exc, "status_code", None)),
        ("status", lambda: getattr(getattr(exc, "response", None), "status_code", None)),
        ("host", request_host),
    )
    parts: list[str] = []
    for label, getter in getters:
        try:
            value = getter()
        except Exception:
            continue
        if label == "status" and any(part.startswith("status=") for part in parts):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, str) and 0 < len(value) <= 64):
            parts.append(f"{label}={value}")
    return " ".join(parts)


def format_exception_safely(exc: BaseException) -> str:
    """Like `traceback.format_exception`, but each exception in the chain is
    shown as its type plus `_safe_detail` -- stack frames and source lines are
    code, so they stay; the free-text messages don't."""
    chain: list[tuple[BaseException, str]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    link = ""
    while current is not None and id(current) not in seen and len(chain) < 10:
        seen.add(id(current))
        chain.append((current, link))
        if current.__cause__ is not None:
            link = "The above exception was the direct cause of the following exception:"
            current = current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            link = "During handling of the above exception, another exception occurred:"
            current = current.__context__
        else:
            current = None
    blocks: list[str] = []
    for index, (item, _) in enumerate(reversed(chain)):
        name = f"{type(item).__module__}.{type(item).__qualname__}"
        detail = _safe_detail(item)
        frames = "".join(traceback.format_tb(item.__traceback__))
        block = "Traceback (most recent call last):\n" + frames if frames else ""
        block += name + (f" [{detail}]" if detail else "") + "\n"
        if index > 0:
            # the link text belongs before this block: it describes the
            # relationship between this exception and the one printed above
            block = "\n" + chain[len(chain) - index][1] + "\n\n" + block
        blocks.append(block)
    return "".join(blocks)


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ts, level, logger, msg, request_id, any
    `extra={"ctx": {...}}` fields, and `exc` for an attached exception. Every
    string passes through `redact`."""

    def format(self, record: logging.LogRecord) -> str:
        args = record.args
        # uvicorn's access line is `client - "METHOD path HTTP/x" status`: the
        # client address is dropped, and the path loses its query string (the
        # Gmail OAuth callback's code rides there).
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) >= 3:
            args = ("-", args[1], _strip_query(str(args[2])), *args[3:])
        try:
            message = str(record.msg) % args if args else str(record.msg)
        except (TypeError, ValueError):
            message = str(record.msg)

        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(message)[:_MAX_TEXT],
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            entry["ctx"] = {str(k): _safe_value(v) for k, v in ctx.items()}
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            entry["exc"] = {
                "type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "traceback": redact(format_exception_safely(exc))[-_MAX_TRACEBACK:],
            }
        return json.dumps(entry, default=str)


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact(str(value))[:_MAX_TEXT]


_HANDLER_MARK = "_between_jobs_handler"


def configure_logging() -> None:
    """Send every log record (the app's, uvicorn's and the libraries') through
    one JSON handler on stdout. Idempotent.

    LOG_LEVEL (DEBUG, INFO, WARNING or ERROR; anything else means INFO) sets
    the level of this app's own loggers. Third-party libraries stay at WARNING
    whatever it says -- see the module docstring."""
    level = _LEVELS.get(os.environ.get("LOG_LEVEL", "INFO").strip().upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    if not any(getattr(h, _HANDLER_MARK, False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)

    logging.getLogger("between_jobs").setLevel(level)
    for name in _QUIET_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)

    # uvicorn installs its own plain-text handlers before it imports the app;
    # route the loggers that have one through the JSON handler instead. A logger
    # uvicorn left without handlers (--no-access-log) stays silent.
    for name in ("uvicorn", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        if uvicorn_logger.handlers:
            uvicorn_logger.handlers.clear()
            uvicorn_logger.propagate = True
