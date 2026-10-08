"""The deferred reply: a resume generation no longer holds the webhook's request open.

Telegram stops waiting on a webhook long before a generation finishes and delivers the same
update again, so the webhook now does the quick part inline -- the per-user limit and the
"this can take a minute" message -- answers 200, and runs the rest as a supervised background
task (`deferred_reply`). What is pinned here, through the real endpoint:

- the answer comes back promptly while a slow engine is still running, and the resume is
  delivered afterwards;
- a redelivery of the same update does not start a second generation;
- the cap on simultaneous generations answers "busy" instead of queueing, without spending
  the user's limit, and a refused request gives its place back;
- one person gets one generation at a time: their further taps are told "still generating",
  spend nothing, and leave the other places to other people;
- a failure inside the task is logged with the request and update ids and told to the user;
  a failure after the resume was delivered is logged and not told as a failed generation;
- a generation that ends in a known error (the engine down, a compile failure) is told in
  chat and logged the way the web would log it;
- a task still running at shutdown is given the grace period, then cancelled, and the user
  is not messaged about it;
- the per-user "prepare" limit still runs before any work starts, and still answers the user.

The other half of the coverage is in test_telegram_prepare_callback.py (what a finished
generation sends) and test_deferred_reply.py (the registry on its own)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import test_telegram_prepare_callback as prepare_fakes
import test_telegram_webhook as webhook_fakes
from channel_fakes import APPLICATION_ID as _APPLICATION_ID
from channel_fakes import ComposedSupabase
from fake_sentry import FakeSentry
from fastapi.testclient import TestClient

from between_jobs.api import channel_messages, deferred_reply, error_reporting, rate_limits
from between_jobs.api.app import app
from between_jobs.api.app_state import get_http_client, get_supabase, get_telegram_client
from between_jobs.api.deferred_reply import DeferredReplies
from between_jobs.api.logging_setup import JsonFormatter, request_id_var

_SECRET = prepare_fakes._WEBHOOK_SECRET


class _Engine(prepare_fakes._FakeHttpClient):
    """forge-engines that can be held at a gate or made to fail, and that notes which request
    id the work was running under."""

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        fail: bool = False,
        unreachable: bool = False,
        seconds: float = 0.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.gate = gate
        self.fail = fail
        self.unreachable = unreachable
        self.seconds = seconds
        self.working = False
        self.request_ids: list[str | None] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url.endswith("/apply"):
            self.request_ids.append(request_id_var.get())
            self.working = True
            if self.gate is not None:
                # bounded, so a handler that wrongly waits for the engine fails this test
                # (a 500, and an answer far slower than 1 s) instead of hanging the suite
                await asyncio.wait_for(self.gate.wait(), timeout=10)
            if self.seconds:
                await asyncio.sleep(self.seconds)
            if self.unreachable:
                raise httpx.ConnectError("connection refused")
            if self.fail:
                raise RuntimeError("the engine fell over")
        return await super().post(url, **kwargs)

    @property
    def generations_started(self) -> int:
        return len(self.request_ids)


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", _SECRET)


@pytest.fixture(autouse=True)
def _own_registry(monkeypatch: pytest.MonkeyPatch) -> DeferredReplies:
    """A registry of this test's own, so what one test leaves running cannot reach the next."""
    registry = DeferredReplies()
    monkeypatch.setattr(deferred_reply, "registry", registry)
    return registry


@contextmanager
def _serving(
    supabase: ComposedSupabase, telegram: prepare_fakes._FakeTelegramClient, http: _Engine
) -> Iterator[TestClient]:
    """The real app, lifespan included: leaving the block runs its shutdown, which is what
    drains (or cancels) the background work."""
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    app.dependency_overrides[get_http_client] = lambda: http
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _post(client: TestClient, update: dict[str, Any]) -> Any:
    return client.post(
        "/telegram/webhook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": _SECRET}
    )


def _tap_generate(update_id: int = 2, *, user: int | None = None) -> dict[str, Any]:
    """A tap on "Generate resume": by the one test user, or by `user` (who is then also the
    chat the reply goes to, as in a private chat)."""
    update = prepare_fakes._callback_update(f"app:prepare:{_APPLICATION_ID}")
    update["update_id"] = update_id
    if user is not None:
        update["callback_query"]["from"]["id"] = user
        update["callback_query"]["message"]["chat"]["id"] = user
    return update


