"""Hiring Signals P3 and P4 -- the per-application search and the standalone
tab's search, end to end.

**Two questions, one provider layer.** `search_application` (P3) is one click on a
tracked application: turn the application into a query (`hiring_signal_query`), get
a provider's answer to it (from the shared cache or from the user's own search
key), read every hit with the pure parser (`hiring_signals`), keep only what is
about this company and inside the window, and hand back structured signals --
never text. `search_tab` (P4) is the hidden-market question with NO company: the
user types a role (and optionally a metro), the same provider layer answers it,
and the filter is about the ROLE (see "The standalone tab" below). Both go through
`_pull_hits` -- one provider policy, one cache, one spend bound -- and both hand
back the same structured fields (`_signal_fields`). No LLM anywhere.

**Which provider.** The user's saved keys decide which providers are tried,
in `hiring_signal_search.PROVIDER_ORDER`. A provider that FAILS (transport
error, HTTP error, unreadable body) is skipped for the next. A provider that
ANSWERS but whose answer holds no LinkedIn post at all -- none of its hits
parses to an activity id -- is skipped too: an index can answer every query
with a clean, empty 200 because LinkedIn is simply not in it (on 2026-09-19
You.com did exactly that on each of the nine LinkedIn-directed queries tried; the
fixtures README records them and `you_com_empty.json` is a sanitized answer, so
this is an observation on a stated date, not a promise about the provider), and
"the provider answered" must not stop the search while a provider that can see
LinkedIn is still configured. A provider that returns posts -- even posts none
of which is about this company -- ends the search: that is the honest empty
answer for a company with no recent posts, and asking another provider the
same question would only spend more of the user's credits. If every provider
that answered had no posts, the FIRST one's (empty) answer is the result: the
order ranks providers by how well they see LinkedIn, so the first to answer is
the best-placed one and its empty answer is the one that says something about
the search. (Reporting the last would let a provider known to be blind to
LinkedIn overwrite it, and the page would then tell a user who already has a
capable provider connected to go and connect one.) Only when EVERY tried
provider failed is it an error (`PROVIDER_UNAVAILABLE`, retryable). With no key
saved at all it is `SETUP_REQUIRED`, in the resolver's own shape.

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
name is transient -- shown, never stored -- and is only ever sent when it reads
as a name AND agrees with the url's author handle
(`hiring_signal_relevance.shown_author`): the parser takes the author out of a
title by position, some positions hand back a stretch of the post's own title,
and a stretch of a headline has the shape of a name -- the handle, which LinkedIn
derives from the name, is what tells them apart. A url with no handle has no
author to show.

**The standalone tab (`search_tab`).** No application, no company: the request is
`{query, location, freshness, locale}` and `hiring_signal_tab.build_tab_query`
turns it into the provider query, the role phrase, and the label (`query_label`,
which says what the provider was actually asked -- the place is searched as its
first comma-separated part and the label says so). Every parsed post lands in
exactly one bucket, in this precedence, so the identity

    raw_hits == rejected + duplicates + role_mismatch_hidden + too_old_hidden
                + job_seekers_hidden + echoes_hidden + shown

always holds (the tests check it on every path, including a seeded fuzz):

1. *rejected* -- no usable activity id, or one with a leading zero;
2. *duplicates* -- another copy of the same post, merged;
3. *role_mismatch_hidden* -- the role is not said in it (`hiring_signal_tab.
   tab_role_fit`: a HARD filter, AND over every word of the role, read from the
   post's title and OPENING -- or, for an auto job-share, its own parsed role --
   and never from the fragments after the first elision, which are other people's
   lines; a role said only there is kept but ranked below, not hidden);
4. *too_old_hidden* -- older than the window, by the post time decoded from the
   activity id, not by the provider's own freshness parameter;
5. *job_seekers_hidden* -- a first-person job-seeker post, judged by its OPENING;
6. *echoes_hidden* -- LinkedIn's auto-generated job-share post for a listing the
   job registry already tracks, and no other kind of post: an echo the registry
   does not (or cannot) confirm is shown.

The order is behavior, not bookkeeping: a post that is both off-role and old is
`role_mismatch_hidden`, one that is a job seeker's and old is `too_old_hidden`, and
an old echo never reaches the registry. Location is NOT verified per post and
nothing is filtered on it. An account with many posts in the pull is tagged
`aggregator` and ranked below everyone else, never hidden. Ranking is
`hiring_signal_tab.tab_rank_key`: non-aggregators first, then posts that state the
role before posts that only mention it late, then the freshest.

**The registry read for echoes is ONE batch.** An echo's company is the page that
posted it, so a single pull can hold echoes of a dozen employers. They are read in
two queries however many echoes there are (`_tab_registry_states` ->
`hiring_signal_registry.fetch_registry_lookup_for_pages`: the registry companies
whose names match ANY of the pages, then those companies' postings narrowed by ANY
of the echoes' roles), never one query per echo. At most `MAX_COMPANIES` distinct
pages are looked up (an echo of one beyond that is left unknown, shown, never
hidden), a page whose name is too short to look up is unknown too, and
`echo_registry_state` -- the same function the per-application path uses --
decides what the lookup says about each echo.

**The saved flag** of a tab result reads STANDALONE saves only (a post saved
against an application is a different save: `saved_activity_ids(..., None, ...)`).

**Best-effort side reads.** A cache read or write that fails, and a registry
lookup that fails, degrade (a miss, a stale cache, an `unknown` registry
match); they never fail the request. The application lookup, the credential
lookup and the saved-flag read are the request's own data access and do.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .applications_store import ApplicationNotFound, get_application
from .credential_resolver import setup_required, try_get_secret
from .errors import ApiError
from .hiring_signal_cache import cache_key, cache_ttl, get_cached_hits, put_cached_hits
from .hiring_signal_company import CompanyNames, company_names
from .hiring_signal_query import ApplicationQuery, NoCompanyError, build_application_query
from .hiring_signal_registry import (
    MAX_COMPANIES,
    echo_registry_state,
    fetch_registry_lookup,
    fetch_registry_lookup_for_pages,
)
from .hiring_signal_relevance import (
    age_hint,
    is_job_seeker_post,
    is_too_old,
    mentions_company,
    rank_key,
    role_match,
    shown_author,
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
from .hiring_signal_tab import (
    InvalidSearchInput,
    build_tab_query,
    role_filter_terms,
    tab_rank_key,
    tab_role_fit,
)
from .hiring_signals import (
    Freshness,
    HiringSignal,
    Locale,
    RawSearchHit,
    analyze_pull,
    embed_url,
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


async def _require_search_keys(supabase: AsyncClient, user_id: str) -> dict[HiringProvider, str]:
    """The user's saved search keys, or the resolver-shaped `SETUP_REQUIRED`
    when there is none (a search with no provider to ask is a setup step, not
    an empty answer)."""
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
    return keys


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
    query: str,
    freshness: Freshness,
    *,
    now: datetime,
    monotonic: Callable[[], float],
) -> _Pull:
    """See the module docstring's "Which provider" and "Bounded spend".
    `query` is the provider query string -- both surfaces build one their own
    way and this is the part that does not differ. It is sent to the provider
    and keyed into the cache, and goes nowhere else."""
    started = monotonic()
    calls = 0
    attempted: list[str] = []
    answered_without_posts: _Pull | None = None
    for provider in PROVIDER_ORDER:
        api_key = keys.get(provider)
        if api_key is None:
            continue
        request = provider_freshness_request(provider, freshness, today=now.date())
        key = cache_key(provider, query, request.param, now.date())
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
                    query=query,
                    freshness_param=request.param,
                    timeout=min(CALL_TIMEOUT_SECONDS, remaining),
                )
            except ApiError:
                continue
            await _write_cache(supabase, key, provider, hits, now=now)
        pull = _Pull(provider=provider, hits=hits, cached=from_cache)
        if _has_posts(hits):
            return pull
        if answered_without_posts is None:  # the first answer is the best-placed one's
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

    Which registry company is the page's company is decided per echo by
    `hiring_signal_registry.echo_registry_state` (one implementation, shared with
    the standalone tab); this function is only the per-application READ: one
    lookup, by the application's company."""
    states: dict[str, str | None] = {e.activity_id: None for e in echoes}
    if not any(e.author_name is not None and e.echo_role is not None for e in echoes):
        return states
    try:
        lookup = await fetch_registry_lookup(supabase, names)
    except _BEST_EFFORT_ERRORS:
        return states
    for echo in echoes:
        states[echo.activity_id] = echo_registry_state(echo, lookup)
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

    keys = await _require_search_keys(supabase, user_id)

    pull = await _pull_hits(
        supabase, http, keys, aq.query.query, freshness, now=moment, monotonic=monotonic
    )

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


