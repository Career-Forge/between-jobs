"""Hiring Signals P4 -- the standalone tab's pure logic: what a person TYPED
(a role, and optionally a metro), turned into a provider query, a role phrase
to filter on and a human label. No I/O, no clock, no LLM.

The per-application panel starts from a stranger's text too (a job title and a
company scraped from a posting), but it can lean on a company name to make its
query precise. The tab has no company on purpose -- it is the hidden-market
surface, "a founder or hiring manager posting we're hiring, DM me with no
requisition anywhere" -- so the role and the metro are all there is, and both
were typed by the user of the page.

**Untrusted input.** A person's typing is still input from outside the trust
boundary: quotes, parentheses and colons that would let a term break out of its
quoted phrase and inject `site:` or grouping operators, control and bidi
characters, an absurd length, another script. Everything typed goes through
`hiring_signal_query.clean_display_text` (control, format, private-use and
unassigned characters removed, every whitespace run collapsed to one space) and
then the operator strip below, and `hiring_signals.build_query` -- the same
builder the per-application query uses, so the caps, the vocabulary and the
operator stripping are shared -- has the last word: it quotes every term and
refuses an over-long one. Over-length input is REFUSED (`InvalidSearchInput`, a
422 at the route), never silently cut: the typed text is the user's search.

**The role is used as typed, not as a job title.** The per-application path
strips level words (`Senior`, `Staff`, `II`) off a job title because a title's
level is not what a company's hiring post is about; a person searching for a
`senior data engineer` may want exactly those words, so none of that runs here.
What runs is smaller and is documented, not clever:

1. the operator/punctuation characters in `_SEPARATOR_RX` -- quotes, brackets,
   colons, commas, slashes, ampersands, hyphens and dashes and a few more --
   become spaces, so `AI/ML engineer` and `full-stack developer` are read as
   the words a post would contain (`full` and `stack` each appear in a post that
   says `full-stack`, `full stack` or `Full Stack`). `+`, `#`, `.`, `'` and `_`
   are NOT separators: `C++`, `C#`, `.NET`, `Node.js` and `Sr.` are one word each
   and stay that way. (A one-character word is never tested -- the shared
   predicate drops it -- so `R developer` filters on `developer` alone; that is
   the predicate's own documented limit, inherited, and a role with NO
   two-character word is refused rather than searched unfiltered);
2. the phrase is lower-cased and cut at `MAX_ROLE_WORDS` words / the 60
   characters `build_query` allows for one term, at a word boundary, and a cut is
   never left ending on a word that only introduces the next one (`... engineer
   in` of `... engineer in test` is `... engineer`). A typed role longer than that
   is searched -- and filtered -- as its leading words, and the response's
   `query_label` says which words those were.

A known limit, left as one: a slash reads as a space, so `frontend/backend
engineer` is searched and filtered as `frontend backend engineer`, which needs BOTH
words in a post. Reading it as "either" would mean distributing the shared tail
(`frontend engineer` OR `backend engineer`) and `24/7 support` shows why that is not
a rule to guess at.

**Location is part of the provider query only.** A search index gives no
structured location for a post, so nothing here (or in the service) verifies a
post is in the metro that was typed, or filters on it, and the UI says so. It
only shapes the query (`build_query`'s `metro`), and it goes in as its FIRST
COMMA-SEPARATED PART: `Bengaluru, Karnataka, India` is asked for as `Bengaluru`.
That was an observation, not a guarantee: on 2026-09-20 a Firecrawl search for
`software engineer` over 3 days returned 20 posts with the place as `Bengaluru`
and 6 with it as `Bengaluru Karnataka India` (the space-joined spelling, the form
that could be tried without a second query shape; the raw captures hold real
people's names and are kept outside the repository, so no committed fixture backs
the numbers). Posts say `Bengaluru` and almost never spell out the state and
country beside it, which is the reason for the rule, and the rule is cheap to
revisit if a provider's index behaves differently. The part is also cut to what
`build_query` accepts (`MAX_LOCATION_WORDS` words, 60 characters) rather than
refused when it runs long.

**The label says what was searched.** The place is shown as typed AND, when the
provider was asked for less than that, as what it was asked for: `Portland, OR
(searched as Portland)`. Dropping the state or country is a real narrowing
(`Portland` is two different places), and a label that showed only the typed
text would claim a search that was not run. What this does NOT do is know that
`Bangalore` is `Bengaluru` (no synonym table exists in this codebase and none is
invented here): the two spellings are different queries, and on 2026-09-20 they
returned posts that overlapped on 3 of 20.

**The hiring vocabulary follows the locale.** The request may name one; without
it the locale is derived from the location (`locale_for_location`: an India
metro reads as `india`, anything else -- including no location -- as `global`,
never a guess at India).

**The role filter is hard, and it is AND -- and it asks WHERE the role is said.**
`tab_role_fit` keeps a post only if EVERY word of a role phrase appears as a whole
word in it -- the shared predicate P3 ranks with (`search_aggregation.
role_term_patterns` / `matches_all_role_terms`, symbol-safe: `c++`, `.net`) -- and
a post that fails it is counted in `role_mismatch_hidden`. Which phrases, and
which text, are the two things that were tuned against real provider answers:

- *The phrases* (`role_filter_terms`): the role as typed, plus the spellings a post
  would use for the same role. The plural of the LAST word is accepted
  (`software engineers` for `software engineer`), because a hiring post says
  `we're hiring software engineers`; a role typed in the plural is also tested as
  its singular, because a post says `we're hiring a data engineer` -- except where
  the singular is an ordinary word (`news` -> `new` would match LinkedIn's own
  `we're #hiring a new ...`, `sales` -> `sale`, `jobs` -> `job`). And a small,
  documented table of equivalent spellings is applied (`_EQUIVALENT_WORDS`:
  `full stack`/`fullstack`, `front end`/`frontend`, `back end`/`backend`,
  `senior`/`sr`/`sr.`, `machine learning`/`ml`, `2`/`ii`, ...), because in the
  2026-09-20 captures (kept outside the repository) the exact-words rule hid
  genuine posts for exactly these differences. It is a table of spellings, not a
  thesaurus: there is no attempt at synonyms.
- *A digit is a word.* The shared predicate drops one-character words (n8n
  behavior, kept for Job Finder), which would make `SDE-2` filter on `sde` alone
  and keep every `SDE-1` and `SDE 3`. A one-digit word of the role is therefore
  tested here as a whole token of its own.
- *The text* (`tab_role_fit`): a snippet joins fragments from all over the page,
  and after the first elision they are other people's lines -- a commenter's
  profile headline (`Software Engineer @ Acme`), a skills list, a `Report this
  comment`. The role has to be said in the post's TITLE or its OPENING (the same
  region the company and job-seeker rules read, `hiring_signal_relevance.
  opening`) to count as *stated*. A LinkedIn auto job-share (`ats_echo`) says its
  role in one fixed sentence, so for one the role is the parsed `echo_role`, and
  nothing later in the snippet can rescue a different role. For any other post a
  role said ONLY after the opening is not thrown away -- a genuine `Role: Software
  Engineer` line sits there as often as a stranger's headline -- but it is
  *unverified*: shown, and ranked below every post that states the role.

A role in a script written without spaces (CJK) cannot be word-matched; the fit is
`unknown` for it, and unknown is kept, not hidden.

**Ranking.** Non-aggregator accounts first, then posts that state the role before
posts that only mention it late, then the freshest post, then the provider's own
order (`tab_rank_key`) -- deterministic. An aggregator (an account with many
distinct posts in one pull, `hiring_signals.AGGREGATOR_MIN_POSTS`) is ranked
BELOW everyone else, never hidden: it is still a useful discovery source. (On
2026-09-20 one job-alert account held 11 of the 17 results of one pull; the
figure is an observation from captures kept outside the repository.)
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .hiring_signal_company import has_unspaced_script
from .hiring_signal_query import (
    clean_display_text,
    freshness_label,
    locale_for_location,
    role_phrase_variants,
)
from .hiring_signal_relevance import opening
from .hiring_signals import (
    Freshness,
    HiringQuery,
    HiringSignal,
    Locale,
    RawSearchHit,
    build_query,
)
from .search_aggregation import matches_all_role_terms, role_term_patterns

MAX_QUERY_CHARS = 200
"""What the request allows a typed role to be (and what is stored for a saved
search). Longer is refused."""
MAX_LOCATION_CHARS = 100
"""What the request allows a typed location to be."""
MAX_ROLE_WORDS = 5
MAX_TERM_CHARS = 60
"""`build_query` refuses a single term longer than this; the role phrase and the
location term are cut to fit before they get there."""
MAX_LOCATION_WORDS = 6
MAX_LABEL_LOCATION_CHARS = 80
MAX_ROLE_PHRASES = 12
"""The most phrases a role is tested as (the typed role, its equivalent spellings
and their singulars). A role with several equivalent-word groups multiplies; this
bounds the work whatever is typed, and the typed role is always the first."""

TAB_DEFAULT_FRESHNESS = Freshness.THREE_DAYS
"""The window a search uses when the request names none. Chosen from provider
yield observed on 2026-09-20 (Firecrawl, `software engineer`, up to 20 results
asked, two metros): the last 24 hours returned 2 posts in each, 3 days returned 20
(Bengaluru) and 17 (Austin), and a week returned 20 for Bengaluru -- the one metro
tried at that width, and capped by the request, so the counts cannot say whether 3
days or a week holds more. What they do say is that 24 hours is too thin to be a
default and that 3 days already filled a page. An observation on a stated date, not
a guarantee: the raw captures hold real people's names and stay outside the
repository, and a different provider or a quieter metro will differ."""


class InvalidSearchInput(ValueError):
    """What was typed cannot be searched. The message is written for the person
    who typed it (it becomes the 422's message) and never echoes the input."""


# ── normalization ────────────────────────────────────────────────────────

_ASCII_SEPARATORS = '"()[]{}<>\\:;,|&/*?!~^=%$@`-'
_SEPARATOR_CODEPOINTS = (
    0x2010,  # hyphen
    0x2011,  # non-breaking hyphen
    0x2012,  # figure dash
    0x2013,  # en dash
    0x2014,  # em dash
    0x2015,  # horizontal bar
    0x2212,  # minus sign
    0x201C,  # left double quotation mark
    0x201D,  # right double quotation mark
    0x201E,  # double low-9 quotation mark
    0x201F,  # double high-reversed-9 quotation mark
    0x00AB,  # left-pointing double angle quotation mark
    0x00BB,  # right-pointing double angle quotation mark
    0x2039,  # single left-pointing angle quotation mark
    0x203A,  # single right-pointing angle quotation mark
    0x00B7,  # middle dot
    0x2022,  # bullet
)
_SEPARATOR_RX = re.compile(
    "[" + re.escape(_ASCII_SEPARATORS + "".join(chr(c) for c in _SEPARATOR_CODEPOINTS)) + "]"
)
"""Characters read as a gap between words (see the module docstring, "The role is
used as typed"). The quote/paren/backslash/colon subset is what `build_query`
would strip anyway; the rest are what a person writes BETWEEN words, and what
would otherwise glue two words into one that no post contains."""

_APOSTROPHES = "'" + chr(0x2019)

_DANGLING_WORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
)
"""Words a role phrase that had to be cut is never left ending on."""


