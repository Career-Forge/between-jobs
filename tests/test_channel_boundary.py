"""The channel boundary stays where the extraction put it.

The business logic (`channel_core`, `channel_messages`) speaks the neutral vocabulary of
`channel_envelope`; Telegram's wire format lives in `telegram_adapter` (and the HTTP client it
wraps, `telegram_client`). A second channel is only cheap while that holds, and it erodes one
convenient shortcut at a time, so it is enforced here by reading the source:

1. Nothing outside the adapter, the client and the wiring (`app.py`, `app_state.py`) imports
   or names `TelegramClient` or `Html`.
2. Nothing outside the adapter and the client reads Telegram's update JSON (no `update_id`,
   `callback_query`, `edited_message` or `message` read from a dict) or writes its wire format
   (`callback_data`, `inline_keyboard`) -- except in the few modules that parse some OTHER
   service's JSON which happens to call a field `message`, each listed below with the reason.
   Spelling `update_id` as a key of a log-context dict is not a read and is fine.
3. The neutral modules do not import the Telegram ones: the dependency points one way. That
   covers the identity lookups, the fan-out push, the progress message and the first-run
   checklist, which every channel shares.

Discord, the second channel, is held to the same rules by the same method (see the second half
of this file): its raw interaction shapes and its REST API belong to `discord_adapter` and the
modules it is made of; the neutral modules do not import any of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "between_jobs"

TELEGRAM_OWN = frozenset({"api/telegram_adapter.py", "api/telegram_client.py"})
"""The only modules that may know Telegram's wire format."""

WIRING = frozenset({"api/app.py", "api/app_state.py"})
"""Where the bot's client is built and handed out; they name `TelegramClient` and nothing more."""

OTHER_SERVICES_MESSAGE_FIELD = {
    "api/ats_liveness.py": "reads an ATS error body's `message` field",
    "api/discord_adapter.py": "reads the `message` a Discord button tap was attached to",
    "api/forge_engines_client.py": "reads a validation violation's `message` from forge-engines",
    "api/gmail_client.py": "reads Gmail's `message.threadId` out of a draft response",
    "api/upstream_errors.py": "reads the `message` of the resume engine's and the LaTeX service's "
    "error answers",
}
"""Modules that read a `message` key from a service that is not Telegram."""

READ_KEYS = frozenset({"update_id", "callback_query", "edited_message"})
"""Keys of Telegram's update JSON: flagged when a module READS one (subscript, `.get`, `in`)."""
WIRE_KEYS = frozenset({"callback_data", "inline_keyboard"})
"""Keys of Telegram's request JSON: flagged wherever a module spells one."""
CLIENT_NAMES = frozenset({"TelegramClient", "Html"})

DISCORD_OWN = frozenset(
    {
        "api/discord_adapter.py",
        "api/discord_client.py",
        "api/discord_commands.py",
        "api/discord_markdown.py",
    }
)
"""The only modules that may know Discord's wire format: the adapter (interactions in, request
bodies out), the REST client it drives, the command definitions the app registers, and the
markdown it writes message content in."""

DISCORD_CLIENT_MAY_BE_NAMED_BY = frozenset(
    {"api/app.py", "api/app_state.py", "api/discord_webhook.py"}
)
"""Besides `DISCORD_OWN`: the wiring that builds the client, and the interactions route, which
hands it to the adapter's `build_renderer` and reads nothing from it."""

DISCORD_READ_KEYS = frozenset(
    {
        "guild_id",
        "channel_id",
        "component_type",
        "custom_id",
        "resolved",
        # The credential of an interaction (valid for 15 minutes) and where the sender's identity
        # comes from: a module that reads either has stepped round the adapter.
        "token",
        "member",
        "user",
        "context",
    }
)
"""Keys of Discord's interaction JSON: flagged when a module outside `DISCORD_OWN` READS one.
`data` and the message's id are left out on purpose: a bare `data` is read in a dozen unrelated
places (a database answer, a provider's response), so listing it would only teach the scan to
be ignored."""
DISCORD_ADDRESS_EXEMPTIONS = {
    "api/discord_config.py": "builds Discord's install link, a page people open in a browser, "
    "from the application id (it calls no API and downloads nothing)",
}
"""Modules outside `DISCORD_OWN` that may spell an address on discord.com, each with the reason."""
DISCORD_WIRE_KEYS = frozenset({"custom_id", "allowed_mentions", "recipient_id", "payload_json"})
"""Keys of Discord's request JSON: flagged wherever a module outside `DISCORD_OWN` spells one."""
DISCORD_API_FRAGMENTS = ("discord.com", "discordapp.com", "discordapp.net")
"""A string that holds one of these is an address on Discord's API or CDN."""
DISCORD_CLIENT_NAMES = frozenset({"DiscordClient"})

