"""Tests for the latex-service HTTP client (Sprint 3.3f).

Same convention as test_forge_engines_client.py: the outbound call is
faked at the httpx client boundary, never a real network call. latex-
service's own compile behavior is covered by its own test suite in the
latex-service sub-project.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from between_jobs.api import latex_service_client
from between_jobs.api.app import app
from between_jobs.api.env import ConfigurationError
from between_jobs.api.errors import ApiError
from between_jobs.api.latex_service_client import call_compile, latex_max_concurrency

_PDF_BYTES = b"%PDF-1.5 fake pdf bytes"


class _FakeHttpClient:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = _PDF_BYTES,
        json_body: Any = None,
        raise_error: bool = False,
    ):
        self.status_code = status_code
        self.content = content
        self.json_body = json_body
        self.raise_error = raise_error
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        if self.raise_error:
            raise httpx.ConnectError("connection refused")
        if self.json_body is not None:
            return httpx.Response(
                status_code=self.status_code,
                json=self.json_body,
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            status_code=self.status_code, content=self.content, request=httpx.Request("POST", url)
        )


async def test_call_compile_sends_the_latex_source() -> None:
    http = _FakeHttpClient()

    await call_compile(http, latex=r"\documentclass{article}")  # type: ignore[arg-type]

    assert len(http.requests) == 1
    url, kwargs = http.requests[0]
    assert url.endswith("/compile")
    assert kwargs["json"] == {"latex": r"\documentclass{article}"}


async def test_call_compile_returns_the_pdf_bytes_on_success() -> None:
    http = _FakeHttpClient()

    result = await call_compile(http, latex="anything")  # type: ignore[arg-type]

    assert result == _PDF_BYTES


async def test_call_compile_raises_provider_unavailable_on_connection_failure() -> None:
    http = _FakeHttpClient(raise_error=True)

    with pytest.raises(ApiError) as exc_info:
        await call_compile(http, latex="anything")  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.retryable is True


async def test_call_compile_raises_run_failed_on_a_compile_error() -> None:
    http = _FakeHttpClient(
        status_code=422,
        json_body={"error": "CompileError", "message": "Undefined control sequence."},
    )

    with pytest.raises(ApiError) as exc_info:
        await call_compile(http, latex="anything")  # type: ignore[arg-type]

    assert exc_info.value.code == "RUN_FAILED"
    assert "Undefined control sequence." in exc_info.value.message


async def test_call_compile_raises_run_failed_on_a_timeout() -> None:
    http = _FakeHttpClient(
        status_code=504, json_body={"error": "CompileTimeout", "message": "pdflatex timed out."}
    )

    with pytest.raises(ApiError) as exc_info:
        await call_compile(http, latex="anything")  # type: ignore[arg-type]

    assert exc_info.value.code == "RUN_FAILED"


async def test_call_compile_raises_provider_unavailable_on_a_5xx() -> None:
    http = _FakeHttpClient(status_code=500, json_body={"detail": "boom"})

    with pytest.raises(ApiError) as exc_info:
        await call_compile(http, latex="anything")  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


# -- the process-wide cap on concurrent compiles ------------------------------------------------


class _SlowCompiler:
    """A fake LaTeX service that holds each compile until told to finish and records how many
    were in flight at once."""

    def __init__(self, *, hold: asyncio.Event | None = None, delay: float = 0.0) -> None:
        self.hold = hold
        self.delay = delay
        self.in_flight = 0
        self.peak = 0
        self.started = 0

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.started += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.hold is not None:
                await self.hold.wait()
            return httpx.Response(200, content=_PDF_BYTES, request=httpx.Request("POST", url))
        finally:
            self.in_flight -= 1


def _slots_free() -> int:
    """How many compile slots are free right now (the semaphore's own count)."""
    return latex_service_client._compile_slots()._value


async def _let_the_loop_run() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_five_concurrent_compiles_never_exceed_the_cap_and_all_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "2")
    http = _SlowCompiler(delay=0.02)

    results = await asyncio.gather(*(call_compile(http, latex=f"doc {i}") for i in range(5)))  # type: ignore[arg-type]

    assert results == [_PDF_BYTES] * 5
    assert http.started == 5
    assert http.peak == 2  # it did run two at a time, and never a third
    assert _slots_free() == 2


async def test_the_default_cap_is_two_and_a_cap_of_one_runs_compiles_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LATEX_MAX_CONCURRENCY", raising=False)
    assert latex_max_concurrency() == 2

    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    http = _SlowCompiler(delay=0.01)
    await asyncio.gather(*(call_compile(http, latex="x") for _ in range(3)))  # type: ignore[arg-type]
    assert http.peak == 1


async def test_a_waiter_that_times_out_gets_a_retryable_error_and_releases_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(latex_service_client, "_QUEUE_WAIT_SECONDS", 0.05)
    hold = asyncio.Event()
    http = _SlowCompiler(hold=hold)

    first = asyncio.create_task(call_compile(http, latex="first"))  # type: ignore[arg-type]
    await _let_the_loop_run()
    assert _slots_free() == 0

    with pytest.raises(ApiError) as exc_info:
        # Bounded by the test itself: if the queue wait ever stops timing out, this fails in
        # five seconds with a TimeoutError instead of hanging until the CI job is cancelled
        # (the first holder is only released further down).
        await asyncio.wait_for(call_compile(http, latex="second"), 5)  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.retryable is True
    assert "busy" in exc_info.value.message
    assert http.started == 1  # the second never reached the service
    assert _slots_free() == 0  # and it did not free the first one's slot

    hold.set()
    assert await first == _PDF_BYTES
    assert _slots_free() == 1  # exactly the one slot, not two