def _clean(value: object, limit: int, what: str) -> str:
    """`clean_display_text`, except over-length is refused instead of cut."""
    if not isinstance(value, str):
        raise InvalidSearchInput(f"{what} must be text.")
    cleaned = clean_display_text(value, limit + 1)
    if len(cleaned) > limit:
        raise InvalidSearchInput(f"{what} is limited to {limit} characters.")
    return cleaned


def role_phrase(text: str) -> str:
    """The role words a search is about, from what was typed: the phrase that
    goes into the provider query AND that a post is filtered on, so what is asked
    for is what is kept. See the module docstring for the rule. Raises
    `InvalidSearchInput` when there is nothing to search (no letter or digit, no
    word of two or more characters, or a word longer than a query term may be)."""
    text = clean_display_text(text, len(text))
    words = [w.strip(_APOSTROPHES) for w in _SEPARATOR_RX.sub(" ", text).lower().split()]
    words = [w for w in words if any(ch.isalnum() for ch in w)]
    if any(len(w) > MAX_TERM_CHARS for w in words):
        raise InvalidSearchInput(f"A word in the role is longer than {MAX_TERM_CHARS} characters.")
    kept: list[str] = []
    length = 0
    for word in words[:MAX_ROLE_WORDS]:
        length += len(word) + (1 if kept else 0)
        if length > MAX_TERM_CHARS:
            break
        kept.append(word)
    if len(kept) < len(words):
        # The cut is ours, not the person's: never leave it on a word that only
        # introduces the next one (`... engineer in` of `... engineer in test`).
        while kept and kept[-1] in _DANGLING_WORDS:
            kept.pop()
    phrase = " ".join(kept)
    if not phrase:
        raise InvalidSearchInput("Type a role to search for, such as data engineer.")
    if not role_term_patterns(phrase):
        # every word is one character: nothing the shared predicate could test,
        # so a search would return the index's answer to a near-empty query
        raise InvalidSearchInput(
            "Type a role with at least one word of two or more characters, such as data engineer."
        )
    return phrase


