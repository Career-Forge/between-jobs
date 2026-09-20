"""Tests for the standalone tab's pure logic (`hiring_signal_tab`): what a person
typed, turned into a provider query, a role phrase and a label -- most of it
against hostile text, because the role and the metro are a stranger's typing as
far as the query builder is concerned.

(Invisible and exotic characters are built with `chr()` on purpose, as in the
feature's own modules: a source file should never carry them literally.)
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from between_jobs.api.hiring_signal_relevance import opening, role_match
from between_jobs.api.hiring_signal_search import MAX_RESULTS_PER_CALL
from between_jobs.api.hiring_signal_tab import (
    MAX_LOCATION_CHARS,
    MAX_LOCATION_WORDS,
    MAX_QUERY_CHARS,
    MAX_ROLE_WORDS,
    MAX_TERM_CHARS,
    TAB_DEFAULT_FRESHNESS,
    InvalidSearchInput,
    build_tab_query,
    location_term,
    normalize_location_text,
    normalize_query_text,
    role_filter_terms,
    role_phrase,
    tab_rank_key,
    tab_role_fit,
)
from between_jobs.api.hiring_signals import (
    GLOBAL_VOCABULARY,
    INDIA_VOCABULARY,
    Freshness,
    HiringSignal,
    Locale,
    RawSearchHit,
    parse_hit,
)
from between_jobs.api.hiring_signals import MAX_QUERY_CHARS as BUILD_QUERY_MAX_CHARS
from between_jobs.api.hiring_signals import MAX_QUERY_WORDS as BUILD_QUERY_MAX_WORDS

ZWSP = chr(0x200B)  # zero-width space
RLO = chr(0x202E)  # right-to-left override
EM_DASH = chr(0x2014)
E_ACUTE = chr(0xE9)
U_UMLAUT = chr(0xFC)
EMOJI = chr(0x1F600)
COMBINING_DOT = chr(0x307)
CJK_ENGINEER = "".join(
    chr(c) for c in (0x8F6F, 0x4EF6, 0x5DE5, 0x7A0B, 0x5E08)
)  # software engineer
KATAKANA_ENGINEER = "".join(chr(c) for c in (0x30A8, 0x30F3, 0x30B8, 0x30CB, 0x30A2))
DEVANAGARI_ENGINEER = "".join(
    chr(c) for c in (0x907, 0x902, 0x91C, 0x940, 0x928, 0x93F, 0x92F, 0x930)
)

# ── the role phrase ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "phrase"),
    [
        ("Software Engineer", "software engineer"),
        ("  Senior   Data  Engineer ", "senior data engineer"),  # level words are kept
        ("C++ Developer", "c++ developer"),
        ("C# developer", "c# developer"),
        (".NET Developer", ".net developer"),
        ("Node.js backend", "node.js backend"),
        ("Sr. Engineer", "sr. engineer"),
        ("AI/ML engineer", "ai ml engineer"),
        ("full-stack developer", "full stack developer"),
        ("data scientist, NLP", "data scientist nlp"),
        ("QA & test engineer", "qa test engineer"),
        (f"engineer{EM_DASH}backend", "engineer backend"),  # a dash is a gap, not a glued word
        ("developer's advocate", "developer's advocate"),
        ("'engineer'", "engineer"),
        (CJK_ENGINEER, CJK_ENGINEER),  # a script written without spaces stays one word
        (DEVANAGARI_ENGINEER, DEVANAGARI_ENGINEER),
    ],
)
def test_the_role_is_used_as_typed_not_as_a_job_title(typed: str, phrase: str) -> None:
    assert role_phrase(typed) == phrase


def test_symbols_that_belong_to_a_word_are_not_separators() -> None:
    for word in ("c++", "c#", ".net", "node.js", "sr.", "developer's"):
        assert word in role_phrase(f"{word} engineer").split()


@pytest.mark.parametrize(
    "typed",
    [
        'x" OR site:evil.example "y',
        'engineer") OR site:linkedin.com/in/ ("',
        "engineer\\",
        "-site:linkedin.com engineer",
        "engineer (remote) [contract] {senior}",
        "engineer*",
        "engineer ~ developer",
    ],
)
def test_no_operator_character_survives_in_the_role_phrase_or_the_provider_query(
    typed: str,
) -> None:
    phrase = role_phrase(typed)
    assert not re.search(r"""["()\[\]{}\\:;,|&/*?!~^=%$@`<>-]""", phrase), phrase
    built = build_tab_query(query=typed, location=None, freshness=Freshness.WEEK)
    # what the user typed lands in ONE quoted term, and the only `site:` is ours
    assert built.query.query.count("site:") == 1
    assert built.query.query.startswith("site:linkedin.com/posts (")
    assert built.query.role_terms == (phrase,)
    assert built.query.query.endswith(f'("{phrase}")')


@pytest.mark.parametrize(
    "typed", ["", "   ", "()", '"', "---", ZWSP + RLO, ":::", "@@@", "***", "\n\t"]
)
def test_text_with_nothing_to_search_is_refused(typed: str) -> None:
    with pytest.raises(InvalidSearchInput, match="role"):
        build_tab_query(query=typed, location=None, freshness=Freshness.WEEK)


@pytest.mark.parametrize("typed", ["a", "a b c", "r", "c", "x y"])
def test_a_role_with_no_word_of_two_characters_is_refused_not_searched_unfiltered(
    typed: str,
) -> None:
    """The shared predicate drops one-character words, so `c` would filter on
    nothing and the search would be the index's answer to a near-empty query."""
    with pytest.raises(InvalidSearchInput, match="two or more characters"):
        role_phrase(typed)


