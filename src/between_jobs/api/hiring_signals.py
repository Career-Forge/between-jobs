"""Hiring Signals P2 -- the pure discovery/parse layer between a search
index and everything that will consume it (routes and UI come later).

Hiring Signals surfaces individual hiring-intent LinkedIn posts by asking a
SEARCH INDEX (Brave, You.com, Serper, Firecrawl) for `site:linkedin.com/
posts` results. All this feature ever learns about a post is therefore the
three strings a provider hands back -- url, title, snippet -- and this
module is the deterministic parse of those three strings and nothing else.
It performs no I/O of any kind: no network, no database, no credentials, no
LLM. `tests/test_hiring_signals.py` enforces that with an AST check on this
file's own imports, and with a subprocess that imports it with sockets blocked
(the imports of the modules it reuses are the part an AST check cannot see).

**The hard line.** No code path here, or anywhere in this feature, may fetch
a `linkedin.com` URL. `embed_url` builds the official public embed address
as a plain string; the USER'S BROWSER loads it, our servers never do. The
moment a server-side request to linkedin.com enters this feature, it stops
being "consume a search index" and becomes scraping.

**What the real data taught us** (a capture of more than 200 real results,
sanitized in `tests/golden/hiring_signals/inputs/`, see its README) --
several assumptions in the original design did not survive contact with it:

- The activity segment is not always `-activity-<id>-`: posts with no slug
  text at all use `_activity-<id>-` (the author handle runs straight into
  it), some have no author handle either (`/posts/activity-<id>-<suffix>`),
  some posts are indexed under `-ugcPost-<id>-` or `-share-<id>-` (other id
  namespaces that cannot be converted to an activity id -- both are real
  posts this module declines on purpose, see `parse_hit`), and some results
  are not `/posts/` at all (`/jobs/view/`, other sites entirely).
- The host is any `*.linkedin.com` (`es.`, `pt.`, `fr.`, `de.`, `cn.`,
  `uk.` all appear), and urls can carry tracking query strings including a
  member-identifying `rcm=` token -- `HiringSignal.post_url` drops
  the query/fragment so it never reaches a store.
- Titles come in many more shapes than `<content> | <Name> - LinkedIn` and
  `<Name>'s Post - LinkedIn`: `<Name> posted this`, `<Name>' Post` (names
  ending in "s"), `<Name> - <topic>`, `<content> | <Name> | N comments`,
  bare content with no author, titles truncated with an ellipsis (which can
  cut the name itself), titles with no ` - LinkedIn` suffix, localized
  templates (`Publicacion de`, `Post de`, `Beitrag von`, `Publicacao de`,
  `<Name>的动态`), and a localized site name (`领英`) that lands in the
  author slot if not stripped.
- The relative-time stamp is missing on most non-English results and on
  anything the provider treats as older than a couple of weeks -- freshness
  is `None` far more often than the design assumed. The activity id itself
  encodes the post time (`posted_at`), so a caller never has to depend on
  the provider's stamp to tell a live post from a years-old one.
- Company-page posts are the same index as personal posts: LinkedIn's own
  auto-generated job-share post (`ats_echo`) is authored by a company page,
  and its title carries the company, not a person.
- The index also returns posts that are not hiring calls at all (event
  invites, new-hire welcomes, job seekers asking for referrals). L1 cannot
  separate those from real hiring posts without reading them, so the
  default species is `unclassified` -- "no template matched" -- and never
  a claim that the post is a hiring call. (A hiring-vocabulary gate was
  measured against the real captures and rejected: of 391 distinct posts
  that got the default species, 143 had no English hiring word, and 63 of
  those 143 -- 44% -- used hiring words in Spanish, Portuguese, French,
  German, Chinese, Japanese or Hindi, so a gate would trade a precision
  problem for a recall problem in every non-English locale.) Job seekers
  are the exception, because their first-person markers are explicit
  enough to tag (`job_seeker`).

**Unknown means unknown.** Every field derived from a string that may not
contain it is `None` when it cannot be parsed. Nothing here fills a default
that looks like data, and `aggregator_source` is three-state: `None` until
a whole pull has been examined, and still `None` for a post whose url names
no author (it cannot be attributed to an account).

**Untrusted input.** Every string is a stranger's post as indexed by a third
party. Titles, snippets and urls are length-capped before any pattern runs,
whitespace is collapsed first (so no pattern ever faces a long space run),
digits are matched as `[0-9]` (never `\\d`, which accepts Arabic-Indic and
full-width digits), and every pattern is anchored or bounded and only ever
runs over that capped, collapsed text -- the tests feed pathological
strings to prove it. A field that is not a string at all (a provider
returning `null` for a description) reads as empty text rather than
raising. A url is only ever echoed back stripped of its query, fragment and
credentials and length-capped, and a `/posts/` path carrying whitespace,
control characters, quotes, angle brackets or extra path segments is
declined outright, so nothing hostile reaches a later HTML renderer or log
line through `post_url` or `author_handle`. Unicode (Devanagari, CJK,
Arabic, emoji) passes through untouched.

**Import note.** This module deliberately reuses `company_tiers.
normalize_company_name` and `search_aggregation`'s role-term predicate
instead of growing second copies. Those modules import `supabase`/`httpx`
at module level, so importing this one loads them transitively -- nothing
here ever calls into them beyond those two pure functions, and importing
them opens no connection (a test imports this module with sockets blocked
to keep it that way).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal
from urllib.parse import unquote, urlsplit

from .company_tiers import normalize_company_name
from .search_aggregation import matches_all_role_terms, role_term_patterns

# ── bounds on untrusted input ────────────────────────────────────────────

_MAX_URL_CHARS = 2048
_MAX_TITLE_CHARS = 512
_MAX_TEXT_CHARS = 2000
"""Real titles top out near 110 characters and snippets near 250. These are
generous ceilings, not tuning knobs: anything longer is either a provider
bug or an attempt to make a pattern work hard, and is truncated (text) or
declined (title/url) rather than scanned."""

_MAX_NAME_CHARS = 100

# ── data shapes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RawSearchHit:
    """One provider result reduced to the three strings this feature ever
    sees. Providers name the fields differently (Firecrawl calls the
    snippet `description`); the P3 adapters map into this shape."""

    url: str
    title: str
    snippet: str


AgeUnit = Literal["minute", "hour", "day", "week", "month"]

_AGE_UNITS: dict[str, AgeUnit] = {
    "minute": "minute",
    "hour": "hour",
    "day": "day",
    "week": "week",
    "month": "month",
}
_HOURS_PER_UNIT: dict[str, float] = {
    "minute": 1 / 60,
    "hour": 1.0,
    "day": 24.0,
    "week": 24.0 * 7,
    "month": 24.0 * 30,
}


@dataclass(frozen=True)
class RelativeAge:
    """The provider's own relative-time stamp ("3 days ago"), kept as the
    pair it was reported as rather than collapsed into a timestamp -- the
    stamp is only as precise as its unit, and it is relative to when the
    provider answered the query, not to when this code runs."""

    amount: int
    unit: AgeUnit

    @property
    def approx_hours(self) -> float:
        """Rough age in hours (a month is taken as 30 days). Good enough
        to sort or to sanity-check a provider's freshness window; not a
        posting time."""
        return self.amount * _HOURS_PER_UNIT[self.unit]


Species = Literal["ats_echo", "hiring_drive", "referral_offer", "job_seeker", "unclassified"]
"""`ats_echo`: LinkedIn's auto-generated job-share post (an ATS listing in a
LinkedIn costume). `hiring_drive`: a walk-in/hiring drive with a date cue.
`referral_offer`: someone offering to refer candidates. `job_seeker`: a
first-person job-seeker post (not a hiring call). `unclassified`: none of the
above matched -- most real hiring posts land here, and so do event invites and
welcome posts; it is the honest "not known", never a claim of any kind."""

_SPECIES_SPECIFICITY: tuple[Species, ...] = (
    "ats_echo",
    "hiring_drive",
    "referral_offer",
    "job_seeker",
    "unclassified",
)
"""Most specific first -- the same order `classify_species` applies its
rules in. Merging two copies of one post keeps the more specific tag,
because a copy whose snippet was cut short can only under-classify."""

RejectReason = Literal[
    "invalid_url",
    "not_linkedin",
    "not_a_post_url",
    "unsupported_urn",
    "no_activity_id",
]


@dataclass(frozen=True)
class RejectedHit:
    """A hit that carries no usable activity id, with the reason. Returned
    rather than silently dropped (or collapsed to `None`) because the
    reasons differ in what they mean for recall: `unsupported_urn` is a
    real post this module cannot embed, `not_a_post_url` is index noise.

    `url` is a loggable form of the input, never the input itself: scheme,
    host and path only (query, fragment and credentials dropped -- a
    member-identifying `rcm=` token would otherwise ride along into whatever
    logs or stores the reject), length-capped, and only the host for a
    non-LinkedIn url."""

    url: str
    reason: RejectReason


@dataclass(frozen=True)
class HiringSignal:
    """Everything L1 knows about one post, derived from its url, title and
    snippet. Holds no post text.

    `author_handle` is the profile or company-page vanity name from the url
    (percent-decoded, case-folded) and the identity the aggregator check
    keys on; it is `None` for the rare url that names no author at all
    (`/posts/activity-<id>-<suffix>`). `author_name` is the display name
    from the title and is `None` whenever the title shape does not reliably
    carry it. For an `ats_echo` post the author is the company page.
    `echo_role`/`echo_location` are only ever set for `ats_echo` posts whose
    snippet carries LinkedIn's whole fixed "a new <role> in <location>.
    Apply today ..." sentence. `posted_at` is when the post was made,
    decoded from the activity id (see `posted_at_from_activity_id`) --
    `None` only if the id decodes to an implausible time. `age` is the
    provider's own stamp, kept as a cross-check: it is relative to when the
    provider answered, and often absent. `aggregator_source` is `None`
    until `tag_aggregator_sources` has looked at a whole pull, and stays
    `None` for a post with no `author_handle`."""

    activity_id: str
    post_url: str
    author_handle: str | None
    author_name: str | None
    age: RelativeAge | None
    species: Species
    comment_count: int | None
    echo_role: str | None
    echo_location: str | None
    posted_at: datetime | None = None
    aggregator_source: bool | None = None


# ── text helpers ─────────────────────────────────────────────────────────


def _text(value: object) -> str:
    """A provider field as text. Providers do return `null` for a missing
    description; that is "no text", not a reason to lose the whole pull."""
    return value if isinstance(value, str) else ""


def _norm(text: str, limit: int) -> str:
    """Collapses all whitespace to single spaces and truncates. Doing this
    first is what keeps every later pattern linear: no pattern ever sees a
    run of spaces to split between two adjacent quantifiers."""
    return " ".join(text.split())[:limit]


_ELLIPSES = ("...", "\u2026")
_SENTENCE_PUNCTUATION_RX = re.compile(r"[!?;]")
"""No display name in real data carries these; post text does. A segment with
one is content, not a name."""


def _clean_name(raw: str) -> str | None:
    name = raw.strip(" -\u2013|")
    if not name or len(name) > _MAX_NAME_CHARS:
        return None
    if name.startswith("#") or name.casefold() in _SITE_NAMES:
        return None
    if not any(ch.isalpha() for ch in name) or _SENTENCE_PUNCTUATION_RX.search(name):
        return None
    return name


# ── url parsing ──────────────────────────────────────────────────────────

_POST_SEGMENT_RX = re.compile(
    r"(?:(?P<handle>[^_/]{1,200})_(?:(?P<slug>[^/]{0,600})-)?)?"
    r"(?P<kind>activity|share|ugcPost)-(?P<id>[0-9]{15,25})(?:-[A-Za-z0-9_-]{1,8})?"
)
"""`[<handle>_[<slug>-]]<kind>-<id>-<suffix>`. The slug is optional (a post
whose text produced no slug is `<handle>_activity-<id>-<suffix>`), greedy so
the LAST `-kind-id` wins over one that merely appears inside the slug, and
the suffix is LinkedIn's 4-character collision guard, which can itself begin
or end with `-`/`_`. A post with no author handle is the bare
`activity-<id>-<suffix>` -- a slug never appears without a handle, so
`janedoe-x-activity-<id>` (no underscore boundary) is still not a match.
Neither handle nor slug may contain `/`: extra path segments are not part of
a post's address. Ids are 19 digits in every observed post; 15-25 is a
sanity band, not a claim about LinkedIn's format."""

