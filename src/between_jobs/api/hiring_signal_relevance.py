"""Hiring Signals P3 -- which of a provider's hits are ABOUT the application's
company, how well they fit its role, and how recent they are. Pure and
deterministic: no I/O, no clock (callers pass `now`), no LLM.

**Company: a hard filter, tightened by what real data showed.** The design
decided that a hit is on-topic only if the company appears in it, and dropped
otherwise. Checked against real captures (Firecrawl, 38 hits for two
companies -- one a large company whose name is also an ordinary word, one known
by an acronym that is also a ticker -- hand-labelled hiring-post-about-this-
company or not) the plain rule "the company name appears anywhere in title,
snippet, author or handle" is necessary but not sufficient:

- **Nothing about the company at all** is what the rule is for, and it works:
  for a small company the index returned ten `/posts/` results none of which
  named it, and all ten were dropped -- the honest empty answer.
- **A mention is not a hiring post about it.** Kept by the plain rule and
  wrong: a stock-ticker post naming the company beside another, a hardware spec
  sheet, a skills list, an acquisition headline, a candidate's own profile
  headline (`Data Engineer @<Company>`), and `ex-<Company>` in a recruiter's
  pitch. On those 34 kept hits the plain rule's precision was 0.53 (recall
  1.00).

The refinement keeps the rule's shape and only says WHERE the name has to
appear. It counts when the company is (a) in the result title -- which is also
where a company page's own name is, since the parser reads the author out of the
title --, (b) the author's vanity HANDLE (the company, or the company plus
`Careers`/`Jobs`/...; see `CompanyNames.is_page_name`), or (c) in the
OPENING of the post text (the snippet up to its first `...` elision, at most
`OPENING_CHARS` characters, after the provider's relative-time stamp). A
mention later in the snippet is where the skills lists, other people's
headlines and comment fragments sit; it no longer counts. On the same labelled
hits that lifts precision to 0.71 at recall 0.94 (one true post lost). The
opening is defined by position, not by hiring vocabulary, so it works in every
language the index returns -- a proximity-to-"hiring" rule scored higher
precision (0.82) but lower recall (0.78) and would have been English-only,
which the parser's own measurements (44% of un-tagged posts with no English
hiring word were non-English hiring calls) argue against. The numbers are
small-sample: they justify the direction, not a decimal. (Re-measured on the
same labelled hits after the "A person is not the company" rules below: the
same 17 kept true, 7 kept false, 1 missed -- those rules were found with
synthetic names, and change nothing on the captures.)

**A person is not the company.** Two rules keep a stranger's NAME from being
read as a mention of the company (both found by feeding the filter names,
which the captures above did not contain):

- (b) is an equality, not a containment: a handle `jordan-block-4b1c9e02` is not
  Block's. (Before this, a token of the author's name or handle was enough, so a
  person named `Block` was shown as a `Block` hiring signal, ranked first.)
- The author's name is taken OUT of the title before (a) reads it when it is
  someone else's name (`display_author` says it looks like one and it is not
  the company's own page name): `Jordan Block's Post - LinkedIn`, and `Square One
  Search Partners - LinkedIn` (another firm whose name starts with the company's
  word), mention the company only if the company is the name.

The company itself is matched by `hiring_signal_company`: every spelling a post
may use (`Meta` for `Meta Platforms, Inc.`, `AT&T`, `ScaleAI`, an accented or
CJK name), not one normalized string.

Known limits, left as limits: a company the post names differently from the
application (`AMD` vs `Advanced Micro Devices`, unless the name carries the
alias) is missed when only the other name is used; a post that names the
company only after its opening is missed; and a company that is also an
ordinary word (`Block`, `Apple`, `Target`) still matches a post that uses the
word in its opening (`Target: 20 hires by June`) or a longer name that starts
with it (`Apple Valley Staffing` in a title) -- telling those apart needs
reading the sentence, which this filter never does.

**Role: a soft rank, never a filter.** A post about another role at the same
company is still a useful signal, so a role mismatch hides nothing. Role match
is true (a role phrase, or its plural, appears as whole words in some copy of
the post's title or snippet), false, or `None` when it cannot be told -- no
role terms were derived, every term is too short for the shared predicate to
test (single characters are dropped), or the role is written in a script
without word boundaries and did not match. Unknown stays unknown.

**Job seekers: the opening decides.** The parser tags a post `job_seeker` from
its whole snippet, and a snippet joins fragments that include OTHER people's
lines -- a commenter's `#OpenToWork` headline sits after the first elision of a
real hiring post. `is_job_seeker_post` therefore asks whether the OPENING of
some copy says it, and a post only its later fragments say it about is not a
job seeker's post.

**Recency: deterministic.** The window is enforced from the post time decoded
off the activity id (`posted_at`), not from the provider's own freshness
parameter, which is only a way to save results.

**An author is shown only when it agrees with the url's handle** (`shown_author`).
`display_author` is a shape test, and title-cased headline text has the shape of a
name: the title `Hiring Backend Software Engineers Bengaluru posted` is read by the
parser as the author `Hiring Backend Software Engineers Bengaluru`, which the
response contract forbids sending (no title text). The parser trusts some title
positions without checking the url, so the response's author is a name that ALSO
agrees with the handle LinkedIn derived from it, and is unknown otherwise (a url
with no handle has nothing to agree with). Company matching above still reads the
parser's own author (`display_author`): that decision never leaves the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from .hiring_signal_company import (
    LEGAL_FORMS,
    CompanyNames,
    has_unspaced_script,
    identity_keys_of,
    tokens_of,
)
from .hiring_signal_query import clean_display_text, role_phrase_variants
from .hiring_signals import (
    HiringSignal,
    RawSearchHit,
    RelativeAge,
    classify_species,
    hit_matches_roles,
)
from .search_aggregation import role_term_patterns

OPENING_CHARS = 200
"""How much of a snippet's opening counts as "the post text starts here"."""

