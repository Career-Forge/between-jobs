"""Role-title synonyms for the registry lane and the role filter.

A job-title search is only as good as its vocabulary. "SDE", "SWE" and "software
engineer" are one job; so are "ML" and "machine learning", "QA" and "quality
assurance". A search that treats them as unrelated words finds a fraction of the
postings a person means: on a real registry "SDE" found about one percent of the
software-engineer titles, and "ML Engineer" missed about half of the "Machine Learning
Engineer" ones.
This module is the one place that vocabulary lives.

What it provides, all pure and deterministic (no I/O, no model):

- `ROLE_SYNONYMS` -- a curated map from a query phrase to alternative phrases that
  name the same role family.
- `parse_role_query` -- splits a query into slots (a user's own word, or a run of
  words that is a key of the map) so a caller can apply the alternatives within one
  slot while every slot stays ANDed.
- `registry_search_text` -- the text sent to the registry's full-text search, built
  from the same slots: an OR over the cross product of per-slot alternatives, capped
  so a long query cannot explode. The cap is spent on the slots with the most synonyms
  first, so the search never depends on the order the words were typed in.

Matching semantics, which callers rely on:

- Across slots it is AND, exactly like a query with no synonyms at all.
- Within a slot, the user's own words keep today's meaning (every word must appear
  as a whole word, anywhere in the title); a synonym is an alternative that must
  appear as a whole PHRASE (its words adjacent). The tighter synonym rule is why
  "qa" maps to phrases such as "quality assurance" and "test automation" rather than
  to the bare words "quality" or "test": a bare word would pull in "Quality Control
  Inspector" and "Test Pilot". A phrase is also matched wherever it sits in a title, so
  it must carry its own software or QA signal: "quality engineer" and "test engineer"
  are not synonyms of "qa" because they are the tail of "Supplier Quality Engineer"
  and "Flight Test Engineer" too. Anyone who wants those titles can search the words.
- A query with no synonym key behaves exactly as it did before this module existed.

Every entry below states why it exists. Add an entry only where a real title
spelling or abbreviation exists; an ambiguous abbreviation ("pm" is a product,
project and program manager; "ds" is also a company prefix) is deliberately not here.

Hiring Signals does not use this map (it has its own, narrower table of spellings in
`hiring_signal_tab._EQUIVALENT_WORDS`, which overlaps this one on full stack, front end,
back end, dev ops, machine learning and quality assurance). The two are kept apart on
purpose: that one matches words of a role phrase in a post, this one matches whole
phrases in a job title.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from types import MappingProxyType

MAX_QUERY_ALTERNATIVES = 16
"""Most OR-ed alternatives `registry_search_text` will send. The alternatives are the
cross product of every expandable slot's choices, so without a bound a query of five
abbreviations would send hundreds. 16 holds every two-slot compound a person types
("backend sde" is 2 x 5, "ml software engineer" 5 x 2, "sde full-stack" 5 x 3) with room
to spare, and is still a small OR for the full-text index."""


def _symmetric(*members: str) -> dict[str, tuple[str, ...]]:
    """Every member maps to all the others: the members are interchangeable names for
    one thing, so a search for any of them should find all of them."""
    return {member: tuple(other for other in members if other != member) for member in members}


_ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    # Software engineer. "SDE" (Amazon-style) and "SWE" are the standard abbreviations;
    # "software development engineer" is SDE spelled out. Developer and engineer are
    # used for the same job in titles, so the family is one group.
    **_symmetric(
        "sde",
        "swe",
        "software engineer",
        "software developer",
        "software development engineer",
    ),
    # Machine learning / artificial intelligence / natural language processing: each
    # abbreviation is routinely used in titles in place of the long form, and the other
    # way round.
    **_symmetric("ml", "machine learning"),
    **_symmetric("ai", "artificial intelligence"),
    **_symmetric("nlp", "natural language processing"),
    # Quality assurance. "qa" and "sdet" (software development engineer in test) name the
    # same family as these phrases. They are deliberately tight (see the module docstring):
    # each one carries its own software or QA signal, so none of them is the bare tail of a
    # manufacturing, hardware or aerospace title ("Supplier Quality Engineer", "Flight Test
    # Engineer"), and none is a bare word that would admit "Quality Control Inspector" or
    # "Test Pilot".
    "qa": (
        "quality assurance",
        "software test",
        "software quality engineer",
        "test automation",
        "automation test",
        "engineer in test",
        "sdet",
    ),
    "sdet": (
        "qa",
        "quality assurance",
        "software test",
        "software quality engineer",
        "test automation",
        "automation test",
        "engineer in test",
    ),
    # The long form of "qa" reaches the same family, so spelling the role out finds as many
    # titles as the abbreviation does.
    "quality assurance": (
        "qa",
        "software test",
        "software quality engineer",
        "test automation",
        "automation test",
        "engineer in test",
        "sdet",
    ),
    # "Software Test Engineer" is the QA family under another name; the reverse
    # direction for "software test".
    "software test": ("qa", "sdet"),
    # A standalone "SDET" title has no "engineer" word, so a search for "qa engineer"
    # (or "qa automation engineer") would lose it if "engineer" were its own required
    # slot. The whole phrase is a key so SDET is a choice for the phrase as a unit.
    "qa engineer": (
        "quality assurance engineer",
        "software test engineer",
        "software quality engineer",
        "test automation engineer",
        "automation test engineer",
        "engineer in test",
        "sdet",
    ),
    "quality assurance engineer": (
        "qa engineer",
        "qa automation engineer",
        "software test engineer",
        "software quality engineer",
        "test automation engineer",
        "automation test engineer",
        "engineer in test",
        "sdet",
    ),
    "qa automation engineer": (
        "test automation engineer",
        "automation test engineer",
        "quality assurance automation engineer",
        "sdet",
    ),
    "quality assurance automation engineer": (
        "qa automation engineer",
        "test automation engineer",
        "automation test engineer",
        "sdet",
    ),
    # "QA Automation Engineer", "Test Automation Engineer" and "Automation Test
    # Engineer" are one job.
    "qa automation": (
        "test automation",
        "automation test",
        "quality assurance automation",
        "sdet",
    ),
    "quality assurance automation": (
        "qa automation",
        "test automation",
        "automation test",
        "sdet",
    ),
    # Business intelligence. "BI" is the abbreviation; "analytics" is how BI roles are
    # often titled (one direction only: an analytics search should not return every
    # BI developer). A deliberate recall-over-precision choice: the slot cannot see the word
    # after "analytics", so a lone "bi" search also admits titles such as "People Analytics
    # Partner" or "Marketing Analytics Manager".
    "bi": ("business intelligence", "analytics"),
    "business intelligence": ("bi",),
    # Site reliability. "SRE" titles usually omit the word "engineer", so the three-word
    # key keeps "Staff SRE" findable from the long form.
    "sre": ("site reliability",),
    "site reliability": ("sre",),
    "site reliability engineer": ("sre",),
    # Spelling variant: "DevOps" and "Dev Ops" are both written. (DevOps and SRE are
    # different jobs and are not linked.)
    **_symmetric("devops", "dev ops"),
    # Spelling variants of the three engineering "end" words. The phrase form matches
    # "Front-End" and "Front End" alike (a hyphen counts as a space inside a phrase).
    **_symmetric("frontend", "front end"),
    "front-end": ("frontend", "front end"),
    **_symmetric("backend", "back end"),
    "back-end": ("backend", "back end"),
    **_symmetric("fullstack", "full stack"),
    "full-stack": ("fullstack", "full stack"),
    # Plain abbreviations that are unambiguous in an IT title.
    **_symmetric("dba", "database administrator"),
    **_symmetric("sysadmin", "system administrator", "systems administrator"),
    **_symmetric("ux", "user experience"),
    **_symmetric("ui", "user interface"),
    **_symmetric("tpm", "technical program manager"),
    **_symmetric("cybersecurity", "cyber security"),
    **_symmetric("infosec", "information security"),
    # Query-side shorthand. People type "eng" and "dev" for what titles spell out. One
    # direction only: titles rarely abbreviate, so the long words get no extra
    # alternative (which would also use up the alternatives budget).
    #
    # A lone "eng" means "engineer" as broadly as the person typed it (it is the word, not a
    # software role). "dev" must NOT map to the bare word "engineer" the same way: it names a
    # software job, and the bare word would admit every Sales, Mechanical and Civil Engineer
    # (and, in "dev engineer", would make the second slot collapse to "any engineer" and
    # lose "Software Developer"). The tight phrase "software engineer" is the engineer
    # spelling of a developer.
    "eng": ("engineer",),
    "dev": ("developer", "software engineer"),
}

ROLE_SYNONYMS: Mapping[str, tuple[str, ...]] = MappingProxyType(_ROLE_SYNONYMS)
"""Query phrase (lowercase, words separated by one space) -> alternative phrases. The
key itself is always an implicit alternative, with its own looser meaning (see the module
docstring), so it is not repeated in the value."""

_MAX_KEY_WORDS = max(len(key.split()) for key in _ROLE_SYNONYMS)


@dataclass(frozen=True)
class RoleTermSlot:
    """One required part of a role query.

    `words` are the user's own words: all of them must appear in the title as whole
    words, anywhere -- the meaning a role search has always had. `alternatives` are
    synonym phrases; if any one appears in the title as a whole phrase, the slot is
    satisfied without `words`. With no `alternatives` this is just the ordinary AND of
    `words`."""

    words: tuple[str, ...]
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleQuery:
    """A role query split into slots. All slots are required."""

    slots: tuple[RoleTermSlot, ...]


def _query_tokens(query: str) -> list[str]:
    """The query's words, lowercased. Single-character words are dropped (noise: "a",
    "r" and "c" are not role words), as the role filter has always done.

    Three things a person types into a search box are not part of a role: double
    quotes, a leading minus, and the bare word "or". They are read here as plain
    words ("machine learning" in quotes is two words, "-java" is "java"), because
    the registry's search text is built from these same words and must never be able
    to switch on the search syntax it is sent through (see `registry_search_text`)."""
    tokens: list[str] = []
    for raw in query.lower().split():
        token = raw.replace('"', "").replace("\u201c", "").replace("\u201d", "").lstrip("-")
        if len(token) > 1 and token != "or":
            tokens.append(token)
    return tokens


_EDGE_PUNCTUATION = ",;:!?()[]{}'\u2018\u2019"


def _lookup_word(token: str) -> str:
    """The token as a synonym-key word: the punctuation a person types around a word (a
    comma, brackets, a trailing period) removed, so a title pasted as "Software Engineer,
    Backend" or "(SDE)" still finds its synonyms. Used for the key lookup only. A term that
    carries punctuation on purpose (`c++`, `c#`, `.net`, `node.js`) is never a key, so it falls
    through to a plain-word slot with its text untouched."""
    return token.strip(_EDGE_PUNCTUATION).rstrip(".") or token


def parse_role_query(query: str) -> RoleQuery:
    """Splits `query` into slots, longest synonym key first (so "qa engineer" is one
    slot where "qa" alone would have been). A word with no synonyms is a slot of its
    own. An empty or all-noise query has no slots and therefore no role constraint."""
    tokens = _query_tokens(query)
    slots: list[RoleTermSlot] = []
    i = 0
    while i < len(tokens):
        for size in range(min(_MAX_KEY_WORDS, len(tokens) - i), 0, -1):
            words = tuple(_lookup_word(t) for t in tokens[i : i + size])
            alternatives = ROLE_SYNONYMS.get(" ".join(words))
            if alternatives is not None:
                slots.append(RoleTermSlot(words=words, alternatives=alternatives))
                i += size
                break
        else:
            slots.append(RoleTermSlot(words=(tokens[i],)))
            i += 1
    return RoleQuery(slots=tuple(slots))


_KEPT_PUNCTUATION = frozenset("+#./-")


def _search_words(text: str) -> list[str]:
    """`text` reduced to words that are safe inside a websearch-style query: letters,
    digits and `+ # . / -` only. Any other character separates words; a leading `-`
    (the NOT operator) is removed; the bare word "or" (the OR operator) is dropped; a
    piece with no letter or digit at all is dropped."""
    cleaned = "".join(
        ch
        if ch.isalnum() or ch in _KEPT_PUNCTUATION or unicodedata.category(ch).startswith("M")
        else " "
        for ch in text
    )
    words: list[str] = []
    for piece in cleaned.split():
        piece = piece.lstrip("-")
        if piece and piece != "or" and any(ch.isalnum() for ch in piece):
            words.append(piece)
    return words


def _as_search_phrase(phrase: str) -> str:
    words = _search_words(phrase)
    if len(words) > 1:
        return '"' + " ".join(words) + '"'
    return words[0] if words else ""


def registry_search_text(query: str, *, max_alternatives: int = MAX_QUERY_ALTERNATIVES) -> str:
    """The text the registry's full-text search runs on, in the syntax of Postgres's
    `websearch_to_tsquery`: words are ANDed, `"a phrase"` is adjacent words, `or`
    separates alternatives.

    It is the cross product of every slot's choices -- the user's own words, or one of
    the slot's synonym phrases -- joined by `or`. "ml engineer" becomes
    `ml engineer or "machine learning" engineer`. The product is capped at
    `max_alternatives`, and the cap is spent by value, not by position: slots are tried
    from the one with the most synonyms down (ties broken by the slot's own words, never by
    where it sits in the query), a slot is expanded if the product still fits, and a slot
    that does not fit is skipped without stopping the ones after it. So the same words send
    the same search in any order. A slot left unexpanded stays as the user typed it. The cap
    bounds the size of the search, not what the role filter later accepts; the filter
    applies every slot's synonyms.

    Only `_search_words`' output ever reaches the result, so nothing a user types can
    inject the search syntax (a quote, a leading minus, a bare `or`). An empty string
    -- an empty query, or one with nothing searchable -- means "no text constraint",
    which the SQL reads as "browse the most recent postings"."""
    slots = parse_role_query(query).slots
    own_words = [" ".join(w for word in slot.words for w in _search_words(word)) for slot in slots]
    phrases = [
        [p for p in (_as_search_phrase(a) for a in slot.alternatives) if p] for slot in slots
    ]

    expandable = [i for i in range(len(slots)) if own_words[i] and phrases[i]]
    expandable.sort(key=lambda i: (-len(phrases[i]), slots[i].words))
    expanded: set[int] = set()
    product_so_far = 1
    for i in expandable:
        size = 1 + len(phrases[i])
        if product_so_far * size <= max_alternatives:
            expanded.add(i)
            product_so_far *= size

    choices_per_slot = [
        [own_words[i], *phrases[i]] if i in expanded else [own_words[i]] for i in range(len(slots))
    ]
    combinations: list[str] = []
    for combination in product(*choices_per_slot):
        text = " ".join(part for part in combination if part)
        if text and text not in combinations:
            combinations.append(text)
    return " or ".join(combinations)
