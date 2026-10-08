"""Static guards on what the Discord modules may put in a log line or an exception message.

An interaction token is a credential for 15 minutes and the bot token is a credential for good, and
neither may reach a log or an error. The behaviour is tested where the tokens actually flow
(tests/test_discord_client.py, test_discord_adapter.py, test_discord_conversations.py); these read
the source, so a new log call or `raise` that names one fails here, before anyone runs it.

Also: nothing logs the body of a request or an interaction (what a person typed), and the only
places a token is allowed to be put in a URL are the two the client builds on purpose."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_API = Path(__file__).parent.parent / "src" / "between_jobs" / "api"
_MODULES = sorted([*_API.glob("discord_*.py"), _API / "channel_accounts.py"])
_SECRET_NAME = re.compile(r"token|secret|password|signature", re.IGNORECASE)
_PAYLOAD_NAMES = {"body", "payload", "raw", "interaction", "options", "values", "data", "text"}
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _identifiers(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _is_log_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LOG_METHODS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"logger", "logging"}
    )


def test_the_scan_sees_the_modules_it_should() -> None:
    assert {path.name for path in _MODULES} >= {
        "discord_adapter.py",
        "discord_client.py",
        "discord_webhook.py",
        "discord_config.py",
        "channel_accounts.py",
    }
    logged = sum(
        1
        for path in _MODULES
        for node in ast.walk(ast.parse(path.read_text()))
        if _is_log_call(node)
    )
    assert logged >= 8  # the scan finds real log calls, so "none found" would mean something


@pytest.mark.parametrize("path", _MODULES, ids=lambda path: path.name)
def test_no_log_call_names_a_token_a_signature_or_a_body(path: Path) -> None:
    offenders = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not _is_log_call(node):
            continue
        assert isinstance(node, ast.Call)
        for argument in [*node.args, *(kw.value for kw in node.keywords)]:
            # A string constant is text we wrote; only what is computed is looked at.
            if isinstance(argument, ast.Constant):
                continue
            used = _identifiers(argument)
            # `has_bot_token` is a yes or no about whether there is one, not the token.
            bad = {n for n in used if _SECRET_NAME.search(n) and not n.startswith("has_")}
            bad |= used & _PAYLOAD_NAMES
            if bad:
                offenders.append(f"line {node.lineno}: {sorted(bad)}")
    assert offenders == []


@pytest.mark.parametrize("path", _MODULES, ids=lambda path: path.name)
def test_no_exception_message_is_built_from_a_token_a_signature_or_a_body(path: Path) -> None:
    offenders = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
            continue
        for argument in node.exc.args:
            if isinstance(argument, ast.Constant):
                continue
            bad = {n for n in _identifiers(argument) if _SECRET_NAME.search(n)}
            if bad:
                offenders.append(f"line {node.lineno}: {sorted(bad)}")
    assert offenders == []


def test_a_token_is_put_in_a_url_in_exactly_one_place() -> None:
    """Interaction tokens belong in the webhook path built by `_webhook_path`; the bot token is
    only ever sent in a header."""
    client = (_API / "discord_client.py").read_text()
    assert client.count("/webhooks/") == 1
    assert client.count('f"Bot {') == 1 and client.count('"Authorization"') == 1
    for path in _MODULES:
        if path.name == "discord_client.py":
            continue
        source = path.read_text()
        assert "/webhooks/" not in source.replace("webhooks)", ""), path.name
        assert "Authorization" not in source or path.name == "discord_config.py", path.name


def test_the_client_never_calls_raise_for_status_whose_message_is_the_url() -> None:
    tree = ast.parse((_API / "discord_client.py").read_text())
    called = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "raise_for_status"
    ]
    assert called == []