_EQUIVALENT_WORDS: tuple[tuple[tuple[str, ...], ...], ...] = (
    (("full", "stack"), ("fullstack",)),
    (("front", "end"), ("frontend",)),
    (("back", "end"), ("backend",)),
    (("cyber", "security"), ("cybersecurity",)),
    (("dev", "ops"), ("devops",)),
    (("senior",), ("sr",), ("sr.",)),
    (("junior",), ("jr",), ("jr.",)),
    (("machine", "learning"), ("ml",)),
    (("quality", "assurance"), ("qa",)),
    (("node.js",), ("nodejs",), ("node", "js")),
    (("2",), ("ii",)),
    (("3",), ("iii",)),
    (("4",), ("iv",)),
)
"""Spellings a post uses for the SAME role word. Each row is a group of forms that
may replace one another wherever one of them appears as whole words of the role
phrase. This is a table of spellings the real answers showed the exact-words rule
losing (`Fullstack Developer` for `full stack developer`, `Sr. Software Engineer`
for `senior software engineer`, `SDE II` for `SDE 2`), not a thesaurus: `developer`
and `engineer` are different words and stay so."""

_TOO_COMMON_SINGULARS = frozenset({"new", "sale", "job", "role", "hire"})
"""A plural role word whose singular is an ordinary English word is not turned into
it: `news` is not searched as `new` (LinkedIn's own auto-post says `we're #hiring a
new ...`), `sales` not as `sale` (`Estate sale`), `jobs` not as `job`."""


