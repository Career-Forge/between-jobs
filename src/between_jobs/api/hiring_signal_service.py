"""Hiring Signals P3 -- the per-application search, end to end.

One click on a tracked application: turn the application into a query
(`hiring_signal_query`), get a provider's answer to it (from the shared cache
or from the user's own search key), read every hit with the pure parser
(`hiring_signals`), keep only what is about this company and inside the
window, and hand back structured signals -- never text. No LLM anywhere.

**Which provider.** The user's saved keys decide which providers are tried,
in `hiring_signal_search.PROVIDER_ORDER`. A provider that FAILS (transport
error, HTTP error, unreadable body) is skipped for the next. A provider that
ANSWERS but whose answer holds no LinkedIn post at all -- none of its hits
parses to an activity id -- is skipped too: on real data an index can answer
every query with a clean, empty 200 because LinkedIn is simply not in it
(You.com did exactly that, on every LinkedIn-directed query tried), and "the
provider answered" must not stop the search while a provider that can see
LinkedIn is still configured. A provider that returns posts -- even posts none
of which is about this company -- ends the search: that is the honest empty
answer for a company with no recent posts, and asking another provider the
same question would only spend more of the user's credits. If every provider
that answered had no posts, the last one's (empty) answer is the result; only
when EVERY tried provider failed is it an error (`PROVIDER_UNAVAILABLE`,
retryable). With no key saved at all it is `SETUP_REQUIRED`, in the resolver's
own shape.

**Bounded spend.** At most `MAX_PROVIDER_CALLS_PER_REQUEST` provider calls per
request (one per provider: no retries, no pagination, no second query). Each
call has a TOTAL time limit -- `CALL_TIMEOUT_SECONDS`, or what is left of
`REQUEST_DEADLINE_SECONDS` if that is less -- so the whole request is bounded
by the deadline (plus the reads around it), and no new call starts once the
deadline has gone by. The shared cache absorbs repeats.

**What is hidden, and where it is counted.** Every parsed post lands in
exactly one bucket, in this precedence, so
`raw_hits == rejected + duplicates + off_topic_hidden + too_old_hidden +
job_seekers_hidden + echoes_hidden + shown` always holds:

1. *rejected* -- no usable activity id (`hiring_signals.RejectedHit`), or one
   with a leading zero (never a real id, and two spellings of one number would
   otherwise be two posts);
2. *duplicates* -- another copy of the same post, merged;
3. *off_topic_hidden* -- not about this company (`hiring_signal_relevance`);
4. *too_old_hidden* -- older than the window, by the post time decoded from the
   activity id, not by the provider's own freshness parameter;
5. *job_seekers_hidden* -- first-person job-seeker posts, not hiring calls: a
   post whose OPENING says it (`hiring_signal_relevance.is_job_seeker_post`);
   the same words in a later fragment are another person's comment and hide
   nothing;
6. *echoes_hidden* -- LinkedIn's auto-generated job-share post for a listing
   the job registry already tracks (a duplicate of something the user can
   already see); an echo the registry does not (or cannot) confirm is shown,
   labelled `unmatched` (we track this company, not this listing), `possible`
   (we track this listing's title but cannot confirm its place) or unknown
   (we cannot tell which registry company, if any, is this page), never hidden
   on a guess.

Role match never hides anything; it only orders the list (see
`hiring_signal_relevance`).

**What a response contains.** Structured fields derived by the parser and
nothing else: no snippet, no title, no raw provider field, and never the
provider query (`query_label` is a human summary of it). The author's display
name is transient -- shown, never stored -- and is only ever sent when it
reads as a name (`hiring_signal_relevance.display_author`): the parser takes
the author out of a title by shape, and some shapes hand back a stretch of the
post's own text.

**Best-effort side reads.** A cache read or write that fails, and a registry
lookup that fails, degrade (a miss, a stale cache, an `unknown` registry
match); they never fail the request. The application lookup, the credential
lookup and the saved-flag read are the request's own data access and do.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .applications_store import ApplicationNotFound, get_application
from .credential_resolver import setup_required, try_get_secret
from .errors import ApiError
from .hiring_signal_cache import cache_key, cache_ttl, get_cached_hits, put_cached_hits
from .hiring_signal_company import CompanyNames, identity_keys_of
from .hiring_signal_query import ApplicationQuery, NoCompanyError, build_application_query
from .hiring_signal_registry import fetch_registry_lookup, is_country_level
from .hiring_signal_relevance import (
    age_hint,
    display_author,
    is_job_seeker_post,
    is_too_old,
    mentions_company,
    rank_key,
    role_match,
    visible_posted_at,
)
from .hiring_signal_saves_store import saved_activity_ids
from .hiring_signal_search import (
    CALL_TIMEOUT_SECONDS,
    MAX_PROVIDER_CALLS_PER_REQUEST,
    PROVIDER_ORDER,
    WINDOW_DAYS,
    HiringProvider,
    provider_freshness_request,
    search_provider,
)
from .hiring_signals import (
    Freshness,
    HiringSignal,
    RawSearchHit,
    analyze_pull,
    embed_url,
    match_ats_echo_to_registry,
    parse_hit,
)
from .jobs_store import SnapshotNotFound, get_snapshot

REQUEST_DEADLINE_SECONDS = 25.0
"""No NEW provider call starts once this much wall-clock time has passed
since the request began (a call already in flight still has its own
`CALL_TIMEOUT_SECONDS`)."""

_CAPABILITY = "hiring_signals"
_BEST_EFFORT_ERRORS = (APIError, httpx.HTTPError)
"""What a best-effort database read/write may raise without failing the
request: a PostgREST error, or a transport error reaching it."""


@dataclass(frozen=True)
class _Pull:
    provider: HiringProvider
    hits: list[RawSearchHit]
    cached: bool


async def _configured_keys(supabase: AsyncClient, user_id: str) -> dict[HiringProvider, str]:
    """The user's saved search keys, one lookup per provider, concurrently."""
    secrets = await asyncio.gather(
        *(
            try_get_secret(supabase, user_id, service="search", provider=provider)
            for provider in PROVIDER_ORDER
        )
    )
    return {
        provider: secret for provider, secret in zip(PROVIDER_ORDER, secrets, strict=True) if secret
    }


