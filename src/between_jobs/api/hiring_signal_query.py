"""Hiring Signals P3 -- turning a tracked application into a search query.

Pure and deterministic: no I/O, no clock, no LLM. An application gives us two
strings that were written by a stranger (a job description's title and a
company name, scraped or pasted) and one that may be missing (a location).
This module derives from them, by fixed and documented rules:

- **role terms** -- what the job is, with the words that describe level
  rather than role taken off (`derive_role_terms`);
- **a locale** -- which hiring vocabulary `hiring_signals.build_query` should
  use (`locale_for_location`);
- **the query itself** and **a human label** for it (`build_application_query`).

**Untrusted input.** Title and company are third-party text, so they are
never interpolated into a query by this module: they go through
`hiring_signals.build_query`, which removes quotes, parentheses, colons and a
leading `-` from every user-supplied term (so a title cannot break out of its
quoted phrase and inject `site:` or grouping operators) and refuses a term
over 60 characters. Everything here that runs BEFORE `build_query` -- the
role-term derivation, the company clean-up -- only ever removes or shortens
text, and every regex is anchored or runs over an input length-capped up
front, so a hostile title costs the same as a normal one.

**Role terms are a rule, not a model.** The rule is the smallest one that was
checked against real job titles (as postings write them, and the titles in
the golden fixtures):

1. take the text before the first separator (` - `, ` | `, ` @ `, `, `,
   ` / `, `: ` -- everything after is a team, location or programme);
2. drop bracketed segments (`(Remote)`, `[Contract]`);
3. take off a leading run of level words (`Senior`, `Staff`, `Principal`,
   `Lead`, `Junior`, `Associate`, `Entry Level`, `New Grad`, ...) and any
   trailing level marker (`II`, `III`, `L5`, `Level 2`, a bare `2`);
4. drop work-mode words wherever they sit (`Remote`, `Hybrid`, `Onsite`,
   `Full-time`, ...).

What is left, lower-cased, is the CORE PHRASE (`Senior Software Engineer II
(Payments) - Remote` -> `software engineer`). A level word in the MIDDLE of a
title is left alone on purpose (`Sales Associate`, `Engineering Lead` are
roles, not levels), as is `Intern` WHEN it is part of the role segment
(`Software Engineering Intern`): an internship is a different hiring market
from the full-time job with the same nouns. An intern marker after a separator
(`Software Engineer - Intern`) goes with the rest of the tail, so that title is
queried as the full-time role -- a known limit, not a rule.

The query asks for the core phrase and, when the phrase ends in a known role
noun (`engineer`, `scientist`, `manager`, ...), that noun alone as well, so a
company's post for a NEIGHBOURING role is still retrieved -- that post is a
warm-path signal too, and ranking (not the query) is what puts the exact role
first. There is no synonym table: none exists in this codebase and this rule
does not invent one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .hiring_signal_company import CompanyNames, company_names
from .hiring_signals import Freshness, HiringQuery, Locale, build_query

_MAX_TITLE_CHARS = 300
_MAX_LOCATION_CHARS = 300
_MAX_QUERY_TERM_CHARS = 60
"""`build_query` refuses a single term longer than this; company names and
role phrases are shortened to fit before they get there."""
_MAX_ROLE_WORDS = 5
"""`build_query` also caps a whole query at 32 words. Its fixed part (the
hiring vocabulary, ORs and `site:`) is at most 17 words, so a company of at
most 5 words (`hiring_signal_company` cuts it there) plus a role phrase of at
most 5 words and its 1-word family -- 5 + 5 + 1 + `OR` + 17 -- can never reach
the cap. Real roles are 2-4 words and real companies 1-3; a title or name
longer than this is cut at a word boundary rather than refused."""


def _clip_words(text: str, *, max_words: int, max_chars: int) -> str:
    """The leading words of `text`, at most `max_words` of them and at most
    `max_chars` characters, always cut at a word boundary."""
    clipped = " ".join(text.split()[:max_words])
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars].rsplit(" ", 1)[0]
    return clipped


# ── role terms ───────────────────────────────────────────────────────────

_SEPARATOR_RX = re.compile(r"\s+[-\u2013\u2014|@/]\s+|\s*[,:;]\s+")
"""What ends the role part of a title: a spaced dash/pipe/at/slash (a bare
`-` inside `Full-Stack` or `AI/ML` is NOT one) or a comma/colon/semicolon
followed by a space (`Software Engineer, Payments`)."""

_BRACKETED_RX = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")

_QUERY_OPERATOR_CHARS_RX = re.compile(r'["()\\:]')
"""The characters `build_query` strips from every term it is given (quotes,
parentheses, backslash, colon -- what would let a term break out of its own
quoted phrase). A role phrase is cleaned of them HERE too, so what is derived
is what will be queried and matched: a title made only of such characters
becomes "no role" instead of an empty term `build_query` would refuse."""

_LEVEL_PHRASES_RX = re.compile(
    r"\b(?:entry[\s-]level|mid[\s-]level|early[\s-]career|new[\s-]grad(?:uate)?"
    r"|early[\s-]in[\s-]career)\b",
    re.IGNORECASE,
)
_LEADING_LEVEL_WORDS = frozenset(
    {"senior", "sr", "junior", "jr", "staff", "principal", "lead", "associate", "graduate"}
)
_TRAILING_LEVEL_RX = re.compile(
    r"\s+(?:(?:i{1,3}|iv|v|vi)|l[1-9]|level\s*[1-9]|[1-9])$",
    re.IGNORECASE,
)
_WORK_MODE_WORDS = frozenset(
    {"remote", "hybrid", "onsite", "on-site", "full-time", "fulltime", "part-time", "parttime"}
)

ROLE_NOUNS = frozenset(
    {
        "engineer",
        "developer",
        "scientist",
        "analyst",
        "manager",
        "designer",
        "architect",
        "researcher",
        "consultant",
        "specialist",
        "recruiter",
        "programmer",
        "administrator",
        "technician",
        "accountant",
        "director",
        "strategist",
        "writer",
        "marketer",
    }
)
"""A title ending in one of these is a role, and the noun alone is the
family. Deliberately a short list of nouns that are unambiguous as the head
of a job title, not a taxonomy: a title that ends in anything else (`Applied
Machine Learning`) simply gets no family term."""


def derive_role_terms(title: object) -> tuple[str, ...]:
    """The core role phrase(s) of a job title, lower-cased -- `()` when no
    role can be read out of it (an empty title, or one that is only level
    words). At most one phrase today; a tuple so a caller never has to
    special-case "none". See the module docstring for the rule."""
    if not isinstance(title, str):
        return ()
    text = " ".join(title.split())[:_MAX_TITLE_CHARS]
    text = _BRACKETED_RX.sub(" ", text)
    for segment in _SEPARATOR_RX.split(text):
        phrase = _core_phrase(segment)
        if phrase:
            return (phrase,)
    return ()


def _core_phrase(segment: str) -> str:
    segment = _LEVEL_PHRASES_RX.sub(" ", _QUERY_OPERATOR_CHARS_RX.sub(" ", segment))
    segment = " ".join(segment.split())
    while True:
        stripped = _TRAILING_LEVEL_RX.sub("", segment)
        if stripped == segment:
            break
        segment = stripped
    tokens = segment.split()
    while tokens and tokens[0].casefold().rstrip(".") in _LEADING_LEVEL_WORDS:
        tokens = tokens[1:]
    tokens = [t for t in tokens if t.casefold() not in _WORK_MODE_WORDS]
    phrase = " ".join(tokens).casefold().strip(" -")
    if not any(ch.isalnum() for ch in phrase):
        return ""
    return _clip_words(phrase, max_words=_MAX_ROLE_WORDS, max_chars=_MAX_QUERY_TERM_CHARS)


def role_family_term(core_phrase: str) -> str | None:
    """The head noun of a multi-word core phrase when it is a known role
    noun (`software engineer` -> `engineer`), else `None`. A one-word phrase
    has no separate family."""
    tokens = core_phrase.split()
    if len(tokens) >= 2 and tokens[-1] in ROLE_NOUNS:
        return tokens[-1]
    return None


def role_phrase_variants(core_phrase: str) -> tuple[str, ...]:
    """The phrase and its plural form (`software engineer`, `software
    engineers`). Matching a role in post text is by whole words, so without
    this a post saying `we're hiring software engineers` would not match
    `software engineer`. Only the LAST word is pluralized, and only when it
    is plain letters; there is no attempt at irregular plurals."""
    tokens = core_phrase.split()
    if not tokens:
        return ()
    last = tokens[-1]
    if not (last.isascii() and last.isalpha() and len(last) >= 3):
        return (core_phrase,)
    if last.endswith(("s", "x", "ch", "sh")):
        plural = last + "es"
    elif last.endswith("y") and last[-2] not in "aeiou":
        plural = last[:-1] + "ies"
    else:
        plural = last + "s"
    return (core_phrase, " ".join([*tokens[:-1], plural]))


# ── locale ───────────────────────────────────────────────────────────────

_INDIA_LOCATION_RX = re.compile(
    r"\b(?:india|bengaluru|bangalore|mumbai|navi mumbai|delhi|new delhi|hyderabad|chennai"
    r"|pune|kolkata|gurgaon|gurugram|noida|ahmedabad|jaipur|kochi|cochin|coimbatore"
    r"|chandigarh|indore|thiruvananthapuram|trivandrum|lucknow|nagpur|vadodara|surat"
    r"|mysuru|mysore|visakhapatnam|bhubaneswar)\b",
    re.IGNORECASE,
)


def locale_for_location(location: object) -> Locale:
    """`Locale.INDIA` when the job's location text names India or one of its
    major metros, else `Locale.GLOBAL`. The two locales differ only in the
    hiring vocabulary `build_query` ORs together (India adds `immediate
    joiners`, `walk-in`, `notice period`, ...). A missing or unrecognized
    location is GLOBAL, never a guess at India."""
    if not isinstance(location, str):
        return Locale.GLOBAL
    return (
        Locale.INDIA if _INDIA_LOCATION_RX.search(location[:_MAX_LOCATION_CHARS]) else Locale.GLOBAL
    )


# ── company ──────────────────────────────────────────────────────────────


def company_query_name(company: object) -> str:
    """The company name as it goes into a query: the display name as written
    (`&`, dots and apostrophes kept -- people write `AT&T` and `McDonald's`)
    minus legal forms and `Group`/`Holdings` tails (`Stripe, Inc.` ->
    `Stripe`), shortened at a word boundary to what `build_query` accepts.
    `""` means there is no company to search for: nothing was given, or what
    was given has no letter or digit in it. See `hiring_signal_company` for why
    this is NOT the registry's normalized form."""
    names = company_names(company)
    return names.query_name if names is not None else ""


