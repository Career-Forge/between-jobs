"""Register the Discord app's slash commands (the ones in api/discord_commands.py).

Discord keeps one list of global commands per application, and this script REPLACES it (Discord's
"bulk overwrite" call): the commands this repository defines are created or updated, and any other
global command on the application is deleted. So it plans first and says what it would remove, and
it will not remove anything unless you pass `--remove-others`.

Like `set_telegram_webhook.py`, the default is a dry run, and the token comes from the shell, never
from `.env` (this script does not load it), and is never printed, logged or put in a URL or an error
message (Discord takes it in an `Authorization: Bot ...` header):

    export DISCORD_APPLICATION_ID=...    # the application's id (developer portal)
    export DISCORD_BOT_TOKEN=...         # the bot's token (developer portal, Bot page)
    python scripts/register_discord_commands.py          # dry run: what is registered, what changes
    python scripts/register_discord_commands.py --apply  # do it

The commands are registered for the direct message with the app's bot only (a server channel never
shows them). `--integration-types` chooses who can install the app to use them: `both` (the
default), `guild` (a server install only) or `user` (a person's own install only). If the
application has not enabled an installation type, Discord refuses the call and says so; narrow the
flag or enable the type under Installation in the developer portal.

Exit codes: 0 done and clean (or a dry run), 1 Discord refused or does not report the commands that
were just sent, 2 refused before anything was changed (bad input or environment, or commands that
would be removed without `--remove-others`).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from typing import Any

import httpx

from between_jobs.api import discord_commands as commands

_API_BASE = "https://discord.com/api/v10"
_USER_AGENT = "DiscordBot (https://github.com/Career-Forge/between-jobs, 0.0.1)"
_APPLICATION_ID = re.compile(r"[0-9]{15,25}")
_TOKEN = re.compile(r"[A-Za-z0-9._\-]{20,200}")  # the shape of a bot token, nothing more
_TIMEOUT_SECONDS = 15.0
_TYPES = {
    "both": commands.DEFAULT_INTEGRATION_TYPES,
    "guild": (commands.INTEGRATION_TYPE_GUILD_INSTALL,),
    "user": (commands.INTEGRATION_TYPE_USER_INSTALL,),
}


class Refusal(Exception):
    """The run was refused before it sent anything."""


class DiscordError(Exception):
    """A call to Discord failed. The message never contains the token."""


def check_application_id(value: str) -> str:
    if _APPLICATION_ID.fullmatch(value) is None:
        raise Refusal("DISCORD_APPLICATION_ID must be the application's id (digits only)")
    return value


def check_token(token: str) -> str:
    if _TOKEN.fullmatch(token) is None:
        raise Refusal("DISCORD_BOT_TOKEN does not look like a bot token")
    return token


def check_definitions(definitions: list[dict[str, Any]]) -> None:
    """Refuses definitions that break a limit Discord publishes, before anything is sent. The
    tests hold the shipped definitions to the same limits; this is for a definition edited in a
    hurry."""
    if not 1 <= len(definitions) <= commands.MAX_COMMANDS:
        raise Refusal(f"Discord takes 1 to {commands.MAX_COMMANDS} global commands")
    name = re.compile(rf"[a-z0-9_-]{{1,{commands.MAX_NAME_LENGTH}}}")
    seen: set[str] = set()
    for definition in definitions:
        if name.fullmatch(definition["name"]) is None or definition["name"] in seen:
            raise Refusal(f"a command name is not valid or is repeated: {definition['name']!r}")
        seen.add(definition["name"])
        if not 1 <= len(definition["description"]) <= commands.MAX_DESCRIPTION_LENGTH:
            raise Refusal(f"/{definition['name']}: the description is not 1-100 characters")
        options = definition["options"]
        if len(options) > commands.MAX_OPTIONS_PER_COMMAND:
            raise Refusal(f"/{definition['name']}: more than 25 options")
        if [bool(o["required"]) for o in options] != sorted(
            (bool(o["required"]) for o in options), reverse=True
        ):
            raise Refusal(f"/{definition['name']}: a required option follows an optional one")


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "[redacted]") if token else text


async def _call(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    path: str,
    body: list[dict[str, Any]] | None = None,
) -> Any:
    """One call; the JSON answer, or a DiscordError that holds no token. A transport error is
    reported by its class alone, since an httpx error can quote the request."""
    try:
        response = await client.request(
            method,
            f"{_API_BASE}{path}",
            headers={"Authorization": f"Bot {token}", "User-Agent": _USER_AGENT},
            json=body,
        )
    except httpx.HTTPError as exc:
        raise DiscordError(f"{method} {path}: the request failed ({type(exc).__name__})") from None
    try:
        answer = response.json()
    except ValueError:
        raise DiscordError(f"{method} {path}: Discord did not answer with JSON") from None
    if response.status_code >= 400:
        message = answer.get("message") if isinstance(answer, dict) else None
        code = answer.get("code") if isinstance(answer, dict) else None
        reason = _scrub(str(message or "no reason given"), token)
        raise DiscordError(
            f"{method} {path}: Discord refused (HTTP {response.status_code}, code {code}: {reason})"
        )
    return answer


def _names(listing: Any) -> list[str]:
    if not isinstance(listing, list):
        return []
    return sorted(str(item.get("name")) for item in listing if isinstance(item, dict))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="send the commands to Discord (the default is a dry run that sends nothing)",
    )
    parser.add_argument(
        "--remove-others",
        action="store_true",
        help="allow deleting global commands this repository does not define",
    )
    parser.add_argument(
        "--integration-types",
        choices=sorted(_TYPES),
        default="both",
        help="which installations may use the commands (default: both)",
    )
    return parser


async def run(args: argparse.Namespace, env: dict[str, str], client: httpx.AsyncClient) -> int:
    application_id = check_application_id(env.get("DISCORD_APPLICATION_ID", "").strip())
    token = check_token(env.get("DISCORD_BOT_TOKEN", "").strip())
    definitions = commands.command_definitions(integration_types=_TYPES[args.integration_types])
    check_definitions(definitions)
    wanted = sorted(d["name"] for d in definitions)
    path = f"/applications/{application_id}/commands"

    try:
        current = await _call(client, token, "GET", path)
        existing = _names(current)
        print(f"Discord has now: {', '.join(existing) or '(no global commands)'}")
        extra = sorted(set(existing) - set(wanted))
        print(f"This repository defines: {', '.join(wanted)}")
        print(f"  to create: {', '.join(sorted(set(wanted) - set(existing))) or '(none)'}")
        print(f"  to update: {', '.join(sorted(set(wanted) & set(existing))) or '(none)'}")
        print(f"  to remove: {', '.join(extra) or '(none)'}")
        print(
            f"Would register {len(definitions)} commands for the direct message with the bot, "
            f"installation types {list(_TYPES[args.integration_types])}"
        )
        if not args.apply:
            print("[DRY RUN] nothing was changed (pass --apply to register)")
            return 0
        if extra and not args.remove_others:
            raise Refusal(
                f"registering would delete {len(extra)} command(s) this repository does not "
                f"define ({', '.join(extra)}); pass --remove-others if that is intended"
            )
        await _call(client, token, "PUT", path, definitions)
        after = await _call(client, token, "GET", path)
    except DiscordError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1

    registered = _names(after)
    print(f"Discord now has: {', '.join(registered)}")
    if registered != wanted:
        print("failed: Discord does not report the commands that were just sent", file=sys.stderr)
        return 1
    return 0


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