def _open(client: TestClient, gate: asyncio.Event) -> None:
    """Lets the held engine go, from the app's own event loop (the gate belongs to it)."""
    assert client.portal is not None
    client.portal.call(gate.set)


def _wait_for(condition: Any, *, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _texts(telegram: prepare_fakes._FakeTelegramClient) -> list[str]:
    return [text for _chat, text, _markup in telegram.sent]


def _shown(telegram: prepare_fakes._FakeTelegramClient) -> list[str]:
    """The messages as the chat reads them now: what was sent, with the edits applied."""
    return [text for _chat, text, _markup in telegram.shown]


def _texts_to(telegram: prepare_fakes._FakeTelegramClient, chat_id: int) -> list[str]:
    return [text for chat, text, _markup in telegram.sent if chat == chat_id]


# -- answering promptly --------------------------------------------------------------------


def test_the_webhook_answers_promptly_while_a_slow_engine_is_still_running() -> None:
    gate = asyncio.Event()
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )

    with _serving(supabase, telegram, engine) as client:
        started = time.monotonic()
        response = _post(client, _tap_generate())
        elapsed = time.monotonic() - started

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert elapsed < 1.0
        # What the person sees today went out before the answer ...
        assert telegram.answered_callback_ids == ["cbq-1"]
        assert _texts(telegram) == [channel_messages.GENERATING_TEXT]
        # ... and the generation is under way but not done.
        assert _wait_for(lambda: engine.generations_started == 1)
        assert telegram.documents_sent == []
        assert deferred_reply.registry.running == 1

        _open(client, gate)
        # Delivered while the app is still up: this is the background task finishing, not a
        # drain at shutdown.
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)

    chat_id, filename, content, caption = telegram.documents_sent[0]
    assert (chat_id, filename, content) == (
        prepare_fakes._CHAT_ID,
        "resume.pdf",
        prepare_fakes._PDF_BYTES,
    )
    assert caption is not None and "ATS score 72/100" in caption
    # One progress message, edited in place: "compiling" while the PDF is made, then the final
    # state with the stage button under it. Nothing is sent after the document.
    assert _texts(telegram) == [channel_messages.GENERATING_TEXT]
    assert [text for _c, _id, text, _m in telegram.edited] == [
        channel_messages.PREPARE_COMPILING_TEXT,
        channel_messages.PREPARE_DONE_TEXT,
    ]
    assert {message_id for _c, message_id, _t, _m in telegram.edited} == {101}  # the one message
    final_markup = telegram.edited[-1][3]
    assert final_markup is not None
    assert final_markup["inline_keyboard"][0][0]["callback_data"] == (
        f"app:stage:{_APPLICATION_ID}:applied"
    )
    assert _shown(telegram) == [channel_messages.PREPARE_DONE_TEXT]
    assert deferred_reply.registry.running == 0


def test_apply_to_a_number_is_deferred_the_same_way() -> None:
    gate = asyncio.Event()
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )
    update = webhook_fakes._message_update("apply to #1")

    with _serving(supabase, telegram, engine) as client:
        started = time.monotonic()
        response = _post(client, update)

        assert response.json() == {"status": "ok"}
        assert time.monotonic() - started < 1.0
        assert _texts(telegram) == [channel_messages.GENERATING_TEXT]
        assert telegram.documents_sent == []
        _open(client, gate)
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)


# -- redelivery ----------------------------------------------------------------------------


def test_a_redelivered_update_does_not_start_a_second_generation() -> None:
    gate = asyncio.Event()
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )
    update = _tap_generate(update_id=321)

    with _serving(supabase, telegram, engine) as client:
        first = _post(client, update)
        assert _wait_for(lambda: engine.generations_started == 1)
        # Telegram gave up waiting and sends it again while the first is still generating.
        second = _post(client, update)
        _open(client, gate)
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)

    assert first.json() == {"status": "ok"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate"}
    assert engine.generations_started == 1
    assert len(telegram.documents_sent) == 1
    assert _texts(telegram).count(channel_messages.GENERATING_TEXT) == 1  # told once, not twice
    assert supabase.update_ledger.rows[321]["completed"] is True


# -- the cap -------------------------------------------------------------------------------


def test_over_the_cap_the_user_is_told_it_is_busy_and_spends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deferred_reply, "registry", DeferredReplies(max_concurrent=1))
    claims: list[str] = []

    async def count(_supabase: Any, _user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        claims.append(bucket)
        return rate_limits.RateLimitDecision(True, 0)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", count)
    gate = asyncio.Event()
    supabase, telegram, engine = (
        ComposedSupabase(distinct_users=True),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )

    with _serving(supabase, telegram, engine) as client:
        first = _post(client, _tap_generate(update_id=10, user=1001))
        assert _wait_for(lambda: engine.generations_started == 1)
        # the one place is taken, by somebody else
        second = _post(client, _tap_generate(update_id=11, user=1002))
        _open(client, gate)
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)

    assert first.status_code == second.status_code == 200
    assert _texts_to(telegram, 1002) == [channel_messages.BUSY_TEXT]
    assert _texts(telegram).count(channel_messages.BUSY_TEXT) == 1
    assert _texts(telegram).count(channel_messages.GENERATING_TEXT) == 1
    assert claims == ["prepare"]  # the busy one never claimed a slot of the user's hourly limit
    assert engine.generations_started == 1
    assert len(telegram.documents_sent) == 1