# ── display text ─────────────────────────────────────────────────────────

_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def clean_display_text(value: object, limit: int) -> str:
    """Text safe to show and to store as a label: control, format (bidi
    overrides, zero-width), surrogate, private-use and unassigned characters
    and line/paragraph separators removed, every whitespace run collapsed to
    one space, cut to `limit` characters. Not an HTML escape -- clients render
    text as text -- but nothing that survives can re-order or hide the
    characters around it."""
    if not isinstance(value, str):
        return ""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in value[: limit * 4]
        if ch.isspace() or unicodedata.category(ch) not in _DROPPED_CATEGORIES
    )
    return " ".join(kept.split())[:limit].strip()


# ── the query ────────────────────────────────────────────────────────────

_FRESHNESS_LABELS: dict[Freshness, str] = {
    Freshness.DAY: "last 24 hours",
    Freshness.THREE_DAYS: "last 3 days",
    Freshness.WEEK: "last 7 days",
}


@dataclass(frozen=True)
class ApplicationQuery:
    """Everything the service needs from one application.

    `query` is the provider query (`build_query`'s output) and NEVER leaves
    the server: it is what is sent to the provider and what the cache is
    keyed on. `label` is the only description of it a client ever sees.
    `role_terms` are the core phrases used to RANK results (empty when no
    role could be derived -- role match is then unknown, not false);
    `query_role_terms` are what the query asked for, which may add the role
    family. `company` is the phrase the provider was asked for and `names` the
    set of spellings the hard relevance filter accepts for it (see
    `hiring_signal_company`)."""

    query: HiringQuery
    label: str
    company: str
    names: CompanyNames
    role_terms: tuple[str, ...]
    query_role_terms: tuple[str, ...]