_UNSAFE_URL_CHARS_RX = re.compile(r"[\s\x00-\x1f\x7f-\x9f\"'<>`\\]")
"""What a `/posts/` path segment (and a decoded handle) may never contain:
whitespace, control characters, quotes, angle brackets, a backtick or a
backslash. LinkedIn percent-encodes all of these, and none was ever seen raw
in real data; refusing them means `post_url` and `author_handle` are safe to
drop into an HTML attribute or a log line without a second escaping pass."""

_HOST_RX = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*")
_MAX_REJECT_URL_CHARS = 200


def _loggable_url(url: object, *, host_only: bool = False) -> str:
    """What a `RejectedHit` may echo of an input url: scheme, host and path
    (or just `scheme://host`), never the query, fragment, credentials or
    port, capped at `_MAX_REJECT_URL_CHARS`, control characters removed. A
    string too malformed to split is cut at its first `?` or `#` instead. The
    work is bounded up front, so a megabyte of url costs the same as a
    short one."""
    if not isinstance(url, str):
        return ""
    head = url.strip()[:_MAX_URL_CHARS]
    try:
        parts = urlsplit(head)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if parts is not None and host and parts.scheme in ("http", "https"):
        rebuilt = f"{parts.scheme}://{host}" + ("" if host_only else parts.path)
    else:
        rebuilt = head.split("#", 1)[0].split("?", 1)[0]
    return "".join(ch for ch in rebuilt if ch.isprintable())[:_MAX_REJECT_URL_CHARS]


def _reject(url: object, reason: RejectReason) -> RejectedHit:
    return RejectedHit(url=_loggable_url(url, host_only=reason == "not_linkedin"), reason=reason)


@dataclass(frozen=True)
class _ParsedUrl:
    activity_id: str
    handle: str | None
    slug: str | None
    post_url: str


def _parse_url(url: object) -> _ParsedUrl | RejectedHit:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_CHARS:
        return _reject(url, "invalid_url")
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return _reject(url, "invalid_url")
    if parts.scheme not in ("http", "https") or not host:
        return _reject(url, "invalid_url")
    host = host.rstrip(".")
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return _reject(url, "not_linkedin")
    if _HOST_RX.fullmatch(host) is None:
        return _reject(url, "invalid_url")
    path = parts.path.rstrip("/")
    if not path.startswith("/posts/"):
        return _reject(url, "not_a_post_url")
    segment = path[len("/posts/") :]
    if _UNSAFE_URL_CHARS_RX.search(segment):
        return _reject(url, "invalid_url")
    match = _POST_SEGMENT_RX.fullmatch(segment)
    if match is None:
        return _reject(url, "no_activity_id")
    if match["kind"] != "activity":
        return _reject(url, "unsupported_urn")
    handle: str | None = None
    if match["handle"] is not None:
        handle = unquote(match["handle"]).casefold()
        if _UNSAFE_URL_CHARS_RX.search(handle) or "/" in handle:
            return _reject(url, "invalid_url")
    return _ParsedUrl(
        activity_id=match["id"],
        handle=handle,
        slug=match["slug"],
        post_url=f"https://{host}/posts/{segment}",
    )


