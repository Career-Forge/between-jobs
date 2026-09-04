"""Tests for the events warm-path engine (outreach-contactfinder.md
Phase D) -- query planning, provider fan-out, and above all the three-
state honesty downgrade (a confirmed-speaker claim that isn't grounded
becomes company_adjacent, never gets dropped, never stays a fabricated
confirmed speaker).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.research_clients import SearchHit
from between_jobs.api.warm_path_events import (
    QueryResults,
    build_event_query_plan,
    extract_events,
    find_warm_path_events,
    run_event_research,
)

_SPEAKER_HIT = SearchHit(
    title="Jane Doe to speak at Data+AI Summit",
    url="https://conf.example/data-ai-summit",
    snippet="Jane Doe, Staff Engineer at Acme, will present on scaling ML infra.",
    published_at=None,
)


def test_build_event_query_plan_covers_the_three_certainty_tiers() -> None:
    queries = build_event_query_plan("Acme", "Brooklyn")
    hints = {q["certainty_hint"] for q in queries}
    assert hints == {"confirmed_speaker", "likely_staffed_sponsor", "company_adjacent"}
    assert all("Acme" in q["query"] for q in queries)
    assert all("Brooklyn" in q["query"] for q in queries)


def test_build_event_query_plan_handles_no_metro() -> None:
    queries = build_event_query_plan("Acme", None)
    assert len(queries) == 3
    assert all("Acme" in q["query"] for q in queries)


class _FakeHttp:
    async def post(self, url: str, **_kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("real http.post should never be reached in these tests")


async def test_run_event_research_raises_setup_required_when_no_provider_configured() -> None:
    with pytest.raises(ApiError) as exc_info:
        await run_event_research(
            _FakeHttp(),  # type: ignore[arg-type]
            build_event_query_plan("Acme", None),
            you_com_key=None,
            firecrawl_key=None,
        )
    assert exc_info.value.code == "SETUP_REQUIRED"


def _results(hits: list[SearchHit]) -> QueryResults:
    query = build_event_query_plan("Acme", None)[0]  # confirmed_speaker hint
    return [(query, hits)]


def _event_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "event_name": "Data+AI Summit",
        "event_url": _SPEAKER_HIT["url"],
        "event_date": "2026-10-01",
        "location": "New York",
        "certainty": "confirmed_speaker",
        "speaker_name": "Jane Doe",
        "speaker_title": "Staff Engineer",
        "talk_topic": "Scaling ML infra",
    }
    base.update(overrides)
    return base


def test_extract_events_keeps_a_grounded_confirmed_speaker() -> None:
    raw = json.dumps([_event_payload()])
    events = extract_events(raw, _results([_SPEAKER_HIT]))

    assert len(events) == 1
    assert events[0]["certainty"] == "confirmed_speaker"
    assert events[0]["speaker_name"] == "Jane Doe"


def test_extract_events_downgrades_an_ungrounded_speaker_claim() -> None:
    """The load-bearing three-state-honesty check: a claimed speaker
    whose name isn't literally in the source text is downgraded to
    company_adjacent with the speaker fields cleared, not dropped."""
    raw = json.dumps([_event_payload(speaker_name="Someone Else Entirely")])
    events = extract_events(raw, _results([_SPEAKER_HIT]))

    assert len(events) == 1
    assert events[0]["certainty"] == "company_adjacent"
    assert events[0]["speaker_name"] is None
    assert events[0]["speaker_title"] is None
    assert events[0]["talk_topic"] is None
    # the event itself survives -- only the ungrounded speaker claim is dropped
    assert events[0]["event_name"] == "Data+AI Summit"


def test_extract_events_keeps_ungrounded_certainty_tiers_that_never_claimed_anyone() -> None:
    raw = json.dumps(
        [_event_payload(certainty="likely_staffed_sponsor", speaker_name=None, speaker_title=None)]
    )
    events = extract_events(raw, _results([_SPEAKER_HIT]))

    assert len(events) == 1
    assert events[0]["certainty"] == "likely_staffed_sponsor"


def test_extract_events_drops_an_event_citing_an_unretrieved_url() -> None:
    raw = json.dumps([_event_payload(event_url="https://not-in-evidence.example/made-up")])
    events = extract_events(raw, _results([_SPEAKER_HIT]))
    assert events == []


def test_extract_events_drops_an_event_with_an_unknown_certainty() -> None:
    raw = json.dumps([_event_payload(certainty="definitely-real")])
    events = extract_events(raw, _results([_SPEAKER_HIT]))
    assert events == []


def test_extract_events_handles_malformed_json() -> None:
    events = extract_events("not json", _results([_SPEAKER_HIT]))
    assert events == []


def test_extract_events_strips_markdown_code_fences() -> None:
    payload = json.dumps([_event_payload()])
    raw = f"```json\n{payload}\n```"
    events = extract_events(raw, _results([_SPEAKER_HIT]))
    assert len(events) == 1


async def test_find_warm_path_events_returns_empty_when_no_evidence_gathered() -> None:
    events = await find_warm_path_events(
        _results([]), llm_api_key="key", llm_model="model", llm_base_url=None
    )
    assert events == []


async def test_find_warm_path_events_wires_the_llm_call_through() -> None:
    async def fake_generate(**_kwargs: Any) -> LLMResponse:
        return LLMResponse(content=json.dumps([_event_payload()]))

    events = await find_warm_path_events(
        _results([_SPEAKER_HIT]),
        llm_api_key="key",
        llm_model="model",
        llm_base_url=None,
        generate=fake_generate,
    )

    assert len(events) == 1
    assert events[0]["speaker_name"] == "Jane Doe"