class NoCompanyError(ValueError):
    """The application has no company name to search for."""


def build_application_query(
    *, company: object, title: object, location: object, freshness: Freshness
) -> ApplicationQuery:
    """The per-application query. Raises `NoCompanyError` when there is no
    usable company (the query would not be about the application at all);
    every other odd input degrades instead: no derivable role -> a
    company-wide query with unknown role match."""
    names = company_names(company)
    if names is None:
        raise NoCompanyError("no company name to search for")
    company_name = names.query_name
    role_terms = derive_role_terms(title)
    query_terms = list(role_terms)
    if role_terms:
        family = role_family_term(role_terms[0])
        if family is not None:
            query_terms.append(family)
    else:
        # `build_query` insists on a role group. With nothing derived, ask
        # for the company's hiring posts in general (the hiring vocabulary
        # is already in the query, so this term adds no constraint).
        query_terms = ["hiring"]
    query = build_query(
        role_terms=query_terms,
        locale=locale_for_location(location),
        freshness=freshness,
        company=company_name,
    )
    display_company = clean_display_text(company, 80) or company_name
    label_parts = [display_company, role_terms[0]] if role_terms else [display_company]
    label_parts.append(_FRESHNESS_LABELS[freshness])
    return ApplicationQuery(
        query=query,
        label=" -- ".join(label_parts),
        company=company_name,
        names=names,
        role_terms=role_terms,
        query_role_terms=tuple(query_terms),
    )