def _has_posts(hits: list[RawSearchHit]) -> bool:
    return any(isinstance(parse_hit(hit), HiringSignal) for hit in hits)


async def _read_cache(
    supabase: AsyncClient, key: str, *, now: datetime, ttl: timedelta
) -> list[RawSearchHit] | None:
    try:
        return await get_cached_hits(supabase, key, now=now, ttl=ttl)
    except _BEST_EFFORT_ERRORS:
        return None


async def _write_cache(
    supabase: AsyncClient,
    key: str,
    provider: HiringProvider,
    hits: list[RawSearchHit],
    *,
    now: datetime,
) -> None:
    try:
        await put_cached_hits(supabase, key, provider, hits, now=now)
    except _BEST_EFFORT_ERRORS:
        return


async def _pull_hits(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    keys: dict[HiringProvider, str],
    aq: ApplicationQuery,
    freshness: Freshness,
    *,
    now: datetime,
    monotonic: Callable[[], float],
) -> _Pull:
    """See the module docstring's "Which provider" and "Bounded spend"."""
    started = monotonic()
    calls = 0
    attempted: list[str] = []
    answered_without_posts: _Pull | None = None
    for provider in PROVIDER_ORDER:
        api_key = keys.get(provider)
        if api_key is None:
            continue
        request = provider_freshness_request(provider, freshness, today=now.date())
        key = cache_key(provider, aq.query.query, request.param, now.date())
        hits = await _read_cache(supabase, key, now=now, ttl=cache_ttl(freshness))
        from_cache = hits is not None
        if hits is None:
            if calls >= MAX_PROVIDER_CALLS_PER_REQUEST:
                break
            remaining = REQUEST_DEADLINE_SECONDS - (monotonic() - started)
            if remaining <= 0:
                break
            calls += 1
            attempted.append(provider)
            try:
                hits = await search_provider(
                    http,
                    provider,
                    api_key=api_key,
                    query=aq.query.query,
                    freshness_param=request.param,
                    timeout=min(CALL_TIMEOUT_SECONDS, remaining),
                )
            except ApiError:
                continue
            await _write_cache(supabase, key, provider, hits, now=now)
        pull = _Pull(provider=provider, hits=hits, cached=from_cache)
        if _has_posts(hits):
            return pull
        answered_without_posts = pull
    if answered_without_posts is not None:
        return answered_without_posts
    raise ApiError(
        "PROVIDER_UNAVAILABLE",
        "Your search provider could not be reached. Try again in a moment.",
        retryable=True,
        details={"providers_tried": attempted},
    )


