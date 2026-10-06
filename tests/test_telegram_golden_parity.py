"""Golden parity for the Telegram bot: the extraction of its logic into `channel_core` and
`telegram_adapter` must not change what a person sees.

`telegram_golden.SCENARIOS` is a corpus of Telegram updates -- every command, message kind
and button the bot handles, plus the failure and delivery edge cases -- each with the world
around it. The expected file records what the bot sent for each one BEFORE the extraction:
the exact sequence of Telegram API calls (method, chat, the rendered HTML text, the
keyboard, a document's name and bytes), and the HTTP answer. It was captured by running the
pre-extraction webhook and client over this same corpus; see
`tests/golden/telegram_bot/README.md`. Text is compared by digest, so the fixture holds no
copy of the bot's messages, and a failure prints the new text in full.

The one deliberate difference from that capture is not visible here, because it is not about
what is sent: a resume generation now runs after the webhook has answered (tests in
test_telegram_deferred_prepare.py), so the fixture records the calls once it has finished.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from telegram_golden import SCENARIOS, Stack, reduce, run_scenario

from between_jobs.api.app import app
from between_jobs.api.telegram_client import TelegramClient

EXPECTED_PATH = (
    Path(__file__).parent / "golden" / "telegram_bot" / "expected" / "telegram_calls.json"
)


def _main_app() -> FastAPI:
    return app


STACK = Stack(
    make_app=_main_app,
    client_class=TelegramClient,
    patch_module="between_jobs.api.channel_core",
)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")


def _expected() -> dict[str, dict[str, object]]:
    loaded: dict[str, dict[str, object]] = json.loads(EXPECTED_PATH.read_text())
    return loaded


def test_regenerate_the_expected_file_when_the_copy_was_changed_on_purpose() -> None:
    """Skipped unless asked. After an intentional change to what the bot says, run
    `REGENERATE_TELEGRAM_GOLDEN=1 pytest tests/test_telegram_golden_parity.py -k regenerate`
    and review the diff of the expected file: every changed digest is a message that now
    reads differently. It rewrites the file from the CURRENT implementation, so it is only
    as honest as that review."""
    if not os.environ.get("REGENERATE_TELEGRAM_GOLDEN"):
        pytest.skip("regeneration was not asked for (REGENERATE_TELEGRAM_GOLDEN=1)")
    regenerated = {name: reduce(run_scenario(name, STACK)) for name in sorted(SCENARIOS)}
    EXPECTED_PATH.write_text(json.dumps(regenerated, indent=1, sort_keys=True) + "\n")


def test_the_corpus_is_broad_and_the_expected_file_holds_exactly_it() -> None:
    expected = _expected()
    assert set(expected) == set(SCENARIOS), "regenerate the expected file, or remove the stale one"
    assert len(SCENARIOS) >= 40

    calls = [call for entry in expected.values() for call in entry["telegram"]]  # type: ignore[attr-defined]
    methods = {call["method"] for call in calls}
    assert {
        "sendMessage",
        "sendDocument",
        "answerCallbackQuery",
        "getFile",
        "downloadFile",
    } <= methods
    assert any(call.get("reply_markup") for call in calls), "no keyboard is covered"
    statuses = {entry["http"][0] for entry in expected.values()}  # type: ignore[index]
    assert {200, 401, 500, 503} <= statuses
    # a message and a button tap of every kind the bot handles
    kinds = {name.split("_")[0] for name in SCENARIOS}
    assert {
        "link",
        "unlink",
        "json",
        "track",
        "list",
        "apply",
        "generate",
        "confirm",
        "cancel",
    } <= kinds


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_bot_answers_exactly_as_it_did_before_the_extraction(name: str) -> None:
    result = run_scenario(name, STACK)
    got = reduce(result)
    want = _expected()[name]

    sent = "\n".join(f"  {i}: {call}" for i, call in enumerate(_readable(result["calls"])))
    assert got == want, f"{name}: what the bot sent now (full text):\n{sent}"


def _readable(calls: list[dict[str, object]]) -> list[str]:
    from telegram_golden import readable

    return [readable(call) for call in calls]