def _signal_fields(signal: HiringSignal, *, now: datetime) -> dict[str, Any]:
    """The fields of a response signal that both surfaces carry, and nothing
    else: structured values derived by the parser, never text. The author's
    display name is transient -- shown, never stored -- and is only ever sent
    when it reads as a name that agrees with the url's handle
    (`hiring_signal_relevance.shown_author`): the parser takes the author out of
    a title by position, and some positions hand back a stretch of the post's
    own title."""
    posted_at = visible_posted_at(signal.posted_at, now=now)
    return {
        "activity_id": signal.activity_id,
        "post_url": signal.post_url,
        "embed_url": embed_url(signal.activity_id),
        "author_name": shown_author(signal.author_name, signal.author_handle),
        "posted_at": posted_at.isoformat() if posted_at is not None else None,
        "age_hint": age_hint(signal.age),
        # `job_seeker` is not a species the contract exposes: such a post is
        # either hidden (its opening says so) or shown as plainly unclassified.
        "species": "unclassified" if signal.species == "job_seeker" else signal.species,
        "comment_count": signal.comment_count,
    }


def _signal_payload(
    signal: HiringSignal,
    *,
    role_match_value: bool | None,
    registry_match: str | None,
    saved: bool,
    now: datetime,
) -> dict[str, Any]:
    return {
        **_signal_fields(signal, now=now),
        "role_match": role_match_value,
        "registry_match": registry_match,
        "saved": saved,
    }