async def _registry_states(
    supabase: AsyncClient,
    echoes: list[HiringSignal],
    names: CompanyNames,
) -> dict[str, str | None]:
    """`activity_id -> registry match state` for the given `ats_echo` signals.

    A state is `None` (unknown) for an echo that carries no company or no role
    to match on, for EVERY echo when the registry could not be read, and for an
    echo whose page cannot be tied to any registry company -- the registry may
    well track its listings under a name this matching cannot connect to it
    (`AMD` for `Advanced Micro Devices`), and "we cannot tell" must not be
    reported as "we do not track it". `unmatched` therefore means something
    definite: the registry HAS this company and none of its open listings is
    this one.

    Which registry company is the page's company is decided here, by run-
    together identity (`Scale AI` is the registry's `scaleai`, `Meta` is `Meta
    Platforms, Inc.`); `hiring_signals.match_ats_echo_to_registry` is then
    asked only the question it is built for, title and place. A location that
    names only a country (or `Remote`) cannot say WHICH opening it is, so it
    is passed as unknown: an echo for `Austin, Texas` is not hidden by a
    registry listing in `United States`."""
    states: dict[str, str | None] = {e.activity_id: None for e in echoes}
    if not any(e.author_name is not None and e.echo_role is not None for e in echoes):
        return states
    try:
        lookup = await fetch_registry_lookup(supabase, names)
    except _BEST_EFFORT_ERRORS:
        return states
    keys_by_company = {name: identity_keys_of(name) for name in lookup.companies}
    for echo in echoes:
        page = echo.author_name
        if page is None or echo.echo_role is None:
            continue
        page_keys = identity_keys_of(page)
        if not any(keys & page_keys for keys in keys_by_company.values()):
            continue  # the registry has no company we can tie this page to: unknown
        aligned = [
            replace(
                c,
                company=page,
                location=None if is_country_level(c.location) else c.location,
            )
            for c in lookup.candidates
            if keys_by_company.get(c.company, frozenset()) & page_keys
        ]
        probe = replace(echo, echo_location=None) if is_country_level(echo.echo_location) else echo
        state = match_ats_echo_to_registry(probe, aligned).state
        states[echo.activity_id] = None if state == "undeterminable" else state
    return states


def _group_hits_by_activity_id(hits: list[RawSearchHit]) -> dict[str, list[RawSearchHit]]:
    grouped: dict[str, list[RawSearchHit]] = {}
    for hit in hits:
        parsed = parse_hit(hit)
        if isinstance(parsed, HiringSignal):
            grouped.setdefault(parsed.activity_id, []).append(hit)
    return grouped


async def _load_query(
    supabase: AsyncClient, user_id: str, application_id: str, freshness: Freshness
) -> ApplicationQuery:
    try:
        application = await get_application(supabase, user_id, application_id)
    except ApplicationNotFound as e:
        raise ApiError("NOT_FOUND", f"no application found for id {application_id!r}") from e
    try:
        snapshot = await get_snapshot(supabase, application["active_job_snapshot_id"])
    except SnapshotNotFound as e:
        raise ApiError("INTERNAL_ERROR", "This application's job snapshot is missing.") from e
    try:
        return build_application_query(
            company=snapshot.get("company_name"),
            title=snapshot.get("title"),
            location=snapshot.get("location_text"),
            freshness=freshness,
        )
    except NoCompanyError as e:
        raise ApiError(
            "INVALID_INPUT", "This application has no company name to search for."
        ) from e