def test_once_a_generation_ends_its_place_is_free_for_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deferred_reply, "registry", DeferredReplies(max_concurrent=1))
    supabase, telegram, engine = ComposedSupabase(), prepare_fakes._FakeTelegramClient(), _Engine()

    with _serving(supabase, telegram, engine) as client:
        _post(client, _tap_generate(update_id=20))
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)
        assert _wait_for(lambda: deferred_reply.registry.running == 0)  # its place is back
        _post(client, _tap_generate(update_id=21))
        assert _wait_for(lambda: len(telegram.documents_sent) == 2)

    assert channel_messages.BUSY_TEXT not in _texts(telegram)
    assert channel_messages.ALREADY_GENERATING_TEXT not in _texts(telegram)


def test_one_person_tapping_again_is_told_so_and_leaves_the_other_places_to_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims: list[str] = []

    async def count(_supabase: Any, user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        claims.append(user_id)
        return rate_limits.RateLimitDecision(True, 0)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", count)
    gate = asyncio.Event()
    supabase, telegram, engine = (
        ComposedSupabase(distinct_users=True),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )

    with _serving(supabase, telegram, engine) as client:
        _post(client, _tap_generate(update_id=40, user=1001))
        assert _wait_for(lambda: engine.generations_started == 1)
        for update_id in (41, 42):  # two more taps: not duplicates of an update, new ones
            again = _post(client, _tap_generate(update_id=update_id, user=1001))
            assert again.status_code == 200
        other = _post(client, _tap_generate(update_id=43, user=1002))
        assert _wait_for(lambda: engine.generations_started == 2)
        assert other.status_code == 200
        _open(client, gate)
        assert _wait_for(lambda: len(telegram.documents_sent) == 2)

    assert _texts_to(telegram, 1001)[:3] == [
        channel_messages.GENERATING_TEXT,
        channel_messages.ALREADY_GENERATING_TEXT,
        channel_messages.ALREADY_GENERATING_TEXT,
    ]
    assert _texts_to(telegram, 1002)[0] == channel_messages.GENERATING_TEXT  # got a place
    assert channel_messages.BUSY_TEXT not in _texts(telegram)
    assert engine.generations_started == 2  # one each, not three for the person who tapped
    assert len(claims) == 2  # the refused taps spent none of the person's hourly limit


def test_a_request_the_limit_refuses_gives_its_place_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deferred_reply, "registry", DeferredReplies(max_concurrent=1))

    async def deny(_supabase: Any, _user_id: str, _bucket: str) -> rate_limits.RateLimitDecision:
        return rate_limits.RateLimitDecision(False, 725)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", deny)
    supabase, telegram, engine = ComposedSupabase(), prepare_fakes._FakeTelegramClient(), _Engine()

    with _serving(supabase, telegram, engine) as client:
        for update_id in (30, 31, 32):
            _post(client, _tap_generate(update_id=update_id))

    refusal = (
        "❌ You've reached the limit for this action (10 per hour). Try again in about 13 minutes."
    )
    assert _texts(telegram) == [refusal] * 3  # never "busy": the place was not kept
    assert engine.generations_started == 0
    assert deferred_reply.registry.running == 0