# ── embed ────────────────────────────────────────────────────────────────

EMBED_URL_TEMPLATE = "https://www.linkedin.com/embed/feed/update/urn:li:activity:{activity_id}"
_EMBED_ID_RX = re.compile(r"[0-9]{1,25}")


def embed_url(activity_id: str) -> str:
    """The official public embed address for a post.

    **The hard line: the browser loads this URL, our servers never do.**
    This is a pure string builder. Nothing in this feature may issue a
    request to it (or to any other linkedin.com URL) from server code --
    that is the property that separates consuming a search index from
    scraping LinkedIn.

    The id comes from a third party's index, so it is validated as ASCII
    digits only (`[0-9]`, full match -- not `str.isdigit`, which accepts
    Arabic-Indic and superscript digits, and not `\\d`) before it is put
    into a URL a browser will load. Anything else raises `ValueError`."""
    if not isinstance(activity_id, str) or _EMBED_ID_RX.fullmatch(activity_id) is None:
        raise ValueError("activity_id must be 1-25 ASCII digits")
    return EMBED_URL_TEMPLATE.format(activity_id=activity_id)


# ── freshness ────────────────────────────────────────────────────────────

_AGE_RX = re.compile(
    r"(?:(?P<amount>[0-9]{1,3})\s+(?P<unit>minute|hour|day|week|month)s?\s+ago|just now)"
    r"\s*[·•]\s*",
    re.IGNORECASE,
)


def parse_age(snippet: str) -> RelativeAge | None:
    """The snippet's leading `"<N> <unit>s ago · "` stamp. Only a stamp at
    the very start counts (the same words mid-snippet are post text), the
    `·` separator is required (every real stamp has it), and singular and
    plural units both parse ("1 hour ago", "1 hours ago"). "Just now" is
    zero minutes. Absent, localized or absolute-date stamps are `None`."""
    match = _AGE_RX.match(_norm(snippet, 64))
    if match is None:
        return None
    if match["amount"] is None:
        return RelativeAge(amount=0, unit="minute")
    return RelativeAge(amount=int(match["amount"]), unit=_AGE_UNITS[match["unit"].lower()])


_ACTIVITY_ID_TIME_SHIFT = 22
_MIN_PLAUSIBLE_POST_MS = 1_041_379_200_000  # 2003-01-01T00:00:00Z, the year LinkedIn launched
_MAX_PLAUSIBLE_POST_MS = 4_102_444_800_000  # 2100-01-01T00:00:00Z
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def posted_at_from_activity_id(activity_id: str) -> datetime | None:
    """When a post was made, read straight off its activity id.

    LinkedIn activity ids are snowflake-style: the id shifted right by 22
    bits is the creation time in milliseconds since the Unix epoch. That
    makes the time available for EVERY parsed post, exactly and with no I/O,
    where the provider's own relative stamp (`parse_age`) is missing on
    roughly a fifth of real results and is relative to when the provider
    answered rather than to the post. On real captured rows the decoded time
    and the provider's stamp agree (a post stamped "1 day ago" decodes to the
    day before the pull), which is why the stamp is kept as a cross-check
    instead of being dropped.

    Deterministic and clock-free: the plausibility band is a fixed calendar
    range (2003 to 2100), not "before now", so a garbage id gives `None`
    rather than a nonsense date and the function never reads the time. It
    is the caller's job to compare the result to its own clock."""
    if not isinstance(activity_id, str) or _EMBED_ID_RX.fullmatch(activity_id) is None:
        return None
    millis = int(activity_id) >> _ACTIVITY_ID_TIME_SHIFT
    if not _MIN_PLAUSIBLE_POST_MS <= millis <= _MAX_PLAUSIBLE_POST_MS:
        return None
    return _UNIX_EPOCH + timedelta(milliseconds=millis)


# ── title parsing ────────────────────────────────────────────────────────

_SITE_NAMES = frozenset({"linkedin", "领英"})
"""The site name LinkedIn appends to titles -- `LinkedIn`, and `领英` on the
China site. Left in place it lands in the author slot (`... | 领英 -
LinkedIn` would parse the author as "领英")."""

_SITE_SUFFIX_RX = re.compile(r"\s[-|\u2013]\s(?:LinkedIn|领英)$", re.IGNORECASE)
_POSSESSIVE_RX = re.compile(r"(?P<name>[^|]{1,100}?)(?:['\u2019]s|['\u2019]) Post(?: - .*)?")
_LOCALIZED_PREFIX_RX = re.compile(
    r"(?:Publicación de|Publicação de|Post de|Beitrag von) (?P<name>[^|]{1,100})"
)
_CJK_POSSESSIVE_RX = re.compile(r"(?P<name>[^|]{1,100}?)的动态")
_POSTED_RX = re.compile(r"(?P<name>.{1,100}?) posted(?: this| on(?: the(?: topic)?)?)?")
_PIPE_SPLIT_RX = re.compile(r"(?:^|\s)\|(?:\s|$)")
_COMMENT_WORDS = (
    r"(?:comments?|comentarios?|comentários?|commentaires?|kommentare?|commenti|commento|评论)"
)
_COMMENTS_RX = re.compile(
    r"(?P<number>[0-9][0-9.,]{0,11})(?P<multiplier>[KkMm])?\s+" + _COMMENT_WORDS,
    re.IGNORECASE,
)
_GROUPED_INT_RX = re.compile(r"[0-9]{1,3}(?:[.,][0-9]{3})+|[0-9]+")


def _comment_count(number: str, multiplier: str | None) -> int | None:
    """`12` -> 12, `1,234` / `1.234` -> 1234 (either grouping separator),
    `1.2K` / `1,2K` -> 1200, `2M` -> 2_000_000. Anything else is `None`: a
    segment that reads as a count is still not a name, but a number this code
    cannot read exactly is not reported as one."""
    if multiplier is not None:
        try:
            value = float(number.replace(",", "."))
        except ValueError:
            return None
        return round(value * (1_000 if multiplier.casefold() == "k" else 1_000_000))
    if _GROUPED_INT_RX.fullmatch(number) is None:
        return None
    return int(number.replace(",", "").replace(".", ""))


def _alnum_tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def _corroborated_by_handle(name: str, handle: str | None) -> bool:
    """A name in an ambiguous title position (`<Name> - <topic>`, the last
    pipe segment, or a lone bare title) is only trusted if some word of it
    also appears in the url's handle -- LinkedIn derives handles from names,
    so this separates `Jane Doe - Software Engineer in Test` (author first)
    from a content-only title that happens to contain a dash. Words under 3
    characters are ignored; the match is on the handle with its separators
    removed, so `janedoe1` corroborates "Jane Doe". A post whose url names no
    author has nothing to corroborate against."""
    if handle is None:
        return False
    handle_key = "".join(_alnum_tokens(handle))
    return any(len(token) >= 3 and token in handle_key for token in _alnum_tokens(name))


