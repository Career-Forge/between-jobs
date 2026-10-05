"""Point a Telegram bot at this API's webhook (launch plan P2.13).

A bot has exactly one webhook, so the bot you register here is the bot that stops talking to
whatever it pointed at before. Use a separate bot for dev and for prod.

The bot token and the webhook secret come from the shell, never from `.env` (this script does
not load it), and neither is ever printed, logged or put in an error message:

    export TELEGRAM_BOT_TOKEN=...            # from BotFather
    export TELEGRAM_WEBHOOK_SECRET=...       # the same value the API runs with
    python scripts/set_telegram_webhook.py --url https://api.between-jobs.tech --dry-run
    python scripts/set_telegram_webhook.py --url https://api.between-jobs.tech

`--info` only reads what Telegram currently has. `--dry-run` reads it and shows what would be
sent. Setting the webhook drops the updates that piled up while it pointed nowhere (Telegram's
`drop_pending_updates`); pass `--keep-pending` to keep them.

If the bot already points at a different host, the script refuses unless you pass
`--replace-existing`: that is how a dev run would silently take a prod bot's webhook away.

Exit codes: 0 done and clean, 1 Telegram refused or the webhook is not what was asked for,
2 refused before changing anything (bad input or environment), 3 set, but Telegram reports a
delivery error or an unexpected set of update types.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

WEBHOOK_PATH = "/telegram/webhook"
ALLOWED_UPDATES = ["message", "callback_query"]
"""The only update types the webhook handler reads (messages, and the confirm/cancel buttons)."""

_API_BASE = "https://api.telegram.org"
_PORTS = frozenset({80, 88, 443, 8443})  # the ports Telegram's webhook accepts
_SECRET = re.compile(r"[A-Za-z0-9_-]{1,256}")  # Telegram's rule for `secret_token`
_TOKEN = re.compile(r"[0-9]{3,}:[A-Za-z0-9_-]{20,}")  # the shape of a bot token, nothing more
_NOT_PUBLIC_SUFFIXES = (".local", ".localhost", ".internal", ".test", ".invalid", ".example")
_TIMEOUT_SECONDS = 15.0


class Refusal(Exception):
    """The run was refused before it changed anything."""


class TelegramError(Exception):
    """A call to Telegram failed. The message never contains the token or the secret."""


def webhook_url(base: str) -> str:
    """The full webhook URL for a public base URL (`https://api.example.com`), or a Refusal.

    Telegram only delivers to public HTTPS hosts, so anything else is refused here rather than
    registered and left to fail silently."""
    try:
        parts = urlsplit(base.strip())
        port = parts.port
    except ValueError as exc:
        raise Refusal("--url is not a valid URL") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise Refusal("--url must start with https:// (Telegram only delivers to HTTPS)")
    if not host or not host.isascii():
        raise Refusal("--url needs an ASCII host name")
    if parts.username is not None or parts.password is not None:
        raise Refusal("--url must not carry credentials")
    if parts.query or parts.fragment:
        raise Refusal("--url must not carry a query or a fragment")
    if parts.path not in ("", "/", WEBHOOK_PATH):
        raise Refusal(f"--url takes the site's base address; {WEBHOOK_PATH} is added for you")
    if port is not None and port not in _PORTS:
        raise Refusal(f"Telegram only delivers to ports {sorted(_PORTS)}, not {port}")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise Refusal("--url needs a host name, not an IP address")
    if "." not in host or host.endswith(_NOT_PUBLIC_SUFFIXES) or host == "localhost":
        raise Refusal("--url must be a public host name Telegram can reach")
    netloc = host if port is None else f"{host}:{port}"
    return f"https://{netloc}{WEBHOOK_PATH}"


def check_secret(secret: str) -> str:
    if _SECRET.fullmatch(secret) is None:
        raise Refusal(
            "TELEGRAM_WEBHOOK_SECRET must be 1 to 256 characters from A-Z a-z 0-9 _ - "
            "(Telegram's rule for a secret token)"
        )
    return secret


def check_token(token: str) -> str:
    if _TOKEN.fullmatch(token) is None:
        raise Refusal("TELEGRAM_BOT_TOKEN does not look like a bot token from BotFather")
    return token


def _scrub(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


async def _call(
    client: httpx.AsyncClient,
    token: str,
    secret: str,
    method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One Bot API call; the `result` object, or a TelegramError that holds no secret.

    Telegram puts the token in the request URL, and an httpx error message quotes the URL, so a
    transport error is reported by its class alone."""
    try:
        response = await client.post(f"{_API_BASE}/bot{token}/{method}", json=payload or {})
        body = response.json()
    except httpx.HTTPError as exc:
        raise TelegramError(f"{method}: the request failed ({type(exc).__name__})") from None
    except ValueError:
        raise TelegramError(f"{method}: Telegram did not answer with JSON") from None
    if not isinstance(body, dict) or body.get("ok") is not True:
        description = body.get("description") if isinstance(body, dict) else None
        reason = _scrub(str(description or "no reason given"), token, secret)
        raise TelegramError(f"{method}: Telegram refused ({reason})")
    result = body.get("result")
    return result if isinstance(result, dict) else {}


