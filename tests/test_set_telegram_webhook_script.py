"""Tests for `scripts/set_telegram_webhook.py` (launch plan P2.13): what gets sent to Telegram,
what is refused before anything is sent, and that the token and the webhook secret never reach
the output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts import set_telegram_webhook as script
from scripts.set_telegram_webhook import Refusal, check_secret, check_token, webhook_url

TOKEN = "123456789:AAExampleBotTokenValue_abcdefgh-1234"
SECRET = "s3cret_value-for-tests"
TARGET = "https://api.example.org/telegram/webhook"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_WEBHOOK_SECRET": SECRET}


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://api.example.org", TARGET),
        ("https://api.example.org/", TARGET),
        ("  https://API.Example.org  ", TARGET),
        ("https://api.example.org/telegram/webhook", TARGET),
        ("https://api.example.org:8443", "https://api.example.org:8443/telegram/webhook"),
        ("https://api.example.org:443", "https://api.example.org:443/telegram/webhook"),
    ],
)
def test_a_public_https_base_becomes_the_webhook_url(base: str, expected: str) -> None:
    assert webhook_url(base) == expected


@pytest.mark.parametrize(
    "base",
    [
        "",
        "api.example.org",
        "http://api.example.org",
        "ftp://api.example.org",
        "https://",
        "https://localhost",
        "https://intranet",
        "https://127.0.0.1",
        "https://[::1]",
        "https://10.0.0.5",
        "https://service.internal",
        "https://box.local",
        "https://user:pw@api.example.org",
        "https://api.example.org?x=1",
        "https://api.example.org#frag",
        "https://api.example.org/other",
        "https://api.example.org/telegram/webhook/extra",
        "https://api.example.org:5000",
        "https://api.example.org:notaport",
        "https://аpi.example.org",  # noqa: RUF001  (a Cyrillic 'a' on purpose)
    ],
)
def test_a_url_telegram_could_not_deliver_to_is_refused(base: str) -> None:
    with pytest.raises(Refusal):
        webhook_url(base)


def test_the_secret_must_obey_telegrams_rule_and_is_never_echoed() -> None:
    assert check_secret("abc_DEF-123") == "abc_DEF-123"
    for bad in ("", "has space", "semi;colon", "x" * 257, "café"):
        with pytest.raises(Refusal) as caught:
            check_secret(bad)
        assert bad == "" or bad not in str(caught.value)


def test_the_token_must_look_like_a_bot_token_and_is_never_echoed() -> None:
    assert check_token(TOKEN) == TOKEN
    for bad in ("", "not-a-token", "123:short", "abc:" + "A" * 30):
        with pytest.raises(Refusal) as caught:
            check_token(bad)
        assert bad == "" or bad not in str(caught.value)


class _Telegram:
    """A pretend Bot API: remembers what it was asked and answers like Telegram."""

    def __init__(self, *, current: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.webhook: dict[str, Any] = current or {"url": "", "pending_update_count": 0}
        self.set_refusal: str | None = None
        self.unreachable = False
        self.not_json = False
        self.after_set: dict[str, Any] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError(f"cannot reach {request.url}")
        method = request.url.path.rsplit("/", 1)[-1]
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        payload = json.loads(request.content) if request.content else {}
        self.calls.append((method, payload))
        if self.not_json:
            return httpx.Response(502, text="<html>bad gateway</html>")
        if method == "getWebhookInfo":
            return httpx.Response(200, json={"ok": True, "result": self.webhook})
        if method == "setWebhook":
            if self.set_refusal is not None:
                return httpx.Response(400, json={"ok": False, "description": self.set_refusal})
            self.webhook = {
                "url": payload["url"],
                "pending_update_count": 0,
                "allowed_updates": payload["allowed_updates"],
                **self.after_set,
            }
            return httpx.Response(200, json={"ok": True, "result": True})
        raise AssertionError(f"unexpected method {method}")

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


async def _run(fake: _Telegram, *argv: str, env: dict[str, str] | None = None) -> int:
    args = script._parser().parse_args(list(argv))
    transport = httpx.MockTransport(fake.handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await script.run(args, ENV if env is None else env, client)


async def test_it_sets_the_webhook_with_the_secret_and_the_two_update_types(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram()
    code = await _run(fake, "--url", "https://api.example.org")
    assert code == 0
    assert fake.methods() == ["getWebhookInfo", "setWebhook", "getWebhookInfo"]
    assert fake.calls[1][1] == {
        "url": TARGET,
        "secret_token": SECRET,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }
    out = capsys.readouterr()
    assert TARGET in out.out


async def test_keep_pending_turns_the_drop_off() -> None:
    fake = _Telegram()
    assert await _run(fake, "--url", "https://api.example.org", "--keep-pending") == 0
    assert fake.calls[1][1]["drop_pending_updates"] is False


async def test_neither_the_token_nor_the_secret_reaches_the_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram(
        current={"url": "https://old.example.org/telegram/webhook", "pending_update_count": 2}
    )
    await _run(fake, "--url", "https://api.example.org", "--replace-existing")
    fake.set_refusal = f"bad value {SECRET} for {TOKEN}"
    await _run(fake, "--url", "https://api.example.org", "--replace-existing")
    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        assert TOKEN not in text
        assert SECRET not in text
        assert TOKEN.split(":")[1] not in text


async def test_a_webhook_url_that_contains_the_secret_is_printed_redacted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram(current={"url": f"https://api.example.org/hook/{SECRET}"})
    assert await _run(fake, "--info") == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "[redacted]" in captured.out


async def test_a_network_error_is_reported_by_class_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram()
    fake.unreachable = True
    assert await _run(fake, "--url", "https://api.example.org") == 1
    captured = capsys.readouterr()
    assert "ConnectError" in captured.err
    assert TOKEN not in captured.err + captured.out
    assert "api.telegram.org" not in captured.err + captured.out


async def test_an_answer_that_is_not_json_fails_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram()
    fake.not_json = True
    assert await _run(fake, "--url", "https://api.example.org") == 1
    assert "did not answer with JSON" in capsys.readouterr().err


async def test_a_refusal_from_telegram_exits_1_after_one_set_attempt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram()
    fake.set_refusal = "Bad Request: bad webhook: HTTPS url must be provided for webhook"
    assert await _run(fake, "--url", "https://api.example.org") == 1
    assert fake.methods() == ["getWebhookInfo", "setWebhook"]
    assert "HTTPS url must be provided" in capsys.readouterr().err


async def test_info_reads_and_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    fake = _Telegram(current={"url": TARGET, "pending_update_count": 4})
    assert await _run(fake, "--info") == 0
    assert fake.methods() == ["getWebhookInfo"]
    assert "pending updates: 4" in capsys.readouterr().out


async def test_dry_run_reads_and_changes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    fake = _Telegram()
    assert await _run(fake, "--url", "https://api.example.org", "--dry-run") == 0
    assert fake.methods() == ["getWebhookInfo"]
    assert "DRY RUN" in capsys.readouterr().out


async def test_a_bot_pointing_at_another_host_is_not_taken_over_by_accident(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram(current={"url": "https://prod.example.net/telegram/webhook"})
    with pytest.raises(Refusal, match=r"prod\.example\.net"):
        await _run(fake, "--url", "https://api.example.org")
    assert fake.methods() == ["getWebhookInfo"]
    assert await _run(fake, "--url", "https://api.example.org", "--replace-existing") == 0
    assert fake.methods()[-2:] == ["setWebhook", "getWebhookInfo"]


async def test_the_same_host_is_refreshed_without_the_flag() -> None:
    fake = _Telegram(current={"url": "https://api.example.org/telegram/webhook"})
    assert await _run(fake, "--url", "https://api.example.org") == 0
    assert "setWebhook" in fake.methods()


async def test_a_delivery_error_after_setting_exits_3(capsys: pytest.CaptureFixture[str]) -> None:
    fake = _Telegram()
    fake.after_set = {"last_error_date": 1_790_000_000, "last_error_message": "Wrong response"}
    assert await _run(fake, "--url", "https://api.example.org") == 3
    assert "delivery error" in capsys.readouterr().err


async def test_a_webhook_telegram_does_not_report_back_exits_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Telegram()
    original = fake.handler

    def drifting(request: httpx.Request) -> httpx.Response:
        response = original(request)
        if request.url.path.endswith("setWebhook"):
            fake.webhook["url"] = "https://elsewhere.example.org/telegram/webhook"
        return response

    args = script._parser().parse_args(["--url", "https://api.example.org"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(drifting)) as client:
        assert await script.run(args, ENV, client) == 1
    assert "does not report the webhook" in capsys.readouterr().err


async def test_a_missing_url_is_refused_before_any_call() -> None:
    fake = _Telegram()
    with pytest.raises(Refusal, match="--url is required"):
        await _run(fake)
    assert fake.calls == []


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"TELEGRAM_BOT_TOKEN": TOKEN},
        {"TELEGRAM_WEBHOOK_SECRET": SECRET},
        {"TELEGRAM_BOT_TOKEN": "nope", "TELEGRAM_WEBHOOK_SECRET": SECRET},
        {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_WEBHOOK_SECRET": "bad secret"},
    ],
)
async def test_missing_or_malformed_credentials_refuse_before_any_call(
    env: dict[str, str],
) -> None:
    fake = _Telegram()
    with pytest.raises(Refusal):
        await _run(fake, "--url", "https://api.example.org", env=env)
    assert fake.calls == []


def test_info_and_dry_run_cannot_be_combined() -> None:
    with pytest.raises(SystemExit):
        script._parser().parse_args(["--info", "--dry-run"])


def test_the_script_never_reads_a_dotenv_file() -> None:
    source = Path(script.__file__).read_text(encoding="utf-8")
    assert "dotenv" not in source