def _wholly_corroborated_by_handle(name: str, handle: str | None) -> bool:
    """Stricter than `_corroborated_by_handle`: EVERY word of the name
    (3+ characters) is in the handle. Used for a lone bare title, which is
    either the author's own name (`Senco Gold and Diamonds` under the handle
    `senco-gold-and-diamonds`) or the post's own headline (`Ajanta Pharma
    Jobs in Hyderabad`, `Schedule: IBM Quantum @ IEEE Quantum Week`) -- and a
    headline about the author shares a word or two with the handle far more
    often than it shares all of them."""
    if handle is None:
        return False
    handle_key = "".join(_alnum_tokens(handle))
    tokens = [token for token in _alnum_tokens(name) if len(token) >= 3]
    return bool(tokens) and all(token in handle_key for token in tokens)


def _is_hashtag_block(segment: str) -> bool:
    tokens = segment.split()
    return bool(tokens) and all(token.startswith("#") for token in tokens)


def _pipe_author(segments: list[str], handle: str | None, *, counted: bool) -> str | None:
    """The author out of `<content> | <Name>[ | <tagline>]`, where the last
    segment is USUALLY the author but can also be content (`Male Nurses
    Required for Oman | Walk-In Interview in Kerala`) or a tagline after the
    real name (`WE ARE HIRING | Kedia Capital | Wealth Management`).

    Trusted without corroboration only where the shape itself says so: a
    comment count was peeled off after it, or the segment before it is a bare
    hashtag block (`#hiring | <Company>` is LinkedIn's own auto-post title,
    and real company pages often have acronym handles). Otherwise the last
    two segments are tried in that order and one is accepted only if the url
    handle corroborates it; a title whose segments nothing corroborates
    yields `None` -- a wrong string that looks like a name is worse than an
    honest unknown. A trailing `'s Post` is a possessive marker, not part of
    the name."""
    last = segments[-1]
    marked = _POSSESSIVE_RX.fullmatch(last)
    last_name = _clean_name(marked["name"] if marked is not None else last)
    if last_name is not None and (counted or _is_hashtag_block(segments[-2])):
        return last_name
    candidates = [last_name]
    if len(segments) >= 3:
        candidates.append(_clean_name(segments[-2]))
    for candidate in candidates:
        if candidate is not None and _corroborated_by_handle(candidate, handle):
            return candidate
    return None


def _parse_title(title: str, handle: str | None) -> tuple[str | None, int | None]:
    """`(author_name, comment_count)` from a result title.

    Shape by shape, in the order they are tried:

    1. `<Name>'s Post[ - <topic>]` / `<Name>' Post`, the localized
       equivalents (`Publicación de`, `Post de`, `Beitrag von`,
       `Publicação de`) and `<Name>的动态` -- the marker makes the name
       unambiguous even when the title is truncated after it.
    2. Pipe titles, `<content> | <Name>[ | N comments]`: a trailing
       comment count (`12 comments`, `1.2K comments`, `20 comentarios`, ...)
       is peeled off first and returned as the count, never as a name.
       `<Name> posted [this|on [the [topic]]]` is unwrapped. Otherwise the
       author is chosen by `_pipe_author`, which needs the url handle to
       corroborate a name unless the shape vouches for it. A title cut off
       with an ellipsis has an unreliable final segment, so it yields no
       name unless the `posted` marker survived or a count follows it.
    3. `<Name> posted this|on the topic` with no pipe.
    4. `<Name> - <topic>` -- first segment, accepted only when the url
       handle corroborates it.
    5. A lone bare segment (`<Company> - LinkedIn` once the site name is
       gone) -- accepted only when the handle contains EVERY word of it,
       and never from a truncated title.
    6. Everything else (bare content, hashtags only, an author-less
       truncated title) is `None`.

    The trailing site name (`- LinkedIn`, `- 领英`, up to twice) is removed
    first; it is optional -- several providers drop it."""
    t = _norm(title, _MAX_TITLE_CHARS + 1)
    if not t or len(t) > _MAX_TITLE_CHARS:
        return None, None
    for _ in range(2):
        suffix = _SITE_SUFFIX_RX.search(t)
        if suffix is None:
            break
        t = t[: suffix.start()]
    truncated = t.endswith(_ELLIPSES)
    if truncated:
        t = t.rstrip(".…").rstrip()
    if not t:
        return None, None

    for rx in (_POSSESSIVE_RX, _LOCALIZED_PREFIX_RX, _CJK_POSSESSIVE_RX):
        m = rx.fullmatch(t)
        if m is not None:
            return _clean_name(m["name"]), None

    segments = [s.strip() for s in _PIPE_SPLIT_RX.split(t)]
    if len(segments) >= 2:
        comment_count: int | None = None
        counted = False
        cm = _COMMENTS_RX.fullmatch(segments[-1])
        if cm is not None:
            comment_count = _comment_count(cm["number"], cm["multiplier"])
            counted = True
            segments = segments[:-1]
        if len(segments) >= 2:
            posted = _POSTED_RX.fullmatch(segments[-1])
            if posted is not None:
                return _clean_name(posted["name"]), comment_count
            if truncated and not counted:
                return None, comment_count
            return _pipe_author(segments, handle, counted=counted), comment_count
        lone = _clean_name(segments[0])
        if lone is not None and _corroborated_by_handle(lone, handle):
            return lone, comment_count
        return None, comment_count

    posted = _POSTED_RX.fullmatch(t)
    if posted is not None:
        return _clean_name(posted["name"]), None

    dash_parts = t.split(" - ")
    if len(dash_parts) >= 2:
        first = _clean_name(dash_parts[0])
        if first is not None and _corroborated_by_handle(first, handle):
            return first, None
        return None, None
    bare = _clean_name(t)
    if bare is not None and not truncated and _wholly_corroborated_by_handle(bare, handle):
        return bare, None
    return None, None


# ── species ──────────────────────────────────────────────────────────────

