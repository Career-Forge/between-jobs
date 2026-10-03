"""The Telegram message templates (P0.9): valid HTML, no leftover Markdown, within Telegram's
size limit, and no way around the client's escaping and parse_mode."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from between_jobs.api import telegram_webhook
from between_jobs.api.telegram_client import Html, render

_TEXT_LIMIT = 4096  # Telegram's cap on a message's text, after entities are parsed
_SRC = Path(telegram_webhook.__file__).parent


def _templates() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(telegram_webhook).items()
        if name.startswith("_") and name.endswith(("_TEXT", "_CAPTION")) and isinstance(value, str)
    }


def test_there_are_templates_to_check() -> None:
    assert len(_templates()) > 20  # a rename of the suffix must not turn this into a no-op


def test_the_templates_with_markup_are_html_and_the_rest_are_plain() -> None:
    marked_up = {name for name, value in _templates().items() if isinstance(value, Html)}
    assert marked_up == {
        "_SETUP_HELP_TEXT",
        "_TRACK_JOB_HELP_TEXT",
        "_JOB_TRACKED_TEXT",
        "_STAGE_CHANGED_TEXT",
    }


@pytest.mark.parametrize("name", sorted(_templates()))
def test_no_template_still_contains_markdown_syntax(name: str) -> None:
    """Before P0.9 onboarding arrived with literal asterisks and backticks: nothing set
    parse_mode, so Telegram never rendered them."""
    value = _templates()[name]
    assert "`" not in value, name
    assert not re.search(r"\*[^*\s][^*]*\*", value), name


@pytest.mark.parametrize("name", ["_SETUP_HELP_TEXT", "_TRACK_JOB_HELP_TEXT"])
def test_the_long_templates_fit_in_one_message(name: str) -> None:
    visible = html.unescape(re.sub(r"</?[a-z]+>", "", _templates()[name]))
    assert len(visible) <= _TEXT_LIMIT


def test_the_copy_blocks_are_tap_to_copy_blocks() -> None:
    assert telegram_webhook._SETUP_HELP_TEXT.count("<pre>") == 1
    assert "<code>work_authorization</code>" in telegram_webhook._SETUP_HELP_TEXT
    assert telegram_webhook._TRACK_JOB_HELP_TEXT.count("<pre>") == 1


def test_the_resume_template_a_user_copies_is_valid_json() -> None:
    parsed = json.loads(telegram_webhook._RESUME_TEMPLATE_JSON)
    assert set(parsed) >= {"personal", "experience", "education", "skills"}


def test_values_rendered_into_templates_cannot_inject_markup() -> None:
    out = render(
        telegram_webhook._JOB_TRACKED_TEXT, title="<b>Boss</b>", company="Smith & <script>"
    )
    assert out == "✅ Tracking <b>&lt;b&gt;Boss&lt;/b&gt;</b> at <b>Smith &amp; &lt;script&gt;</b>."

    stage = render(telegram_webhook._STAGE_CHANGED_TEXT, status="<i>x</i>")
    assert stage == "✅ Marked as <b>&lt;i&gt;x&lt;/i&gt;</b>."


def test_nothing_but_the_client_talks_to_sendmessage() -> None:
    """A second route to the Bot API would bypass the escaping and parse_mode in
    `TelegramClient.send_message`."""
    offenders = [
        path.name
        for path in _SRC.glob("*.py")
        if path.name != "telegram_client.py" and "sendMessage" in path.read_text()
    ]
    assert offenders == []
