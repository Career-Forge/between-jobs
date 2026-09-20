"""Hiring Signals P3 -- the ONLY network code in this feature: one search
call against one search-index provider, reduced to the three strings
(`url`, `title`, `snippet`) that `hiring_signals.py` knows how to read.

`hiring_signals.py` is pure on purpose (a subprocess test imports it with
sockets blocked). Everything that has to touch a network therefore lives
here, and nowhere else in the feature.

**The hard line.** Nothing in this module -- or anywhere in Hiring Signals --
ever requests a `linkedin.com` URL. The provider call sends a QUERY STRING
that says `site:linkedin.com/posts`; the request itself goes to the
provider's own fixed API host (`ydc-index.io`, `api.search.brave.com`,
`google.serper.dev`, `api.firecrawl.dev`). The URLs that come BACK in a
result are never fetched, resolved, expanded or probed: they are parsed as
strings and handed to a browser, which loads LinkedIn's own public embed
iframe client-side. `tests/test_hiring_signal_search.py` pins this with a
transport that records every request host of a full search, on a fixture
whose results are full of `linkedin.com` links, and
`tests/test_hiring_signal_architecture.py` pins it from the source side: only
this module may call a client, only through the four provider helpers, whose
endpoints are the four fixed https hosts, and a provider redirect to
linkedin.com is never followed. And it is enforced at RUNTIME: the client the
routes are handed is not the app's shared one but its own, built with
`refuse_non_provider_hosts` as a request hook, so a request to any host but the
four providers fails before it is sent.

**One copy of each provider's wire format.** The endpoint, the auth header,
the error mapping and the response shape (where the result list sits, which
entry fields carry url/title/snippet) live in `search_providers.py`, next to
Discover's job search, as small `<provider>_search` / `<provider>_entries` /
`<provider>_hit_fields` helpers; this module calls those instead of growing
a second copy. What is specific to Hiring Signals -- the freshness
parameter each provider takes, the result cap, the timeout, the defensive
reading of a body we do not trust -- is here.

**Freshness.** The feature's window (`day` / `3days` / `week`, the P2
`Freshness` enum) maps to each provider's own parameter by
`provider_freshness_request`. Where a provider has no way to say "3 days"
(or the way it has was not confirmed against the live API), the NEXT WIDER
window is requested and the caller enforces the real window itself from the
post time decoded off the activity id -- so the provider's window is only
ever a way to save results, never the source of truth for what "recent"
means. `FreshnessRequest.window_days` records how wide the requested window
really is, so the table can be tested against that promise.

**Provider results are untrusted.** A 200 whose body is not the shape we
expect, an entry whose fields are not strings, a snippet a megabyte long, a
body over `MAX_BODY_BYTES` or nested deeper than the JSON decoder will go:
all of it is handled here (dropped, blanked, clipped, or a provider failure)
rather than passed to a parser. A provider that answers too slowly -- in TOTAL,
not per chunk -- is a failure too (`CALL_TIMEOUT_SECONDS`). A body that is not
an object, or whose result container is not a list, is a provider failure --
not an empty answer, because "the provider said nothing" and "the provider
changed its format" must never look alike to the person clicking the button.

Provider-specific yield and freshness findings, measured against the real
You.com and Firecrawl APIs, are in `tests/golden/hiring_signals/
provider_responses/README.md`. Brave and Serper are implemented from their
official documentation and are covered by mocked-transport tests only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import httpx

from .errors import ApiError
from .hiring_signals import Freshness, RawSearchHit
from .search_providers import (
    brave_entries,
    brave_hit_fields,
    brave_search,
    firecrawl_entries,
    firecrawl_hit_fields,
    firecrawl_search,
    serper_entries,
    serper_hit_fields,
    serper_search,
    you_com_entries,
    you_com_hit_fields,
    you_com_search,
)

HiringProvider = Literal["you_com", "brave", "serper", "firecrawl"]

PROVIDER_ORDER: tuple[HiringProvider, ...] = ("brave", "firecrawl", "you_com", "serper")
"""Preference order. Brave runs its own index and Firecrawl search is the one
provider verified against real LinkedIn results, so they come first.
**You.com is behind them, against the original design order (You.com first),
on real data:** its index returned zero `linkedin.com` results on every
LinkedIn-directed query tried -- nine queries: the per-application query for
four real companies, and five variants of it for one of them (a bare `site:`
path query, a domain-only `site:`, a month-wide window, a three-term query, a
`linkedin` keyword instead of `site:`) -- eight of them a clean 200 with an
empty `results` object and one a single non-LinkedIn page, while three
unscoped control queries returned 45 pages between them, none from LinkedIn. It
still belongs on the list (a user whose only key is You.com should get an
honest empty answer, and coverage may change), but first place would spend a
call on a guaranteed-empty answer every time.