def _expansions(tokens: tuple[str, ...]) -> list[tuple[str, ...]]:
    """`tokens` and every variant obtained by replacing a run of words with an
    equivalent spelling (`_EQUIVALENT_WORDS`), groups applied one after another so
    two different words of one role can each be respelled. The typed spelling is
    always first; the result is bounded by `MAX_ROLE_PHRASES`."""
    found: list[tuple[str, ...]] = [tokens]
    for group in _EQUIVALENT_WORDS:
        additions: list[tuple[str, ...]] = []
        for current in found:
            for at in range(len(current)):
                for form in group:
                    if current[at : at + len(form)] != form:
                        continue
                    additions.extend(
                        (*current[:at], *other, *current[at + len(form) :])
                        for other in group
                        if other != form
                    )
        found = list(dict.fromkeys([*found, *additions]))[:MAX_ROLE_PHRASES]
    return found


def _singular(tokens: tuple[str, ...]) -> tuple[str, ...] | None:
    """The phrase with its LAST word made singular, when that word is a plain
    plural: ASCII letters, four or more, ending in `s` (but not `ss`, `us`, `is`, `js`:
    `business`, `campus`, `analysis`, `nodejs`) or in `ies`, and the singular is not an
    ordinary word (`_TOO_COMMON_SINGULARS`). No attempt at irregular plurals: the
    singular is only ever an ADDITIONAL phrase a post may match, and a wrong guess
    (`devops` -> `devop`) is a word no post contains."""
    if not tokens:
        return None
    last = tokens[-1]
    if not (last.isascii() and last.isalpha() and len(last) >= 4):
        return None
    if last.endswith("ies") and len(last) >= 5:
        singular = last[:-3] + "y"
    elif last.endswith("s") and not last.endswith(("ss", "us", "is", "js")):
        singular = last[:-1]
    else:
        return None
    if singular in _TOO_COMMON_SINGULARS:
        return None
    return (*tokens[:-1], singular)


def role_filter_terms(role: str) -> tuple[str, ...]:
    """The role phrases a post is tested against, the role as typed FIRST: its
    equivalent spellings (`_EQUIVALENT_WORDS`) and, for each, the singular of a
    plain plural last word (`data engineers` -> `data engineer`; `analysts` ->
    `analyst`, `technologies` -> `technology`; but not `news` -> `new`).

    `tab_role_fit` (through `role_phrase_variants`) already accepts the plural of
    a SINGULAR phrase; this is the inverse, for a role the person typed in the
    plural, plus the spelling variants. Every phrase here is only an ADDITIONAL
    way a post may say the role: a wrong one is a phrase no post contains, so it
    matches nothing it should not."""
    tokens = tuple(role.split())
    if not tokens:
        return (role,)
    phrases: list[str] = []
    for variant in _expansions(tokens):
        phrases.append(" ".join(variant))
        singular = _singular(variant)
        if singular is not None:
            phrases.append(" ".join(singular))
    return tuple(dict.fromkeys(phrases))[:MAX_ROLE_PHRASES]