# ── the standalone tab ───────────────────────────────────────────────────


async def _tab_registry_states(
    supabase: AsyncClient, echoes: list[HiringSignal]
) -> dict[str, str | None]:
    """`activity_id -> registry match state` for the tab's `ats_echo` signals
    (see `_registry_states` for what a state means).

    The tab has no company to read the registry by: each echo's company is the
    page that posted it. Those pages -- at most `MAX_COMPANIES` DISTINCT ones, in
    order of appearance; an echo of a page beyond that is left unknown, which is
    shown, never hidden -- are read in ONE batch
    (`fetch_registry_lookup_for_pages`: two queries however many echoes there
    are), and `echo_registry_state` then decides each echo against it exactly as
    it does for the per-application search. A registry that cannot be read leaves
    every state unknown."""
    states: dict[str, str | None] = {e.activity_id: None for e in echoes}
    pages: dict[frozenset[str], CompanyNames] = {}
    batched: list[HiringSignal] = []
    for echo in echoes:
        if echo.author_name is None or echo.echo_role is None:
            continue
        # LinkedIn page names carry a tagline (`Acme Engineers Pvt Ltd - Aerospace`)
        names = company_names(echo.author_name.split(" - ")[0])
        if names is None:
            continue
        if names.identity_keys not in pages:
            if len(pages) >= MAX_COMPANIES:
                continue
            pages[names.identity_keys] = names
        batched.append(echo)
    if not batched:
        return states
    try:
        lookup = await fetch_registry_lookup_for_pages(
            supabase,
            list(pages.values()),
            [e.echo_role for e in batched if e.echo_role is not None],
        )
    except _BEST_EFFORT_ERRORS:
        return states
    for echo in batched:
        states[echo.activity_id] = echo_registry_state(echo, lookup)
    return states