async def search_application(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    application_id: str,
    *,
    freshness: Freshness,
    now: datetime | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The `POST /applications/{id}/hiring-signals/search` response body."""
    moment = now if now is not None else datetime.now(UTC)
    aq = await _load_query(supabase, user_id, application_id, freshness)

    keys = await _configured_keys(supabase, user_id)
    if not keys:
        raise setup_required(
            _CAPABILITY,
            missing=["search_credential"],
            message=(
                "Hiring signals need a search provider. "
                "Connect Brave, Serper, Firecrawl or You.com in Settings."
            ),
        )

    pull = await _pull_hits(supabase, http, keys, aq, freshness, now=moment, monotonic=monotonic)

    analysis = analyze_pull(pull.hits)
    copies = _group_hits_by_activity_id(pull.hits)
    names = aq.names
    window_days = WINDOW_DAYS[freshness]

    off_topic = too_old = job_seekers = unusable = 0
    survivors: list[HiringSignal] = []
    for signal in analysis.signals:
        post_copies = copies[signal.activity_id]
        if signal.activity_id.startswith("0"):
            unusable += 1
        elif not any(mentions_company(names, hit=hit, signal=signal) for hit in post_copies):
            off_topic += 1
        elif is_too_old(
            visible_posted_at(signal.posted_at, now=moment), now=moment, window_days=window_days
        ):
            too_old += 1
        elif signal.species == "job_seeker" and is_job_seeker_post(post_copies):
            job_seekers += 1
        else:
            survivors.append(signal)

    echoes = [s for s in survivors if s.species == "ats_echo"]
    registry_state = await _registry_states(supabase, echoes, names) if echoes else {}

    shown: list[tuple[HiringSignal, bool | None]] = []
    echoes_hidden = 0
    for signal in survivors:
        if registry_state.get(signal.activity_id) == "matched":
            echoes_hidden += 1
            continue
        shown.append((signal, role_match(copies[signal.activity_id], aq.role_terms)))

    saved = await saved_activity_ids(
        supabase, user_id, application_id, [s.activity_id for s, _ in shown]
    )
    order = sorted(
        range(len(shown)),
        key=lambda i: rank_key(
            role_match_value=shown[i][1],
            posted_at=visible_posted_at(shown[i][0].posted_at, now=moment),
            position=i,
        ),
    )
    signals = [
        _signal_payload(
            shown[i][0],
            role_match_value=shown[i][1],
            registry_match=registry_state.get(shown[i][0].activity_id),
            saved=shown[i][0].activity_id in saved,
            now=moment,
        )
        for i in order
    ]

    return {
        "provider": pull.provider,
        "cached": pull.cached,
        "freshness": freshness.value,
        "query_label": aq.label,
        "signals": signals,
        "counts": {
            "raw_hits": len(pull.hits),
            "rejected": len(analysis.rejected) + unusable,
            "duplicates": analysis.duplicates_dropped,
            "off_topic_hidden": off_topic,
            "echoes_hidden": echoes_hidden,
            "job_seekers_hidden": job_seekers,
            "too_old_hidden": too_old,
            "shown": len(signals),
        },
    }


def _signal_payload(
    signal: HiringSignal,
    *,
    role_match_value: bool | None,
    registry_match: str | None,
    saved: bool,
    now: datetime,
) -> dict[str, Any]:
    posted_at = visible_posted_at(signal.posted_at, now=now)
    return {
        "activity_id": signal.activity_id,
        "post_url": signal.post_url,
        "embed_url": embed_url(signal.activity_id),
        "author_name": display_author(signal.author_name),
        "posted_at": posted_at.isoformat() if posted_at is not None else None,
        "age_hint": age_hint(signal.age),
        # `job_seeker` is not a species the contract exposes: such a post is
        # either hidden (its opening says so) or shown as plainly unclassified.
        "species": "unclassified" if signal.species == "job_seeker" else signal.species,
        "comment_count": signal.comment_count,
        "role_match": role_match_value,
        "registry_match": registry_match,
        "saved": saved,
    }