def test_a_one_character_word_beside_a_real_one_is_kept_in_the_phrase() -> None:
    assert role_phrase("R developer") == "r developer"  # filtered on `developer` alone, by design


def test_the_role_is_cut_at_a_word_boundary_to_what_a_query_term_may_be() -> None:
    phrase = role_phrase("one two three four five six seven eight")
    assert phrase == "one two three four five"
    assert len(phrase.split()) == MAX_ROLE_WORDS

    long_words = " ".join(["w" * 20] * 4)  # 20 + 1 + 20 + 1 + 20 = 62 > 60
    cut = role_phrase(long_words)
    assert cut == " ".join(["w" * 20] * 2)
    assert len(cut) <= MAX_TERM_CHARS


def test_a_single_word_longer_than_a_query_term_is_refused() -> None:
    with pytest.raises(InvalidSearchInput, match="longer than 60"):
        role_phrase("a" * (MAX_TERM_CHARS + 1))
    assert role_phrase("a" * MAX_TERM_CHARS) == "a" * MAX_TERM_CHARS


def test_the_typed_role_is_limited_to_the_documented_length_not_silently_cut() -> None:
    build_tab_query(query="ab " * 66 + "ab", location=None, freshness=Freshness.WEEK)  # 200 chars
    with pytest.raises(InvalidSearchInput, match="limited to 200"):
        build_tab_query(query="a" * (MAX_QUERY_CHARS + 1), location=None, freshness=Freshness.WEEK)
    # whitespace does not count toward it
    long_but_spacey = "engineer" + " " * 500
    assert (
        build_tab_query(query=long_but_spacey, location=None, freshness=Freshness.WEEK).role
        == "engineer"
    )


@pytest.mark.parametrize("bad", [None, 5, ["engineer"], b"engineer", {"role": "x"}])
def test_a_role_that_is_not_text_is_refused(bad: object) -> None:
    with pytest.raises(InvalidSearchInput, match="must be text"):
        build_tab_query(query=bad, location=None, freshness=Freshness.WEEK)


def test_control_bidi_and_zero_width_characters_are_removed_before_anything_else() -> None:
    typed = f"soft{ZWSP}ware{RLO} engineer\x00\x07\n\t"
    built = build_tab_query(query=typed, location=f"Pu{ZWSP}ne{RLO}", freshness=Freshness.WEEK)
    assert built.role == "software engineer"
    assert built.location == "Pune"
    everything = built.query.query + built.label
    assert not any(ord(ch) < 32 or ch in (ZWSP, RLO) for ch in everything)


def test_role_phrase_cleans_control_characters_even_when_called_directly() -> None:
    assert role_phrase(f"engineer{RLO}{ZWSP} evil") == "engineer evil"


# ── the location ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "term"),
    [
        ("Bengaluru", "Bengaluru"),
        ("Bengaluru, Karnataka, India", "Bengaluru"),  # the first comma-separated part (measured)
        ("Austin, TX", "Austin"),
        ("New York", "New York"),
        ("New York, NY", "New York"),
        (", Karnataka", "Karnataka"),  # an empty first part is skipped
        ("Hyderabad; Telangana", "Hyderabad Telangana"),
        (f"Z{U_UMLAUT}rich, Schweiz", f"Z{U_UMLAUT}rich"),
        ("Remote", "Remote"),
    ],
)
def test_the_location_term_is_the_first_part_of_what_was_typed(typed: str, term: str) -> None:
    assert location_term(typed) == term


def test_the_full_location_is_kept_for_the_label_and_the_stored_search() -> None:
    built = build_tab_query(
        query="software engineer",
        location="Bengaluru, Karnataka, India",
        freshness=Freshness.THREE_DAYS,
    )
    assert built.location == "Bengaluru, Karnataka, India"
    # the label says what the provider was asked for, not only what was typed
    assert built.label == (
        "software engineer -- Bengaluru, Karnataka, India (searched as Bengaluru) -- last 3 days"
    )
    assert built.query.query.endswith('("software engineer") "Bengaluru"')


@pytest.mark.parametrize("typed", ["()", '"', ",,,", "---", ":::"])
def test_a_location_with_nothing_usable_is_refused_not_dropped(typed: str) -> None:
    """Dropping a place the person typed would widen the search past what they
    asked for."""
    with pytest.raises(InvalidSearchInput, match="location"):
        build_tab_query(query="engineer", location=typed, freshness=Freshness.WEEK)


@pytest.mark.parametrize("blank", [None, "", "   ", ZWSP, "\n\t"])
def test_a_blank_location_means_none(blank: str | None) -> None:
    built = build_tab_query(query="engineer", location=blank, freshness=Freshness.WEEK)
    assert built.location is None
    assert built.label == "engineer -- last 7 days"
    assert built.query.query.endswith('("engineer")')  # no metro term at all


def test_the_location_is_cut_to_what_a_query_term_may_be_and_a_huge_word_is_refused() -> None:
    term = location_term("aa bb cc dd ee ff gg hh")
    assert term == "aa bb cc dd ee ff"
    assert len(term.split()) == MAX_LOCATION_WORDS
    assert len(location_term(" ".join(["w" * 15] * 6))) <= MAX_TERM_CHARS
    with pytest.raises(InvalidSearchInput, match="longer than 60"):
        location_term("z" * (MAX_TERM_CHARS + 1))