RoleFit = Literal["stated", "later", "mismatch", "unknown"]
"""How a post relates to the searched role: `stated` (the role is in its title or
opening, or is the parsed role of an auto job-share), `later` (only in a later
fragment of the snippet: unverified), `mismatch` (not in it), `unknown` (cannot be
told: no word of the role can be tested, or it is written without spaces)."""

_MAX_FIT_TEXT_CHARS = 2000
"""The same ceiling `hiring_signals.hit_matches_roles` reads a hit's text under."""


@dataclass(frozen=True)
class _PhraseTest:
    words: tuple[re.Pattern[str], ...]
    digits: tuple[re.Pattern[str], ...]
    unspaced: bool

    def found_in(self, text: str) -> bool:
        lowered = text.lower()
        return matches_all_role_terms(lowered, self.words) and all(
            digit.search(lowered) for digit in self.digits
        )


def _digit_pattern(digit: str) -> re.Pattern[str]:
    """A one-digit word as a whole token: `sde-2` and `sde 2` have it, `sde-12`
    and `sde2` do not."""
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(digit)}(?![0-9A-Za-z])")


def _phrase_tests(role_terms: Sequence[str]) -> list[_PhraseTest]:
    """One test per phrase to try (each with its plural), leaving out any phrase
    whose words are all too short for the shared predicate to test."""
    tests: list[_PhraseTest] = []
    seen: set[str] = set()
    for term in role_terms:
        for phrase in role_phrase_variants(term):
            if phrase in seen:
                continue
            seen.add(phrase)
            words = role_term_patterns(phrase)
            if not words:
                continue
            digits = tuple(_digit_pattern(w) for w in phrase.split() if len(w) == 1 and w.isdigit())
            tests.append(_PhraseTest(tuple(words), digits, has_unspaced_script(phrase)))
    return tests


def _text_of(hit: RawSearchHit, *, opening_only: bool) -> str:
    snippet = opening(hit.snippet) if opening_only else hit.snippet
    return " ".join(f"{hit.title} {snippet}".split())[:_MAX_FIT_TEXT_CHARS]


def tab_role_fit(
    hits: Sequence[RawSearchHit], signal: HiringSignal, role_terms: Sequence[str]
) -> RoleFit:
    """See the module docstring's "The role filter is hard, and it is AND -- and it
    asks WHERE the role is said". `hits` are all the copies of ONE post (one
    activity id can come back as several results), `signal` its parsed form and
    `role_terms` the phrases from `role_filter_terms`.

    - An auto job-share (`ats_echo`) says its role in one fixed sentence: with the
      parsed `echo_role`, that is the whole answer (`stated` or `mismatch`); without
      it (the snippet was cut before the sentence's end) the title and the opening
      decide. Nothing after the opening is read for an echo -- the fragments there
      are other people's, and the role is not in them.
    - Any other post is `stated` when some copy names the role in its title or
      opening, `later` when only a later fragment does, `mismatch` when none does.
    """
    tests = _phrase_tests(role_terms)
    if not tests:
        return "unknown"
    if signal.species == "ats_echo" and signal.echo_role is not None:
        role_text = " ".join(signal.echo_role.split())[:_MAX_FIT_TEXT_CHARS]
        if any(test.found_in(role_text) for test in tests):
            return "stated"
    elif any(t.found_in(_text_of(hit, opening_only=True)) for hit in hits for t in tests):
        return "stated"
    if signal.species != "ats_echo" and any(
        t.found_in(_text_of(hit, opening_only=False)) for hit in hits for t in tests
    ):
        return "later"
    if any(test.unspaced for test in tests):
        return "unknown"
    return "mismatch"


def location_term(text: str) -> str:
    """The metro as a provider query term: the first comma-separated part of
    what was typed that has a letter or digit in it (see the module docstring),
    with the characters that would break out of a quoted phrase taken out, cut at
    a word boundary to what `build_query` accepts. Raises `InvalidSearchInput`
    when nothing usable is left (silently dropping a place the person typed would
    widen the search past what they asked for)."""
    text = clean_display_text(text, len(text))
    words: list[str] = []
    for part in text.split(","):
        words = [w for w in _SEPARATOR_RX.sub(" ", part).split() if any(c.isalnum() for c in w)]
        if words:
            break
    if any(len(w) > MAX_TERM_CHARS for w in words):
        raise InvalidSearchInput(
            f"A word in the location is longer than {MAX_TERM_CHARS} characters."
        )
    kept: list[str] = []
    length = 0
    for word in words[:MAX_LOCATION_WORDS]:
        length += len(word) + (1 if kept else 0)
        if length > MAX_TERM_CHARS:
            break
        kept.append(word)
    if not kept:
        raise InvalidSearchInput("The location has no usable letters or digits.")
    return " ".join(kept)


