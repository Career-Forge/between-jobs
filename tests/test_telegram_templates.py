"""The bot's message templates (P0.9): valid HTML once Telegram renders them, no leftover
Markdown, within Telegram's size limit, and no way around the client's escaping and parse_mode.

The templates themselves are channel-neutral (`channel_messages`): plain `str`, or `RichText`
where a message has formatting. What a user receives is the Telegram rendering of them
(`telegram_adapter.render_html`), so the checks that are about Telegram's HTML look at that.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from between_jobs.api import channel_messages
from between_jobs.api.channel_envelope import RichText, rich
from between_jobs.api.first_run import (
    ApplicationFacts,
    FirstRunFacts,
    FirstRunView,
    derive_first_run,
)
from between_jobs.api.telegram_adapter import render_html
from between_jobs.api.telegram_client import Html

_TEXT_LIMIT = 4096  # Telegram's cap on a message's text, after entities are parsed
_SRC = Path(channel_messages.__file__).parent


def _templates() -> dict[str, str | RichText]:
    return {
        name: value
        for name, value in vars(channel_messages).items()
        if name.endswith(("_TEXT", "_CAPTION")) and isinstance(value, str | RichText)
    }


def _as_sent(value: str | RichText) -> str:
    """The template as the Telegram client puts it in a request: rendered if it has
    formatting, else the plain text (which the client escapes)."""
    return str(render_html(value)) if isinstance(value, RichText) else value


def test_there_are_templates_to_check() -> None:
    assert len(_templates()) > 20  # a rename of the suffix must not turn this into a no-op


def test_the_templates_with_markup_are_rich_text_and_the_rest_are_plain() -> None:
    marked_up = {name for name, value in _templates().items() if isinstance(value, RichText)}
    assert marked_up == {"SETUP_HELP_TEXT", "TRACK_JOB_HELP_TEXT"}
    # and the two templates that carry a value are builders, whose result is formatted
    assert channel_messages.job_tracked_text("a", "b").is_styled()
    assert channel_messages.stage_changed_text("applied").is_styled()
    assert channel_messages.privacy_text(None, linked=True).is_styled()
    assert channel_messages.privacy_text(None, linked=False).is_styled()
    # nothing plain smuggles markup in: a plain template is shown as written
    for name, value in _templates().items():
        if isinstance(value, str):
            assert not re.search(r"</?(?:b|i|code|pre)>", value), name


@pytest.mark.parametrize("name", sorted(_templates()))
def test_no_template_still_contains_markdown_syntax(name: str) -> None:
    """Before P0.9 onboarding arrived with literal asterisks and backticks: nothing set
    parse_mode, so Telegram never rendered them."""
    value = _as_sent(_templates()[name])
    assert "`" not in value, name
    assert not re.search(r"\*[^*\s][^*]*\*", value), name


@pytest.mark.parametrize("name", ["SETUP_HELP_TEXT", "TRACK_JOB_HELP_TEXT"])
def test_the_long_templates_fit_in_one_message(name: str) -> None:
    visible = html.unescape(re.sub(r"</?[a-z]+>", "", _as_sent(_templates()[name])))
    assert len(visible) <= _TEXT_LIMIT


def _learn_views() -> list[tuple[FirstRunView, FirstRunFacts]]:
    """Every account shape the shared table lists, as the chat would show it."""
    shared = json.loads((_SRC.parents[2] / "tests" / "shared" / "first_run_steps.json").read_text())
    views = []
    for case in shared["cases"]:
        applications = case["facts"]["applications"]
        facts = FirstRunFacts(
            profile=case["facts"]["profile"],
            model_key=case["facts"]["model_key"],
            saved_searches=case["facts"]["saved_searches"],
            applications=None
            if applications is None
            else ApplicationFacts(
                applications["count"], applications["from_discover"], applications["with_resume"]
            ),
        )
        views.append((derive_first_run(facts, None), facts))
    return views


_LONG_ADDRESS = "https://" + "a" * 60 + ".example.com/" + "p" * 100


@pytest.mark.parametrize("web_url", [None, "https://between-jobs.tech", _LONG_ADDRESS])
def test_privacy_and_learn_are_valid_telegram_html_within_the_size_limit(
    web_url: str | None,
) -> None:
    texts = [
        channel_messages.privacy_text(web_url, linked=True),
        channel_messages.privacy_text(web_url, linked=False),
    ]
    texts += [channel_messages.learn_text(view, facts, web_url) for view, facts in _learn_views()]
    texts.append(rich(channel_messages.link_first_text(web_url)))
    for text in texts:
        rendered = render_html(text)
        assert isinstance(rendered, Html)  # checked markup: nothing but our own tags
        visible = html.unescape(re.sub(r"</?[a-z]+>", "", str(rendered)))
        assert len(visible) <= _TEXT_LIMIT
        assert "`" not in visible and not re.search(r"\*[^*\s][^*]*\*", visible)


def test_privacy_and_learn_carry_the_website_address_as_text_not_markup() -> None:
    address = "https://example.com/a&b<c>"
    view, facts = _learn_views()[0]

    for text in (
        channel_messages.privacy_text(address, linked=True),
        channel_messages.privacy_text(address, linked=False),
        channel_messages.learn_text(view, facts, address),
    ):
        rendered = str(render_html(text))
        assert "a&amp;b&lt;c&gt;" in rendered
        assert "<c>" not in rendered


def test_the_copy_blocks_are_tap_to_copy_blocks() -> None:
    setup = render_html(channel_messages.SETUP_HELP_TEXT)
    track = render_html(channel_messages.TRACK_JOB_HELP_TEXT)
    assert isinstance(setup, Html)  # the renderer's output is checked markup, not a bare str
    assert setup.count("<pre>") == 1
    assert "<code>work_authorization</code>" in setup
    assert track.count("<pre>") == 1


def test_the_resume_template_a_user_copies_is_valid_json() -> None:
    parsed = json.loads(channel_messages.RESUME_TEMPLATE_JSON)
    assert set(parsed) >= {"personal", "experience", "education", "skills"}


def test_values_rendered_into_templates_cannot_inject_markup() -> None:
    out = render_html(channel_messages.job_tracked_text("<b>Boss</b>", "Smith & <script>"))
    assert out == "✅ Tracking <b>&lt;b&gt;Boss&lt;/b&gt;</b> at <b>Smith &amp; &lt;script&gt;</b>."

    stage = render_html(channel_messages.stage_changed_text("<i>x</i>"))
    assert stage == "✅ Marked as <b>&lt;i&gt;x&lt;/i&gt;</b>."


def test_nothing_but_the_client_talks_to_sendmessage() -> None:
    """A second route to the Bot API would bypass the escaping and parse_mode in
    `TelegramClient.send_message` (and `edit_message_text`, which shares them)."""
    for method in ("sendMessage", "editMessageText"):
        offenders = [
            path.name
            for path in _SRC.glob("*.py")
            if path.name != "telegram_client.py" and method in path.read_text()
        ]
        assert offenders == [], method