def test_the_limit_is_claimed_before_the_work_starts_and_the_reply_comes_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def allow(_supabase: Any, _user_id: str, bucket: str) -> rate_limits.RateLimitDecision:
        order.append(f"claim:{bucket}")
        return rate_limits.RateLimitDecision(True, 0)

    class _OrderedEngine(_Engine):
        async def post(self, url: str, **kwargs: Any) -> httpx.Response:
            if url.endswith("/apply"):
                order.append("engine")
            return await super().post(url, **kwargs)

    class _OrderedTelegram(prepare_fakes._FakeTelegramClient):
        async def send_message(
            self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
        ) -> int:
            order.append("reply")
            return await super().send_message(chat_id, text, reply_markup=reply_markup)

    monkeypatch.setattr(rate_limits, "claim_rate_limit_slot", allow)
    supabase, telegram, engine = ComposedSupabase(), _OrderedTelegram(), _OrderedEngine()

    with _serving(supabase, telegram, engine) as client:
        _post(client, _tap_generate())

    assert order[:3] == ["claim:prepare", "reply", "engine"]  # claim, "this can take a minute"


# -- failure -------------------------------------------------------------------------------


def test_a_failure_in_the_background_task_is_logged_and_told_to_the_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(fail=True),
    )

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
        _serving(supabase, telegram, engine) as client,
    ):
        response = _post(client, _tap_generate(update_id=444))
        assert _wait_for(lambda: channel_messages.PREPARE_FAILED_TEXT in _shown(telegram))

    assert response.status_code == 200  # the delivery itself succeeded; the work is separate
    assert _shown(telegram) == [channel_messages.PREPARE_FAILED_TEXT]  # the progress message ended
    assert telegram.documents_sent == []
    failures = [r for r in caplog.records if r.getMessage() == "deferred work failed"]
    assert len(failures) == 1
    assert failures[0].exc_info is not None
    assert failures[0].ctx == {  # type: ignore[attr-defined]
        "task": "prepare",
        "update_id": "444",
        "channel": "telegram",
    }
    # The task carries the request id of the delivery that began it, so its log lines can be
    # traced back to that request (the response header is what a person could quote).
    assert engine.request_ids == [response.headers["X-Request-ID"]]


_SENTRY_DSN = "https://0123456789abcdef0123456789abcdef@o123456.ingest.sentry.io/4501234567"


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> FakeSentry:
    """Error reporting switched on through the app's own boot, with a fake SDK."""
    monkeypatch.setattr(error_reporting, "_sdk", None)
    monkeypatch.setattr(error_reporting, "_failure_warned", False)
    monkeypatch.setenv("SENTRY_DSN", _SENTRY_DSN)
    return FakeSentry().install(monkeypatch)


def test_a_crash_in_the_background_task_reaches_the_error_tracker_once(
    sentry: FakeSentry,
) -> None:
    """A chat generation runs outside any request, and the registry catches what it raises:
    without its own report, the one kind of failure that is a bug would leave only a log line."""
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(fail=True),
    )

    with _serving(supabase, telegram, engine) as client:
        _post(client, _tap_generate(update_id=446))
        assert _wait_for(lambda: channel_messages.PREPARE_FAILED_TEXT in _shown(telegram))

    [(reported, scope)] = sentry.captured
    assert isinstance(reported, RuntimeError)
    assert scope == {"tags": {"task": "prepare"}}


def test_a_server_side_api_error_ended_in_chat_is_reported_once_not_twice(
    sentry: FakeSentry,
) -> None:
    """The chat boundary answers an ApiError itself and reports a 5xx; it does not reach the
    registry, which would report a second time."""
    supabase, telegram = ComposedSupabase(), prepare_fakes._FakeTelegramClient()

    with _serving(supabase, telegram, _Engine(compile_status_code=422)) as client:
        _post(client, _tap_generate(update_id=447))
        assert _wait_for(lambda: any(text.startswith("❌") for text in _shown(telegram)))

    [(reported, scope)] = sentry.captured
    assert getattr(reported, "code", None) == "RUN_FAILED"
    assert scope == {"tags": {"task": "prepare", "channel": "telegram", "error_code": "RUN_FAILED"}}


def test_a_known_client_side_failure_ended_in_chat_is_not_reported(sentry: FakeSentry) -> None:
    supabase = ComposedSupabase()
    supabase.profile_versions.select_rows = []  # no resume on file: SETUP_REQUIRED, a 409
    telegram = prepare_fakes._FakeTelegramClient()

    with _serving(supabase, telegram, _Engine()) as client:
        _post(client, _tap_generate(update_id=448))
        assert _wait_for(lambda: any(text.startswith("❌") for text in _shown(telegram)))

    assert sentry.captured == []