_TEMPLATE_SENTENCES = (
    "apply today or share this post with your network",
    "bewerben sie sich noch heute oder teilen sie",
    "postulez aujourd'hui ou partagez",
    "candidate-se hoje mesmo ou partilhe",
    "candidate-se hoje mesmo ou compartilhe",
)
"""The closing sentence of LinkedIn's auto-generated job-share post. English
in full, and the German, French and Portuguese versions up to where a
snippet is usually cut. Each marker includes the second half ("... oder
teilen Sie", "... ou partagez", "... ou partilhe/compartilhe"): the first half
alone -- "Bewerben Sie sich noch heute", "Postulez aujourd'hui", "Candidate-se
hoje mesmo" -- is ordinary hiring copy that humans write, and only the
template has both. Portuguese has two spellings of "share" (pt-PT
`partilhe`, pt-BR `compartilhe`); the French post is often bilingual and
repeats the English sentence after its own. The localized posts also carry
localized slugs (`embauchons-hiring`, `contratando-hiring`), so the bare
`hiring` slug rule below only ever helps English and German. Spanish was
probed and NO fixed template was confirmed (the near hits read as ordinary
human copy), so none is claimed."""
_ECHO_HEAD_RXS = (
    re.compile(r"we['\u2019]re #hiring a new (?P<body>.{1,200}?)\. apply today", re.IGNORECASE),
    re.compile(
        r"wir suchen (?:eine:n )?neue:n (?P<body>.{1,200}?)\. bewerben sie sich noch heute",
        re.IGNORECASE,
    ),
)
_ECHO_LEAD_RX = re.compile(
    r"we['\u2019]re #hiring a new\b|\bhiring\s*[-\u2013]\s*wir suchen eine:n neue:n\b",
    re.IGNORECASE,
)
_MONTHS_NO_MAY = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_MONTHS = r"(?:" + _MONTHS_NO_MAY + r"|(?-i:May|MAY))"
"""`may` is also a verb ("Freshers 2 may apply"), so a month name only counts
as `May` there, capitalized; a lower-case `may` is read as the month only
where a four-digit year follows (`_MONTHS_BEFORE_A_YEAR`)."""
_MONTHS_BEFORE_A_YEAR = r"(?:" + _MONTHS_NO_MAY + r"|may)"
_DRIVE_PHRASE_RX = re.compile(
    r"\bwalk[\s-]?in\s+(?:interviews?|drives?|hiring|recruitment|opportunit(?:y|ies)|process"
    r"|dates?|venue|timings?|schedule|registration)\b"
    r"|#walkin(?:s|interviews?)?\b"
    r"|\b(?:hiring|recruitment)\s+drive\b",
    re.IGNORECASE,
)
"""An explicit drive phrase. A bare `walk-in` is deliberately NOT one: it also
means a customer who walks into a shop or a clinic that takes no
appointments, and every real drive post found in the capture names the
event too ("walk-in interview", "walk-in drive", "hiring drive", "walk-in
date"/"venue" -- the logistics lines a drive post lists)."""
_DATE_CUE_RX = re.compile(
    r"\b[0-9]{1,2}[./-][0-9]{1,2}[./-](?:[0-9]{4}|[0-9]{2})\b"
    r"|\b[0-9]{1,2}(?:st|nd|rd|th)?\s*(?:of\s+)?" + _MONTHS + r"\b"
    r"|\b" + _MONTHS + r"\s+[0-9]{1,2}(?:st|nd|rd|th)?\b"
    r"|\bthis\s+(?:weekend|saturday|sunday)\b",
    re.IGNORECASE,
)
_WEAK_DATE_CUE_RX = re.compile(
    r"\b" + _MONTHS_BEFORE_A_YEAR + r"\s+[0-9]{4}\b"
    r"|\b(?:[12]?[0-9]|3[01])(?:st|nd|rd|th)\b"
    r"(?!\s+(?:round|floor|year|yr|gen|shift|batch|place|prize|rank|stage|level|tier|semester))"
    r"|\b(?:time|venue)\s*:",
    re.IGNORECASE,
)
"""Cues too weak to make a drive on their own, accepted only next to an
explicit drive phrase: a month and year with the day cut off by the snippet
ellipsis ("September 2026"), an ordinal day alone ("17th ..."), and the
`Time:` / `Venue:` lines a drive post lists. Real drive posts lose their day
number to the ellipsis often enough that requiring one dropped four of ten.
An ordinal followed by a word like `round` or `floor` is not a day ("1st
round" of a walk-in interview)."""
_QUOTED_RX = re.compile(r'["\u201c][^"\u201c\u201d]{0,200}["\u201d]')
"""Quoted text is somebody's words, not the poster's own -- advice posts quote
the sentence they tell people to send ("Don't ask: 'Who can refer me?'"). The
referral and job-seeker markers are first-person claims, so they are
searched with quoted spans removed."""
_REFERRAL_OFFER_RX = re.compile(
    r"\b(?:happy|glad|willing|ready|pleased)\s+to\s+refer\b"
    r"|\bi(?:['\u2019]ll|\s+(?:can|will|could|would))\s+"
    r"(?:(?:gladly|happily|also|love\s+to|like\s+to)\s+)*refer\b(?!\s+(?:to|me)\b)"
    r"|\bi(?:['\u2019]m|\s+am)\s+(?:planning|going)\s+to\s+refer\b(?!\s+(?:to|me)\b)"
    r"|\bi(?:['\u2019]m|\s+am|['\u2019]ll\s+be|\s+will\s+be)\s+referring\b(?!\s+to\b)"
    r"|\bcan\s+refer\s+(?:you|candidates?|profiles?|relevant|suitable)\b"
    r"|\breferrals?\s+(?:are\s+|is\s+)?(?:open|available|offered|possible)\b"
    r"|\bdm\s+(?:me\s+)?for\s+(?:a\s+)?referrals?\b"
    r"|\b(?:offering|providing|giving)\s+referrals?\b"
    r"|\bi\s+have\s+referrals?\b",
    re.IGNORECASE,
)
_JOB_SEEKER_RX = re.compile(
    r"\bcan\s+refer\s+me\b"
    r"|\b(?:please|kindly|pls|plz)\s+refer\s+me\b"
    r"|\bcan\s+(?:anyone|someone|somebody|you)\s+(?:please\s+)?refer\s+me\b"
    r"|\bi(?:['\u2019]m|\s+am)\s+looking\s+for\s+(?:my\s+next|a\s+new|new)\s+"
    r"(?:roles?|jobs?|opportunit(?:y|ies)|positions?|challenges?|chapter)\b"
    r"|\bi(?:['\u2019]m|\s+am)\s+(?:currently\s+|actively\s+)?open\s+to\s+"
    r"(?:work|new\s+opportunit(?:y|ies))\b"
    r"|#opentowork\b",
    re.IGNORECASE,
)
"""First-person job-seeker markers only. A bare `open to work` is NOT one:
it turns up in hiring posts ("candidate must be open to work from office"),
and a search snippet carries commenters' profile headlines, so a stranger's
`Open to work | Java` line under a hiring post would otherwise tag the post
itself. `refer me` needs an asking form (`can refer me`, `please refer me`,
`can anyone refer me`) -- real job seekers' snippets are often cut so the
sentence starts at `can refer me`."""
_STRONG_HIRING_CUE_RX = re.compile(
    r"\bwe(?:['\u2019]re|\s+are)\s+#?hiring\b|\bi(?:['\u2019]m|\s+am)\s+#?hiring\b"
    r"|\bmy\s+team\s+is\s+hiring\b|\bhiring\s+for\b|\bnow\s+hiring\b|#(?:now)?hiring\b",
    re.IGNORECASE,
)
"""A snippet that says outright that someone is hiring is not a job seeker's
post, whatever else the snippet quotes (a commenter's `#OpenToWork`)."""


def _echo_fields(text: str) -> tuple[str | None, str | None]:
    """Role and location out of LinkedIn's fixed "We're #hiring a new
    <role> in <location>. Apply today ..." sentence (or its German twin).
    The role is cut at the LAST " in " (roles can contain one -- "Software
    Engineer in Test in Bengaluru"; locations do not). The French and
    Portuguese templates are recognized (`_TEMPLATE_SENTENCES`) but their
    role and location are not read: no real sample established where their
    fixed opening ends and the role begins, so both stay `None`.

    Requires the whole sentence, tail included. Without the tail there is no
    telling where the location starts: "... a new Software Engineer in Test
    ..." cut off before its location would otherwise read as role "Software
    Engineer" in "Test". A snippet truncated before the tail therefore
    yields neither field (the post is still an `ats_echo` -- see
    `_is_ats_echo`), and neither does the second template variant ("We're
    #hiring. Apply today ...", role in trailing unstructured text)."""
    for head in _ECHO_HEAD_RXS:
        match = head.search(text)
        if match is None:
            continue
        role, sep, location = match["body"].rpartition(" in ")
        if not sep:
            return match["body"].strip() or None, None
        return role.strip() or None, location.strip() or None
    return None, None


def _is_ats_echo(text: str, slug: str | None) -> bool:
    lowered = text.casefold().replace("\u2019", "'")
    if any(sentence in lowered for sentence in _TEMPLATE_SENTENCES):
        return True
    # A snippet can be cut mid-sentence, before the closing line. LinkedIn's
    # own auto-post also gets the bare slug `hiring`, which a human's post
    # almost never does -- with that slug, either the tail marker or the
    # template's fixed opening is enough.
    if slug != "hiring":
        return False
    if "#hiring" in lowered and "apply today" in lowered:
        return True
    return _ECHO_LEAD_RX.search(text) is not None