NEUTRAL = (
    "api/channel_core.py",
    "api/channel_messages.py",
    "api/channel_envelope.py",
    "api/channel_identity.py",
    "api/channel_push.py",
    "api/progress_message.py",
    "api/first_run.py",
)
TELEGRAM_MODULES = frozenset({"telegram_adapter", "telegram_client", "telegram_webhook"})
DISCORD_MODULES = frozenset(
    {
        "discord_adapter",
        "discord_client",
        "discord_commands",
        "discord_config",
        "discord_copy",
        "discord_interactions_store",
        "discord_markdown",
        "discord_webhook",
    }
)


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _sources() -> dict[str, str]:
    return {_rel(p): p.read_text() for p in sorted(SRC.rglob("*.py"))}


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.rsplit(".", 1)[-1])
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return modules


def client_violations(source: str) -> list[str]:
    """Where a module imports the Telegram client module or names `TelegramClient`/`Html`."""
    tree = ast.parse(source)
    found = []
    if "telegram_client" in _imported_modules(tree):
        found.append("imports telegram_client")
    for node in ast.walk(tree):
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        if isinstance(name, str) and name in CLIENT_NAMES:
            found.append(f"line {getattr(node, 'lineno', 0)}: names {name}")
        if isinstance(node, ast.ImportFrom):
            found.extend(
                f"line {node.lineno}: imports {alias.name}"
                for alias in node.names
                if alias.name in CLIENT_NAMES
            )
    return found


def _key_reads(tree: ast.AST) -> list[tuple[str, int, str]]:
    """(key, line, how) for every dict read with a constant string key."""
    reads = []
    for node in ast.walk(tree):
        # x["key"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            reads.append((node.slice.value, node.lineno, f"['{node.slice.value}']"))
        # x.get("key") / x.get("key", default)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            reads.append((node.args[0].value, node.lineno, f".get('{node.args[0].value}')"))
        # "key" in x
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and any(isinstance(op, ast.In | ast.NotIn) for op in node.ops)
        ):
            reads.append((node.left.value, node.lineno, f"'{node.left.value}' in ..."))
    return reads


