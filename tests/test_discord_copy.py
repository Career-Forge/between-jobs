"""The bot's wording on Discord (api/discord_copy.py).

The neutral copy in `channel_messages` is written in the words of the first channel (it tells a
person to `Send "list"`, speaks of "your Telegram account"). On Discord a person cannot send a
sentence, only run a slash command, so a message that still said so would point them at something
that silently does nothing. These tests render every message the bot can send and fail if one still
does, and fail if a row of the table has stopped matching anything -- so rewording a sentence in
`channel_messages` cannot leave Discord with the old advice, nor the table with a dead row.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import test_channel_core as channel_core_tests
from channel_fakes import ComposedSupabase, FakeRenderer, inbound

from between_jobs.api import channel_messages as messages
from between_jobs.api.channel_core import handle_inbound
from between_jobs.api.channel_envelope import RichText, Segment, code, pre, rich
from between_jobs.api.deferred_reply import DeferredReplies
from between_jobs.api.discord_copy import localize, phrases_replaced
from between_jobs.api.first_run import ApplicationFacts, FirstRunFacts, derive_first_run

_FORBIDDEN = [
    re.compile(r"Telegram"),
    re.compile(r'\b[Ss]end "'),
    re.compile(r"\bsend me\b", re.IGNORECASE),
    re.compile(r'"(?:set up my resume|check my resume|list|track a job|apply to #N)"'),
    re.compile(r"as pasted text"),
    re.compile(r"\bresend\b"),
    re.compile(r"send (?:it|the same) "),
]
"""What is true of the neutral copy and not of Discord: telling someone to send a sentence, to
send "me" something, to paste text in a format, or naming Telegram."""


def _scanned_text(text: RichText) -> str:
    """The words a person reads as prose: code and pre-formatted blocks (the resume template) are
    left out, since they are literal."""
    return "".join(s.text for s in text.segments if s.style not in ("code", "pre"))


def _facts() -> list[FirstRunFacts]:
    combos = [
        FirstRunFacts(None, None, None, None),
        FirstRunFacts(False, False, 0, ApplicationFacts(0, 0, 0)),
        FirstRunFacts(True, False, 0, ApplicationFacts(0, 0, 0)),
        FirstRunFacts(True, True, 1, ApplicationFacts(1, 1, 1)),
        FirstRunFacts(True, True, 0, ApplicationFacts(2, 0, 2)),
        FirstRunFacts(True, True, 1, ApplicationFacts(2, 1, 2)),
        # Every step in turn is the next one to do: each has its own hint.
        FirstRunFacts(True, True, 0, ApplicationFacts(0, 0, 0)),
        FirstRunFacts(True, True, 1, ApplicationFacts(0, 0, 0)),
        FirstRunFacts(True, True, 1, ApplicationFacts(1, 1, 0)),
    ]
    return combos


def _constant_messages() -> list[RichText]:
    found: list[RichText] = []
    for name, value in vars(messages).items():
        if name.startswith("_") or not name.isupper():
            continue
        if isinstance(value, str):
            found.append(rich(value))
        elif isinstance(value, RichText):
            found.append(value)
        elif isinstance(value, dict):
            found.extend(rich(v) for v in value.values() if isinstance(v, str))
    return found


def _built_messages() -> list[RichText]:
    version = {
        "canonical_json": {"personal": {"name": "Jane", "headline": "Eng", "emails": []}},
        "activated_at": "2026-01-01",
    }
    built: list[RichText] = [
        rich(messages.document_too_large_text(1048576)),
        rich(messages.not_a_real_stage_text("nope")),
        rich(messages.link_first_text(None)),
        rich(messages.link_first_text("https://between-jobs.tech")),
        rich(messages.declined_warnings_text(["a", "b"])),
        rich(messages.format_merge_summary({})),
        rich(messages.format_merge_summary({"applications": 2, "profile_versions": 1})),
        rich(messages.format_active_profile(version)),
        rich(
            messages.build_preview_message(
                version, {"experience": 1, "projects": 0, "education": 0, "skills": 2}, ("careful",)
            )
        ),
        messages.job_tracked_text("T", "C"),
        messages.stage_changed_text("applied"),
    ]
    for url in (None, "https://between-jobs.tech"):
        for linked in (True, False):
            built.append(messages.privacy_text(url, linked=linked))
        for facts in _facts():
            built.append(messages.learn_text(derive_first_run(facts, None), facts, url))
    return built


async def _flow(text: str, **config: Any) -> list[RichText]:
    renderer = FakeRenderer()
    await handle_inbound(
        ComposedSupabase(**config),  # type: ignore[arg-type]
        httpx.AsyncClient(),
        renderer,
        inbound(text, channel="discord"),
        deferred=DeferredReplies(),
    )
    return [intent.text for _chat, intent in renderer.sent]


async def _flow_messages() -> list[RichText]:
    """What the core writes itself (not in `channel_messages`), reached through the real logic."""
    found: list[RichText] = []
    for text in ("list", "apply to 1", "apply to 9", "track a job", "check my resume", "hi"):
        found += await _flow(text)
    found += await _flow("list", applications=_empty_applications())
    found += await _flow("apply to 1", working_sets=_empty_working_sets())
    return found


def _empty_applications() -> Any:
    from test_telegram_webhook import _FakeSimpleTable

    return _FakeSimpleTable(select_rows=[])


def _empty_working_sets() -> Any:
    from test_telegram_webhook import _FakeSimpleTable

    return _FakeSimpleTable(select_rows=[])


async def _corpus() -> list[RichText]:
    return [*_constant_messages(), *_built_messages(), *(await _flow_messages())]


async def test_no_message_tells_a_discord_user_to_send_a_sentence_or_names_telegram() -> None:
    corpus = await _corpus()
    assert len(corpus) > 60  # the scan sees the whole copy, not a sample
    offenders: list[str] = []
    for message in corpus:
        text = _scanned_text(localize(message))
        for pattern in _FORBIDDEN:
            if pattern.search(text):
                offenders.append(f"{pattern.pattern!r} in {text[:120]!r}")
    assert offenders == []


async def test_the_scan_would_notice_the_neutral_copy_it_guards_against() -> None:
    """Guards the guard: the neutral copy does contain every one of the patterns, so the test
    above passes because of the table and not because the patterns match nothing."""
    corpus = await _corpus()
    raw = "\n".join(_scanned_text(message) for message in corpus)
    for pattern in _FORBIDDEN:
        assert pattern.search(raw), pattern.pattern


async def test_every_row_of_the_table_still_matches_something() -> None:
    corpus = await _corpus()
    raw = "\n".join(s.text for message in corpus for s in message.segments)
    for old, _new in phrases_replaced():
        assert old in raw, f"a row no longer matches any message: {old!r}"


def test_the_replacements_say_the_slash_command_and_are_not_empty() -> None:
    for old, new in phrases_replaced():
        assert new and new != old
        assert "Send" not in new and 'send "' not in new


def test_code_and_pre_formatted_blocks_are_left_exactly_as_they_are() -> None:
    text = rich("Send ", code('"list"'), " to Telegram ", pre('Telegram: Send "list"'))
    localized = localize(text)
    assert [s for s in localized.segments if s.style in ("code", "pre")] == [
        Segment('"list"', "code"),
        Segment('Telegram: Send "list"', "pre"),
    ]


def test_the_shared_copys_telegram_phrases_become_discord_and_nothing_else_is_touched() -> None:
    assert localize(rich("Your Telegram account is linked.")) == rich(
        "Your Discord account is linked."
    )
    assert localize(rich("Nothing to change here.")) == rich("Nothing to change here.")


def test_a_company_or_job_called_telegram_is_shown_as_it_is() -> None:
    """Text that came from a job posting passes through `localize` like the shared copy does, so
    the word is only ever rewritten as part of a phrase the shared copy wrote."""
    tracked = messages.job_tracked_text("Telegram Bot Developer", "Telegram")
    assert localize(tracked) == tracked
    listing = rich("1. Telegram Bot Developer @ Telegram FZ-LLC -- saved")
    assert localize(listing) == listing
    mixed = rich("This Telegram account is linked. ", "Telegram Messenger is hiring.")
    assert localize(mixed).plain_text() == (
        "This Discord account is linked. Telegram Messenger is hiring."
    )


def test_the_track_a_job_help_is_replaced_whole() -> None:
    localized = localize(messages.TRACK_JOB_HELP_TEXT)
    assert "/job" in localized.plain_text()
    assert "Title:" not in localized.plain_text()  # no paste format to follow
    assert all(s.style != "pre" for s in localized.segments)


def test_the_resume_template_still_reaches_discord_whole_with_the_import_step() -> None:
    text = localize(messages.SETUP_HELP_TEXT).plain_text()
    assert messages.RESUME_TEMPLATE_JSON in text
    assert "/import" in text and "as pasted text" not in text


def test_the_privacy_summary_says_what_discord_keeps() -> None:
    text = localize(messages.privacy_text("https://between-jobs.tech", linked=True)).plain_text()
    assert "From Discord: only your numeric Discord user id" in text
    assert "the id Discord gives each command, only so that none is run twice" in text
    # Like Telegram's, the summary names the number and the trigger and promises no schedule:
    # with no later command nothing is removed, so "for 7 days" would be a claim not kept.
    assert "older than 7 days, as a side effect of the app receiving later ones" in text
    assert "for 7 days" not in text and "up to 7 days" not in text
    assert "Discord carries this chat" in text
    assert "Telegram" not in text


def test_a_message_with_nothing_to_change_comes_back_equal() -> None:
    for message in (messages.job_tracked_text("T", "C"), rich(messages.SAVED_TEXT)):
        assert localize(message) == message


@pytest.mark.parametrize(
    "name", ["LINK_SOURCE_LINKED_TEXT", "LINK_TARGET_LINKED_TEXT", "UNLINK_TEXT"]
)
def test_the_link_texts_name_the_right_service(name: str) -> None:
    text = localize(rich(getattr(messages, name))).plain_text()
    assert "Discord" in text and "Telegram" not in text


# -- the privacy summary on Discord is held to the web policy, as Telegram's is ----------------

_LEGAL = Path(__file__).parent.parent / "web" / "src" / "content" / "legal.ts"
_DISCORD_PRIVACY_CLAIMS = [
    # Everything the neutral summary claims that is not about Telegram specifically...
    *[
        pair
        for pair in channel_core_tests._PRIVACY_CLAIMS
        if "Telegram" not in pair[0] and pair[0] != "no name, no username"
    ],
    # ...and what it says about Discord, each of which the policy's Discord text must also say.
    ("numeric Discord user id", "numeric Discord user id"),
    ("no name, no username", "no name or username"),
    ("failed link-code attempts", "failed link-code attempts from it"),
    ("the id Discord gives each command", "the id Discord gives each command or button tap"),
    ("older than 7 days", "older than 7 days"),
    (
        "as a side effect of the app receiving later ones",
        "as a side effect of the app receiving later ones",
    ),
    ("only so that none is run twice", "only so that the same one is not processed twice"),
    ("Discord carries this chat", "Discord processes these messages"),
]


@pytest.mark.parametrize("linked", [True, False], ids=["linked", "bot-only"])
def test_every_claim_in_the_discord_privacy_summary_is_one_the_web_policy_makes(
    linked: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WEB_APP_URL", raising=False)
    legal = _LEGAL.read_text().lower()
    summary = localize(messages.privacy_text(None, linked=linked)).plain_text().lower()
    claims = [
        *_DISCORD_PRIVACY_CLAIMS,
        *(
            channel_core_tests._PRIVACY_CLAIMS_LINKED
            if linked
            else channel_core_tests._PRIVACY_CLAIMS_BOT_ONLY
        ),
    ]
    for in_the_bot, in_the_policy in claims:
        # A claim about Telegram's commands is spelled the Discord way in the Discord summary.
        if in_the_bot.startswith("/unlink") or "/link" in in_the_bot:
            continue
        assert in_the_bot.lower() in summary, in_the_bot
        assert in_the_policy.lower() in legal, in_the_policy