def classify_species(title: str, snippet: str, slug: str | None = None) -> Species:
    """Deterministic species tag from title + snippet text (and the url
    slug, for the one template check that needs it). First match wins, in
    order: `ats_echo` (LinkedIn's fixed template), `hiring_drive` (an
    explicit walk-in/drive phrase AND a date cue -- "walk in and use it"
    with a date in the same snippet is an event, not a drive), then
    `referral_offer`, `job_seeker`, else `unclassified`.

    The referral pattern is deliberately narrow, because the words appear
    in three opposite senses in real data: offering ("I can refer you"),
    asking ("I'd love your referrals", "can refer me") and pointing
    ("You can refer to version 3.0"). Only first-person offers match, and
    quoted text is ignored. A job-seeker marker never overrides an explicit
    "we're hiring" in the same snippet."""
    text = _norm(f"{_text(title)} {_text(snippet)}", _MAX_TEXT_CHARS)
    if _is_ats_echo(text, slug):
        return "ats_echo"
    if _DRIVE_PHRASE_RX.search(text) and (
        _DATE_CUE_RX.search(text) or _WEAK_DATE_CUE_RX.search(text)
    ):
        return "hiring_drive"
    own_words = _QUOTED_RX.sub(" ", text)
    if _REFERRAL_OFFER_RX.search(own_words):
        return "referral_offer"
    if _JOB_SEEKER_RX.search(own_words) and not _STRONG_HIRING_CUE_RX.search(text):
        return "job_seeker"
    return "unclassified"


# ── L1: one hit -> one signal ────────────────────────────────────────────


def parse_hit(hit: RawSearchHit) -> HiringSignal | RejectedHit:
    """L1 parse of one search hit.

    Returns a `RejectedHit` with an explicit reason -- never `None` -- when
    the hit cannot yield an activity id: not a linkedin.com host, not a
    `/posts/` url, a `/posts/` url whose id is a `share`/`ugcPost` urn, or no
    recognizable id at all. The reasons are kept because P5's recall
    measurement needs to know WHY hits were dropped.

    `share` and `ugcPost` urns are declined on purpose, not overlooked: both
    are real posts (one `-share-` result has been seen alongside the
    `-ugcPost-` ones), but the embed address this feature builds is keyed on
    the ACTIVITY urn, and a share or ugcPost id is not a proven alias of an
    activity id -- guessing one would embed the wrong post or nothing. They
    come back as `unsupported_urn`, counted rather than silently lost.

    A returned signal carries only what the three strings reliably support;
    see `HiringSignal` for which fields can be `None`."""
    parsed = _parse_url(hit.url)
    if isinstance(parsed, RejectedHit):
        return parsed
    title = _text(hit.title)
    snippet = _text(hit.snippet)
    author_name, comment_count = _parse_title(title, parsed.handle)
    species = classify_species(title, snippet, parsed.slug)
    echo_role: str | None = None
    echo_location: str | None = None
    if species == "ats_echo":
        echo_role, echo_location = _echo_fields(_norm(snippet, _MAX_TEXT_CHARS))
    return HiringSignal(
        activity_id=parsed.activity_id,
        post_url=parsed.post_url,
        author_handle=parsed.handle,
        author_name=author_name,
        age=parse_age(snippet),
        species=species,
        comment_count=comment_count,
        echo_role=echo_role,
        echo_location=echo_location,
        posted_at=posted_at_from_activity_id(parsed.activity_id),
    )


# ── L2: pull-level helpers ───────────────────────────────────────────────

AGGREGATOR_MIN_POSTS = 5
"""An account with this many DISTINCT posts in one pull is a reposting or
aggregator account, not a hiring manager. Real data: one account posted the
same "<Company> Hiring for <Role> ... Job Requirements ... Apply Link" template
for eight companies in a single pull, while a recruiting firm's four posts in
another pull sit just under the line."""


def tag_aggregator_sources(signals: Iterable[HiringSignal]) -> tuple[HiringSignal, ...]:
    """Sets `aggregator_source` on every signal of ONE pull: `True` when its
    author handle has `AGGREGATOR_MIN_POSTS` or more distinct activity ids
    in the pull, else `False` -- and `None` for a signal with no author
    handle, which cannot be attributed to any account.

    Author-level, not post-level -- the same account's posts are all tagged
    or all not. Counted on DISTINCT ids so the same post indexed under two
    slugs (real data has this) does not inflate a count, which also makes
    the result independent of whether the caller deduped first. Keyed on
    the url handle, not the display name: names vary in case and format
    across one account's results and collide across strangers."""
    materialized = list(signals)
    ids_by_handle: dict[str, set[str]] = {}
    for signal in materialized:
        if signal.author_handle is not None:
            ids_by_handle.setdefault(signal.author_handle, set()).add(signal.activity_id)
    return tuple(
        replace(
            signal,
            aggregator_source=(
                None
                if signal.author_handle is None
                else len(ids_by_handle[signal.author_handle]) >= AGGREGATOR_MIN_POSTS
            ),
        )
        for signal in materialized
    )


def _first_known[T](values: Iterable[T | None]) -> T | None:
    return next((value for value in values if value is not None), None)


def _merge_copies(copies: Sequence[HiringSignal]) -> HiringSignal:
    """One signal out of several copies of the SAME post (one activity id).

    The same post shows up more than once in a pull -- under two slugs, or
    with a different snippet each time -- and the copies rarely know the same
    things: one has the provider's age stamp, another the comment count,
    another a snippet long enough to reveal a drive date. Keeping only the
    first copy threw the others' facts away. Instead, field by field: the
    first copy's value that is not `None` (input order, so the result is
    deterministic and a caller can still steer it), and the MOST SPECIFIC
    species of any copy, because a snippet cut short can only
    under-classify. The `ats_echo` role and location are only ever set on
    `ats_echo` copies, so they merge without special casing."""
    first = copies[0]
    species = min((copy.species for copy in copies), key=_SPECIES_SPECIFICITY.index)
    return replace(
        first,
        author_name=_first_known(copy.author_name for copy in copies),
        age=_first_known(copy.age for copy in copies),
        species=species,
        comment_count=_first_known(copy.comment_count for copy in copies),
        echo_role=_first_known(copy.echo_role for copy in copies),
        echo_location=_first_known(copy.echo_location for copy in copies),
        aggregator_source=_first_known(copy.aggregator_source for copy in copies),
    )


def dedupe_signals(signals: Iterable[HiringSignal]) -> tuple[HiringSignal, ...]:
    """One signal per activity id, in order of first appearance, with the
    duplicates' facts merged in (see `_merge_copies`). Deterministic and
    stable: the same input always yields the same output in the same order."""
    groups: dict[str, list[HiringSignal]] = {}
    for signal in signals:
        groups.setdefault(signal.activity_id, []).append(signal)
    return tuple(_merge_copies(group) for group in groups.values())


def _require_term_sequence(role_terms: Sequence[str]) -> None:
    """A bare string is a `Sequence[str]` too -- iterating it yields single
    characters, which would silently become "no role constraint" (filter) or
    a query made of letters (builder). Refuse it loudly instead."""
    if isinstance(role_terms, str):
        raise TypeError("role_terms must be a sequence of phrases, not a single string")