def _place_of_length(n: int) -> str:
    """A place of exactly `n` characters made of ordinary-length words (a single
    word longer than a query term is refused on its own, a different rule)."""
    words = ["x" * 9] * ((n + 1) // 10)
    place = " ".join(words)
    return place + "x" * (n - len(place))


def test_the_location_is_limited_to_the_documented_length() -> None:
    at_limit = _place_of_length(MAX_LOCATION_CHARS)
    assert len(at_limit) == MAX_LOCATION_CHARS
    assert normalize_location_text(at_limit) == at_limit
    with pytest.raises(InvalidSearchInput, match="limited to 100"):
        normalize_location_text(_place_of_length(MAX_LOCATION_CHARS + 1))


def test_a_location_that_is_not_text_is_refused() -> None:
    with pytest.raises(InvalidSearchInput, match="must be text"):
        normalize_location_text(7)


def test_the_location_only_ever_shapes_the_provider_query() -> None:
    """Nothing about a post is verified against it: it is not a parameter of the
    role filter, only of the query."""
    # The locale is pinned on both sides: a place can change the hiring
    # vocabulary (an India metro reads as `india`), and that is a different test.
    with_place = build_tab_query(
        query="engineer", location="Pune", freshness=Freshness.WEEK, locale=Locale.GLOBAL
    )
    without = build_tab_query(
        query="engineer", location=None, freshness=Freshness.WEEK, locale=Locale.GLOBAL
    )
    assert with_place.role == without.role
    assert with_place.query.query == without.query.query + ' "Pune"'


# ── locale ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("location", "locale"),
    [
        ("Bengaluru", Locale.INDIA),
        ("Pune, Maharashtra", Locale.INDIA),
        ("India", Locale.INDIA),
        ("Austin", Locale.GLOBAL),
        (None, Locale.GLOBAL),  # never a guess at India
        ("Remote", Locale.GLOBAL),
    ],
)
def test_the_locale_is_derived_from_the_location_when_none_is_given(
    location: str | None, locale: Locale
) -> None:
    built = build_tab_query(query="engineer", location=location, freshness=Freshness.WEEK)
    assert built.locale is locale
    assert built.query.locale is locale


def test_a_locale_in_the_request_wins_over_the_location() -> None:
    india = build_tab_query(
        query="engineer", location="Austin", freshness=Freshness.WEEK, locale=Locale.INDIA
    )
    assert india.locale is Locale.INDIA
    assert india.query.vocabulary == INDIA_VOCABULARY[:6]
    global_ = build_tab_query(
        query="engineer", location="Pune", freshness=Freshness.WEEK, locale=Locale.GLOBAL
    )
    assert global_.locale is Locale.GLOBAL
    assert global_.query.vocabulary == GLOBAL_VOCABULARY


# ── the query and the label ──────────────────────────────────────────────


def test_the_provider_query_is_the_shared_builders_and_has_no_company() -> None:
    built = build_tab_query(
        query="Data Engineer", location="Austin", freshness=Freshness.THREE_DAYS
    )
    assert built.query.query == (
        'site:linkedin.com/posts ("we\'re hiring" OR "we are hiring" OR "#hiring" '
        'OR "my team is hiring" OR "hiring now") ("data engineer") "Austin"'
    )


@pytest.mark.parametrize(
    ("freshness", "tail"),
    [
        (Freshness.DAY, "last 24 hours"),
        (Freshness.THREE_DAYS, "last 3 days"),
        (Freshness.WEEK, "last 7 days"),
    ],
)
def test_the_label_is_a_human_summary_never_the_provider_query(
    freshness: Freshness, tail: str
) -> None:
    built = build_tab_query(query="Data Engineer", location="Pune", freshness=freshness)
    assert built.label == f"data engineer -- Pune -- {tail}"
    assert "site:" not in built.label and '"' not in built.label
    assert re.fullmatch(r".+ -- (.+ -- )?last (24 hours|3 days|7 days)", built.label)


def test_the_label_shows_the_words_that_were_searched_when_the_role_was_cut() -> None:
    built = build_tab_query(
        query="one two three four five six seven", location=None, freshness=Freshness.WEEK
    )
    assert built.label == "one two three four five -- last 7 days"


def test_the_label_location_is_capped() -> None:
    place = _place_of_length(MAX_LOCATION_CHARS)
    built = build_tab_query(query="engineer", location=place, freshness=Freshness.WEEK)
    shown = built.label.split(" -- ")[1]
    assert built.location == place  # what is stored and returned is the full place
    assert 0 < len(shown) <= 80  # only the label's copy is cut


@pytest.mark.parametrize("locale", [Locale.INDIA, Locale.GLOBAL])
def test_the_worst_case_input_stays_inside_the_query_builders_caps(locale: Locale) -> None:
    """Five 12-character words and a six-word place of 59 characters, in each
    vocabulary: a query the builder accepts (its caps are 350 characters and 32
    words)."""
    role = " ".join(["abcdefghijkl"] * 5)
    place = "aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee ffffffffff"
    built = build_tab_query(query=role, location=place, freshness=Freshness.WEEK, locale=locale)
    assert len(built.query.query) <= BUILD_QUERY_MAX_CHARS
    assert len(built.query.query.split()) <= BUILD_QUERY_MAX_WORDS


@pytest.mark.parametrize(
    "typed",
    [
        "software engineer",
        "  ",
        "C++",
        "a" * 1000,
        "\x00" * 50,
        RLO * 10 + "engineer",
        EMOJI + " engineer",
        "engineer\n\nsite:linkedin.com",
        "((((((((",
        "ab " * 500,
        chr(0) + chr(1) + chr(2) + " ab",
        "I" + COMBINING_DOT + "stanbul developer",
    ],
)
def test_nothing_typed_makes_the_builder_raise_anything_but_invalid_search_input(
    typed: str,
) -> None:
    try:
        built = build_tab_query(query=typed, location=typed, freshness=Freshness.WEEK)
    except InvalidSearchInput:
        return
    assert built.query.query.startswith("site:linkedin.com/posts (")


