"""Tests for the latex-service HTTP client (Sprint 3.3f).

Same convention as test_forge_engines_client.py: the outbound call is
faked at the httpx client boundary, never a real network call. latex-
service's own compile behavior is covered by its own test suite in the
latex-service sub-project.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.latex_service_client import call_compile

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