def hit_matches_roles(hit: RawSearchHit, role_terms: Sequence[str]) -> bool:
    """Role-family filter over a hit's title + snippet: true if ANY role
    phrase matches, where a phrase matches when ALL of its words appear as
    whole words. The word-boundary/AND rules are `search_aggregation`'s own
    (shared, not copied), and a "word" may carry symbols: `c++`, `c#`,
    `.net` and `sr.` all match as written. No role terms means no
    constraint.

    Inherited limits: single-character terms are dropped (n8n behavior --
    `R developer` constrains on `developer` alone), and text in a script
    written without spaces (CJK) only matches a term bounded by
    punctuation."""
    _require_term_sequence(role_terms)
    phrases = [role_term_patterns(term) for term in role_terms]
    phrases = [patterns for patterns in phrases if patterns]
    if not phrases:
        return True
    text = _norm(f"{_text(hit.title)} {_text(hit.snippet)}", _MAX_TEXT_CHARS)
    return any(matches_all_role_terms(text, patterns) for patterns in phrases)


@dataclass(frozen=True)
class RegistryCandidate:
    """A registry (company, title[, location]) triple supplied by the
    caller -- the database lookup that produces these is P3's job. `ref` is
    an opaque handle back to the caller's own row. `location` is optional
    because not every registry row has one, and an unknown location is
    handled as unknown (see `match_ats_echo_to_registry`), never assumed to
    match."""

    company: str
    title: str
    ref: str | None = None
    location: str | None = None


MatchState = Literal["matched", "possible", "unmatched", "undeterminable"]


@dataclass(frozen=True)
class RegistryMatch:
    state: MatchState
    candidate: RegistryCandidate | None = None


_NON_ALNUM_RX = re.compile(r"[\W_]+")
_LEGAL_FORM_RX = re.compile(r"\s+(?:private|pvt)$")
_GENERIC_LOCATION_WORDS = frozenset({"area", "greater", "metropolitan", "region", "metro", "city"})


def _title_key(title: str) -> str:
    """Case- and punctuation-insensitive title, except that `+` and `#` are
    kept as words: `C++`, `C#` and `C` are different jobs, and collapsing
    them let one registry row read as another's twin."""
    spelled = title.casefold().replace("#", " sharp ").replace("+", " plus ")
    return _NON_ALNUM_RX.sub(" ", spelled).strip()


def _company_key(name: str) -> str:
    """`normalize_company_name`, plus the Indian legal-form words it does not
    know: it already drops `Ltd`/`Limited`, which leaves `Acme Pvt` and
    `Acme Private` -- neither equal to a registry's plain `Acme`."""
    return _LEGAL_FORM_RX.sub("", normalize_company_name(name))


def _company_keys(name: str) -> set[str]:
    """Normalized forms of an ats_echo author name to compare registry
    companies against: the whole name, and the part before a ` - ` tagline
    (LinkedIn page names often carry one: "Aquascape Engineers Pvt Ltd -
    Aerospace")."""
    keys = {_company_key(name), _company_key(name.split(" - ")[0])}
    keys.discard("")
    return keys


LocationRelation = Literal["same", "different", "unknown"]