def test_the_default_window_is_three_days() -> None:
    """Chosen from real yield (see `hiring_signal_service.search_tab`): `day`
    returned 2 posts where `3days` returned 20 for the same query, and `week`
    added nothing once a page is full."""
    assert TAB_DEFAULT_FRESHNESS is Freshness.THREE_DAYS


# ── normalization for a saved search ─────────────────────────────────────


def test_a_saved_role_is_stored_cleaned_but_otherwise_as_typed() -> None:
    assert normalize_query_text("  Senior \t Data\nEngineer  ") == "Senior Data Engineer"
    assert normalize_query_text("C++ Developer") == "C++ Developer"  # not lower-cased, not split


def test_a_saved_role_that_could_not_be_searched_is_refused() -> None:
    for typed in ("", "()", "a b", ZWSP):
        with pytest.raises(InvalidSearchInput):
            normalize_query_text(typed)


def test_a_saved_location_is_cleaned_and_blank_means_none() -> None:
    assert normalize_location_text("  Pune ,   Maharashtra ") == "Pune , Maharashtra"
    assert normalize_location_text("   ") is None
    assert normalize_location_text(None) is None
    with pytest.raises(InvalidSearchInput):
        normalize_location_text("()")


# ── the role filter this module's phrase feeds ───────────────────────────


def _hit(text: str, title: str = "Someone's Post - LinkedIn") -> RawSearchHit:
    return RawSearchHit(
        url="https://www.linkedin.com/posts/x_y-activity-7506381452083381426-AbCd",
        title=title,
        snippet=text,
    )


@pytest.mark.parametrize(
    ("typed", "text", "kept"),
    [
        ("software engineer", "We are hiring a Software Engineer in Pune", True),
        ("software engineer", "Hiring Software Engineers in Pune", True),  # the plural
        ("software engineer", "Software Engineering Manager wanted", False),  # not `engineer`
        ("software engineer", "Hiring a Senior Frontend Engineer", False),  # a neighbouring role
        ("software engineer", "engineer, software, wanted", True),  # any order
        ("senior data engineer", "Hiring a Data Engineer", False),  # every word, level included
        ("senior data engineer", "Senior Data Engineer wanted", True),
        ("c++ developer", "We need a C++ developer", True),
        ("c++ developer", "We need a C developer", False),
        ("c++ developer", "Senior Software Engineer - C++ & Java", False),  # measured limit
        (".net developer", "Looking for a .NET Developer", True),
        (".net developer", "Looking for an ASP.NET developer", True),  # symbol-led terms may follow
        ("c# developer", "hiring a C# developer", True),
        ("node.js backend", "Node.js backend engineer", True),
        ("full-stack developer", "Hiring a Full Stack Developer", True),
        ("full-stack developer", "Hiring a Full-Stack Developer", True),
        ("full-stack developer", "Hiring a Fullstack Developer", False),
        ("data scientist", "DATA SCIENTIST needed", True),
        (f"ing{E_ACUTE}nieur logiciel", f"Nous recrutons un ing{E_ACUTE}nieur logiciel", True),
    ],
)
def test_the_role_filter_keeps_a_post_only_if_every_word_of_the_role_is_in_it(
    typed: str, text: str, kept: bool
) -> None:
    phrase = role_phrase(typed)
    assert (role_match([_hit(text)], (phrase,)) is not False) is kept


@pytest.mark.parametrize("typed", [CJK_ENGINEER, KATAKANA_ENGINEER])
def test_a_role_in_a_script_without_word_boundaries_is_never_hidden(typed: str) -> None:
    """It cannot be word-matched, so the filter answers unknown -- and unknown is
    kept, not hidden."""
    phrase = role_phrase(typed)
    assert role_match([_hit("we are hiring " + typed + " to join")], (phrase,)) is not False
    assert role_match([_hit("nothing relevant here")], (phrase,)) is None


def test_a_word_of_the_role_in_the_title_counts_like_one_in_the_text() -> None:
    phrase = role_phrase("software engineer")
    assert role_match([_hit("hiring now", title="Software Engineer opening | Jo")], (phrase,))


@pytest.mark.parametrize(
    ("role", "terms"),
    [
        ("software engineer", ("software engineer",)),
        ("data engineers", ("data engineers", "data engineer")),
        ("analysts", ("analysts", "analyst")),
        ("technologies", ("technologies", "technology")),
        (
            "qa engineers",
            (
                "qa engineers",
                "qa engineer",
                "quality assurance engineers",
                "quality assurance engineer",
            ),
        ),
        ("business analyst", ("business analyst",)),  # `business` is not the last word
        ("business", ("business",)),  # ... and `ss` is not a plural
        ("campus", ("campus",)),  # ... nor `us`
        ("analysis", ("analysis",)),  # ... nor `is`
        ("ops", ("ops",)),  # too short to be sure
        ("c++ developers", ("c++ developers", "c++ developer")),
        (".net developers", (".net developers", ".net developer")),
        (
            "node.js",
            ("node.js", "nodejs", "node js"),
        ),  # not plain letters: no plural, but spellings
        (CJK_ENGINEER, (CJK_ENGINEER,)),
        ("", ("",)),
    ],
)
def test_a_role_typed_in_the_plural_is_also_tested_as_its_singular(
    role: str, terms: tuple[str, ...]
) -> None:
    assert role_filter_terms(role) == terms


def test_the_singular_of_a_typed_plural_finds_the_posts_that_name_the_role() -> None:
    """`role_match` accepts the plural of a singular phrase; a typed plural needs the
    inverse, or the posts that say `data engineer` would be hidden as another role."""
    plural = role_phrase("data engineers")
    hit = _hit("We're hiring a Data Engineer in Pune")
    assert role_match([hit], (plural,)) is False  # the shared predicate alone
    assert role_match([hit], role_filter_terms(plural)) is True
    assert role_match([_hit("We're hiring a data analyst")], role_filter_terms(plural)) is False