def test_a_refused_final_state_after_the_resume_was_delivered_is_logged_and_not_told(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram can answer the edit that ends the progress message, and the new message that is
    its fallback, with a 429 or a 5xx. The person has the resume; "something went wrong
    generating it, try again" would be false and would cost them a second paid generation."""

    def refusal() -> httpx.HTTPStatusError:
        return httpx.HTTPStatusError(
            "429 Too Many Requests",
            request=httpx.Request("POST", "https://api.telegram.org/bot/sendMessage"),
            response=httpx.Response(429),
        )

    class _RefusesTheFinalState(prepare_fakes._FakeTelegramClient):
        async def send_message(
            self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
        ) -> int:
            if text == channel_messages.PREPARE_DONE_TEXT:
                raise refusal()
            return await super().send_message(chat_id, text, reply_markup=reply_markup)

        async def edit_message_text(
            self,
            chat_id: int,
            message_id: int,
            text: str,
            *,
            reply_markup: dict[str, Any] | None = None,
        ) -> int:
            if text == channel_messages.PREPARE_DONE_TEXT:
                raise refusal()
            return await super().edit_message_text(
                chat_id, message_id, text, reply_markup=reply_markup
            )

    supabase, telegram, engine = ComposedSupabase(), _RefusesTheFinalState(), _Engine()

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.channel_core"),
        caplog.at_level(logging.INFO, logger="between_jobs.api.progress_message"),
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
        _serving(supabase, telegram, engine) as client,
    ):
        response = _post(client, _tap_generate(update_id=446))
        assert _wait_for(lambda: len(telegram.documents_sent) == 1)
        assert _wait_for(lambda: deferred_reply.registry.running == 0)

    assert response.status_code == 200
    assert len(telegram.documents_sent) == 1
    assert channel_messages.PREPARE_FAILED_TEXT not in _shown(telegram)  # nothing false after it
    messages = [r.getMessage() for r in caplog.records]
    assert "deferred work failed" not in messages
    (logged,) = [r for r in caplog.records if "how their request ended" in r.getMessage()]
    assert logged.exc_info is not None
    assert logged.ctx == {"update_id": "446", "channel": "telegram"}  # type: ignore[attr-defined]


def test_the_failure_line_the_app_writes_carries_the_request_id_and_the_update_id() -> None:
    """Formatted the way the app formats every line, inside the task: the request id comes
    from the context the task inherited when the delivery started it."""
    lines: list[dict[str, Any]] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(json.loads(JsonFormatter().format(record)))

    logger = logging.getLogger("between_jobs.api.deferred_reply")
    handler = _Capture()
    logger.addHandler(handler)
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(fail=True),
    )
    try:
        with _serving(supabase, telegram, engine) as client:
            response = _post(client, _tap_generate(update_id=555))
            assert _wait_for(lambda: channel_messages.PREPARE_FAILED_TEXT in _shown(telegram))
    finally:
        logger.removeHandler(handler)

    failure = next(line for line in lines if line["msg"] == "deferred work failed")
    assert failure["level"] == "ERROR"
    assert failure["request_id"] == response.headers["X-Request-ID"]
    assert failure["ctx"] == {"task": "prepare", "update_id": "555", "channel": "telegram"}
    assert "RuntimeError" in failure["exc"]["type"]