**Serper is LAST, as the design says ("fallback only, never primary").** It is
a Google results proxy, so it depends on a search engine's terms rather than on
an index the provider owns; that is the reason the design kept it out of first
place, and it is worth more than the price of one empty You.com call (a
fraction of a cent) for the rare user who holds both keys and no better one.
Brave and Serper could not be verified against the live API (no key exists to
test with); the order among the ones that were is the design's own. A user's
saved keys decide which of these are even tried."""

MAX_RESULTS_PER_CALL = 20
"""The most results asked of a provider in one call. One query is enough for
the per-application surface (the company name in the query is what makes it
precise), and every provider bills by call or by result on the USER'S OWN
key, so the ceiling is a cost control, not a tuning knob. 20 is also the most
Brave will return for a single call."""

MAX_PROVIDER_CALLS_PER_REQUEST = len(PROVIDER_ORDER)
"""A request makes at most ONE call per configured provider: no retry of a
failed call, no pagination, no second query. The ceiling is therefore the
number of supported providers, and it is a constant only so that adding a
provider or a retry cannot quietly raise what one click can spend -- the
service enforces it, and a test pins it."""

CALL_TIMEOUT_SECONDS = 10.0
"""The most wall-clock time one provider call may take, in TOTAL: it is passed
to httpx as its per-phase timeout AND enforced around the whole call with
`asyncio.timeout`. httpx's own timeout restarts on every chunk of a response, so
on its own a provider that answers one byte every few seconds never trips it
and holds the request (and the user's click) for as long as the body takes. This
runs on a button click; a provider that has not finished in ten seconds is
treated as failed so the next one can be tried."""

MAX_BODY_BYTES = 2_000_000
"""The largest response body that is parsed. Real answers are tens of
kilobytes; a body over this is a provider failure, never handed to the JSON
decoder."""

PROVIDER_HOSTS = frozenset(
    {"ydc-index.io", "api.search.brave.com", "google.serper.dev", "api.firecrawl.dev"}
)
"""The only hosts this feature's HTTP client may ever contact: the four
providers' own API hosts. Enforced at runtime by `refuse_non_provider_hosts`,
on top of the source-level checks in `tests/test_hiring_signal_architecture.py`."""

_MAX_URL_CHARS = 2048
_MAX_TITLE_CHARS = 512
_MAX_SNIPPET_CHARS = 600
"""Bounds on what is kept of each hit. Real titles top out near 110
characters and snippets near 250 (the golden capture). The url and title
caps are the same numbers `hiring_signals.py` treats as "too long to be
real" -- clipped ONE character past them here, so an over-long value is still
over the limit when the parser sees it and is declined there exactly as it
would have been unclipped, while never being stored at full length."""


@dataclass(frozen=True)
class FreshnessRequest:
    """One provider's parameter for a window, plus how wide that window
    REALLY is. `param` is also what the cache keys freshness on: two
    windows that map to the same parameter are the same provider call and
    share a cache row."""

    param: str
    window_days: int


WINDOW_DAYS: dict[Freshness, int] = {
    Freshness.DAY: 1,
    Freshness.THREE_DAYS: 3,
    Freshness.WEEK: 7,
}

# Static values. `3days` is absent for a provider only when the provider
# says it as a date range built from today (see `_RANGE_PARAMS`).
_STATIC_PARAMS: dict[HiringProvider, dict[Freshness, FreshnessRequest]] = {
    # You.com `freshness`: "day" | "week" | "month" | "year" | a date range.
    # `day`, `week` and the range were each confirmed live -- against a
    # non-LinkedIn control query, because its index holds no LinkedIn pages
    # (see `PROVIDER_ORDER`): the returned pages' `page_age` dates sat inside
    # the requested window every time (see the provider_responses README).
    "you_com": {
        Freshness.DAY: FreshnessRequest("day", 1),
        Freshness.WEEK: FreshnessRequest("week", 7),
    },
    # Brave `freshness`: "pd" | "pw" | "pm" | "py" | a date range. A range is
    # documented but was not confirmed live (no Brave key exists to test
    # with), so `3days` asks for the documented week instead.
    "brave": {
        Freshness.DAY: FreshnessRequest("pd", 1),
        Freshness.THREE_DAYS: FreshnessRequest("pw", 7),
        Freshness.WEEK: FreshnessRequest("pw", 7),
    },
    # Serper `tbs` is Google's own: qdr:d / qdr:w / qdr:m. `qdr:d3` (which
    # Firecrawl was confirmed to honor) is not documented for Serper and was
    # not verified against it -> the week, trimmed by the caller.
    "serper": {
        Freshness.DAY: FreshnessRequest("qdr:d", 1),
        Freshness.THREE_DAYS: FreshnessRequest("qdr:w", 7),
        Freshness.WEEK: FreshnessRequest("qdr:w", 7),
    },
    # Firecrawl `tbs` is Google's syntax; `qdr:d3` = past 3 days was
    # confirmed live (seven results, the oldest 67 hours, where `qdr:d` gave
    # one and `qdr:w` twenty).
    "firecrawl": {
        Freshness.DAY: FreshnessRequest("qdr:d", 1),
        Freshness.THREE_DAYS: FreshnessRequest("qdr:d3", 3),
        Freshness.WEEK: FreshnessRequest("qdr:w", 7),
    },
}


def _date_range(today: date, days: int) -> str:
    """`YYYY-MM-DDtoYYYY-MM-DD`, the range form You.com (and Brave) accept:
    `days` days ending today, inclusive of both ends."""
    start = today - timedelta(days=days)
    return f"{start.isoformat()}to{today.isoformat()}"


_RANGE_PARAMS: dict[tuple[HiringProvider, Freshness], Callable[[date], FreshnessRequest]] = {
    # `YYYY-MM-DDtoYYYY-MM-DD` with the start three days back: confirmed live
    # (twenty results, page ages from the start date to the day before the
    # end date).
    ("you_com", Freshness.THREE_DAYS): lambda today: FreshnessRequest(_date_range(today, 3), 3),
}
"""Windows a provider can express only as a date range built from today."""


def provider_freshness_request(
    provider: HiringProvider, freshness: Freshness, *, today: date
) -> FreshnessRequest:
    """The provider-native freshness parameter for a window. Never narrower
    than the window asked for (the caller trims to the exact window itself
    from the decoded post time); see the module docstring."""
    ranged = _RANGE_PARAMS.get((provider, freshness))
    if ranged is not None:
        return ranged(today)
    return _STATIC_PARAMS[provider][freshness]


async def refuse_non_provider_hosts(request: httpx.Request) -> None:
    """An httpx REQUEST HOOK for the client this feature is handed: refuses,
    before anything is sent, every request that is not https to one of the
    four provider hosts. It is the runtime half of the hard line (no
    server-side request to linkedin.com, ever): if a code change ever pointed a
    call somewhere else, the call would fail here instead of going out. It
    raises `httpx.RequestError`, which the provider helpers already turn into
    a `PROVIDER_UNAVAILABLE`."""
    if request.url.scheme != "https" or request.url.host not in PROVIDER_HOSTS:
        raise httpx.RequestError("refused: not a search-provider host", request=request)


# ── one call, one provider ───────────────────────────────────────────────


def _clip(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _hits_from_body(
    body: Any,
    entries_of: Callable[[Any], list[Any]],
    fields_of: Callable[[Any], tuple[str, str, str]],
) -> list[RawSearchHit]:
    """Entries -> `RawSearchHit`s, defensively. A body that is not an object,
    or whose result container is not a list, raises: that is a provider
    failure, not an empty answer. An entry that is not an object, or has no
    string url, is skipped; a title or snippet that is not a string is blank."""
    if not isinstance(body, dict):
        raise ValueError("provider body is not an object")
    entries = entries_of(body)
    if not isinstance(entries, list):
        raise ValueError("provider result container is not a list")
    hits: list[RawSearchHit] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            url, title, snippet = fields_of(entry)
        except (AttributeError, TypeError):
            continue
        if not isinstance(url, str) or not url:
            continue
        hits.append(
            RawSearchHit(
                url=url[: _MAX_URL_CHARS + 1],
                title=_clip(title, _MAX_TITLE_CHARS + 1),
                snippet=_clip(snippet, _MAX_SNIPPET_CHARS),
            )
        )
    return hits


_LABELS: dict[HiringProvider, str] = {
    "you_com": "You.com",
    "brave": "Brave",
    "serper": "Serper",
    "firecrawl": "Firecrawl",
}


async def search_provider(
    http: httpx.AsyncClient,
    provider: HiringProvider,
    *,
    api_key: str,
    query: str,
    freshness_param: str,
    max_results: int = MAX_RESULTS_PER_CALL,
    timeout: float = CALL_TIMEOUT_SECONDS,
) -> list[RawSearchHit]:
    """One search call against `provider`. Returns at most `max_results`
    hits (url/title/snippet, clipped). Every failure -- transport, a call that
    outlasts `timeout` in total, HTTP error, an oversized or unreadable body,
    unexpected shape -- surfaces as a retryable `ApiError("PROVIDER_UNAVAILABLE")`
    whose message names the provider and nothing else: never the query, a key,
    or anything the provider sent."""
    label = _LABELS[provider]
    try:
        async with asyncio.timeout(timeout):
            if provider == "you_com":
                body = await you_com_search(
                    http,
                    api_key=api_key,
                    query=query,
                    freshness=freshness_param,
                    count=max_results,
                    timeout=timeout,
                    max_body_bytes=MAX_BODY_BYTES,
                )
                hits = _hits_from_body(body, you_com_entries, you_com_hit_fields)
            elif provider == "brave":
                body = await brave_search(
                    http,
                    api_key=api_key,
                    query=query,
                    freshness=freshness_param,
                    count=max_results,
                    timeout=timeout,
                    max_body_bytes=MAX_BODY_BYTES,
                )
                hits = _hits_from_body(body, brave_entries, brave_hit_fields)
            elif provider == "serper":
                body = await serper_search(
                    http,
                    api_key=api_key,
                    query=query,
                    tbs=freshness_param,
                    num=max_results,
                    timeout=timeout,
                    max_body_bytes=MAX_BODY_BYTES,
                )
                hits = _hits_from_body(body, serper_entries, serper_hit_fields)
            else:
                body = await firecrawl_search(
                    http,
                    api_key=api_key,
                    query=query,
                    limit=max_results,
                    tbs=freshness_param,
                    timeout=timeout,
                    max_body_bytes=MAX_BODY_BYTES,
                )
                hits = _hits_from_body(body, firecrawl_entries, firecrawl_hit_fields)
    except ApiError:
        raise
    except TimeoutError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            f"{label} did not answer in time.",
            retryable=True,
        ) from e
    except (ValueError, AttributeError, TypeError, RecursionError) as e:
        # Unreadable JSON (json.JSONDecodeError is a ValueError; a hostile,
        # deeply nested body is a RecursionError), or a body that did not
        # have the shape the entry helpers index into.
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            f"{label} returned a response this feature could not read.",
            retryable=True,
        ) from e
    return hits[:max_results]
