"""How the server talks to an employer's job board: the polite half of the link check
(link_check.py is the deciding half).

The check reads somebody else's servers on behalf of a person who pressed a button, so this module
is where every promise about manners is kept, in one place:

- THE HOST LIST IS CLOSED, and it is enforced where it cannot be forgotten. `is_allowed_host` names
  the only hosts this feature may ever contact (the five fixed job-board API hosts and a Workday
  tenant's own host), and `refuse_hosts_outside_allowlist` is an httpx REQUEST HOOK on the client
  the feature is handed (`build_link_check_client`), so a request to anywhere else -- or over
  plain http, to a port, with userinfo, or to an IP literal -- fails before it is sent, whatever
  the code that built the address got wrong. The same pattern as Hiring signals' client
  (hiring_signal_search.refuse_non_provider_hosts), and for the same reason. The addresses are
  also built from a parsed link, never taken from one (link_check.classify_link); this is the
  second lock.
- NO REDIRECT IS FOLLOWED. A 3xx is an answer, reported as what it is. A hosted posting URL that
  redirects to a company's own page would otherwise read as "alive" for a job that has closed.
- robots.txt IS READ AND OBEYED (RFC 9309) for every host before any other request to it, cached for
  six hours. A 4xx means no restrictions; a 5xx, a redirect or a network failure means the rules
  cannot be known, so nothing is requested (`robots_unavailable`, cached for five minutes so a
  host that is down is not asked again at once). A disallowed path is `robots_disallowed`. The
  matching is this module's own and follows the RFC (the longest matching rule wins, `Allow` wins a
  tie, `*` and `$` in patterns, groups of the same agent combine), because the standard library's
  parser takes the first match and has no wildcards.
- AT MOST ONE REQUEST A SECOND TO A HOST. In-process (`_pace` hands out time slots), which is
  enough while the API runs as one worker; a second worker would need a shared pacer.
- A HOST THAT ASKS US TO STOP IS LEFT ALONE. A 429, or any answer carrying a Retry-After header,
  stops every request to that host for ten minutes and answers `rate_limited` without a request.
  Nothing is retried, ever.
- EVERYTHING IS BOUNDED. 15 seconds for a request, in total and not per read; the body is read as a
  stream and abandoned at 16 MiB (`provider_http._read_bounded`, so a compressed bomb is stopped
  too); a status that is all a caller needs is read without reading the body at all.
- IT SAYS WHO IT IS. One User-Agent, with an address a site operator can write to.
- IT KEEPS NO COOKIES. The client is shared by every person's checks, so a cookie a site sets
  is dropped and never sent back.

Nothing here keeps anything of an answer: the robots rules and the cool-downs are the only state,
and both are in memory.

`fetch` never raises for what a site answers. It raises `Undecided`, which carries the reason code
the check reports (`rate_limited`, `robots_disallowed`, `robots_unavailable`, `timeout`,
`network`), and returns every other status for the caller to judge.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.cookiejar import DefaultCookiePolicy
from typing import Any

import httpx

from .provider_http import UpstreamFailure, _read_bounded

logger = logging.getLogger(__name__)

USER_AGENT = "BetweenJobsLinkCheck/1.0 (+https://between-jobs.tech; support@between-jobs.tech)"
ROBOTS_PRODUCT_TOKEN = "BetweenJobsLinkCheck"
"""What a robots.txt group names to address this crawler (RFC 9309 matches on the product token)."""

MAX_BODY_BYTES = 16 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 6.0
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
HOST_COOLDOWN_SECONDS = 600.0
ROBOTS_TTL_SECONDS = 6 * 3600.0
ROBOTS_UNAVAILABLE_TTL_SECONDS = 300.0
ROBOTS_MAX_PARSED_BYTES = 512 * 1024
"""RFC 9309 asks a crawler to parse at least 500 KiB of a robots.txt; the rest is ignored."""

_PACER_MAX_HOSTS = 256
_REMEMBERED_HOSTS_MAX = 512
"""The most hosts whose robots rules (or cool-down) are kept; the oldest go first."""

STATIC_HOSTS = frozenset(
    {
        "boards-api.greenhouse.io",
        "api.lever.co",
        "api.eu.lever.co",
        "api.ashbyhq.com",
        "jobs.ashbyhq.com",
    }
)
WORKDAY_HOST = re.compile(r"[a-z0-9][a-z0-9-]*\.wd[0-9]+\.myworkdayjobs\.com")
"""A Workday tenant's own host. Matched with `fullmatch` on the lower-case host httpx and urlsplit
hand over, so `evilmyworkdayjobs.com` and `x.myworkdayjobs.com.evil.example` are not it."""


def is_allowed_host(host: str) -> bool:
    return host in STATIC_HOSTS or WORKDAY_HOST.fullmatch(host) is not None


class HostRefused(httpx.RequestError):
    """A request the allowlist hook refused. A bug in this feature if it ever happens: every
    address is built from the allowlist."""


async def refuse_hosts_outside_allowlist(request: httpx.Request) -> None:
    """An httpx REQUEST HOOK for the client this feature is handed: refuses, before anything is
    sent, every request that is not https to the standard port of an allowed host, with no
    userinfo. An IP literal is not an allowed host, so it is refused too."""
    url = request.url
    if (
        url.scheme != "https"
        or url.port is not None
        or url.userinfo
        or not is_allowed_host(url.host)
    ):
        raise HostRefused("refused: not an allowed job-board host", request=request)


def build_link_check_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The one client the check uses: allowlisted by `refuse_hosts_outside_allowlist`, never
    following a redirect, and always sending its User-Agent. A separate client from the app's
    shared one, which legitimately talks to many other hosts. `transport` is for tests."""
    client = httpx.AsyncClient(
        transport=transport,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.8"},
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        follow_redirects=False,
        event_hooks={"request": [refuse_hosts_outside_allowlist]},
    )
    # One client serves everyone's checks: a cookie a site sets must not come back on the next
    # person's request. An empty allow-list accepts and returns none.
    client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return client