_AGE_STAMP_RX = re.compile(
    r"^\s*(?:[0-9]{1,3}\s+(?:minute|hour|day|week|month)s?\s+ago|just now)\s*[·•]\s*",
    re.IGNORECASE,
)
_ELISION_RX = re.compile(r"\s\.\.\.\s|…")
# ` - LinkedIn` and its Chinese-site twin, at the end of a title. The two code
# points are built rather than written as escapes (see hiring_signal_company).
_SITE_SUFFIX_RX = re.compile(
    rf"\s[-|{chr(0x2013)}]\s(?:LinkedIn|{chr(0x9886)}{chr(0x82F1)})\s*$", re.IGNORECASE
)

MAX_FUTURE_SKEW = timedelta(days=1)
"""A decoded post time this far past `now` is not a real post time (clock skew
between us and the index is minutes, not days); it is reported as unknown."""

MAX_AUTHOR_CHARS = 100
MAX_AUTHOR_WORDS = 6
_NAME_FORBIDDEN_RX = re.compile(r'[,;:!?#@|/\\<>{}\[\]=~^*"]')
_DIGIT_RUN_RX = re.compile(r"[0-9]{2,}")
_LEGAL_COMMA_RX = re.compile(r",\s*(?:inc|llc|ltd|llp|corp|co|plc|gmbh)\.?$", re.IGNORECASE)
_NAME_PARTICLES = frozenset(
    {
        "of",
        "the",
        "and",
        "for",
        "in",
        "at",
        "de",
        "del",
        "della",
        "di",
        "da",
        "du",
        "van",
        "von",
        "der",
        "den",
        "la",
        "le",
        "el",
        "al",
        "bin",
        "ibn",
        "e",
        "y",
        "et",
        "und",
        "dos",
        "das",
        "do",
        "ter",
        "ten",
    }
)