def update_json_violations(source: str, *, may_read_message_key: bool) -> list[str]:
    """Where a module reads one of Telegram's update keys (or `message`), or spells one of
    its request keys."""
    tree = ast.parse(source)
    found = []
    for key, line, how in _key_reads(tree):
        if key in READ_KEYS or (key == "message" and not may_read_message_key):
            found.append(f"line {line}: reads {how}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in WIRE_KEYS:
            found.append(f"line {node.lineno}: spells the request key {node.value!r}")
    return found


def test_nothing_but_the_adapter_client_and_wiring_touches_the_telegram_client() -> None:
    allowed = TELEGRAM_OWN | WIRING
    offenders = {
        rel: violations
        for rel, source in _sources().items()
        if rel not in allowed and (violations := client_violations(source))
    }
    assert offenders == {}, (
        "TelegramClient and Html belong to telegram_adapter.py / telegram_client.py (and the "
        "wiring in app.py / app_state.py); everything else speaks channel_envelope"
    )


def test_nothing_outside_the_adapter_and_the_client_reads_telegram_update_json() -> None:
    offenders = {
        rel: violations
        for rel, source in _sources().items()
        if rel not in TELEGRAM_OWN
        and (
            violations := update_json_violations(
                source, may_read_message_key=rel in OTHER_SERVICES_MESSAGE_FIELD
            )
        )
    }
    assert offenders == {}, (
        "raw Telegram update access belongs in telegram_adapter.parse_update; the business "
        "logic takes an InboundMessage"
    )


@pytest.mark.parametrize("rel", sorted(OTHER_SERVICES_MESSAGE_FIELD))
def test_each_message_key_exemption_is_still_needed(rel: str) -> None:
    """A stale exemption would quietly widen the rule."""
    assert rel not in TELEGRAM_OWN | WIRING
    strict = update_json_violations((SRC / rel).read_text(), may_read_message_key=False)
    assert any("message" in violation for violation in strict), rel


def test_the_adapter_really_holds_what_the_rules_keep_out_of_everywhere_else() -> None:
    """Guards the guard: the rules above would pass on nothing if the scan had stopped seeing."""
    adapter = (SRC / "api/telegram_adapter.py").read_text()
    client = (SRC / "api/telegram_client.py").read_text()
    found = " ".join(update_json_violations(adapter, may_read_message_key=True))
    for key in ("update_id", "callback_query", "callback_data", "inline_keyboard"):
        assert key in found, key
    assert "TelegramClient" in adapter and "Html" in adapter
    assert "class TelegramClient" in client
    wiring = {rel: client_violations((SRC / rel).read_text()) for rel in WIRING}
    assert all(wiring.values())  # app.py and app_state.py do name the client


def test_the_scan_flags_what_it_is_meant_to_flag() -> None:
    sneaky = """
from .telegram_client import TelegramClient, Html

def handle(update, client: TelegramClient):
    message = update.get("message", {})
    data = update["callback_query"]["data"]
    return Html("<b>x</b>"), update["message"], update.get("update_id")
"""
    assert client_violations(sneaky)
    flagged = update_json_violations(sneaky, may_read_message_key=False)
    assert any("callback_query" in v for v in flagged)
    assert any("update_id" in v for v in flagged)
    assert any(".get('message')" in v for v in flagged)
    assert any("['message']" in v for v in flagged)
    assert update_json_violations("k = {'callback_data': 'x'}\n", may_read_message_key=False)
    # and leaves ordinary code alone
    assert update_json_violations("ctx = {'update_id': 1}\n", may_read_message_key=False) == []
    assert client_violations("import httpx\nx = {'a': 1}['a']\n") == []
    assert (
        update_json_violations("x = {'text': 1}\ny = x.get('text')\n", may_read_message_key=False)
        == []
    )


@pytest.mark.parametrize("rel", NEUTRAL)
def test_the_neutral_modules_do_not_import_the_telegram_ones(rel: str) -> None:
    imported = _imported_modules(ast.parse((SRC / rel).read_text()))
    assert imported & TELEGRAM_MODULES == set(), rel


def test_the_envelope_is_pure() -> None:
    """No I/O and no imports from the rest of the package: it is the vocabulary everything
    else shares, so it can be imported by any adapter without dragging anything along."""
    tree = ast.parse((SRC / "api/channel_envelope.py").read_text())
    relative = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert relative == []
    stdlib_only = {"__future__", "dataclasses", "typing"}
    absolute = {
        n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert absolute <= stdlib_only


# -- Discord -----------------------------------------------------------------------------------


def discord_client_violations(source: str) -> list[str]:
    """Where a module imports `discord_client` or names `DiscordClient`."""
    tree = ast.parse(source)
    found = []
    if "discord_client" in _imported_modules(tree):
        found.append("imports discord_client")
    for node in ast.walk(tree):
        name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
        if isinstance(name, str) and name in DISCORD_CLIENT_NAMES:
            found.append(f"line {getattr(node, 'lineno', 0)}: names {name}")
    return found


def discord_json_violations(source: str, *, may_spell_address: bool = False) -> list[str]:
    """Where a module reads one of Discord's interaction keys, spells one of its request keys,
    or spells an address on Discord's API or CDN (unless `may_spell_address`)."""
    tree = ast.parse(source)
    found = []
    for key, line, how in _key_reads(tree):
        if key in DISCORD_READ_KEYS:
            found.append(f"line {line}: reads {how}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value in DISCORD_WIRE_KEYS:
            found.append(f"line {node.lineno}: spells the request key {node.value!r}")
        if not may_spell_address and any(
            fragment in node.value for fragment in DISCORD_API_FRAGMENTS
        ):
            found.append(f"line {node.lineno}: spells an address on Discord")
    return found


def test_nothing_but_the_adapter_the_client_and_the_wiring_touches_the_discord_client() -> None:
    allowed = DISCORD_OWN | DISCORD_CLIENT_MAY_BE_NAMED_BY
    offenders = {
        rel: violations
        for rel, source in _sources().items()
        if rel not in allowed and (violations := discord_client_violations(source))
    }
    assert offenders == {}, (
        "DiscordClient belongs to discord_adapter.py / discord_client.py (and the wiring and the "
        "interactions route); everything else speaks channel_envelope"
    )


def test_nothing_outside_the_discord_modules_reads_its_json_or_calls_its_api() -> None:
    offenders = {
        rel: violations
        for rel, source in _sources().items()
        if rel not in DISCORD_OWN
        and (
            violations := discord_json_violations(
                source, may_spell_address=rel in DISCORD_ADDRESS_EXEMPTIONS
            )
        )
    }
    assert offenders == {}, (
        "raw Discord interaction access and Discord API calls belong in discord_adapter.py and "
        "the modules it is made of; the business logic takes an InboundMessage"
    )


@pytest.mark.parametrize("rel", sorted(DISCORD_ADDRESS_EXEMPTIONS))
def test_each_discord_address_exemption_is_still_needed(rel: str) -> None:
    """A stale exemption would quietly widen the rule."""
    assert rel not in DISCORD_OWN
    strict = discord_json_violations((SRC / rel).read_text())
    assert any("address on Discord" in violation for violation in strict), rel
    # An exemption is for the address and nothing else: no interaction key is read there.
    lenient = discord_json_violations((SRC / rel).read_text(), may_spell_address=True)
    assert lenient == [], rel


def test_the_discord_modules_really_hold_what_the_rules_keep_out_of_everywhere_else() -> None:
    """Guards the guard, as for Telegram: the rules above would pass on nothing if the scan had
    stopped seeing."""
    adapter = discord_json_violations((SRC / "api/discord_adapter.py").read_text())
    for key in (
        "guild_id",
        "channel_id",
        "custom_id",
        "allowed_mentions",
        "token",
        "member",
        "user",
        "context",
    ):
        assert any(key in violation for violation in adapter), key
    client = discord_json_violations((SRC / "api/discord_client.py").read_text())
    assert any("address on Discord" in violation for violation in client)
    assert any("recipient_id" in violation for violation in client)
    wiring = {rel: discord_client_violations((SRC / rel).read_text()) for rel in WIRING}
    assert all(wiring.values())  # app.py and app_state.py do name the client


def test_the_discord_scan_flags_what_it_is_meant_to_flag() -> None:
    sneaky = """
from .discord_client import DiscordClient

def handle(interaction, client: DiscordClient):
    guild = interaction.get("guild_id")
    data = interaction["data"]["custom_id"]
    token = interaction["token"]
    sender = interaction["member"]["user"]["id"]
    where = interaction.get("context")
    body = {"allowed_mentions": {"parse": []}}
    return client.post("https://discord.com/api/v10/channels/1/messages", json=body), guild, data
"""
    assert discord_client_violations(sneaky)
    flagged = discord_json_violations(sneaky)
    assert any("guild_id" in v for v in flagged)
    assert any("custom_id" in v for v in flagged)
    assert any("allowed_mentions" in v for v in flagged)
    assert any("address on Discord" in v for v in flagged)
    assert any("['token']" in v for v in flagged)  # the credential
    assert any("['member']" in v for v in flagged) and any("['user']" in v for v in flagged)
    assert any(".get('context')" in v for v in flagged)
    # an exempt module may spell the address, and still not read the rest
    assert not any(
        "address on Discord" in v for v in discord_json_violations(sneaky, may_spell_address=True)
    )
    # and leaves ordinary code alone
    assert discord_json_violations("ctx = {'update_id': 1}\nx = {'a': 1}['a']\n") == []
    assert discord_client_violations("import httpx\nx = {'a': 1}['a']\n") == []


@pytest.mark.parametrize("rel", NEUTRAL)
def test_the_neutral_modules_do_not_import_the_discord_ones(rel: str) -> None:
    imported = _imported_modules(ast.parse((SRC / rel).read_text()))
    assert imported & DISCORD_MODULES == set(), rel


def test_every_discord_module_is_known_to_these_rules() -> None:
    """A new `discord_*` module has to be put in one of the two lists above on purpose."""
    on_disk = {path.stem for path in (SRC / "api").glob("discord_*.py")}
    assert on_disk == set(DISCORD_MODULES)
    assert {Path(rel).stem for rel in DISCORD_OWN} <= DISCORD_MODULES