async def test_cancelling_a_waiter_does_not_leak_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    hold = asyncio.Event()
    http = _SlowCompiler(hold=hold)

    first = asyncio.create_task(call_compile(http, latex="first"))  # type: ignore[arg-type]
    await _let_the_loop_run()
    waiter = asyncio.create_task(call_compile(http, latex="waiter"))  # type: ignore[arg-type]
    await _let_the_loop_run()
    assert http.started == 1  # the waiter is queued behind the first

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert _slots_free() == 0  # still held by the first, not given away by the waiter

    hold.set()
    assert await first == _PDF_BYTES
    assert _slots_free() == 1
    # The slot works: a fresh compile is served at once.
    hold_free = _SlowCompiler()
    assert await call_compile(hold_free, latex="after") == _PDF_BYTES  # type: ignore[arg-type]
    assert _slots_free() == 1


async def test_cancelling_a_compile_in_flight_gives_its_slot_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    http = _SlowCompiler(hold=asyncio.Event())

    running = asyncio.create_task(call_compile(http, latex="x"))  # type: ignore[arg-type]
    await _let_the_loop_run()
    assert _slots_free() == 0

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert _slots_free() == 1


async def test_a_failed_compile_gives_its_slot_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    http = _FakeHttpClient(raise_error=True)

    with pytest.raises(ApiError):
        await call_compile(http, latex="x")  # type: ignore[arg-type]

    assert _slots_free() == 1


def test_each_event_loop_gets_its_own_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """An asyncio primitive belongs to one loop: sharing one across loops (the import-time
    mistake) fails with 'bound to a different event loop' the moment it has to wait."""
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")

    async def make() -> asyncio.Semaphore:
        return latex_service_client._compile_slots()

    first = asyncio.run(make())
    second = asyncio.run(make())
    assert first is not second


def test_the_loops_of_a_long_lived_process_do_not_pile_up_in_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A semaphore that has had to make a caller wait holds a reference to its loop, so a
    registry that only forgot loops when they were garbage collected would keep every closed
    loop that ever queued a compile. Each pass here runs its own loop with one compile waiting
    behind another."""
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")

    async def two_compiles_one_waiting() -> None:
        http = _SlowCompiler(delay=0.01)
        await asyncio.gather(call_compile(http, latex="a"), call_compile(http, latex="b"))  # type: ignore[arg-type]
        assert http.peak == 1  # it really did wait

    for _ in range(6):
        asyncio.run(two_compiles_one_waiting())
        assert len(latex_service_client._SEMAPHORES) <= 1


def test_the_shipped_queue_wait_is_thirty_seconds_and_is_what_the_waiter_is_held_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not the value a test patches in: the constant as shipped, and that it is the number
    `call_compile` hands to the timeout."""
    assert latex_service_client._QUEUE_WAIT_SECONDS == 30.0
    delays: list[float | None] = []
    real_timeout = asyncio.timeout

    def recording_timeout(delay: float | None) -> Any:
        delays.append(delay)
        return real_timeout(delay)

    monkeypatch.setattr(asyncio, "timeout", recording_timeout)

    assert asyncio.run(call_compile(_SlowCompiler(), latex="x")) == _PDF_BYTES  # type: ignore[arg-type]
    assert delays == [30.0]


async def test_a_changed_cap_is_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "1")
    assert _slots_free() == 1
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "3")
    assert _slots_free() == 3


@pytest.mark.parametrize("raw", ["0", "-1", "two", "2.5", "1_0", "+2", " ", "0x2"])
def test_a_bad_concurrency_setting_stops_the_boot_and_never_echoes_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", raw)

    with pytest.raises(ConfigurationError) as excinfo:
        latex_max_concurrency()

    assert str(excinfo.value) == "LATEX_MAX_CONCURRENCY must be a positive whole number"
    critical = [r.getMessage() for r in caplog.records if r.levelname == "CRITICAL"]
    assert critical == [
        "the API cannot start: LATEX_MAX_CONCURRENCY must be a positive whole number"
    ]


def test_an_empty_concurrency_setting_reads_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank `KEY=` line in a .env file is how "not configured" usually looks."""
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "")
    assert latex_max_concurrency() == 2


def test_a_sane_concurrency_setting_is_read_with_surrounding_spaces_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", " 4 ")
    assert latex_max_concurrency() == 4


def test_the_api_refuses_to_boot_on_a_bad_concurrency_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is in the lifespan so a typo stops the API at start, not at the first PDF of
    the first user (every /prepare compiles too)."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("LATEX_MAX_CONCURRENCY", "lots")

    with pytest.raises(ConfigurationError, match="LATEX_MAX_CONCURRENCY"), TestClient(app):
        pass