# ── ranking ──────────────────────────────────────────────────────────────

_NOW = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)


def _rank(items: list[tuple[bool | None, float | None]]) -> list[int]:
    """Indexes of `(aggregator, age_hours)` items in ranked order."""
    keyed = [
        tab_rank_key(
            aggregator=aggregator,
            posted_at=None if age is None else _NOW - timedelta(hours=age),
            position=i,
        )
        for i, (aggregator, age) in enumerate(items)
    ]
    return sorted(range(len(items)), key=lambda i: keyed[i])


def test_non_aggregators_come_first_then_the_freshest() -> None:
    order = _rank([(False, 30), (True, 2), (False, 10), (None, 20), (True, 40)])
    assert order == [2, 3, 0, 1, 4]  # 10h, 20h, 30h -- then the aggregators, 2h before 40h


def test_an_aggregator_is_below_everyone_even_when_it_is_the_freshest() -> None:
    assert _rank([(True, 1), (False, 500)]) == [1, 0]


def test_an_unknown_author_is_not_a_known_aggregator() -> None:
    assert _rank([(True, 5), (None, 5)]) == [1, 0]


def test_an_unknown_post_time_sorts_after_every_known_one_within_its_group() -> None:
    assert _rank([(False, None), (False, 900), (True, None), (True, 1)]) == [1, 0, 3, 2]


def test_ties_keep_the_providers_order_so_the_ranking_is_deterministic() -> None:
    items: list[tuple[bool | None, float | None]] = [(False, 10)] * 6
    assert _rank(items) == list(range(6))
    shuffled: list[tuple[bool | None, float | None]] = [
        (True, 3),
        (False, 3),
        (True, 3),
        (False, 3),
    ]
    assert _rank(shuffled) == _rank(list(shuffled))


# ── YH-1..YH-3: where the role is said, and which spellings count ────────

_POST_URL = (
    "https://www.linkedin.com/posts/jane-doe-1a2b3c4d_hiring-activity-7506381452083381426-AbCd"
)
_ECHO_URL = "https://www.linkedin.com/posts/northwind-labs_hiring-activity-7506381452083381427-AbCd"
_COMMENTER = "Priya S. Software Engineer @ Acme | #OpenToWork | Python"


def _post(
    title: str, snippet: str, url: str = _POST_URL
) -> tuple[list[RawSearchHit], HiringSignal]:
    hit = RawSearchHit(url=url, title=title, snippet=snippet)
    signal = parse_hit(hit)
    assert isinstance(signal, HiringSignal), signal
    return [hit], signal


def _fit(role: str, title: str, snippet: str, url: str = _POST_URL) -> str:
    hits, signal = _post(title, snippet, url)
    return tab_role_fit(hits, signal, role_filter_terms(role_phrase(role)))


@pytest.mark.parametrize(
    ("title", "snippet", "expected"),
    [
        # said in the opening
        (
            "Jane Doe's Post - LinkedIn",
            "1 day ago \u00b7 We are hiring a software engineer ... x",
            "stated",
        ),
        # said in the title
        ("Software Engineer opening | Jane Doe", "1 day ago \u00b7 Join us ... more", "stated"),
        # said only after the first elision: someone else's line, or a later part of the post
        (
            "Jane Doe's Post - LinkedIn",
            f"1 day ago \u00b7 We are hiring for our team ... {_COMMENTER}",
            "later",
        ),
        (
            "Jane Doe's Post - LinkedIn",
            "1 day ago \u00b7 We are hiring in Pune ... Role: Software Engineer, 3 years",
            "later",
        ),
        # not said at all
        (
            "Jane Doe's Post - LinkedIn",
            "1 day ago \u00b7 We are hiring a data analyst ... x",
            "mismatch",
        ),
    ],
)
def test_a_role_said_only_after_the_opening_is_unverified_not_stated(
    title: str, snippet: str, expected: str
) -> None:
    assert _fit("software engineer", title, snippet) == expected


_ECHO = (
    "2 days ago \u00b7 We're #hiring a new {role} in Pune, Maharashtra. "
    "Apply today or share this post with your network."
)


def test_an_auto_job_share_is_judged_by_its_own_role_and_nothing_a_commenter_says() -> None:
    """The role of an auto job-share is one fixed sentence. A different role there is a
    mismatch, whatever a later fragment (a commenter's headline) says."""
    other = _ECHO.format(role="Senior AI Engineer") + f" ... {_COMMENTER}"
    hits, signal = _post("#hiring | Northwind Labs - LinkedIn", other, _ECHO_URL)
    assert signal.species == "ats_echo" and signal.echo_role == "Senior AI Engineer"
    assert tab_role_fit(hits, signal, role_filter_terms("software engineer")) == "mismatch"

    own = _ECHO.format(role="Software Engineer II")
    assert (
        _fit("software engineer", "#hiring | Northwind Labs - LinkedIn", own, _ECHO_URL) == "stated"
    )


def test_an_auto_job_share_is_not_matched_across_the_page_name_and_its_role() -> None:
    """The role words are AND-ed, so words from the PAGE's name (`Software Labs`) and
    from the role of its listing (`Hardware Engineer`) must not add up to a
    `software engineer` post: for an auto job-share only the parsed role is read."""
    text = _ECHO.format(role="Hardware Engineer")
    hits, signal = _post("#hiring | Software Labs - LinkedIn", text, _ECHO_URL)
    assert signal.species == "ats_echo" and signal.echo_role == "Hardware Engineer"
    assert tab_role_fit(hits, signal, role_filter_terms("software engineer")) == "mismatch"
    assert tab_role_fit(hits, signal, role_filter_terms("hardware engineer")) == "stated"