def display_author(name: object) -> str | None:
    """`name` as an author's display name, or `None` when it does not look like
    one. The parser takes the author out of the title by shape, and some shapes
    (`#hiring | <last segment>`, `<content> posted`) hand back a stretch of the
    POST TEXT instead; an author field is allowed to carry a name and nothing
    more, so a string is shown only if it reads as one: control, format and
    bidi characters removed; at most `MAX_AUTHOR_WORDS` words; none of the
    punctuation a sentence has and a name does not (`, ; : ! ? # @ | /` ...;
    the comma of `Stripe, Inc.` is the one exception);
    no run of two digits; and every word capitalized, or a name particle
    (`van`, `of`, `and`...), or carrying a capital inside (`eBay`). An
    all-lowercase name is therefore unknown -- the safe direction: a wrong
    string that looks like a name is worse than an honest blank."""
    cleaned = clean_display_text(name, MAX_AUTHOR_CHARS)
    # `Stripe, Inc.` is the one place a company name carries a comma
    core = _LEGAL_COMMA_RX.sub("", cleaned)
    if not cleaned or _NAME_FORBIDDEN_RX.search(core) or _DIGIT_RUN_RX.search(core):
        return None
    words = cleaned.split()
    if len(words) > MAX_AUTHOR_WORDS:
        return None
    for word in words:
        if word.casefold() in _NAME_PARTICLES:
            continue
        if word[0].isalpha() and word[0].islower() and not any(ch.isupper() for ch in word):
            return None
    return cleaned


_HANDLE_ID_SUFFIX_RX = re.compile(r"[0-9a-f]{6,10}")
_TRADEMARK_MARKS_RX = re.compile("[™®©℠]")
_TAGLINE_SEPARATOR = " - "


def _handle_forms(handle: str) -> tuple[frozenset[str], str]:
    """`(tokens, run-together key)` of a url handle, without LinkedIn's trailing
    collision id (`priya-testwell-2f7a91c3` -> `priya`, `testwell`): the last
    token when it is six to ten hexadecimal characters with at least one digit."""
    tokens = tokens_of(handle)
    last = tokens[-1] if tokens else ""
    if len(tokens) > 1 and _HANDLE_ID_SUFFIX_RX.fullmatch(last) and any(c.isdigit() for c in last):
        tokens = tokens[:-1]
    return frozenset(tokens), "".join(tokens)


def shown_author(name: object, handle: str | None) -> str | None:
    """The author's name as it may be SENT in a response, or `None`.

    `display_author` decides whether a string reads as a name; that is a shape
    test, and title-cased headline text has the shape of a name
    (`Hiring Backend Software Engineers Bengaluru`, `Software Engineer Openings
    At Northwind`). The parser takes a name out of a title by position and only
    sometimes checks it against the url, so a stretch of the post's own title can
    arrive here as an author. The contract lets a response carry an author's name
    and no title text, so the name must also AGREE WITH THE URL'S HANDLE -- LinkedIn
    derives a handle from a name, and a stretch of a headline is not in it:

    - every significant word of the name (two or more characters, not a
      particle such as `of`/`de`, not a legal form such as `Inc`) is a word of the
      handle or, from three characters up, sits inside the handle's run-together
      form (`janedoe1` corroborates `Jane Doe`); or
    - the name is a company page's own name, by the same run-together identity the
      registry matching uses (`Northwind Labs` is the page `northwind`; `Scale AI`
      is `scaleai`).

    A page name carries its tagline after ` - ` (`Northwind - Cloud Security`); the
    tagline is post-title text and is cut off before the check, and the part before
    it is what is returned. A url that names no author (`/posts/activity-<id>`) has
    nothing to agree with, so the name is unknown, and so is one that does not agree:
    an honest blank is better than a wrong string that looks like a name."""
    shown = display_author(name)
    if shown is None or not handle:
        return None
    head = _TRADEMARK_MARKS_RX.sub("", shown.split(_TAGLINE_SEPARATOR)[0]).strip()
    if not head:
        return None
    handle_tokens, handle_key = _handle_forms(handle)
    if not handle_key:
        return None
    if handle_key in identity_keys_of(head):
        return head
    significant = [
        token
        for token in tokens_of(head)
        if len(token) >= 2 and token not in _NAME_PARTICLES and token not in LEGAL_FORMS
    ]
    if not significant:
        return None
    for token in significant:
        if token not in handle_tokens and not (len(token) >= 3 and token in handle_key):
            return None
    return head