def _when(epoch: object) -> str:
    if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch > 0:
        return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")
    return "never"


def _print_info(info: dict[str, Any], token: str, secret: str) -> None:
    allowed = info.get("allowed_updates")
    print(f"  url: {_scrub(str(info.get('url') or '(none)'), token, secret)}")
    print(f"  pending updates: {info.get('pending_update_count', 0)}")
    print(f"  allowed updates: {allowed if allowed else '(all)'}")
    print(f"  last delivery error: {_when(info.get('last_error_date'))}")
    if info.get("last_error_message"):
        print(f"  last error message: {_scrub(str(info['last_error_message']), token, secret)}")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--url", help="the API's public base URL, e.g. https://api.between-jobs.tech"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--info", action="store_true", help="only read what Telegram has now")
    mode.add_argument(
        "--dry-run", action="store_true", help="show what would be sent, send nothing"
    )
    parser.add_argument(
        "--keep-pending", action="store_true", help="do not drop updates queued while unset"
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="allow replacing a webhook that points at a different host",
    )
    return parser


async def run(args: argparse.Namespace, env: dict[str, str], client: httpx.AsyncClient) -> int:
    token = check_token(env.get("TELEGRAM_BOT_TOKEN", "").strip())
    secret = check_secret(env.get("TELEGRAM_WEBHOOK_SECRET", "").strip())
    if not args.info and not args.url:
        raise Refusal("--url is required (or use --info)")
    target = None if args.info else webhook_url(args.url)

    try:
        current = await _call(client, token, secret, "getWebhookInfo")
        print("Telegram has now:")
        _print_info(current, token, secret)
        if args.info:
            return 0
        assert target is not None
        existing = str(current.get("url") or "")
        if existing and _host(existing) != _host(target) and not args.replace_existing:
            raise Refusal(
                f"the bot already points at {_host(existing)}; a bot has one webhook, so "
                "setting this one takes it away. Pass --replace-existing if that is intended"
            )
        drop = not args.keep_pending
        print(
            f"Would set: {target} (updates {ALLOWED_UPDATES}, secret token set, "
            f"drop pending: {drop})"
        )
        if args.dry_run:
            print("[DRY RUN] nothing was changed")
            return 0
        await _call(
            client,
            token,
            secret,
            "setWebhook",
            {
                "url": target,
                "secret_token": secret,
                "allowed_updates": ALLOWED_UPDATES,
                "drop_pending_updates": drop,
            },
        )
        after = await _call(client, token, secret, "getWebhookInfo")
    except TelegramError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1

    print("Telegram now has:")
    _print_info(after, token, secret)
    if after.get("url") != target:
        print("failed: Telegram does not report the webhook that was just set", file=sys.stderr)
        return 1
    warnings = []
    if after.get("last_error_date"):
        warnings.append("Telegram reports a delivery error (see above)")
    if sorted(after.get("allowed_updates") or []) != sorted(ALLOWED_UPDATES):
        warnings.append(f"allowed updates are not exactly {ALLOWED_UPDATES}")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 3 if warnings else 0


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines quote request URLs
    args = _parser().parse_args()

    async def go() -> int:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            return await run(args, dict(os.environ), client)

    try:
        sys.exit(asyncio.run(go()))
    except Refusal as refusal:
        sys.exit(f"refused: {refusal}")


if __name__ == "__main__":
    main()