def test_an_auto_job_share_cut_before_its_tail_is_judged_by_its_opening_alone() -> None:
    """No `echo_role` was parsed (the snippet stops before `Apply today`): the title and
    the opening still say the role, and the later fragments are never a way in."""
    cut = (
        "2 days ago \u00b7 We're #hiring a new Artificial Intelligence Engineer "
        "in Bengaluru, Karnataka"
    )
    hits, signal = _post(
        "#hiring | Northwind Labs - LinkedIn", f"{cut} ... {_COMMENTER}", _ECHO_URL
    )
    assert signal.species == "ats_echo" and signal.echo_role is None
    assert tab_role_fit(hits, signal, role_filter_terms("software engineer")) == "mismatch"

    own = "2 days ago \u00b7 We're #hiring a new Software Engineer in Bengaluru, Karnataka"
    assert (
        _fit("software engineer", "#hiring | Northwind Labs - LinkedIn", own, _ECHO_URL) == "stated"
    )


def test_any_copy_that_states_the_role_makes_the_post_stated() -> None:
    hits, signal = _post("Jane Doe's Post - LinkedIn", "1 day ago \u00b7 Hiring ... more")
    second = RawSearchHit(
        _POST_URL, "Jane Doe's Post - LinkedIn", "Hiring a software engineer ... x"
    )
    terms = role_filter_terms("software engineer")
    assert tab_role_fit(hits, signal, terms) == "mismatch"
    assert tab_role_fit([*hits, second], signal, terms) == "stated"


def test_a_role_that_cannot_be_word_matched_is_unknown_never_a_mismatch() -> None:
    assert (
        _fit(CJK_ENGINEER, "Jane Doe's Post - LinkedIn", "1 day ago \u00b7 We are hiring ... x")
        == "unknown"
    )
    # ... and a role none of whose words the shared predicate can test at all
    hits, signal = _post("Jane Doe's Post - LinkedIn", "1 day ago \u00b7 We are hiring ... x")
    assert tab_role_fit(hits, signal, ("x",)) == "unknown"
    assert tab_role_fit(hits, signal, ()) == "unknown"


@pytest.mark.parametrize(
    ("typed", "text", "kept"),
    [
        # spelled differently, said the same
        ("full stack developer", "Hiring a Fullstack Developer", True),
        ("fullstack developer", "Hiring a full stack developer", True),
        ("front end engineer", "Hiring a Frontend Engineer", True),
        ("frontend engineer", "Hiring a front end engineer", True),
        ("backend engineer", "Hiring a back end engineer", True),
        ("cybersecurity analyst", "Hiring a cyber security analyst", True),
        ("devops engineer", "Hiring a dev ops engineer", True),
        ("senior software engineer", "Hiring a Sr. Software Engineer", True),
        ("senior software engineer", "Hiring a Sr Software Engineer", True),
        ("sr software engineer", "Hiring a Senior Software Engineer", True),
        ("machine learning engineer", "Hiring an ML Engineer", True),
        ("ml engineer", "Hiring a Machine Learning Engineer", True),
        ("qa engineer", "Hiring a Quality Assurance Engineer", True),
        ("node.js developer", "Hiring a Node JS developer", True),
        ("node.js developer", "Hiring a NodeJS developer", True),
        ("software engineer ii", "Hiring a Software Engineer 2", True),
        ("software engineer 2", "Hiring a Software Engineer II", True),
        ("data engineers", "Hiring a Full Stack Developer 3", False),
        # ... but a spelling is not a synonym
        ("software developer", "Hiring a software engineer", False),
        ("senior software engineer", "Hiring a software engineer", False),
        ("frontend engineer", "Hiring a backend engineer", False),
        ("full stack developer", "Hiring a stack developer", False),
    ],
)
def test_the_spellings_a_post_uses_for_the_same_role_are_the_same_role(
    typed: str, text: str, kept: bool
) -> None:
    fit = _fit(typed, "Jane Doe's Post - LinkedIn", f"1 day ago \u00b7 {text} ... more")
    assert (fit == "stated") is kept


@pytest.mark.parametrize(
    ("typed", "text", "kept"),
    [
        ("sde 2", "SDE-2 opening in Pune", True),
        ("sde 2", "SDE 2 opening in Pune", True),
        ("sde 2", "SDE-1 opening in Pune", False),
        ("sde 2", "SDE 3 opening in Pune", False),
        ("sde 2", "SDE-12 opening in Pune", False),
        ("sde 2", "SDE opening in Pune", False),
        ("software engineer 3", "Hiring a Software Engineer 1", False),
        ("software engineer 3", "Hiring a Software Engineer III", True),
        ("software engineer 3", "Hiring a Software Engineer 3", True),
    ],
)
def test_a_digit_in_the_role_is_a_word_that_has_to_be_there(
    typed: str, text: str, kept: bool
) -> None:
    """The shared predicate drops one-character words (`R developer` filters on
    `developer` alone). A DIGIT is a level, so it is tested as a whole token."""
    fit = _fit(typed, "Jane Doe's Post - LinkedIn", f"1 day ago \u00b7 {text} ... more")
    assert (fit == "stated") is kept


