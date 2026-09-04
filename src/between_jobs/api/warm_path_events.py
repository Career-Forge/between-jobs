"""Events warm-path engine (outreach-contactfinder.md Phase D) --
Proposal §27.5 / MASTER_PLAN §5.10b, "the best idea of the session."
Scans public event pages (conference/meetup/sponsor listings) for a
target company's presence, reusing the SAME L1 general-search fetch and
LLM-extraction-plus-deterministic-re-grounding shape `contact_research.py`
already established -- a different query shape over the same evidence
discipline, not parallel infrastructure.

Three-state honesty is the load-bearing rule here, not a nice-to-have:
"confirmed speaker" != "sponsor booth, likely staffed" != "company-
adjacent event." A confirmed speaker's name is grounded the same way a
ContactFinder candidate's name is (the source's own text must literally
name them); if it isn't grounded, the row is downgraded to
"company_adjacent" with the speaker fields cleared, never dropped
outright -- the event/company relationship itself can still be real even
when a specific named-speaker claim isn't. "Attendees are unknowable --
don't fake it" (Proposal's own words): this module never claims anyone
will attend, only that a company is confirmed speaking, likely
sponsoring, or adjacent to an event.

Proactive trigger wiring (auto-firing off an application being tracked or
a dream-tier match, per §27.5's own trigger language) is explicitly
deferred -- this ships as an on-demand endpoint, the same shape Company
Intel and InterviewForge's registry both used before any later proactive
feed wired into them. A disclosed follow-up, not silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, TypedDict, cast

import httpx

from .errors import ApiError
from .llm_client import LLMResponse
from .llm_client import generate as llm_generate
from .research_clients import SearchHit, search_firecrawl, search_you_com

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

Certainty = Literal["confirmed_speaker", "likely_staffed_sponsor", "company_adjacent"]
_CERTAINTY_VALUES: tuple[Certainty, ...] = (
    "confirmed_speaker",
    "likely_staffed_sponsor",
    "company_adjacent",
)


class EventQuery(TypedDict):
    certainty_hint: Certainty
    query: str


def build_event_query_plan(company: str, location: str | None) -> list[EventQuery]:
    """A fixed, bounded query plan, same "planner cannot generate an
    unbounded loop" discipline as `contact_research.build_contact_query_
    plan`. `location` is the user's own metro, not the job's -- warm-path
    events are about where the USER can physically show up, matching
    MASTER_PLAN's own "metro-lopsided coverage" framing."""
    loc = f" {location}" if location else ""
    return [
        EventQuery(
            certainty_hint="confirmed_speaker",
            query=f"{company} speaker conference meetup{loc} 2026",
        ),
        EventQuery(
            certainty_hint="likely_staffed_sponsor",
            query=f"{company} sponsor booth event{loc} 2026",
        ),
        EventQuery(
            certainty_hint="company_adjacent",
            query=f"{company} hosting attending meetup{loc}",
        ),
    ]


QueryResults = list[tuple[EventQuery, list[SearchHit]]]


async def run_event_research(
    http: httpx.AsyncClient,
    queries: list[EventQuery],
    *,
    you_com_key: str | None,
    firecrawl_key: str | None,
) -> tuple[QueryResults, list[str], list[str]]:
    """Same You.com-primary/Firecrawl-fallback shape as `contact_research.
    run_contact_research` and `company_intel_pipeline.run_research` --
    reuses the exact same two providers, no new BYOK credential needed
    for this phase."""
    if you_com_key is None and firecrawl_key is None:
        raise ApiError(
            "SETUP_REQUIRED",
            "The events warm-path engine needs a You.com or Firecrawl key configured.",
            capability="warm_path_events",
            missing=["you_com_or_firecrawl_credential"],
            settings_path="/profile/integrations",
        )

    provider = "you_com" if you_com_key is not None else "firecrawl"
    results: QueryResults = []
    warnings: list[str] = []
    for q in queries:
        try:
            if you_com_key is not None:
                hits = await search_you_com(http, api_key=you_com_key, query=q["query"])
            else:
                assert firecrawl_key is not None
                hits = await search_firecrawl(http, api_key=firecrawl_key, query=q["query"])
        except ApiError as e:
            warnings.append(f"{q['certainty_hint']}: {e.message}")
            hits = []
        results.append((q, hits))
    return results, [provider], warnings


class WarmPathEvent(TypedDict):
    event_name: str
    event_url: str
    event_date: str | None
    location: str | None
    certainty: Certainty
    speaker_name: str | None
    speaker_title: str | None
    talk_topic: str | None
    source_title: str
    source_snippet: str


_EXTRACTION_SYSTEM_PROMPT = """You are an events-discovery extractor. You are given search \
snippets that may mention a company's presence at a public event (conference, meetup, sponsored \
event), each with a source URL, title, and snippet. Your job is to identify real events with a \
real, honest certainty label -- never invent an event, a speaker, or a company's involvement.

Rules:
- "confirmed_speaker": the source explicitly names an individual as a speaker/presenter at this \
event, representing the company. The speaker's full name must appear verbatim in the source's \
own title or snippet.
- "likely_staffed_sponsor": the source states the company is a sponsor or exhibitor, without a \
named individual confirmed to attend.
- "company_adjacent": the source only loosely connects the company to the event (e.g. hosting, \
mentioned attending) -- the weakest, most honest label when unsure.
- NEVER claim any specific person will attend unless the source explicitly names them as a \
speaker/presenter. Sponsoring a booth does not mean any named person will be there.
- If the evidence doesn't describe a real event, omit it -- do not invent one to fill a category.

Each object in your JSON array must have exactly these fields:
- "event_name": the event's name as stated in the source
- "event_url": must be one of the URLs given in the evidence below, verbatim
- "event_date": the event's date if stated, else null
- "location": the event's location if stated, else null
- "certainty": one of "confirmed_speaker", "likely_staffed_sponsor", "company_adjacent"
- "speaker_name": the speaker's full name, ONLY if certainty is "confirmed_speaker", else null
- "speaker_title": the speaker's title, if stated, else null
- "talk_topic": the talk's topic/title, if stated, else null

Return ONLY the JSON array, no prose, no markdown code fences."""


def _format_evidence(results: QueryResults) -> str:
    lines = []
    for query, hits in results:
        for hit in hits:
            lines.append(
                f"[{query['certainty_hint']}] {hit['title']} -- {hit['url']}\n{hit['snippet']}"
            )
    return "\n\n".join(lines)


def _normalize_name(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _speaker_is_grounded(speaker_name: str, source_title: str, source_snippet: str) -> bool:
    """Same `sourceExplicitlyNamesCandidate`-style check `contact_
    research._source_explicitly_names_candidate` uses -- a claimed
    speaker's name must literally appear in the cited source's own text,
    not merely a URL that happens to be in the evidence set."""
    normalized_name = _normalize_name(speaker_name)
    haystack = _normalize_name(f"{source_title} {source_snippet}")
    return bool(normalized_name) and normalized_name in haystack


def extract_events(raw: str, results: QueryResults) -> list[WarmPathEvent]:
    """Deterministic re-grounding applied to the LLM's raw extraction
    output. Every event's `event_url` is re-checked against evidence
    actually retrieved. A "confirmed_speaker" claim whose name isn't
    literally in the source text is DOWNGRADED to "company_adjacent"
    with the speaker fields cleared, not dropped outright -- the event
    itself can still be real even when the specific speaker claim isn't
    grounded."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []

    hits_by_url: dict[str, SearchHit] = {}
    for _query, hits in results:
        for hit in hits:
            hits_by_url[hit["url"]] = hit

    events: list[WarmPathEvent] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        event_name = item.get("event_name")
        event_url = item.get("event_url")
        certainty = item.get("certainty")

        if not event_name or not isinstance(event_name, str):
            continue
        if event_url not in hits_by_url:
            continue
        if certainty not in _CERTAINTY_VALUES:
            continue
        hit = hits_by_url[event_url]

        speaker_name = (
            item.get("speaker_name") if isinstance(item.get("speaker_name"), str) else None
        )
        speaker_title = (
            item.get("speaker_title") if isinstance(item.get("speaker_title"), str) else None
        )
        talk_topic = item.get("talk_topic") if isinstance(item.get("talk_topic"), str) else None

        if certainty == "confirmed_speaker" and (
            not speaker_name or not _speaker_is_grounded(speaker_name, hit["title"], hit["snippet"])
        ):
            certainty = "company_adjacent"
            speaker_name = None
            speaker_title = None
            talk_topic = None

        events.append(
            WarmPathEvent(
                event_name=event_name,
                event_url=event_url,
                event_date=item.get("event_date")
                if isinstance(item.get("event_date"), str)
                else None,
                location=item.get("location") if isinstance(item.get("location"), str) else None,
                certainty=cast(Certainty, certainty),
                speaker_name=speaker_name,
                speaker_title=speaker_title,
                talk_topic=talk_topic,
                source_title=hit["title"],
                source_snippet=hit["snippet"],
            )
        )
    return events


async def find_warm_path_events(
    results: QueryResults,
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate = llm_generate,
) -> list[WarmPathEvent]:
    evidence_text = _format_evidence(results)
    if not evidence_text.strip():
        return []

    response = await generate(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_EXTRACTION_SYSTEM_PROMPT,
        user_prompt=f"Evidence:\n{evidence_text}",
        max_tokens=2000,
    )

    return extract_events(response.content, results)
