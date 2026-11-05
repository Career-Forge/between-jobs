"""Check this link: is a saved job still on the employer's own job board?

One press of a button makes one check of one saved link and answers with one of three states,
always with the reason:

- `still_listed`: the board's own listing contains the job.
- `gone`: the board loaded completely, the job is not on it, and a second signal agrees. It says
  the posting is no longer on the company's board. It does not say the person was turned down,
  that the job was filled, or that anything should be archived: that is the person's call.
- `cant_tell`: everything else. A board that is not where we expected, an empty listing, two
  signals that disagree, a site that declined or asked us to slow down, rules that keep automated
  tools out, a link we do not read: each is its own reason (THE REASONS, below) and none is ever
  guessed into a yes or a no. Unknown is labelled as unknown.

Deterministic. No model is involved anywhere in the path, and nothing is stored: not a table, not
a column, not a log line with more than the platform, the state, the reason code and how long it
took. It is read-only and user-initiated; nothing here runs on a schedule.

THE SERVER NEVER FETCHES THE PERSON'S URL. `classify_link` parses the saved link with strict
patterns into a platform, a board and an id, and the requests are built from those pieces against
a closed list of hosts (link_fetch.py enforces the list again at request time). A link that is not
a plain job link on a covered platform (a company's own site, `boards.greenhouse.io.evil.example`,
a link with a userinfo part or a port, an IP address) is `cant_tell` and causes no request at all.

THE DECISION RULE, per platform (all of it from the platform's own public endpoints):

- Greenhouse: the board's job list. In it -> still listed. A 404 on the list -> the board is
  missing or moved (a missing board and a missing job answer with the same body, so a job 404
  alone never proves anything). Absent from a non-empty list -> the single-job endpoint: 404 ->
  gone, 200 -> the signals disagree.
- Lever: the same, on the site's postings list (and the EU region's API when the global one has no
  such site) and the single posting.
- Ashby: the same, on the organization's job board and the hosted job page. A job that is on the
  list but not listed (reachable by direct link only) is `unlisted_direct_link`.
- Workday: there is no list to read, so the tenant's own job-detail endpoint decides. Found and
  open -> still listed; found but not open for applications, or refused with the permission-denied
  error that a closed posting answers with -> gone; a 404 -> gone only if the tenant's own search
  also lacks the requisition; a bare 403 -> blocked.

A saved link is never followed: a posting URL of a company that has its own careers site redirects
to that site, where a closed job reads as alive.

NOT COVERED ON PURPOSE: SmartRecruiters and Workable are `not_covered`. Their robots rules and
terms of use do not give a clear allowance for automated reads of their postings, so as a matter
of policy this feature does not read them. That is a choice, not a technical gap. LinkedIn,
Indeed, Glassdoor and every host in `scrape_denylist` are `denylisted`; a link on a company's own
domain is `company_page`.

THE REASONS. The codes a client is given (the web app has the words for them, in
web/src/lib/linkCheck.ts):

    cant_tell  board_missing_or_moved  empty_listing  signals_disagree  blocked  rate_limited
               robots_disallowed  robots_unavailable  not_covered  denylisted  company_page
               no_link  unlisted_direct_link  timeout  network
    also       detail_404_unconfirmed (Workday: a 404 the tenant's search could not confirm),
               unreadable_answer (a 200 that is not the shape the platform documents) and
               status_NNN (any other status). The web app words these as an answer it cannot use.

POLITENESS (robots.txt, one request a second, cool-downs, the User-Agent, bounds) lives in
link_fetch.py. What this module adds: at most two requests for a board's list and one more to
corroborate, a five-minute memory of a board's parsed ids (so five cards at one company do one
list fetch), three checks at a time, and twenty-five seconds for the whole check once it has a
slot. A check that cannot get a slot within five seconds is not a `cant_tell` (nothing was asked of
the company, so nothing can be said about its site): `check` raises `Busy` and the route answers
503 `link_check_busy`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import weakref
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from .errors import ApiError
from .link_fetch import WORKDAY_HOST, PoliteFetcher, Undecided
from .scrape_denylist import is_denied_scrape_host

logger = logging.getLogger(__name__)

State = Literal["still_listed", "gone", "cant_tell"]
Ats = Literal["greenhouse", "lever", "ashby", "workday", "other"]

LINK_CHECK_DISABLED = "link_check_disabled"
LINK_CHECK_BUSY = "link_check_busy"

MAX_CONCURRENT_CHECKS = 3
SLOT_WAIT_SECONDS = 5.0
"""How long a check waits for one of the MAX_CONCURRENT_CHECKS slots before it is turned away as
busy. The wait is not part of the check's own budget: nothing has been asked of an employer yet."""
CHECK_BUDGET_SECONDS = 25.0
BOARD_CACHE_SECONDS = 300.0
BOARD_CACHE_MAX_ENTRIES = 64
MAX_LIST_REQUESTS = 2
"""The most requests one check makes for a board's list: Lever has two regions, nothing else has
more than one. A corroborating request is always exactly one more."""
WORKDAY_SEARCH_LIMIT = 20
_PARSE_IN_THREAD_BYTES = 1024 * 1024
"""A body this large is parsed off the event loop; a big Ashby board is megabytes of JSON."""