def _location_words(location: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """`(city words, all words)` of a location, generic words ("area",
    "greater", "metropolitan"...) set aside. The city is the part before the
    first comma."""
    if location is None:
        return frozenset(), frozenset()
    city = frozenset(_alnum_tokens(location.split(",")[0])) - _GENERIC_LOCATION_WORDS
    return city, frozenset(_alnum_tokens(location)) - _GENERIC_LOCATION_WORDS


def _location_relation(echo: str | None, candidate: str | None) -> LocationRelation:
    """How an echo's location relates to a registry row's.

    `same`: one place's words are all among the other's (`Bengaluru` within
    `Bengaluru, Karnataka`; `New York City Metropolitan Area` within `New
    York, NY`, once generic words are set aside). `different`: no city word
    in common. `unknown`: either location is missing, or the city name is
    shared but the rest is not one within the other -- `Bronx, New York`
    against `Bronx, NY` (a state spelled out vs abbreviated), or `Springfield,
    IL` against `Springfield, MO` (two real places); nothing here can tell
    those apart, so it does not pretend to. Different spellings of one city
    (`Bangalore`/`Bengaluru`) read as `different` -- the safe direction, since
    the state this feeds decides whether a post is hidden."""
    echo_city, echo_all = _location_words(echo)
    candidate_city, candidate_all = _location_words(candidate)
    if not echo_all or not candidate_all:
        return "unknown"
    if echo_all <= candidate_all or candidate_all <= echo_all:
        return "same"
    return "unknown" if echo_city & candidate_city else "different"


def match_ats_echo_to_registry(
    signal: HiringSignal, candidates: Iterable[RegistryCandidate]
) -> RegistryMatch:
    """Links an `ats_echo` post back to a registry posting, so a listing the
    registry already tracks is not shown a second time as a "hiring post".

    What an echo actually carries is narrow: the COMPANY is the page name
    from the title (`signal.author_name`), and the ROLE and LOCATION are
    `echo_role`/`echo_location` from the fixed sentence (only present when
    the snippet held the whole sentence). A match needs the company (via
    `normalize_company_name`, after also trying the pre-tagline part of the
    name and dropping `Pvt`/`Private`) AND a punctuation/case-insensitive
    equal role title (`C++` and `C#` stay different).

    Four states, not two, because `matched` is the state that hides a post:
    - `undeterminable`: the echo has no company or no role (never guessed).
    - `matched`: company and title equal, and the locations are the same
      place (`_location_relation`).
    - `possible`: company and title equal, but the location cannot confirm
      or refute it (unknown on either side, or a shared city name with an
      unresolvable region) -- the same title at the same company in two
      cities is two openings, so this cannot be called a match. The first
      such candidate is returned for the caller to decide.
    - `unmatched`: no candidate is equal, or every equal one is known to be
      somewhere else.
    Known limits, left as limits: a title spelled differently in the ATS
    ("Sr." vs "Senior"), and a staffing firm posting for a client, which
    matches on the firm, not the client."""
    if signal.species != "ats_echo":
        raise ValueError("match_ats_echo_to_registry needs an ats_echo signal")
    if signal.author_name is None or signal.echo_role is None:
        return RegistryMatch(state="undeterminable")
    company_keys = _company_keys(signal.author_name)
    title_key = _title_key(signal.echo_role)
    if not company_keys or not title_key:
        return RegistryMatch(state="undeterminable")
    possible: RegistryCandidate | None = None
    for candidate in candidates:
        if _company_key(candidate.company) not in company_keys:
            continue
        if _title_key(candidate.title) != title_key:
            continue
        relation = _location_relation(signal.echo_location, candidate.location)
        if relation == "same":
            return RegistryMatch(state="matched", candidate=candidate)
        if relation == "unknown" and possible is None:
            possible = candidate
    if possible is not None:
        return RegistryMatch(state="possible", candidate=possible)
    return RegistryMatch(state="unmatched")


@dataclass(frozen=True)
class PullAnalysis:
    """The result of `analyze_pull`: surviving signals plus the accounting
    a caller needs to report what happened to the rest."""

    signals: tuple[HiringSignal, ...]
    rejected: tuple[RejectedHit, ...]
    duplicates_dropped: int
    role_filtered_out: int


def analyze_pull(hits: Iterable[RawSearchHit], *, role_terms: Sequence[str] = ()) -> PullAnalysis:
    """Parse -> aggregator-tag -> dedupe -> role-filter over ONE provider
    pull, in that order on purpose: aggregator tagging must see the whole
    pull, so a role filter (or a dedupe) cannot pull an aggregator's post
    count under the threshold before it is counted.

    Duplicates are merged by `dedupe_signals` (one rule, not a second copy
    here), and the role filter keeps a post when ANY of its copies' text
    matches -- a snippet cut before the role words must not lose a post whose
    other copy carries them."""
    _require_term_sequence(role_terms)
    signals: list[HiringSignal] = []
    hits_by_id: dict[str, list[RawSearchHit]] = {}
    rejected: list[RejectedHit] = []
    for hit in hits:
        parsed = parse_hit(hit)
        if isinstance(parsed, RejectedHit):
            rejected.append(parsed)
        else:
            signals.append(parsed)
            hits_by_id.setdefault(parsed.activity_id, []).append(hit)

    survivors = dedupe_signals(tag_aggregator_sources(signals))
    kept = tuple(
        signal
        for signal in survivors
        if any(hit_matches_roles(hit, role_terms) for hit in hits_by_id[signal.activity_id])
    )
    return PullAnalysis(
        signals=kept,
        rejected=tuple(rejected),
        duplicates_dropped=len(signals) - len(survivors),
        role_filtered_out=len(survivors) - len(kept),
    )


# ── query builder ────────────────────────────────────────────────────────


class Locale(StrEnum):
    INDIA = "india"
    GLOBAL = "global"


class Freshness(StrEnum):
    """How recent a post must be. Provider-specific parameter mapping
    (`tbs=qdr:d`, `freshness=pd`, ...) is the P3 adapters' job."""

    DAY = "day"
    THREE_DAYS = "3days"
    WEEK = "week"


INDIA_VOCABULARY: tuple[str, ...] = (
    "we're hiring",
    "hiring for",
    "I'm hiring",
    "immediate joiners",
    "walk-in",
    "notice period",
    "WFO",
    "DM your resume",
    "share your CV at",
    "looking for",
)
GLOBAL_VOCABULARY: tuple[str, ...] = (
    "we're hiring",
    "we are hiring",
    "#hiring",
    "my team is hiring",
    "hiring now",
)
"""The design's curated lists. The India order is by observed yield in the
first real pulls (a qualitative read of ~200 results, not a measurement):
"looking for" produced the most job-seeker noise ("looking for my next
role") and goes last, "immediate joiners" and "walk-in" surfaced the
posts unique to that market. Only the first `MAX_VOCAB_TERMS` are used, so
the order is the priority -- which means the last four India terms (`WFO`,
`DM your resume`, `share your CV at`, `looking for`) never reach a query
today; they are the reserve behind the six that do, kept in the tuple for the
day a cap is raised or a term is measured out of the top six, not a claim
that they are searched.

The global list is the result of a real pull: `join us` and `open role`
returned mostly event invitations, keynote sign-ups and conference posts
(one Singapore query held one hiring post in ten) and were dropped, and the
plain `we are hiring` and `#hiring` -- both very common in real hiring posts
and absent before -- took their place. That is a qualitative fix from
reading the results, not a per-term yield measurement; measuring yield per
term needs live provider calls and belongs with the P3 adapters."""

MAX_VOCAB_TERMS = 6
MAX_ROLE_TERMS = 4
MAX_QUERY_CHARS = 350
MAX_QUERY_WORDS = 32
"""Caps chosen against the tightest documented limits among the providers:
Google-backed search (Serper) limits a query to 32 words and silently drops
the rest -- which would drop the metro at the END of this query -- and
Brave's `q` parameter is capped at 400 characters and 50 words. 350
characters leaves margin under Brave's ceiling; 32 words is Google's. Terms
past the term caps are dropped in the order given (and reported in the
returned query); a query that still exceeds the character or word cap is an
error, never silently truncated mid-term."""

_MAX_TERM_CHARS = 60
_QUERY_UNSAFE_RX = re.compile(r'["()\\:]')


@dataclass(frozen=True)
class HiringQuery:
    """A provider-agnostic query string plus the freshness the caller asked
    for, and the exact vocabulary and role terms that made it in (after
    capping) -- a caller that role-filters results should filter with
    `role_terms`, not the list it passed in."""

    query: str
    freshness: Freshness
    locale: Locale
    vocabulary: tuple[str, ...]
    role_terms: tuple[str, ...]


def _clean_query_term(raw: str) -> str:
    """Role terms, metro and company come from users: quotes, parentheses
    and colons would let one break out of its own quoted phrase and inject
    operators (`site:`, grouping), so they are removed rather than escaped.
    A leading `-` (exclusion operator) is dropped too."""
    term = " ".join(_QUERY_UNSAFE_RX.sub(" ", raw).split()).lstrip("-").strip()
    if len(term) > _MAX_TERM_CHARS:
        raise ValueError(f"query term longer than {_MAX_TERM_CHARS} characters: {term[:20]!r}...")
    return term


def _optional_query_term(raw: str | None) -> str:
    """A blank metro/company means "not given". One that is present but
    sanitizes to nothing (`'"()"'`) is an error -- silently dropping it
    would widen the search past what the caller asked for."""
    if raw is None or not raw.strip():
        return ""
    cleaned = _clean_query_term(raw)
    if not cleaned:
        raise ValueError("metro/company has no usable characters after sanitizing")
    return cleaned


def _dedupe_terms(terms: Iterable[str], limit: int) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term and term.casefold() not in seen:
            seen.add(term.casefold())
            out.append(term)
    return tuple(out[:limit])


def _or_group(terms: Sequence[str]) -> str:
    return "(" + " OR ".join(f'"{term}"' for term in terms) + ")"


def build_query(
    *,
    role_terms: Sequence[str],
    locale: Locale,
    freshness: Freshness,
    metro: str | None = None,
    company: str | None = None,
) -> HiringQuery:
    """`site:linkedin.com/posts (hiring vocabulary OR'd) ["company"]
    (role terms OR'd) ["metro"]`.

    `role_terms` is supplied by the caller -- no role-synonym expansion
    table exists in this codebase, and this module does not invent one.
    The tab path passes a `metro` and no company (company-less discovery is
    its whole point); the per-JD path passes a `company` and no metro
    (the company name is what makes it precise). Passing both is a
    `ValueError`, as is an empty role list or a query over the caps.

    Every user-supplied term is sanitized (see `_clean_query_term`) and
    quoted; the vocabulary is this module's own."""
    _require_term_sequence(role_terms)
    if metro and metro.strip() and company and company.strip():
        raise ValueError("pass a metro (tab) or a company (per-JD), not both")
    roles = _dedupe_terms((_clean_query_term(r) for r in role_terms), MAX_ROLE_TERMS)
    if not roles:
        raise ValueError("at least one role term is required")
    vocabulary = _dedupe_terms(
        INDIA_VOCABULARY if locale is Locale.INDIA else GLOBAL_VOCABULARY, MAX_VOCAB_TERMS
    )

    parts = ["site:linkedin.com/posts", _or_group(vocabulary)]
    clean_company = _optional_query_term(company)
    if clean_company:
        parts.append(f'"{clean_company}"')
    parts.append(_or_group(roles))
    clean_metro = _optional_query_term(metro)
    if clean_metro:
        parts.append(f'"{clean_metro}"')
    query = " ".join(parts)

    if len(query) > MAX_QUERY_CHARS or len(query.split()) > MAX_QUERY_WORDS:
        raise ValueError(
            f"query exceeds {MAX_QUERY_CHARS} characters / {MAX_QUERY_WORDS} words "
            f"({len(query)} / {len(query.split())}); shorten the role terms, metro or company"
        )
    return HiringQuery(
        query=query,
        freshness=freshness,
        locale=locale,
        vocabulary=vocabulary,
        role_terms=roles,
    )