@pytest.mark.parametrize(
    ("role", "terms"),
    [
        ("news", ("news",)),  # not `new`: LinkedIn's auto post says `a new ...`
        ("sales", ("sales",)),  # not `sale`
        ("jobs", ("jobs",)),  # not `job`
        ("roles", ("roles",)),
        ("hires", ("hires",)),
        ("engineers", ("engineers", "engineer")),
        ("data analysts", ("data analysts", "data analyst")),
        ("sales managers", ("sales managers", "sales manager")),  # only the LAST word
        ("nodejs", ("nodejs", "node.js", "node js")),
    ],
)
def test_a_plural_whose_singular_is_an_ordinary_word_is_not_turned_into_it(
    role: str, terms: tuple[str, ...]
) -> None:
    assert role_filter_terms(role) == terms


def test_the_ordinary_word_a_plural_role_would_have_matched_is_no_longer_a_match() -> None:
    hits, signal = _post(
        "Jane Doe's Post - LinkedIn", "1 day ago \u00b7 We're #hiring a new Graphic Designer ... x"
    )
    assert tab_role_fit(hits, signal, role_filter_terms("news")) == "mismatch"
    hits, signal = _post(
        "Jane Doe's Post - LinkedIn", "1 day ago \u00b7 Estate sale this weekend ... x"
    )
    assert tab_role_fit(hits, signal, role_filter_terms("sales")) == "mismatch"


def test_the_phrases_a_role_is_tested_as_are_bounded_and_start_with_the_typed_role() -> None:
    role = "sr full stack front end back end machine learning quality assurance"
    terms = role_filter_terms(role)
    assert terms[0] == role
    assert 1 < len(terms) <= 12
    assert len(set(terms)) == len(terms)


# ── YH-3 / BC-6: what is searched is what the label says, and a cut is clean ─


@pytest.mark.parametrize(
    ("typed", "phrase"),
    [
        ("Senior Software Development Engineer in Test", "senior software development engineer"),
        ("one two three four of five", "one two three four"),
        ("one two three four five six", "one two three four five"),
        # a role the person typed is theirs: only OUR cut is kept off a dangling word
        ("software engineer in", "software engineer in"),
        ("engineer of", "engineer of"),
        # the cut is mid-phrase and lands on a real word: the label shows what was searched
        (
            "Senior Vice President of Machine Learning Engineering",
            "senior vice president of machine",
        ),
    ],
)
def test_a_role_that_has_to_be_cut_is_never_left_ending_on_a_connecting_word(
    typed: str, phrase: str
) -> None:
    assert role_phrase(typed) == phrase


@pytest.mark.parametrize(
    ("location", "label"),
    [
        ("Portland, OR", "software engineer -- Portland, OR (searched as Portland) -- last 3 days"),
        (
            "Washington, DC",
            "software engineer -- Washington, DC (searched as Washington) -- last 3 days",
        ),
        ("Bengaluru", "software engineer -- Bengaluru -- last 3 days"),
        ("bengaluru", "software engineer -- bengaluru -- last 3 days"),
        ("New York City", "software engineer -- New York City -- last 3 days"),
        # punctuation between the words is not a word that was left out
        (", Karnataka", "software engineer -- , Karnataka -- last 3 days"),
        ("Bengaluru,", "software engineer -- Bengaluru, -- last 3 days"),
        ("Hyderabad; Telangana", "software engineer -- Hyderabad; Telangana -- last 3 days"),
        (None, "software engineer -- last 3 days"),
    ],
)
def test_the_label_says_which_place_was_actually_searched(location: str | None, label: str) -> None:
    built = build_tab_query(
        query="software engineer", location=location, freshness=Freshness.THREE_DAYS
    )
    assert built.label == label


def test_the_provider_query_holds_only_the_place_the_label_says_was_searched() -> None:
    built = build_tab_query(
        query="software engineer", location="Portland, OR", freshness=Freshness.THREE_DAYS
    )
    assert built.query.query.endswith('"Portland"')
    assert "OR" not in built.query.query.split('("software engineer")')[1]
    assert "searched as Portland" in built.label


def test_the_places_segment_of_the_label_never_exceeds_its_cap_even_with_the_suffix() -> None:
    place = "a" * 59 + ", " + "b" * 39
    built = build_tab_query(query="engineer", location=place, freshness=Freshness.WEEK)
    segment = built.label.split(" -- ")[1]
    assert 0 < len(segment) <= 80
    assert segment.endswith(f"(searched as {'a' * 59})")


# ── ranking: the role tier ───────────────────────────────────────────────


def test_a_post_that_only_mentions_the_role_late_ranks_below_one_that_states_it() -> None:
    keys = [
        tab_rank_key(
            aggregator=False, posted_at=_NOW - timedelta(hours=1), position=0, unverified=True
        ),
        tab_rank_key(aggregator=False, posted_at=_NOW - timedelta(hours=90), position=1),
    ]
    assert sorted(range(2), key=lambda i: keys[i]) == [1, 0]  # the stale, stated post first


def test_the_aggregator_tier_still_outranks_the_role_tier() -> None:
    """The contract's first ranking rule is unchanged: non-aggregator accounts first."""
    keys = [
        tab_rank_key(aggregator=True, posted_at=_NOW, position=0),
        tab_rank_key(aggregator=False, posted_at=_NOW, position=1, unverified=True),
    ]
    assert sorted(range(2), key=lambda i: keys[i]) == [1, 0]


def test_within_a_role_tier_the_freshest_comes_first() -> None:
    keys = [
        tab_rank_key(
            aggregator=False, posted_at=_NOW - timedelta(hours=h), position=i, unverified=True
        )
        for i, h in enumerate((30, 5, 60))
    ]
    assert sorted(range(3), key=lambda i: keys[i]) == [1, 0, 2]


# ── real-shaped posts (the sanitized corpus), not only the composed fixture ─

_CORPUS = Path(__file__).parent / "golden" / "hiring_signals" / "inputs" / "search_hits.json"