class Undecided(Exception):
    """The check cannot go on and must answer `cant_tell` with `reason`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Answer:
    status: int
    body: bytes
    """Empty when the caller asked not to read it."""


# -- robots.txt (RFC 9309) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RobotsRules:
    """The rules that apply to this crawler: (is it an Allow, the pattern), in file order."""

    rules: tuple[tuple[bool, str], ...] = ()

    def allows(self, path: str) -> bool:
        """`path` is the path and query of the request. The longest matching pattern decides, an
        Allow wins a tie, and no matching rule means allowed. An empty pattern matches nothing
        (an empty Disallow means "nothing is off limits")."""
        longest = -1
        allowed = True
        for allow, pattern in self.rules:
            if not pattern or not _matches(pattern, path):
                continue
            if len(pattern) > longest or (len(pattern) == longest and allow):
                longest, allowed = len(pattern), allow
        return allowed


def _matches(pattern: str, path: str) -> bool:
    """RFC 9309 pattern matching: a prefix match, `*` stands for any run of characters and a
    trailing `$` ends the match. Done piece by piece rather than by compiling a regular
    expression, because a hostile file with many stars must not be able to make it slow."""
    anchored = pattern.endswith("$")
    pieces = (pattern[:-1] if anchored else pattern).split("*")
    if not path.startswith(pieces[0]):
        return False
    position = len(pieces[0])
    if len(pieces) == 1:
        return position == len(path) if anchored else True
    for piece in pieces[1:-1]:
        found = path.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    last = pieces[-1]
    if anchored:
        return len(path) - len(last) >= position and path.endswith(last)
    return path.find(last, position) >= 0


def parse_robots(text: str) -> RobotsRules:
    """The rules for this crawler out of a robots.txt body: the groups that name its product
    token, combined, or else the groups for `*`, combined. Lines it does not understand are
    ignored, as the RFC asks."""
    agents_by_group: list[list[str]] = []
    rules_by_group: list[list[tuple[bool, str]]] = []
    in_rules = False
    for raw in text.removeprefix("﻿")[:ROBOTS_MAX_PARSED_BYTES].splitlines():
        name, separator, value = raw.split("#", 1)[0].partition(":")
        if not separator:
            continue
        name, value = name.strip().lower(), value.strip()
        if name == "user-agent":
            if not agents_by_group or in_rules:
                agents_by_group.append([])
                rules_by_group.append([])
                in_rules = False
            # `BetweenJobsLinkCheck/1.0` names the same crawler as `BetweenJobsLinkCheck`
            agents_by_group[-1].append(value.split("/", 1)[0].strip().lower())
        elif name in ("allow", "disallow") and agents_by_group:
            in_rules = True
            rules_by_group[-1].append((name == "allow", value))
    named = [
        i for i, agents in enumerate(agents_by_group) if ROBOTS_PRODUCT_TOKEN.lower() in agents
    ]
    chosen = named or [i for i, agents in enumerate(agents_by_group) if "*" in agents]
    return RobotsRules(tuple(rule for i in chosen for rule in rules_by_group[i]))


# -- the fetcher -------------------------------------------------------------------------------


class PoliteFetcher:
    """One polite request at a time to an allowed host; see the module docstring for the rules.

    One instance serves the whole process (it is created with the app and kept on `app.state`),
    because the pacing, the cool-downs and the robots cache only mean something if every check
    shares them. `clock` and `sleep` are for tests."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        max_body_bytes: int = MAX_BODY_BYTES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.http = http
        self._max_body_bytes = max_body_bytes
        self._clock = clock
        self._sleep = sleep
        self._pacing = asyncio.Lock()
        self._next_slot: dict[str, float] = {}
        self._cooldowns: dict[str, float] = {}
        self._robots: dict[str, tuple[float, RobotsRules | None]] = {}
        self._robots_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def fetch(
        self, method: str, url: str, *, json_body: Any = None, read_body: bool = True
    ) -> Answer:
        """One request to `url`, after the host's cool-down and robots.txt have been checked.
        Raises `Undecided` for anything that stops it; otherwise returns the status and, unless
        `read_body` is false, the (bounded) body."""
        target = httpx.URL(url)
        self._refuse_while_cooling(target.host)
        rules = await self._robots_for(target.host)
        if not rules.allows(target.raw_path.decode("ascii", "replace")):
            raise Undecided("robots_disallowed")
        return await self._send(method, url, json_body=json_body, read_body=read_body)

    # -- robots.txt

    async def _robots_for(self, host: str) -> RobotsRules:
        lock = self._robots_locks.setdefault(host, asyncio.Lock())
        async with lock:
            cached = self._robots.get(host)
            if cached is None or cached[0] <= self._clock():
                cached = await self._load_robots(host)
                self._robots.pop(host, None)
                self._robots[host] = cached
                self._forget_beyond_the_limit(self._robots)
        if cached[1] is None:
            raise Undecided("robots_unavailable")
        return cached[1]

    async def _load_robots(self, host: str) -> tuple[float, RobotsRules | None]:
        now = self._clock()
        try:
            answer = await self._send("GET", f"https://{host}/robots.txt")
        except Undecided as e:
            if e.reason == "rate_limited":
                raise
            return now + ROBOTS_UNAVAILABLE_TTL_SECONDS, None
        if 200 <= answer.status < 300:
            return now + ROBOTS_TTL_SECONDS, parse_robots(answer.body.decode("utf-8", "replace"))
        if 400 <= answer.status < 500:
            return now + ROBOTS_TTL_SECONDS, RobotsRules()
        return now + ROBOTS_UNAVAILABLE_TTL_SECONDS, None

    # -- pacing, cool-downs and the request itself

    def _refuse_while_cooling(self, host: str) -> None:
        until = self._cooldowns.get(host)
        if until is None:
            return
        if self._clock() < until:
            raise Undecided("rate_limited")
        del self._cooldowns[host]

    def _cool_down(self, host: str) -> None:
        self._cooldowns[host] = self._clock() + HOST_COOLDOWN_SECONDS
        self._forget_beyond_the_limit(self._cooldowns)
        logger.warning("job board asked us to slow down", extra={"ctx": {"host": host}})

    @staticmethod
    def _forget_beyond_the_limit(remembered: dict[str, Any]) -> None:
        while len(remembered) > _REMEMBERED_HOSTS_MAX:
            del remembered[next(iter(remembered))]

    async def _pace(self, host: str) -> None:
        """Waits for this host's next one-second slot. Slots are handed out under a lock but
        waited for outside it, so checks racing for one host queue up one second apart and a
        wait on one host never delays another."""
        async with self._pacing:
            now = self._clock()
            if len(self._next_slot) >= _PACER_MAX_HOSTS:
                self._next_slot = {h: s for h, s in self._next_slot.items() if s > now}
            slot = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = slot + MIN_SECONDS_BETWEEN_REQUESTS
        if slot > now:
            await self._sleep(slot - now)

    async def _send(
        self, method: str, url: str, *, json_body: Any = None, read_body: bool = True
    ) -> Answer:
        host = httpx.URL(url).host
        # Twice, because another request to this host can be answered 429 while this one waits
        # for its slot, and a request already queued must stop too.
        self._refuse_while_cooling(host)
        await self._pace(host)
        self._refuse_while_cooling(host)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self.http.stream(method, url, json=json_body) as response:
                    if response.status_code == 429 or "retry-after" in response.headers:
                        self._cool_down(host)
                        raise Undecided("rate_limited")
                    body = await _read_bounded(response, self._max_body_bytes) if read_body else b""
                    return Answer(response.status_code, body)
        except TimeoutError as e:
            raise Undecided("timeout") from e
        except HostRefused as e:
            logger.error("link check refused a host outside its allowlist", exc_info=e)
            raise Undecided("network") from e
        except httpx.TimeoutException as e:
            raise Undecided("timeout") from e
        except (httpx.HTTPError, httpx.InvalidURL, UpstreamFailure) as e:
            raise Undecided("network") from e
