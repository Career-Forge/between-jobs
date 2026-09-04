"""Tests for Apollo-based contact enrichment (outreach-contactfinder.md
Phase C) -- opt-in, one candidate at a time, forced-off personal-data
reveal, and the real "email_not_unlocked" placeholder quirk.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from between_jobs.api.contact_enrichment import enrich_candidate
from between_jobs.api.errors import ApiError


class _FakeHttp:
    def __init__(self, *, status_code: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("POST", url)
        )


async def test_enrich_candidate_returns_a_real_email() -> None:
    http = _FakeHttp(
        body={"person": {"email": "jane.doe@acme.example", "email_status": "verified"}}
    )

    result = await enrich_candidate(http, api_key="key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert result["email"] == "jane.doe@acme.example"
    assert result["email_status"] == "verified"


async def test_enrich_candidate_never_reveals_personal_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = _FakeHttp(body={"person": {"email": None}})

    await enrich_candidate(http, api_key="key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    _url, kwargs = http.requests[0]
    assert kwargs["params"]["reveal_personal_emails"] == "false"
    assert kwargs["params"]["reveal_phone_number"] == "false"


async def test_enrich_candidate_treats_the_email_not_unlocked_placeholder_as_no_email() -> None:
    """A real, documented Apollo quirk: with reveal_personal_emails=false
    and no confirmed work email, Apollo returns the literal string
    "email_not_unlocked@domain.com" rather than null."""
    http = _FakeHttp(body={"person": {"email": "email_not_unlocked@domain.com"}})

    result = await enrich_candidate(http, api_key="key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert result["email"] is None


async def test_enrich_candidate_raises_provider_rejected_on_401() -> None:
    http = _FakeHttp(status_code=401)

    with pytest.raises(ApiError) as exc_info:
        await enrich_candidate(http, api_key="bad-key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_REJECTED"


async def test_enrich_candidate_handles_no_match_gracefully() -> None:
    http = _FakeHttp(body={"person": None})

    result = await enrich_candidate(http, api_key="key", person_name="Nobody Real", company="Acme")  # type: ignore[arg-type]

    assert result["email"] is None
    assert result["email_status"] is None