def _corpus_fit(role: str, *, pull: str, title_has: str) -> str:
    """The fit of the one post of a sanitized real-shaped pull whose title holds
    `title_has`. The corpus is real text with identities replaced (see its README),
    so these are the shapes a provider really returns: a commenter's headline, an
    auto job-share for another role, a walk-in drive."""
    pulls = json.loads(_CORPUS.read_text(encoding="utf-8"))["pulls"]
    hits = [
        RawSearchHit(url=h["url"], title=h["title"], snippet=h["snippet"])
        for p in pulls
        if p["pull_id"] == pull
        for h in p["hits"]
    ]
    matched = [
        (h, sig)
        for h in hits
        if title_has in h.title and isinstance(sig := parse_hit(h), HiringSignal)
    ]
    assert len(matched) == 1, (pull, title_has, len(matched))
    _, signal = matched[0]
    copies = [
        h
        for h in hits
        if isinstance(p := parse_hit(h), HiringSignal) and p.activity_id == signal.activity_id
    ]
    return tab_role_fit(copies, signal, role_filter_terms(role_phrase(role)))


def test_real_shaped_a_role_only_a_commenters_line_names_is_unverified() -> None:
    """A referral post whose role appears only in a later fragment (another person's
    line): the old any-text rule kept it as a match, the tab now shows it as
    unverified."""
    assert (
        _corpus_fit("financial analyst", pull="per_jd_deloitte", title_has="HireHub.io") == "later"
    )
    assert (
        _corpus_fit("developer", pull="second_capture_global", title_has="Hard Rock Games")
        == "later"
    )


def test_real_shaped_an_auto_job_share_for_another_role_is_a_mismatch() -> None:
    """LinkedIn's own `We're #hiring a new Production Engineer` post: a commenter's
    or a tagline's `Engineer` never makes it a `mechanical engineer` post."""
    assert (
        _corpus_fit("mechanical engineer", pull="ahmedabad_mixed", title_has="Aquascape")
        == "mismatch"
    )
    assert (
        _corpus_fit("production engineer", pull="ahmedabad_mixed", title_has="Aquascape")
        == "stated"
    )


def test_real_shaped_a_post_that_says_the_role_in_its_opening_is_stated() -> None:
    assert (
        _corpus_fit("developer", pull="aggregator_template_blr", title_has="Databricks") == "stated"
    )


def test_real_shaped_a_two_letter_role_matches_a_time_of_day_and_that_is_a_documented_limit() -> (
    None
):
    """`PM` is a role AND a time of day. Across the whole sanitized corpus the only posts
    a `PM` role keeps are three walk-in posts that name a time -- the server cannot tell
    the senses apart, so the web app warns about a role this short before anyone trusts
    the list (`isLooseRole`, `LOOSE_ROLE_NOTE`). This pins the behavior the warning is for,
    so the warning is removed only together with the reason for it."""
    pulls = json.loads(_CORPUS.read_text(encoding="utf-8"))["pulls"]
    hits = [
        RawSearchHit(url=h["url"], title=h["title"], snippet=h["snippet"])
        for p in pulls
        for h in p["hits"]
    ]
    copies: dict[str, list[RawSearchHit]] = {}
    signals: dict[str, HiringSignal] = {}
    for hit in hits:
        parsed = parse_hit(hit)
        if isinstance(parsed, HiringSignal):
            copies.setdefault(parsed.activity_id, []).append(hit)
            signals.setdefault(parsed.activity_id, parsed)
    terms = role_filter_terms(role_phrase("PM"))
    kept = [
        aid for aid, sig in signals.items() if tab_role_fit(copies[aid], sig, terms) != "mismatch"
    ]
    assert len(kept) == 3
    for aid in kept:
        opening_text = opening(copies[aid][0].snippet)
        assert re.search(r"\bpm\b", opening_text, re.IGNORECASE)
        assert re.search(r"\d{1,2}(:\d{2})?\s*pm", opening_text, re.IGNORECASE)  # a clock time


def test_the_corpus_snippets_used_above_really_say_what_the_tests_claim() -> None:
    """A guard against the assertions above passing because the corpus changed."""
    pulls = json.loads(_CORPUS.read_text(encoding="utf-8"))["pulls"]
    by_pull = {p["pull_id"]: p["hits"] for p in pulls}
    hirehub = next(h for h in by_pull["per_jd_deloitte"] if "HireHub.io" in h["title"])
    assert "financial analyst" not in opening(hirehub["snippet"]).lower()
    assert "financial analyst" in hirehub["snippet"].lower()
    aquascape = next(h for h in by_pull["ahmedabad_mixed"] if "Aquascape" in h["title"])
    assert "production engineer" in opening(aquascape["snippet"]).lower()


# ── the web app's copy of the provider cap ───────────────────────────────

_WEB_TAB_LIB = Path(__file__).parent.parent / "web" / "src" / "lib" / "hiringSignalsTab.ts"


def test_the_web_apps_provider_result_limit_is_the_number_the_server_asks_for() -> None:
    """The page says a search "stopped at the provider's cap" when it returned as many
    results as the server asks a provider for. That number lives in two languages (the
    response carries no truncation flag, and adding one is a contract change), so this
    keeps the two literals one: a change on either side that is not made on the other
    fails here instead of silently making the note wrong."""
    source = _WEB_TAB_LIB.read_text(encoding="utf-8")
    found = re.search(r"export const PROVIDER_RESULT_LIMIT = (\d+);", source)
    assert found is not None, "the web app no longer declares PROVIDER_RESULT_LIMIT"
    assert int(found.group(1)) == MAX_RESULTS_PER_CALL
