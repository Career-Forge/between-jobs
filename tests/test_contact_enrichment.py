"""Tests for contact enrichment (outreach-contactfinder.md Phase C --
Apollo; widened outreach-v2-search-first.md Phase K -- Hunter's second
email-enrichment provider and Exa's LinkedIn-URL discovery). Apollo tests
cover opt-in, one candidate at a time, forced-off personal-data reveal,
and the real "email_not_unlocked" placeholder quirk.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from between_jobs.api.contact_enrichment import enrich_candidate, enrich_hunter, find_linkedin_exa
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

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        self.requests.append((url, kwargs))
        return httpx.Response(
            status_code=self.status_code, json=self.body, request=httpx.Request("GET", url)
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


async def test_enrich_hunter_returns_a_real_email() -> None:
    http = _FakeHttp(
        body={"data": {"email": "jane.doe@acme.example", "verification": {"status": "valid"}}}
    )

    result = await enrich_hunter(http, api_key="key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert result["email"] == "jane.doe@acme.example"
    assert result["email_status"] == "valid"


async def test_enrich_hunter_sends_full_name_and_company_not_a_split_name() -> None:
    """Hunter's `full_name` param accepts a bare name directly -- no
    first/last splitting of our own, which would risk a wrong split on a
    multi-word surname."""
    http = _FakeHttp(body={"data": {"email": None}})

    await enrich_hunter(http, api_key="key", person_name="Mary Jane Watson", company="Acme")  # type: ignore[arg-type]

    _url, kwargs = http.requests[0]
    assert kwargs["params"]["full_name"] == "Mary Jane Watson"
    assert kwargs["params"]["company"] == "Acme"


async def test_enrich_hunter_handles_no_match_gracefully() -> None:
    http = _FakeHttp(body={"data": {"email": None, "verification": None}})

    result = await enrich_hunter(http, api_key="key", person_name="Nobody Real", company="Acme")  # type: ignore[arg-type]

    assert result["email"] is None
    assert result["email_status"] is None


async def test_enrich_hunter_raises_provider_rejected_on_401() -> None:
    http = _FakeHttp(status_code=401)

    with pytest.raises(ApiError) as exc_info:
        await enrich_hunter(http, api_key="bad-key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_REJECTED"


async def test_enrich_hunter_raises_provider_unavailable_on_network_error() -> None:
    class _RaisingHttp:
        async def get(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("boom")

    with pytest.raises(ApiError) as exc_info:
        await enrich_hunter(_RaisingHttp(), api_key="key", person_name="Jane Doe", company="Acme")  # type: ignore[arg-type]

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_find_linkedin_exa_confirms_a_name_matched_result_as_strong() -> None:
    http = _FakeHttp(
        body={
            "results": [
                {
                    "url": "https://www.linkedin.com/in/janedoe",
                    "entities": [{"type": "person", "properties": {"name": "Jane Doe"}}],
                }
            ]
        }
    )

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title="Engineer",
    )

    assert result["linkedin_url"] == "https://www.linkedin.com/in/janedoe"
    assert result["match_confidence"] == "strong"


async def test_find_linkedin_exa_marks_an_unconfirmed_top_hit_as_inferred() -> None:
    """A common name can surface someone else's profile as the top hit --
    no entity confirming THIS candidate's name means the URL is kept but
    marked inferred, never trusted as strong."""
    http = _FakeHttp(
        body={
            "results": [
                {"url": "https://www.linkedin.com/in/janedoe-2", "entities": []},
            ]
        }
    )

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title=None,
    )

    assert result["linkedin_url"] == "https://www.linkedin.com/in/janedoe-2"
    assert result["match_confidence"] == "inferred"


async def test_find_linkedin_exa_returns_unsupported_when_nothing_looks_like_a_profile() -> None:
    http = _FakeHttp(body={"results": [{"url": "https://example.com/about", "entities": []}]})

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title=None,
    )

    assert result["linkedin_url"] is None
    assert result["match_confidence"] == "unsupported"


async def test_find_linkedin_exa_raises_provider_unavailable_on_error() -> None:
    http = _FakeHttp(status_code=500)

    with pytest.raises(ApiError) as exc_info:
        await find_linkedin_exa(
            http,  # type: ignore[arg-type]
            api_key="key",
            person_name="Jane Doe",
            company="Acme",
            claimed_title=None,
        )

    assert exc_info.value.code == "PROVIDER_UNAVAILABLE"


async def test_find_linkedin_exa_does_not_treat_a_superstring_name_as_strong() -> None:
    """Regression test for a real, adversarially-confirmed bug: a bare
    substring check (`target in entity_name`) would falsely mark "Jane
    Doe" as a "strong" match against an unrelated "Mary Jane Doerty" --
    exact-match-after-normalization is required for "strong"."""
    http = _FakeHttp(
        body={
            "results": [
                {
                    "url": "https://www.linkedin.com/in/mary-jane-doerty",
                    "entities": [{"type": "person", "properties": {"name": "Mary Jane Doerty"}}],
                }
            ]
        }
    )

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title=None,
    )

    assert result["match_confidence"] == "inferred"


async def test_find_linkedin_exa_loops_past_an_unconfirmed_top_hit_to_a_confirmed_lower_one() -> (
    None
):
    """Regression test for a real coverage gap: a broken rewrite that
    only inspected the first hit would pass every OTHER existing test
    (all single-hit) but silently misclassify this real, reachable
    multi-hit case."""
    http = _FakeHttp(
        body={
            "results": [
                {"url": "https://www.linkedin.com/in/janedoe-unrelated", "entities": []},
                {
                    "url": "https://www.linkedin.com/in/janedoe-real",
                    "entities": [{"type": "person", "properties": {"name": "Jane Doe"}}],
                },
            ]
        }
    )

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title=None,
    )

    assert result["linkedin_url"] == "https://www.linkedin.com/in/janedoe-real"
    assert result["match_confidence"] == "strong"


async def test_find_linkedin_exa_rejects_a_url_that_only_contains_the_substring() -> None:
    """Regression test for a real, adversarially-confirmed security bug:
    a bare substring check (`"linkedin.com/in/" in url.lower()`) would
    accept a phishing/tracker URL that merely embeds that substring in
    its path or query string, on a completely different host."""
    http = _FakeHttp(
        body={
            "results": [
                {"url": "https://phish.example/next=linkedin.com/in/jane-doe", "entities": []},
            ]
        }
    )

    result = await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title=None,
    )

    assert result["linkedin_url"] is None
    assert result["match_confidence"] == "unsupported"


async def test_find_linkedin_exa_sends_the_person_title_and_company_in_the_query() -> None:
    http = _FakeHttp(body={"results": []})

    await find_linkedin_exa(
        http,  # type: ignore[arg-type]
        api_key="key",
        person_name="Jane Doe",
        company="Acme",
        claimed_title="Staff Engineer",
    )

    _url, kwargs = http.requests[0]
    query = kwargs["json"]["query"]
    assert "Jane Doe" in query
    assert "Staff Engineer" in query
    assert "Acme" in query