def _same_words(searched: str, typed: str) -> bool:
    """Whether the place the provider was asked for holds exactly the words that
    were typed (punctuation between them aside), so the label only says `searched as`
    when something -- a state, a country, an over-long tail -- really was left out."""
    typed_words = [w for w in _SEPARATOR_RX.sub(" ", typed).split() if any(c.isalnum() for c in w)]
    return " ".join(typed_words).casefold() == searched.casefold()


def normalize_query_text(raw: object) -> str:
    """A typed role as it is stored for a saved search: control, format and
    separator characters removed, whitespace collapsed, at most
    `MAX_QUERY_CHARS` characters. Refuses text that could not be searched, so
    every saved search is one a click can run."""
    cleaned = _clean(raw, MAX_QUERY_CHARS, "The role")
    role_phrase(cleaned)
    return cleaned


def normalize_location_text(raw: object) -> str | None:
    """A typed metro as it is stored: the same cleaning, at most
    `MAX_LOCATION_CHARS` characters, and `None` for a blank one."""
    if raw is None:
        return None
    cleaned = _clean(raw, MAX_LOCATION_CHARS, "The location")
    if not cleaned:
        return None
    location_term(cleaned)
    return cleaned


# ── the query ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TabQuery:
    """Everything the service needs from what was typed.

    `query` is the provider query and NEVER leaves the server: it is what is
    sent to the provider and what the cache is keyed on. `label` is the only
    description of it a client ever sees. `role` is the phrase the provider was
    asked for and every post is filtered on; `location` is the cleaned metro as
    typed (display only -- see the module docstring); `locale` the hiring
    vocabulary used."""

    query: HiringQuery
    label: str
    role: str
    location: str | None
    locale: Locale


def build_tab_query(
    *,
    query: object,
    location: object,
    freshness: Freshness,
    locale: Locale | None = None,
) -> TabQuery:
    """The standalone query. Raises `InvalidSearchInput` for anything that
    cannot be searched; nothing else it is given makes it fail."""
    role = role_phrase(_clean(query, MAX_QUERY_CHARS, "The role"))
    location_text = normalize_location_text(location)
    term = location_term(location_text) if location_text is not None else None
    resolved = locale if locale is not None else locale_for_location(location_text)
    try:
        built = build_query(role_terms=[role], locale=resolved, freshness=freshness, metro=term)
    except ValueError as e:  # over a cap `build_query` enforces: a too-long search
        raise InvalidSearchInput(
            "That search is too long. Shorten the role or the location."
        ) from e
    parts = [role]
    if location_text is not None and term is not None:
        searched_as = "" if _same_words(term, location_text) else f" (searched as {term})"
        # the whole place segment stays within `MAX_LABEL_LOCATION_CHARS`: the typed
        # text is what gives way (a query term is at most 60 characters, so the
        # suffix always fits)
        limit = MAX_LABEL_LOCATION_CHARS - len(searched_as)
        parts.append(clean_display_text(location_text, limit) + searched_as)
    parts.append(freshness_label(freshness))
    return TabQuery(
        query=built,
        label=" -- ".join(parts),
        role=role,
        location=location_text,
        locale=resolved,
    )


# ── ranking ──────────────────────────────────────────────────────────────


def tab_rank_key(
    *,
    aggregator: bool | None,
    posted_at: datetime | None,
    position: int,
    unverified: bool = False,
) -> tuple[int, int, float, int]:
    """Sort key: accounts that are not known aggregators first (`None` -- no
    author to attribute a post to -- is not a known aggregator), then posts that
    state the role before posts that only mention it after their opening
    (`unverified`, see `tab_role_fit`), then the freshest post, then the
    provider's own order, so the ranking is deterministic. An unknown post time
    sorts after every known one."""
    return (
        1 if aggregator is True else 0,
        1 if unverified else 0,
        -posted_at.timestamp() if posted_at is not None else float("inf"),
        position,
    )