def test_a_known_failure_is_still_a_message_and_not_a_failure_of_the_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ApiError` is the expected way for a generation to say no (no resume on file, a
    declined fit): it is answered in chat and is not a failure of the task. It is logged,
    but as what it is, a known error and not a crash."""
    supabase = ComposedSupabase()
    supabase.profile_versions.select_rows = []  # no resume on file
    telegram, engine = prepare_fakes._FakeTelegramClient(), _Engine()

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
        caplog.at_level(logging.INFO, logger="between_jobs.api.channel_core"),
        _serving(supabase, telegram, engine) as client,
    ):
        _post(client, _tap_generate())

    assert _texts(telegram)[0] == channel_messages.GENERATING_TEXT
    assert _shown(telegram)[-1].startswith("❌")  # the progress message ended as the error
    assert channel_messages.PREPARE_FAILED_TEXT not in _shown(telegram)
    assert [r for r in caplog.records if r.getMessage() == "deferred work failed"] == []
    (known,) = [r for r in caplog.records if r.getMessage().startswith("api error")]
    assert known.getMessage() == "api error SETUP_REQUIRED"
    assert known.levelno == logging.INFO  # the person's to fix, not an outage


_CONNECT_ERROR_TYPE = f"{httpx.ConnectError.__module__}.{httpx.ConnectError.__qualname__}"


@pytest.mark.parametrize(
    ("engine_kwargs", "code", "status", "level", "extra_ctx"),
    [
        pytest.param(
            {"unreachable": True},
            "PROVIDER_UNAVAILABLE",
            503,
            logging.WARNING,
            {"cause_type": _CONNECT_ERROR_TYPE},
            id="the engine cannot be reached",
        ),
        pytest.param(
            {"apply_status_code": 502},
            "PROVIDER_UNAVAILABLE",
            503,
            logging.WARNING,
            {},
            id="the engine answers with a server error",
        ),
        pytest.param(
            {"compile_status_code": 500},
            "PROVIDER_UNAVAILABLE",
            503,
            logging.WARNING,
            {},
            id="the PDF renderer answers with a server error",
        ),
        pytest.param(
            {"compile_status_code": 422},
            "RUN_FAILED",
            500,
            logging.ERROR,
            {},
            id="the resume does not compile",
        ),
    ],
)
def test_a_known_failure_leaves_a_log_line_with_the_code_and_where_it_came_from(
    engine_kwargs: dict[str, Any],
    code: str,
    status: int,
    level: int,
    extra_ctx: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bot answers these in chat, so the web's error handler never sees them; without a
    line of their own an engine outage seen through the bot would leave no trace at all."""
    supabase, telegram = ComposedSupabase(), prepare_fakes._FakeTelegramClient()

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.channel_core"),
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
        _serving(supabase, telegram, _Engine(**engine_kwargs)) as client,
    ):
        _post(client, _tap_generate(update_id=77))

    assert _shown(telegram)[-1].startswith("❌")
    assert channel_messages.PREPARE_FAILED_TEXT not in _shown(telegram)
    (record,) = [r for r in caplog.records if r.getMessage().startswith("api error")]
    assert record.getMessage() == f"api error {code}"
    assert record.levelno == level
    assert record.ctx == {  # type: ignore[attr-defined]
        "code": code,
        "status": status,
        "task": "prepare",
        "update_id": "77",
        "channel": "telegram",
        **extra_ctx,
    }
    assert [r for r in caplog.records if r.getMessage() == "deferred work failed"] == []


# -- shutdown ------------------------------------------------------------------------------


def test_work_still_running_at_shutdown_is_cancelled_and_the_user_is_not_messaged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(deferred_reply, "SHUTDOWN_GRACE_SECONDS", 0.05)
    gate = asyncio.Event()  # never opened: the engine is stuck
    supabase, telegram, engine = (
        ComposedSupabase(),
        prepare_fakes._FakeTelegramClient(),
        _Engine(gate=gate),
    )

    with (
        caplog.at_level(logging.INFO, logger="between_jobs.api.deferred_reply"),
        _serving(supabase, telegram, engine) as client,
    ):
        _post(client, _tap_generate())
        assert _wait_for(lambda: engine.generations_started == 1)
    # leaving the block ran the app's shutdown

    assert _texts(telegram) == [channel_messages.GENERATING_TEXT]  # nothing after it
    assert telegram.documents_sent == []
    assert deferred_reply.registry.running == 0
    messages = [r.getMessage() for r in caplog.records]
    assert "deferred work cancelled; the user is not messaged" in messages
    assert "deferred work failed" not in messages


def test_a_generation_still_running_when_shutdown_begins_is_given_the_grace_period() -> None:
    """The case the grace period exists for: a deploy that lands as a generation is ending.
    The engine is still working when the app starts to shut down, so only the lifespan's hook
    and the default grace (no patching of it here) let the resume be delivered; without
    either, the task would be cancelled at once."""
    supabase, telegram = ComposedSupabase(), prepare_fakes._FakeTelegramClient()
    engine = _Engine(seconds=0.5)  # a tenth of the real five-second grace

    with _serving(supabase, telegram, engine) as client:
        _post(client, _tap_generate())
        assert _wait_for(lambda: engine.working)
        assert telegram.documents_sent == []  # still generating as the block exits

    assert len(telegram.documents_sent) == 1  # shutdown waited for it, then it was delivered