def opening(snippet: str) -> str:
    """The start of the post text within a snippet: the provider's
    relative-time stamp removed, cut at the first elision (`...` or `…`),
    at most `OPENING_CHARS` characters. A snippet without an elision (a
    provider that does not join fragments) is simply its first
    `OPENING_CHARS` characters."""
    body = _AGE_STAMP_RX.sub("", snippet, count=1)
    return _ELISION_RX.split(body, maxsplit=1)[0][:OPENING_CHARS].strip()


def _title_without_author(title: str, author: str) -> str:
    return re.sub(re.escape(author), " ", title, flags=re.IGNORECASE)


def mentions_company(
    names: CompanyNames | None,
    *,
    hit: RawSearchHit,
    signal: HiringSignal,
) -> bool:
    """See the module docstring for what counts. `names` is
    `company_names(...)`; no company matches nothing."""
    if names is None:
        return False
    author = display_author(signal.author_name)
    author_is_the_company = author is not None and names.is_page_name(author)
    title = clean_display_text(hit.title, 600)
    title = _SITE_SUFFIX_RX.sub("", _SITE_SUFFIX_RX.sub("", title))
    if author is not None and not author_is_the_company:
        title = _title_without_author(title, author)
    # (The author's NAME needs no rule of its own: the parser reads it out of the
    # title, so a company page's name is in `title` and rule (a) finds it.)
    if names.found_in(title):
        return True
    if signal.author_handle and names.is_page_name(signal.author_handle):
        return True
    return names.found_in(opening(hit.snippet))


def is_job_seeker_post(hits: Iterable[RawSearchHit]) -> bool:
    """Whether the OPENING of some copy of the post (its title and the first
    fragment of its snippet) carries a first-person job-seeker marker -- see
    the module docstring's "Job seekers" section."""
    return any(classify_species(hit.title, opening(hit.snippet)) == "job_seeker" for hit in hits)


def role_match(hits: Iterable[RawSearchHit], role_terms: Sequence[str]) -> bool | None:
    """`True` when ANY copy of the post (one activity id can come back as
    several hits) names a role phrase -- or its plural -- as whole words in its
    title or snippet; `False` when none does; `None` when that cannot be
    told (see the module docstring)."""
    if not role_terms:
        return None
    phrases = [variant for term in role_terms for variant in role_phrase_variants(term)]
    testable = [phrase for phrase in phrases if role_term_patterns(phrase)]
    if not testable:
        return None
    if any(hit_matches_roles(hit, testable) for hit in hits):
        return True
    if any(has_unspaced_script(phrase) for phrase in testable):
        return None
    return False


def visible_posted_at(posted_at: datetime | None, *, now: datetime) -> datetime | None:
    """The decoded post time, or `None` when it is missing or lies in the
    future beyond `MAX_FUTURE_SKEW` (which is a parsing artifact, not a
    post)."""
    if posted_at is None or posted_at > now + MAX_FUTURE_SKEW:
        return None
    return posted_at


def is_too_old(posted_at: datetime | None, *, now: datetime, window_days: int) -> bool:
    """Older than the requested window. An unknown time (`None`) is NOT too
    old -- unknown is kept and labelled, never guessed at."""
    return posted_at is not None and posted_at < now - timedelta(days=window_days)


def age_hint(age: RelativeAge | None) -> str | None:
    """The index's own relative-time stamp as display text (`3 days ago`,
    `just now`); `None` when the index gave none."""
    if age is None:
        return None
    if age.amount == 0:
        return "just now"
    plural = "" if age.amount == 1 else "s"
    return f"{age.amount} {age.unit}{plural} ago"


def rank_key(
    *, role_match_value: bool | None, posted_at: datetime | None, position: int
) -> tuple[int, float, int]:
    """Sort key: exact-role posts first, then newest, then input order (so
    the ranking is deterministic). An unknown post time sorts after every
    known one."""
    return (
        0 if role_match_value is True else 1,
        -posted_at.timestamp() if posted_at is not None else float("inf"),
        position,
    )