async def search_tab(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    *,
    query: str,
    location: str | None,
    freshness: Freshness,
    locale: Locale | None = None,
    now: datetime | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The `POST /hiring-signals/search` response body: recent hiring posts for
    a typed role (and optionally a metro), with no company.

    Same provider layer and cache as `search_application`, a different question
    and a different filter (see the module docstring's "The standalone tab" and
    `hiring_signal_tab`). Every parsed post lands in exactly one bucket, in this
    precedence, so `raw_hits == rejected + duplicates + role_mismatch_hidden +
    too_old_hidden + job_seekers_hidden + echoes_hidden + shown` always holds:

    1. *rejected* -- no usable activity id, or one with a leading zero;
    2. *duplicates* -- another copy of the same post, merged;
    3. *role_mismatch_hidden* -- the role is not said in the post's title or
       opening (the hard role filter, `tab_role_fit`; a role that cannot be
       word-matched is never hidden by it);
    4. *too_old_hidden* -- older than the window, by the post time decoded from
       the activity id;
    5. *job_seekers_hidden* -- a first-person job-seeker post, judged by its
       OPENING;
    6. *echoes_hidden* -- LinkedIn's auto-generated job-share post for a listing
       the job registry already tracks (`_tab_registry_states`).

    An account with many posts in the pull is tagged `aggregator` and ranked
    below everyone else, never hidden and never counted as hidden; a post that
    names the role only after its opening is shown, ranked below the ones that
    state it. Location is NOT verified per post and nothing is filtered on it. The response holds
    structured fields only -- no snippet, no title, never the provider query --
    and no LLM is involved anywhere.
    """
    moment = now if now is not None else datetime.now(UTC)
    try:
        tq = build_tab_query(query=query, location=location, freshness=freshness, locale=locale)
    except InvalidSearchInput as e:
        raise ApiError("INVALID_INPUT", str(e)) from e

    keys = await _require_search_keys(supabase, user_id)
    pull = await _pull_hits(
        supabase, http, keys, tq.query.query, freshness, now=moment, monotonic=monotonic
    )

    analysis = analyze_pull(pull.hits)
    copies = _group_hits_by_activity_id(pull.hits)
    window_days = WINDOW_DAYS[freshness]
    role_terms = role_filter_terms(tq.role)

    role_mismatch = too_old = job_seekers = unusable = 0
    survivors: list[HiringSignal] = []
    unverified: set[str] = set()  # the role is said only after the post's opening
    for signal in analysis.signals:
        post_copies = copies[signal.activity_id]
        if signal.activity_id.startswith("0"):
            unusable += 1
            continue
        fit = tab_role_fit(post_copies, signal, role_terms)
        if fit == "mismatch":
            role_mismatch += 1
        elif is_too_old(
            visible_posted_at(signal.posted_at, now=moment), now=moment, window_days=window_days
        ):
            too_old += 1
        elif signal.species == "job_seeker" and is_job_seeker_post(post_copies):
            job_seekers += 1
        else:
            survivors.append(signal)
            if fit == "later":
                unverified.add(signal.activity_id)

    echoes = [s for s in survivors if s.species == "ats_echo"]
    registry_state = await _tab_registry_states(supabase, echoes) if echoes else {}

    shown: list[HiringSignal] = []
    echoes_hidden = 0
    for signal in survivors:
        if registry_state.get(signal.activity_id) == "matched":
            echoes_hidden += 1
        else:
            shown.append(signal)

    saved = await saved_activity_ids(supabase, user_id, None, [s.activity_id for s in shown])
    order = sorted(
        range(len(shown)),
        key=lambda i: tab_rank_key(
            aggregator=shown[i].aggregator_source,
            posted_at=visible_posted_at(shown[i].posted_at, now=moment),
            position=i,
            unverified=shown[i].activity_id in unverified,
        ),
    )
    signals = [
        {
            **_signal_fields(shown[i], now=moment),
            "registry_match": registry_state.get(shown[i].activity_id),
            "aggregator": shown[i].aggregator_source,
            "saved": shown[i].activity_id in saved,
        }
        for i in order
    ]

    return {
        "provider": pull.provider,
        "cached": pull.cached,
        "freshness": freshness.value,
        "locale": tq.locale.value,
        "query_label": tq.label,
        "signals": signals,
        "counts": {
            "raw_hits": len(pull.hits),
            "rejected": len(analysis.rejected) + unusable,
            "duplicates": analysis.duplicates_dropped,
            "role_mismatch_hidden": role_mismatch,
            "echoes_hidden": echoes_hidden,
            "job_seekers_hidden": job_seekers,
            "too_old_hidden": too_old,
            "shown": len(signals),
        },
    }