_MAX_URL_CHARS = 2048
_UNSAFE_CHARS = re.compile(r"[\x00-\x20\x7f]")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_DIGITS = re.compile(r"[0-9]{1,18}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LOCALE = re.compile(r"[a-z]{2}-[A-Za-z]{2}")
_PATH_SEGMENT = re.compile(r"(?!\.+\Z)[A-Za-z0-9._~-]{1,200}")
_REQUISITION = re.compile(r"(?P<token>[A-Za-z]{1,4}-?[0-9]{4,12}|[0-9]{5,12})(?:-[0-9]{1,3})?")

_GREENHOUSE_HOSTS = frozenset(
    {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
)
_LEVER_HOSTS = frozenset({"jobs.lever.co", "jobs.eu.lever.co"})
_ASHBY_HOST = "jobs.ashbyhq.com"
_NOT_COVERED_DOMAINS = ("smartrecruiters.com", "workable.com")
_ALSO_DENIED_DOMAINS = ("indeed.com", "glassdoor.com")
"""Denied on top of `scrape_denylist` (LinkedIn and X). Matched like it: the domain and every
subdomain of it, never a bare substring."""


# -- what a saved link means -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """A saved link parsed into the pieces a request is built from."""

    ats: Literal["greenhouse", "lever", "ashby", "workday"]
    board: str
    """Greenhouse board token, Lever site, Ashby organization, Workday tenant."""
    job: str
    """Greenhouse's numeric id, Lever's and Ashby's UUID in lower case, Workday's path after
    `/job/`."""
    host: str = ""
    """Lever: the host the link was on (which region to ask first). Workday: the tenant's own
    host, which every request to it goes to."""
    site: str = ""
    """Workday's career site."""


@dataclass(frozen=True, slots=True)
class Refusal:
    """A link the check will not act on, and why. No request is made."""

    ats: Ats
    reason: str


def _segments(path: str) -> list[str] | None:
    """The path's segments, or None for a path that is empty, does not start with a slash or has
    an empty segment in it. One trailing slash is allowed."""
    if not path.startswith("/"):
        return None
    parts = path[1:].split("/")
    if parts and parts[-1] == "":
        parts.pop()
    return None if not parts or "" in parts else parts


def _on_domain(host: str, domains: tuple[str, ...]) -> bool:
    """`host` is the domain or a subdomain of one of `domains`. A trailing dot (a fully qualified
    name) is the same site, so it is ignored here; the exact-host checks for the platforms we read
    stay strict about it."""
    host = host.removesuffix(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _unreadable(ats: Ats) -> Refusal:
    """A link on a platform we cover whose path is not a job we can name."""
    return Refusal(ats, "board_missing_or_moved")


def classify_link(url: str | None) -> Target | Refusal:
    """Parses a saved link, never fetching it. A `Target` only for a link whose host is EXACTLY a
    covered platform's job-board host (or a Workday tenant's), with no userinfo and no port but 443,
    and whose path is that platform's job path with a board and an id of the right shape. Every
    other link is a `Refusal` carrying the reason to give."""
    text = (url or "").strip()
    if not text or len(text) > _MAX_URL_CHARS or _UNSAFE_CHARS.search(text):
        return Refusal("other", "no_link")
    if is_denied_scrape_host(text):
        return Refusal("other", "denylisted")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return Refusal("other", "no_link")
    host = parts.hostname
    if parts.scheme.lower() not in ("http", "https") or not host:
        return Refusal("other", "no_link")
    if _on_domain(host, _ALSO_DENIED_DOMAINS):
        return Refusal("other", "denylisted")
    if parts.username is not None or parts.password is not None or port not in (None, 443):
        return Refusal("other", "company_page")
    path = parts.path
    if host in _GREENHOUSE_HOSTS:
        return _greenhouse_target(path)
    if host in _LEVER_HOSTS:
        return _lever_target(host, path)
    if host == _ASHBY_HOST:
        return _ashby_target(path)
    if WORKDAY_HOST.fullmatch(host):
        return _workday_target(host, path)
    if _on_domain(host, _NOT_COVERED_DOMAINS):
        return Refusal("other", "not_covered")
    return Refusal("other", "company_page")


def _greenhouse_target(path: str) -> Target | Refusal:
    seg = _segments(path)
    if (
        seg is None
        or len(seg) != 3
        or seg[1] != "jobs"
        or not _SLUG.fullmatch(seg[0])
        or not _DIGITS.fullmatch(seg[2])
    ):
        return _unreadable("greenhouse")
    return Target("greenhouse", seg[0], str(int(seg[2])))


def _lever_target(host: str, path: str) -> Target | Refusal:
    seg = _segments(path)
    if seg is not None and len(seg) == 3 and seg[2] == "apply":
        seg = seg[:2]
    if seg is None or len(seg) != 2 or not _SLUG.fullmatch(seg[0]) or not _UUID.fullmatch(seg[1]):
        return _unreadable("lever")
    return Target("lever", seg[0], seg[1].lower(), host=host)


def _ashby_target(path: str) -> Target | Refusal:
    seg = _segments(path)
    if seg is not None and len(seg) == 3 and seg[2] == "application":
        seg = seg[:2]
    if seg is None or len(seg) != 2 or not _SLUG.fullmatch(seg[0]) or not _UUID.fullmatch(seg[1]):
        return _unreadable("ashby")
    return Target("ashby", seg[0], seg[1].lower())


def _workday_target(host: str, path: str) -> Target | Refusal:
    """`/[locale/]{site}/job/{location...}/{title_REQ}`, optionally ending in `/apply`."""
    seg = _segments(path)
    if seg is not None and seg and _LOCALE.fullmatch(seg[0]):
        seg = seg[1:]
    if seg is None or len(seg) < 3 or seg[1] != "job" or not _SLUG.fullmatch(seg[0]):
        return _unreadable("workday")
    slug = seg[2:]
    for index, part in enumerate(slug):
        if index > 0 and part.lower() == "apply":
            slug = slug[:index]
            break
    if len(slug) > 4 or not all(_PATH_SEGMENT.fullmatch(part) for part in slug):
        return _unreadable("workday")
    return Target("workday", host.split(".", 1)[0], "/".join(slug), host=host, site=seg[0])


def requisition_token(slug: str) -> str | None:
    """The requisition id at the end of a Workday job slug (`Title_JR1234567`, `Title_R-12345`,
    `Title_JR1234567-1`), or None when the last segment does not end in one we recognize. A bare
    number needs five digits (`Title_2024` is a year, not a requisition). A search for anything
    else could not tell a closed job from a mis-read one."""
    last = slug.rsplit("/", 1)[-1]
    if "_" not in last:
        return None
    match = _REQUISITION.fullmatch(last.rsplit("_", 1)[1])
    return match.group("token") if match else None


# -- the answer -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    state: State
    reason: str


STILL_LISTED = Decision("still_listed", "still_listed")
GONE = Decision("gone", "gone")


@dataclass(frozen=True, slots=True)
class LinkCheckResult:
    state: State
    reason: str
    ats: Ats
    checked_at: str
    """ISO 8601, UTC."""

    def as_body(self) -> dict[str, str]:
        return {
            "state": self.state,
            "reason": self.reason,
            "ats": self.ats,
            "checked_at": self.checked_at,
        }


def link_check_enabled() -> bool:
    """`DISABLE_LINK_CHECK` (any non-empty value, like the worker switches) turns the check off. It
    is read on every request: nothing is started or stopped, the route just says no."""
    return not os.environ.get("DISABLE_LINK_CHECK")


def disabled_error() -> ApiError:
    """The 503 the route answers with while the switch is on. PROVIDER_UNAVAILABLE is the one code
    that is a 503; `details.reason` is what tells a client it is this switch and not an outage."""
    return ApiError(
        "PROVIDER_UNAVAILABLE",
        "Checking a posting is switched off on this server.",
        details={"reason": LINK_CHECK_DISABLED},
    )


class Busy(Exception):
    """Every slot stayed taken for SLOT_WAIT_SECONDS, so nothing was asked of any employer. Not an
    answer about a posting: the route turns it into a 503 (`busy_error`)."""


def busy_error() -> ApiError:
    """The 503 the route answers with when the check is turned away; `details.reason` tells it from
    the switch and from an outage, and the message is what the web app shows."""
    return ApiError(
        "PROVIDER_UNAVAILABLE",
        "Too many postings are being checked right now. Try again in a moment.",
        details={"reason": LINK_CHECK_BUSY},
    )


def _status_reason(status: int) -> str:
    if status in (401, 403):
        return "blocked"
    if status == 429:
        return "rate_limited"
    return f"status_{status}"


async def _parse_json(body: bytes) -> Any:
    try:
        if len(body) > _PARSE_IN_THREAD_BYTES:
            return await asyncio.to_thread(json.loads, body)
        return json.loads(body)
    except (ValueError, RecursionError):
        raise Undecided("unreadable_answer") from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class _Board:
    ids: frozenset[str]
    unlisted: frozenset[str] = frozenset()
    api_host: str = ""


@dataclass(frozen=True, slots=True)
class _CachedBoard:
    board: _Board
    expires_at: float


# -- the check ---------------------------------------------------------------------------------


class LinkChecker:
    """Checks saved links against the employers' own boards. One instance per process (it is
    created with the app and kept on `app.state`): it owns the concurrency limit and the board
    cache, and its fetcher owns the pacing and the cool-downs. `clock` and `now` are for tests."""

    def __init__(
        self,
        fetcher: PoliteFetcher,
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.fetcher = fetcher
        self._clock = clock
        self._now = now
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
        self._boards: OrderedDict[tuple[str, str, str], _CachedBoard] = OrderedDict()
        self._board_locks: weakref.WeakValueDictionary[tuple[str, str, str], asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def aclose(self) -> None:
        await self.fetcher.aclose()

    async def check(self, url: str | None) -> LinkCheckResult:
        started = self._clock()
        target = classify_link(url)
        if isinstance(target, Refusal):
            ats: Ats = target.ats
            decision = Decision("cant_tell", target.reason)
        else:
            ats = target.ats
            try:
                decision = await self._decide(target)
            except Busy:
                logger.info(
                    "link check busy",
                    extra={
                        "ctx": {
                            "ats": ats,
                            "duration_ms": int((self._clock() - started) * 1000),
                        }
                    },
                )
                raise
        result = LinkCheckResult(
            decision.state, decision.reason, ats, self._now().isoformat(timespec="seconds")
        )
        # The one line a check writes: what was decided, never what was asked or what came back.
        logger.info(
            "link check",
            extra={
                "ctx": {
                    "ats": ats,
                    "state": result.state,
                    "reason": result.reason,
                    "duration_ms": int((self._clock() - started) * 1000),
                }
            },
        )
        return result

    async def _decide(self, target: Target) -> Decision:
        try:
            async with asyncio.timeout(SLOT_WAIT_SECONDS):
                await self._slots.acquire()
        except TimeoutError:
            raise Busy from None
        try:
            async with asyncio.timeout(CHECK_BUDGET_SECONDS):
                if target.ats == "greenhouse":
                    return await self._greenhouse(target)
                if target.ats == "lever":
                    return await self._lever(target)
                if target.ats == "ashby":
                    return await self._ashby(target)
                return await self._workday(target)
        except Undecided as e:
            return Decision("cant_tell", e.reason)
        except TimeoutError:
            return Decision("cant_tell", "timeout")
        finally:
            self._slots.release()

    # -- the boards' lists

    async def _board(self, target: Target, load: Callable[[Target], Awaitable[_Board]]) -> _Board:
        """The board's parsed ids: from the five-minute memory when it has them, else `load`.
        Only a non-empty list is remembered, so a board caught empty for a moment is not called
        empty for five minutes. Checks racing for one board wait for the first one's answer."""
        # Lever's regions are separate worlds that can share a site name, so the link's host is
        # part of the key there.
        key = (target.ats, target.board, target.host if target.ats == "lever" else "")
        lock = self._board_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._boards.get(key)
            if cached is not None and cached.expires_at > self._clock():
                self._boards.move_to_end(key)
                return cached.board
            board = await load(target)
            if board.ids:
                self._boards[key] = _CachedBoard(board, self._clock() + BOARD_CACHE_SECONDS)
                self._boards.move_to_end(key)
                while len(self._boards) > BOARD_CACHE_MAX_ENTRIES:
                    self._boards.popitem(last=False)
            else:
                self._boards.pop(key, None)
            return board

    async def _gone_if_not_found(self, url: str) -> Decision:
        """The second signal for a job the list did not have: its own address says 404 (gone), or
        says anything else (never a guess). The body is not read."""
        answer = await self.fetcher.fetch("GET", url, read_body=False)
        if answer.status == 404:
            return GONE
        if answer.status == 200:
            raise Undecided("signals_disagree")
        raise Undecided(_status_reason(answer.status))

    # -- Greenhouse

    async def _greenhouse_board(self, target: Target) -> _Board:
        answer = await self.fetcher.fetch(
            "GET", f"https://boards-api.greenhouse.io/v1/boards/{target.board}/jobs"
        )
        if answer.status == 404:
            raise Undecided("board_missing_or_moved")
        if answer.status != 200:
            raise Undecided(_status_reason(answer.status))
        data = await _parse_json(answer.body)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise Undecided("unreadable_answer")
        ids = frozenset(str(j["id"]) for j in jobs if isinstance(j, dict) and _is_int(j.get("id")))
        if jobs and not ids:
            raise Undecided("unreadable_answer")
        return _Board(ids, api_host="boards-api.greenhouse.io")

    async def _greenhouse(self, target: Target) -> Decision:
        board = await self._board(target, self._greenhouse_board)
        if not board.ids:
            raise Undecided("empty_listing")
        if target.job in board.ids:
            return STILL_LISTED
        return await self._gone_if_not_found(
            f"https://boards-api.greenhouse.io/v1/boards/{target.board}/jobs/{target.job}"
        )

    # -- Lever

    async def _lever_board(self, target: Target) -> _Board:
        hosts = ("api.lever.co", "api.eu.lever.co")
        if target.host == "jobs.eu.lever.co":
            hosts = hosts[::-1]
        for host in hosts[:MAX_LIST_REQUESTS]:
            answer = await self.fetcher.fetch(
                "GET", f"https://{host}/v0/postings/{target.board}?mode=json"
            )
            if answer.status == 404:
                continue
            if answer.status != 200:
                raise Undecided(_status_reason(answer.status))
            data = await _parse_json(answer.body)
            if not isinstance(data, list):
                raise Undecided("unreadable_answer")
            ids = frozenset(
                p["id"].lower()
                for p in data
                if isinstance(p, dict) and isinstance(p.get("id"), str)
            )
            if data and not ids:
                raise Undecided("unreadable_answer")
            return _Board(ids, api_host=host)
        raise Undecided("board_missing_or_moved")

    async def _lever(self, target: Target) -> Decision:
        board = await self._board(target, self._lever_board)
        if not board.ids:
            raise Undecided("empty_listing")
        if target.job in board.ids:
            return STILL_LISTED
        return await self._gone_if_not_found(
            f"https://{board.api_host}/v0/postings/{target.board}/{target.job}?mode=json"
        )

    # -- Ashby

    async def _ashby_board(self, target: Target) -> _Board:
        answer = await self.fetcher.fetch(
            "GET", f"https://api.ashbyhq.com/posting-api/job-board/{target.board}"
        )
        if answer.status == 404:
            raise Undecided("board_missing_or_moved")
        if answer.status != 200:
            raise Undecided(_status_reason(answer.status))
        data = await _parse_json(answer.body)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise Undecided("unreadable_answer")
        listed = [j for j in jobs if isinstance(j, dict) and isinstance(j.get("id"), str)]
        if jobs and not listed:
            raise Undecided("unreadable_answer")
        return _Board(
            frozenset(j["id"].lower() for j in listed),
            frozenset(j["id"].lower() for j in listed if j.get("isListed") is False),
            api_host="api.ashbyhq.com",
        )

    async def _ashby(self, target: Target) -> Decision:
        board = await self._board(target, self._ashby_board)
        if not board.ids:
            raise Undecided("empty_listing")
        if target.job in board.unlisted:
            raise Undecided("unlisted_direct_link")
        if target.job in board.ids:
            return STILL_LISTED
        return await self._gone_if_not_found(
            f"https://jobs.ashbyhq.com/{target.board}/{target.job}"
        )

    # -- Workday

    async def _workday(self, target: Target) -> Decision:
        base = f"https://{target.host}/wday/cxs/{target.board}/{target.site}"
        answer = await self.fetcher.fetch("GET", f"{base}/job/{target.job}")
        if answer.status == 200:
            data = await _parse_json(answer.body)
            info = data.get("jobPostingInfo") if isinstance(data, dict) else None
            if (
                not isinstance(info, dict)
                or not info
                or not _is_posting_asked_for(info, target.job)
            ):
                raise Undecided("unreadable_answer")
            return GONE if info.get("canApply") is False else STILL_LISTED
        if answer.status == 403:
            if _is_permission_denied(answer.body):
                return GONE
            raise Undecided("blocked")
        if answer.status == 404:
            return await self._workday_search_agrees(base, target.job)
        raise Undecided(_status_reason(answer.status))

    async def _workday_search_agrees(self, base: str, slug: str) -> Decision:
        """A detail 404 can also mean a moved address, so it counts only when the tenant's own
        search is up and has no posting carrying the requisition id."""
        token = requisition_token(slug)
        if token is None:
            raise Undecided("detail_404_unconfirmed")
        answer = await self.fetcher.fetch(
            "POST",
            f"{base}/jobs",
            json_body={
                "appliedFacets": {},
                "limit": WORKDAY_SEARCH_LIMIT,
                "offset": 0,
                "searchText": token,
            },
        )
        data: Any = None
        if answer.status == 200:
            try:
                data = await _parse_json(answer.body)
            except Undecided:
                data = None
        postings = data.get("jobPostings") if isinstance(data, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if not isinstance(postings, list) or not _is_int(total):
            raise Undecided("detail_404_unconfirmed")
        if any(token.lower() in json.dumps(posting).lower() for posting in postings):
            raise Undecided("signals_disagree")
        if total != len(postings):
            # absent from one page of a longer result, or from an answer that contradicts itself
            raise Undecided("detail_404_unconfirmed")
        return GONE


def _is_posting_asked_for(info: dict[str, Any], slug: str) -> bool:
    """False only when the answer names a requisition and it is not the one in the link, so an
    answer for some other job is never read as this job's state. An answer that names none, or a
    link with none to compare, is taken as asked for."""
    token = requisition_token(slug)
    named = info.get("jobReqId")
    if token is None or not isinstance(named, str) or not named.strip():
        return True
    return _alphanumerics(token) in _alphanumerics(named)


def _alphanumerics(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_permission_denied(body: bytes) -> bool:
    """Workday answers a closed posting's detail with 403 and error code S22, "permission
    denied". A 403 that is anything else (a firewall's page, another error) is a refusal."""
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return False
    if not isinstance(data, dict):
        return False
    message = data.get("message")
    return data.get("errorCode") == "S22" or (
        isinstance(message, str) and "permission denied" in message.lower()
    )
