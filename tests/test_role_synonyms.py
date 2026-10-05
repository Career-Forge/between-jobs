"""Role synonyms: the curated map, the slot parser, the registry search text, and the
role filter that applies the same synonyms to titles.

The corpus tests use invented, generic titles (no real people or employers) built from a
small cross product of seniority, core title and suffix, so each role family is about 60
distinct titles."""

from __future__ import annotations

import random
import re
from itertools import permutations, product
from typing import Any

import pytest

from between_jobs.api.hiring_signals import RawSearchHit, hit_matches_roles
from between_jobs.api.role_synonyms import (
    MAX_QUERY_ALTERNATIVES,
    ROLE_SYNONYMS,
    parse_role_query,
    registry_search_text,
)
from between_jobs.api.search_aggregation import (
    fetch_registry_lane,
    filter_by_role,
    matches_all_role_terms,
    role_term_patterns,
)
from between_jobs.api.search_providers import SearchResult

# -- the map itself ---------------------------------------------------------------------


def test_the_required_mappings_are_present() -> None:
    for abbreviation in ("sde", "swe"):
        assert {"software engineer", "software developer"} <= set(ROLE_SYNONYMS[abbreviation])
    assert {"sde", "swe"} <= set(ROLE_SYNONYMS["software engineer"])
    assert {"sde", "swe"} <= set(ROLE_SYNONYMS["software developer"])
    assert "machine learning" in ROLE_SYNONYMS["ml"]
    assert "ml" in ROLE_SYNONYMS["machine learning"]
    for abbreviation in ("qa", "sdet"):
        assert {
            "quality assurance",
            "software test",
            "test automation",
            "engineer in test",
        } <= set(ROLE_SYNONYMS[abbreviation])
    assert "qa" in ROLE_SYNONYMS["quality assurance"]
    assert "quality assurance engineer" in ROLE_SYNONYMS["qa engineer"]
    assert "qa engineer" in ROLE_SYNONYMS["quality assurance engineer"]
    assert {"business intelligence", "analytics"} <= set(ROLE_SYNONYMS["bi"])
    assert "bi" in ROLE_SYNONYMS["business intelligence"]
    assert "sre" in ROLE_SYNONYMS["site reliability"]
    assert "site reliability" in ROLE_SYNONYMS["sre"]


_QA_KEYS = (
    "qa",
    "sdet",
    "quality assurance",
    "software test",
    "qa engineer",
    "quality assurance engineer",
    "qa automation engineer",
    "quality assurance automation engineer",
    "qa automation",
    "quality assurance automation",
)


def test_the_qa_family_uses_tight_phrases_not_bare_quality_or_test() -> None:
    """The bare words would admit "Quality Control Inspector" and "Test Pilot"; the bare
    phrases "quality engineer" and "test engineer" are the tail of "Supplier Quality
    Engineer", "Flight Test Engineer" and "RF Test Engineer", so they are not synonyms of
    "qa" either."""
    for key in _QA_KEYS:
        alternatives = ROLE_SYNONYMS[key]  # a missing key is a failure, not a skip
        for bare in ("quality", "test", "quality engineer", "test engineer"):
            assert bare not in alternatives, (key, bare)


@pytest.mark.parametrize("key", sorted(ROLE_SYNONYMS))
def test_every_entry_is_well_formed(key: str) -> None:
    alternatives = ROLE_SYNONYMS[key]
    assert key == key.lower().strip()
    assert "  " not in key
    assert alternatives, "an entry with no alternatives does nothing"
    assert len(set(alternatives)) == len(alternatives)
    assert key not in alternatives, "the key is already an implicit alternative"
    for alternative in alternatives:
        assert alternative == alternative.lower().strip()
        assert "  " not in alternative
        assert re.fullmatch(r"[a-z0-9+#./ -]+", alternative), alternative
    # a slot must always be expandable on its own: the alternatives budget has to fit it
    assert 1 + len(alternatives) <= MAX_QUERY_ALTERNATIVES


@pytest.mark.parametrize(
    "group",
    [
        ("sde", "swe", "software engineer", "software developer", "software development engineer"),
        ("ml", "machine learning"),
        ("ai", "artificial intelligence"),
        ("nlp", "natural language processing"),
        ("devops", "dev ops"),
        ("frontend", "front end"),
        ("backend", "back end"),
        ("fullstack", "full stack"),
        ("dba", "database administrator"),
        ("sysadmin", "system administrator", "systems administrator"),
        ("ux", "user experience"),
        ("ui", "user interface"),
        ("tpm", "technical program manager"),
        ("cybersecurity", "cyber security"),
        ("infosec", "information security"),
    ],
)
def test_interchangeable_names_map_to_each_other(group: tuple[str, ...]) -> None:
    for member in group:
        assert set(ROLE_SYNONYMS[member]) == set(group) - {member}


# -- the parser ---------------------------------------------------------------------------


def test_parse_splits_a_query_into_slots_longest_key_first() -> None:
    slots = parse_role_query("senior qa engineer").slots

    assert [s.words for s in slots] == [("senior",), ("qa", "engineer")]
    assert slots[0].alternatives == ()
    assert "sdet" in slots[1].alternatives


def test_parse_keeps_the_users_own_words_even_when_a_key_matches() -> None:
    (slot,) = parse_role_query("SDE").slots

    assert slot.words == ("sde",)
    assert "software engineer" in slot.alternatives


def test_parse_of_a_query_without_synonyms_is_one_plain_slot_per_word() -> None:
    slots = parse_role_query("Backend Python Developer").slots

    assert [s.words for s in slots] == [("backend",), ("python",), ("developer",)]
    assert slots[1].alternatives == ()


@pytest.mark.parametrize("query", ["", "   ", "a", "a b c", "or", '""', "-", "- -"])
def test_parse_of_an_empty_or_all_noise_query_has_no_slots(query: str) -> None:
    assert parse_role_query(query).slots == ()


def test_parse_reads_quotes_a_leading_minus_and_a_bare_or_as_plain_words() -> None:
    slots = parse_role_query('"machine learning" -java python or spring').slots

    assert [s.words for s in slots] == [
        ("machine", "learning"),
        ("java",),
        ("python",),
        ("spring",),
    ]


def test_parse_looks_a_key_up_through_the_punctuation_a_person_types_around_it() -> None:
    slots = parse_role_query("Software Engineer, Backend").slots

    assert [s.words for s in slots] == [("software", "engineer"), ("backend",)]
    assert "sde" in slots[0].alternatives
    assert slots[1].alternatives == ("back end",)


@pytest.mark.parametrize("query", ["(SDE)", "sde,", "sde.", "[sde];", "'sde'", "(sde),"])
def test_parse_finds_a_synonym_key_wrapped_in_punctuation(query: str) -> None:
    (slot,) = parse_role_query(query).slots

    assert slot.words == ("sde",)
    assert "software engineer" in slot.alternatives


@pytest.mark.parametrize(
    "term", ["c++", "c#", ".net", "node.js", "ci/cd", "web3", "sr.", "full-time"]
)
def test_parse_leaves_a_term_that_carries_punctuation_on_purpose_untouched(term: str) -> None:
    (slot,) = parse_role_query(term).slots

    assert slot.words == (term,)
    assert slot.alternatives == ()


def test_a_pasted_title_with_commas_finds_the_same_roles_as_the_same_words_without() -> None:
    titles = ["SDE II, Backend", "Software Developer, Backend", "Backend Software Engineer"]

    assert _kept(titles, "Software Engineer, Backend") == titles
    assert _kept(titles, "software engineer backend") == titles
    assert _kept(["SDE, Platform", "Software Engineer", "Hardware Engineer"], "SDE,") == [
        "SDE, Platform",
        "Software Engineer",
    ]


# -- registry search text -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ""),
        ("engineer", "engineer"),
        ("python developer", "python developer"),
        ("ml engineer", 'ml engineer or "machine learning" engineer'),
        (
            "machine learning engineer",
            "machine learning engineer or ml engineer",
        ),
        (
            "sde",
            'sde or swe or "software engineer" or "software developer"'
            ' or "software development engineer"',
        ),
        (
            "bi developer",
            'bi developer or "business intelligence" developer or analytics developer',
        ),
        ("backend engineer", 'backend engineer or "back end" engineer'),
        ("SRE", 'sre or "site reliability"'),
        ("dev", 'dev or developer or "software engineer"'),
        (
            "dev engineer",
            'dev engineer or developer engineer or "software engineer" engineer',
        ),
    ],
)
def test_registry_search_text(query: str, expected: str) -> None:
    assert registry_search_text(query) == expected


@pytest.mark.parametrize(
    ("with_punctuation", "without"),
    [
        ("(SDE)", "sde"),
        ("sde,", "sde"),
        ("sde.", "sde"),
        ("Software Engineer, Backend", "software engineer backend"),
        ("ml, engineer", "ml engineer"),
    ],
)
def test_punctuation_around_a_word_does_not_change_the_search(
    with_punctuation: str, without: str
) -> None:
    assert registry_search_text(with_punctuation) == registry_search_text(without)


def test_a_query_with_no_searchable_word_is_empty_so_the_registry_browses() -> None:
    for query in ("", "   ", "a", '""', "-", "++ //", "or"):
        assert registry_search_text(query) == ""


def _units(alternative: str) -> frozenset[str]:
    """An alternative as the unordered set of its words and quoted phrases: websearch ANDs
    them, so two alternatives with the same units are the same search."""
    return frozenset(re.findall(r'"[^"]*"|\S+', alternative))


def _alternative_set(query: str) -> set[frozenset[str]]:
    return {_units(a) for a in registry_search_text(query).split(" or ")}


def test_the_cap_is_sixteen_and_a_query_that_exactly_fills_it_is_fully_expanded() -> None:
    assert MAX_QUERY_ALTERNATIVES == 16
    # ml, ai, nlp and sre have one synonym phrase each: 2 x 2 x 2 x 2 = 16, exactly the cap
    assert len(registry_search_text("ml ai nlp sre").split(" or ")) == 16
    # a fifth would make 32: it stays as typed, in every alternative
    five = registry_search_text("ml ai nlp sre ux").split(" or ")
    assert len(five) == 16
    assert all(alternative.endswith(" ux") for alternative in five)


def test_the_product_is_multiplied_not_added_and_the_boundary_is_inclusive() -> None:
    # three slots of two choices each: 8 with room for 8, 4 (the third stays literal) with 7
    assert len(registry_search_text("ml ai nlp", max_alternatives=8).split(" or ")) == 8
    assert len(registry_search_text("ml ai nlp", max_alternatives=7).split(" or ")) == 4
    # sysadmin has three choices: two of them make 9, a third would make 27
    assert len(registry_search_text("sysadmin sysadmin", max_alternatives=9).split(" or ")) == 9
    assert len(registry_search_text("sysadmin sysadmin", max_alternatives=8).split(" or ")) == 3
    assert len(registry_search_text("sysadmin sysadmin sysadmin").split(" or ")) == 9


def test_a_two_slot_compound_is_expanded_in_both_slots_whichever_comes_first() -> None:
    """The cap used to be spent left to right, so which slot got its synonyms depended on word
    order and the other slot's synonyms were lost: 'ml software engineer' kept only the ml
    spelling and dropped sde/swe/software developer, while 'software engineer ml' did the
    opposite. Both slots are in the search now, in either order."""
    for query in ("ml software engineer", "software engineer ml"):
        alternatives = _alternative_set(query)
        assert len(alternatives) == 10
        assert frozenset({"ml", "sde"}) in alternatives
        assert frozenset({'"machine learning"', "swe"}) in alternatives
        assert frozenset({"ml", '"software developer"'}) in alternatives

    for query in ("backend sde", "sde backend"):
        alternatives = _alternative_set(query)
        assert len(alternatives) == 10
        assert frozenset({"backend", "swe"}) in alternatives
        assert frozenset({'"back end"', '"software engineer"'}) in alternatives


def test_the_synonym_slot_with_most_choices_is_expanded_first_not_the_leftmost() -> None:
    # qa has 8 choices, sde 5, ml 2. qa goes in (8); sde would make 40 and is skipped; ml still
    # fits (16) and goes in even though it comes AFTER the skipped slot.
    alternatives = _alternative_set("sde qa ml")

    assert len(alternatives) == 16
    assert all("sde" in a for a in alternatives)  # the skipped slot stayed as typed
    assert any('"machine learning"' in a for a in alternatives)  # the later, smaller slot expanded
    assert any('"quality assurance"' in a for a in alternatives)


@pytest.mark.parametrize(
    "words",
    [
        ("sde", "qa", "ml"),
        ("ml", "sde", "ai"),
        ("sde", "backend", "ml", "ai"),
        ("sysadmin", "sdet", "ui", "ux", "sre"),
        ("frontend", "backend", "fullstack", "devops", "dba"),
        ("ml", "ai", "nlp", "sre", "ux", "ui", "tpm"),
    ],
)
def test_the_same_words_send_the_same_search_in_any_order(words: tuple[str, ...]) -> None:
    expected = _alternative_set(" ".join(words))

    for ordering in permutations(words):
        assert _alternative_set(" ".join(ordering)) == expected, ordering


@pytest.mark.parametrize(
    "query",
    [
        "sde",
        "qa",
        "sdet",
        "qa engineer",
        "senior qa automation engineer",
        "ml ai nlp engineer",
        "sde qa bi ml sre dba ux ui tpm",
        "frontend backend fullstack developer",
        "front-end back-end full-stack engineer",
        "software engineer machine learning quality assurance",
        "dev eng sde swe qa ml bi",
    ],
)
def test_no_query_sends_more_than_the_alternatives_cap(query: str) -> None:
    text = registry_search_text(query)

    assert 1 <= len(text.split(" or ")) <= MAX_QUERY_ALTERNATIVES


def test_a_smaller_cap_is_honoured() -> None:
    assert len(registry_search_text("sde", max_alternatives=3).split(" or ")) == 1
    assert len(registry_search_text("ml", max_alternatives=2).split(" or ")) == 2


_HOSTILE = [
    "python -java or spring",
    '"machine learning" or "x',
    'data" or "science',
    "a or b or c",
    "OR OR",
    "-sde -qa",
    "o'reilly (a&b) | c !d e:* f<->g",
    "sde' or 1=1; --",
    "\u201cdata\u201d or \u201cscience\u201d",
    "c++ or c# or .net",
    "node.js/express -",
    "नौकरी इंजीनियर",
]


@pytest.mark.parametrize("query", _HOSTILE)
def test_user_text_never_reaches_the_search_syntax(query: str) -> None:
    """Only `or` between alternatives and balanced double quotes around a synonym phrase may
    appear. Nothing a person types can add a NOT, an OR, a phrase, or a tsquery operator."""
    text = registry_search_text(query)

    assert not set(text) & set("|&!():*<>'\\;,")
    assert text.count('"') % 2 == 0
    for alternative in text.split(" or "):
        outside_quotes = re.sub(r'"[^"]*"', " ", alternative)
        for word in outside_quotes.split():
            assert word != "or"
            assert not word.startswith("-")
        # a quoted run is only ever one of OUR synonym phrases, never the user's text
        for phrase in re.findall(r'"([^"]*)"', alternative):
            assert any(phrase == a for alts in ROLE_SYNONYMS.values() for a in alts)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # punctuation between two letters or digits splits a word; the NOT and OR the sanitizer
        # exists to stop are the pieces left over, so each must be removed from the text
        ("python (-java)", "python java"),
        ("x/(-y)", "x/ y"),
        ("python,or,java", "python java"),
        ("a,or,b", "a b"),
    ],
)
def test_punctuation_cannot_smuggle_a_not_or_an_or_past_the_sanitizer(
    query: str, expected: str
) -> None:
    """Exact output, not a property: a stray `or` becomes an alternative separator, so a
    check that splits on ` or ` first cannot see one that got through."""
    assert registry_search_text(query) == expected


def test_curly_quotes_are_read_like_straight_ones() -> None:
    assert registry_search_text("\u201cdata scientist\u201d") == "data scientist"


def test_the_users_own_words_survive_when_they_are_ordinary() -> None:
    assert registry_search_text("node.js developer") == "node.js developer"
    assert registry_search_text("c++ developer") == "c++ developer"
    assert registry_search_text('"data scientist"') == "data scientist"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("web3 developer", "web3 developer"),  # digits are kept
        ("k8s engineer", "k8s engineer"),
        ("c# developer", "c# developer"),  # # / - are kept
        ("ci/cd engineer", "ci/cd engineer"),
        (
            "full-stack developer",
            'full-stack developer or fullstack developer or "full stack" developer',
        ),
        ("\u0928\u094c\u0915\u0930\u0940", "\u0928\u094c\u0915\u0930\u0940"),  # combining marks
        # curly quotes are stripped BEFORE the key lookup, so the synonym is still found
        ("\u201cml\u201d engineer", 'ml engineer or "machine learning" engineer'),
    ],
)
def test_ordinary_search_characters_survive(query: str, expected: str) -> None:
    assert registry_search_text(query) == expected


def test_a_curly_quoted_key_word_is_still_a_key() -> None:
    (slot, _) = parse_role_query("\u201cml\u201d engineer").slots

    assert slot.words == ("ml",)
    assert "machine learning" in slot.alternatives


# -- the role filter applies the same synonyms to titles ------------------------------------


def _result(title: str, n: int = 0) -> SearchResult:
    return SearchResult(
        provider="registry",
        title=title,
        company="Acme",
        location=None,
        remote=None,
        apply_url=f"https://example.com/jobs/{n}/{title}",
        snippet="",
        posted_at=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        sponsorship_signal="unknown",
        source_tier=1.0,
    )


def _kept(titles: list[str], query: str) -> list[str]:
    results = [_result(t, i) for i, t in enumerate(titles)]
    return [r.title for r in filter_by_role(results, query)]


def test_ml_engineer_matches_both_spellings_and_not_a_machine_shop() -> None:
    titles = [
        "Machine Learning Engineer",
        "ML Engineer",
        "Staff ML Engineer, Platform",
        "Senior Machine Learning Engineer II",
        "Engineering Manager, Machine Shop",
        "Machine Operator",
        "Data Engineer",
        "AI Engineer",
        "Mechanical Engineer",
    ]

    assert _kept(titles, "ml engineer") == titles[:4]
    assert _kept(titles, "machine learning engineer") == titles[:4]


def test_sde_matches_every_software_engineer_spelling_but_not_a_manager_or_a_sales_role() -> None:
    titles = [
        "SDE II",
        "Software Engineer",
        "Software Development Engineer, Payments",
        "Senior SWE",
        "Software Developer",
        "Software Engineering Manager",
        "Software Sales Representative",
        "Sales Engineer",
        "Hardware Engineer",
    ]

    assert _kept(titles, "sde") == titles[:5]
    assert _kept(titles, "software engineer") == [
        "SDE II",
        "Software Engineer",
        "Software Development Engineer, Payments",
        "Senior SWE",
        "Software Developer",
    ]


_QA_FAMILY = [
    "QA Analyst",
    "Quality Assurance Engineer",
    "Software Test Engineer",
    "SDET",
    "Software Development Engineer in Test",
    "Software Quality Engineer",
    "Test Automation Engineer",
]
_QA_DISTRACTORS = [
    "Quality Control Inspector",
    "Quality Inspector",
    "Quality Manager",
    "Test Pilot",
    "Test Prep Tutor",
    "Software Engineer",
    "Equality and Inclusion Lead",
    # a synonym phrase inside a longer word is not that phrase
    "Equality Assurance Officer",
    # manufacturing, hardware and aerospace quality and test roles are not software QA
    "Supplier Quality Engineer",
    "Manufacturing Quality Engineer",
    "Hardware Test Engineer",
    "RF Test Engineer",
    "Flight Test Engineer",
]


def test_qa_matches_the_quality_family_but_not_inspectors_pilots_or_hardware_roles() -> None:
    assert _kept(_QA_FAMILY + _QA_DISTRACTORS, "qa") == _QA_FAMILY
    assert _kept(_QA_FAMILY + _QA_DISTRACTORS, "sdet") == _QA_FAMILY


def test_spelling_quality_assurance_out_reaches_the_same_family_as_qa() -> None:
    """The long form used to reach only "QA" titles; it now finds every spelling the
    abbreviation does."""
    assert _kept(_QA_FAMILY + _QA_DISTRACTORS, "quality assurance") == _QA_FAMILY
    # "engineer" is its own requirement, so a title with no such word (QA Analyst) is not a match
    with_engineer = [t for t in _QA_FAMILY if t != "QA Analyst"]
    assert _kept(_QA_FAMILY + _QA_DISTRACTORS, "quality assurance engineer") == with_engineer
    assert _kept(_QA_FAMILY + _QA_DISTRACTORS, "qa engineer") == with_engineer
    assert _kept(
        ["SDET", "Test Automation Engineer", "QA Automation Engineer", *_QA_DISTRACTORS],
        "quality assurance automation engineer",
    ) == ["SDET", "Test Automation Engineer", "QA Automation Engineer"]


def test_a_plain_test_or_quality_engineer_is_not_a_qa_synonym_but_its_own_words_find_it() -> None:
    titles = ["Test Engineer", "Quality Engineer"]

    assert _kept(titles, "qa") == []
    assert _kept(titles, "test engineer") == ["Test Engineer"]
    assert _kept(titles, "quality engineer") == ["Quality Engineer"]


def test_qa_engineer_reaches_a_standalone_sdet_title() -> None:
    assert _kept(
        ["SDET II", "QA Engineer", "QA Software Engineer", "QA Analyst"], "qa engineer"
    ) == [
        "SDET II",
        "QA Engineer",
        "QA Software Engineer",
    ]


def test_bi_matches_business_intelligence_and_analytics_titles_only() -> None:
    titles = [
        "BI Analyst",
        "Business Intelligence Developer",
        "Analytics Developer",
        "Bilingual Support Agent",
        "Biomedical Engineer",
        "Intelligence Analyst",
        "Geoanalytics Developer",  # "analytics" inside a longer word is not the word
    ]

    assert _kept(titles, "bi developer") == [
        "Business Intelligence Developer",
        "Analytics Developer",
    ]
    assert _kept(titles, "bi") == titles[:3]


def test_dev_means_developer_not_every_kind_of_engineer() -> None:
    titles = [
        "Software Developer",
        "Dev Lead",
        "Senior Dev",
        "Developer Advocate",
        "Software Engineer",
        "Sales Engineer",
        "Mechanical Engineer",
        "Civil Engineer",
    ]

    assert _kept(titles, "dev") == titles[:5]
    assert _kept(titles, "dev engineer") == ["Software Engineer"]
    assert _kept(["Senior Software Engineer", "Senior Mechanical Engineer"], "senior dev") == [
        "Senior Software Engineer"
    ]
    assert _kept(["iOS Software Engineer", "iOS Designer"], "ios dev") == ["iOS Software Engineer"]


def test_dev_never_maps_to_the_bare_word_engineer_and_eng_does() -> None:
    assert "developer" in ROLE_SYNONYMS["dev"]
    assert "engineer" not in ROLE_SYNONYMS["dev"]
    assert "engineer" in ROLE_SYNONYMS["eng"]
    # a lone "eng" is the word "engineer", as typed: every Engineer title, not Engineering Manager
    assert _kept(["Mechanical Engineer", "Software Engineer", "Engineering Manager"], "eng") == [
        "Mechanical Engineer",
        "Software Engineer",
    ]


# One title per hand-written alternative, written out here (not read from the map), so
# deleting an entry or one of its alternatives from the map fails a test.
@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("software test", "QA Analyst"),
        ("software test", "SDET"),
        ("quality assurance", "SDET"),
        ("quality assurance", "Software Test Engineer"),
        ("quality assurance", "Software Quality Engineer"),
        ("quality assurance", "Test Automation Engineer"),
        ("quality assurance", "Automation Test Engineer"),
        ("quality assurance", "Software Development Engineer in Test"),
        ("qa", "Quality Assurance Engineer"),
        ("qa", "Software Test Engineer"),
        ("qa", "Software Quality Engineer"),
        ("qa", "Test Automation Engineer"),
        ("qa", "Automation Test Engineer"),
        ("qa", "Software Engineer in Test"),
        ("qa", "SDET"),
        ("sdet", "QA Analyst"),
        ("sdet", "Quality Assurance Analyst"),
        ("sdet", "Software Test Engineer"),
        ("sdet", "Software Quality Engineer"),
        ("sdet", "Test Automation Engineer"),
        ("sdet", "Automation Test Engineer"),
        ("sdet", "Software Engineer in Test"),
        ("qa engineer", "Quality Assurance Engineer"),
        ("qa engineer", "Software Test Engineer"),
        ("qa engineer", "Software Quality Engineer"),
        ("qa engineer", "Test Automation Engineer"),
        ("qa engineer", "Automation Test Engineer"),
        ("qa engineer", "Engineer in Test"),
        ("qa engineer", "SDET"),
        ("quality assurance engineer", "QA Engineer"),
        ("quality assurance engineer", "QA Automation Engineer"),
        ("quality assurance engineer", "Software Test Engineer"),
        ("quality assurance engineer", "Software Quality Engineer"),
        ("quality assurance engineer", "Test Automation Engineer"),
        ("quality assurance engineer", "Automation Test Engineer"),
        ("quality assurance engineer", "Engineer in Test"),
        ("quality assurance engineer", "SDET"),
        ("qa automation engineer", "SDET"),
        ("qa automation engineer", "Test Automation Engineer"),
        ("qa automation engineer", "Automation Test Engineer"),
        ("qa automation engineer", "Quality Assurance Automation Engineer"),
        ("quality assurance automation engineer", "QA Automation Engineer"),
        ("quality assurance automation engineer", "Test Automation Engineer"),
        ("quality assurance automation engineer", "Automation Test Engineer"),
        ("quality assurance automation engineer", "SDET"),
        ("qa automation", "Test Automation"),
        ("qa automation", "Automation Test"),
        ("qa automation", "Quality Assurance Automation"),
        ("qa automation", "SDET"),
        ("quality assurance automation", "QA Automation"),
        ("quality assurance automation", "Test Automation"),
        ("quality assurance automation", "Automation Test"),
        ("quality assurance automation", "SDET"),
        ("back-end engineer", "Backend Engineer"),
        ("back-end engineer", "Back End Engineer"),
        ("back-end", "Backend"),
        ("back-end", "Back End"),
        ("front-end", "Frontend"),
        ("front-end", "Front End"),
        ("full-stack developer", "Full Stack Developer"),
        ("full-stack developer", "Fullstack Developer"),
        ("full-stack", "Full Stack"),
        ("full-stack", "Fullstack"),
        ("bi", "Business Intelligence Developer"),
        ("bi", "Analytics Engineer"),
        ("business intelligence", "BI Analyst"),
        ("eng", "Engineer"),
        ("dev", "Developer"),
        ("dev", "Software Engineer"),
    ],
)
def test_each_hand_written_synonym_finds_its_title(query: str, title: str) -> None:
    assert _kept([title], query) == [title]


@pytest.mark.parametrize(
    ("title", "query"),
    [
        ("Front-End Engineer", "frontend engineer"),
        ("Front End Developer", "frontend developer"),
        ("Frontend Engineer", "front end engineer"),
        ("Back-End Engineer", "backend engineer"),
        ("Full Stack Engineer", "fullstack engineer"),
        ("Fullstack Engineer", "full-stack engineer"),
        ("Staff SRE", "site reliability engineer"),
        ("Dev Ops Engineer", "devops engineer"),
        ("Cyber Security Analyst", "cybersecurity analyst"),
        ("Systems Administrator", "sysadmin"),
        ("User Experience Designer", "ux designer"),
    ],
)
def test_spelling_variants_and_abbreviations_match_in_both_directions(
    title: str, query: str
) -> None:
    assert _kept([title, "Unrelated Role"], query) == [title]


def test_a_query_without_synonyms_filters_exactly_as_before() -> None:
    titles = ["Senior Backend Engineer", "Backend Engineers", "Backend Engineering Manager"]
    # (backend is a spelling-variant key, but that only ADDS the "back end" spelling)
    assert _kept(titles, "backend engineer") == ["Senior Backend Engineer"]
    assert _kept(["AI Engineering Intern", "AI Engineer II"], "ai engineer") == ["AI Engineer II"]
    assert _kept(["Senior .NET Developer", "ASP.NET Developer"], ".net developer") == [
        "Senior .NET Developer",
        "ASP.NET Developer",
    ]


def test_a_quoted_or_dashed_query_filters_like_the_plain_words() -> None:
    titles = ["Machine Learning Engineer", "Java Engineer"]

    assert _kept(titles, '"machine learning" engineer') == ["Machine Learning Engineer"]
    assert _kept(titles, "engineer -java") == ["Java Engineer"]  # a minus is not a NOT
    assert _kept(titles, "machine or learning") == ["Machine Learning Engineer"]


# -- recall and precision on a generated corpus -----------------------------------------------

_LEVELS = ("", "Senior ", "Staff ", "Principal ", "Junior ", "Lead ")
_TAILS = ("", " II", ", Platform", " - Remote", " (Contract)", ", Payments")


def _corpus(cores: list[str], size: int = 60) -> list[str]:
    titles: list[str] = []
    for tail, level, core in product(_TAILS, _LEVELS, cores):
        title = f"{level}{core}{tail}"
        if title not in titles:
            titles.append(title)
        if len(titles) == size:
            break
    return titles


def _literal(title: str, query: str) -> bool:
    """What a role search matched before synonyms: every word of the query, nothing else."""
    return matches_all_role_terms(title, role_term_patterns(query))


_FAMILIES: list[dict[str, Any]] = [
    {
        "name": "software engineer",
        "cores": [
            "Software Engineer",
            "Software Developer",
            "SDE",
            "SWE",
            "Software Development Engineer",
        ],
        "queries": ["sde", "swe", "software engineer", "software developer"],
        "canonical": "software engineer",
        "distractors": [
            "Software Engineering Manager",
            "Software Sales Representative",
            "Sales Engineer",
            "Hardware Engineer",
            "Engineering Manager",
            "Software Architect",
            "Product Manager",
        ],
    },
    {
        "name": "machine learning engineer",
        "cores": [
            "Machine Learning Engineer",
            "ML Engineer",
            "Applied Machine Learning Engineer",
            "ML Platform Engineer",
        ],
        "queries": ["ml engineer", "machine learning engineer"],
        "canonical": "machine learning engineer",
        "distractors": [
            "Machine Operator",
            "Engineering Manager, Machine Shop",
            "Learning and Development Manager",
            "Mechanical Engineer",
            "Data Engineer",
            "AI Engineer",
        ],
    },
    {
        "name": "quality assurance",
        "cores": [
            "QA Engineer",
            "QA Analyst",
            "Quality Assurance Engineer",
            "Quality Assurance Analyst",
            "Software Test Engineer",
            "Software Quality Engineer",
            "Test Automation Engineer",
            "SDET",
            "Software Development Engineer in Test",
        ],
        "queries": ["qa", "sdet", "quality assurance"],
        "canonical": "quality assurance",
        "distractors": [
            "Quality Control Inspector",
            "Quality Inspector",
            "Quality Technician",
            "Quality Manager",
            "Test Pilot",
            "Test Prep Tutor",
            "Software Engineer",
            "Equality Assurance Officer",
            "Supplier Quality Engineer",
            "Manufacturing Quality Engineer",
            "Hardware Test Engineer",
            "RF Test Engineer",
            "Flight Test Engineer",
        ],
    },
    {
        "name": "qa engineer",
        "cores": [
            "QA Engineer",
            "Quality Assurance Engineer",
            "Software Test Engineer",
            "Software Quality Engineer",
            "Test Automation Engineer",
            "SDET",
            "Software Development Engineer in Test",
            "QA Automation Engineer",
        ],
        "queries": ["qa engineer", "quality assurance engineer"],
        "canonical": "qa engineer",
        "distractors": [
            "Quality Control Inspector",
            "QA Analyst",
            "Test Pilot",
            "Sales Engineer",
            "Software Engineer",
            "Supplier Quality Engineer",
            "Hardware Test Engineer",
            "Flight Test Engineer",
            "RF Test Engineer",
        ],
    },
    {
        "name": "business intelligence",
        "cores": [
            "Business Intelligence Analyst",
            "BI Analyst",
            "BI Developer",
            "Business Intelligence Developer",
            "BI Engineer",
        ],
        "queries": ["bi", "business intelligence"],
        "canonical": "business intelligence",
        "distractors": [
            "Bilingual Support Agent",
            "Biomedical Engineer",
            "Intelligence Analyst",
            "Biology Researcher",
            "Data Engineer",
        ],
    },
    {
        "name": "sre abbreviation",
        "cores": ["Site Reliability Engineer", "SRE", "Site Reliability Lead"],
        "queries": ["sre"],
        "canonical": "site reliability",
        "distractors": [
            "Reliability Engineer, Manufacturing",
            "Site Manager",
            "Site Engineer, Construction",
            "DevOps Engineer",
        ],
    },
    {
        "name": "site reliability engineer",
        "cores": ["Site Reliability Engineer", "SRE"],
        "queries": ["sre", "site reliability engineer"],
        "canonical": "site reliability engineer",
        "distractors": [
            "Reliability Engineer, Manufacturing",
            "Site Manager",
            "Site Engineer, Construction",
            "DevOps Engineer",
        ],
    },
    {
        "name": "front end",
        "cores": ["Frontend Engineer", "Front-End Engineer", "Front End Engineer"],
        "queries": ["frontend engineer", "front end engineer", "front-end engineer"],
        "canonical": "front end engineer",
        "distractors": ["Backend Engineer", "Front Desk Agent", "Mechanical Engineer"],
    },
]


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda f: str(f["name"]))
def test_each_synonym_query_retrieves_every_title_of_its_family(
    family: dict[str, Any],
) -> None:
    """All of them, not most: a threshold would let one whole core title (about one in
    seven of a family's titles) vanish unnoticed."""
    corpus = _corpus(family["cores"])
    assert len(corpus) >= 40

    for query in family["queries"]:
        kept = _kept(corpus, query)
        assert set(kept) == set(corpus), (query, set(corpus) - set(kept))


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda f: str(f["name"]))
def test_each_synonym_query_keeps_everything_the_long_form_found_before(
    family: dict[str, Any],
) -> None:
    """No regression and a real gain: whatever the plain long-form query matched, every
    synonym query of the family still matches."""
    corpus = _corpus(family["cores"]) + family["distractors"]
    before = {t for t in corpus if _literal(t, family["canonical"])}
    assert before

    for query in family["queries"]:
        kept = set(_kept(corpus, query))
        assert before <= kept, (query, before - kept)


@pytest.mark.parametrize("family", _FAMILIES, ids=lambda f: str(f["name"]))
def test_no_synonym_query_retrieves_a_distractor(family: dict[str, Any]) -> None:
    for query in family["queries"]:
        assert _kept(family["distractors"], query) == [], query


def test_the_synonym_queries_find_what_the_literal_query_missed() -> None:
    sde = _corpus(_FAMILIES[0]["cores"])
    ml = _corpus(_FAMILIES[1]["cores"])

    assert len(_kept(sde, "sde")) > sum(_literal(t, "sde") for t in sde)
    assert len(_kept(ml, "ml engineer")) > sum(_literal(t, "ml engineer") for t in ml)


def test_the_synonym_filter_never_loses_a_match_the_plain_filter_had_and_the_cap_holds() -> None:
    """Seeded random queries and titles over the whole vocabulary of the map: whatever the
    plain AND-of-words filter kept, the synonym filter keeps; and no query ever sends more than
    the cap of alternatives."""
    rng = random.Random(11)
    words = sorted(
        {w for key in ROLE_SYNONYMS for w in key.split()}
        | {w for values in ROLE_SYNONYMS.values() for phrase in values for w in phrase.split()}
        | {"engineer", "manager", "senior", "python", "analyst", "designer", "lead"}
    )
    for _ in range(3000):
        query = " ".join(rng.choice(words) for _ in range(rng.randint(1, 4)))
        titles = [" ".join(rng.choice(words) for _ in range(rng.randint(1, 5))) for _ in range(6)]
        patterns = role_term_patterns(query)
        plain = {t for t in titles if matches_all_role_terms(t.lower(), patterns)}
        assert plain <= set(_kept(titles, query)), (query, plain - set(_kept(titles, query)))
        text = registry_search_text(query)
        assert len(text.split(" or ")) <= MAX_QUERY_ALTERNATIVES


# -- Hiring Signals keeps its own, unexpanded matching ------------------------------------


def test_hiring_signals_role_matching_is_not_expanded() -> None:
    """Hiring Signals shares the term patterns, not the synonym filter: a role phrase there
    still means exactly its own words."""
    assert [p.pattern for p in role_term_patterns("sde")] == [r"(?<!\w)sde(?!\w)"]
    hit = RawSearchHit("u", "We're hiring", "SDE II, Payments. Python.")
    assert not hit_matches_roles(hit, ["software engineer"])
    assert hit_matches_roles(hit, ["sde"])


# -- the registry lane sends the expanded text --------------------------------------------


class _Rpc:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> Any:
        class _R:
            data = self._data

        return _R()


class _Supabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        self.calls.append((name, params))
        return _Rpc([])


async def test_the_registry_lane_sends_the_expanded_query_and_keeps_the_limit() -> None:
    supabase = _Supabase()

    await fetch_registry_lane(supabase, query="ml engineer", limit=40)  # type: ignore[arg-type]

    assert supabase.calls == [
        (
            "search_job_registry_postings",
            {"search_query": 'ml engineer or "machine learning" engineer', "result_limit": 40},
        )
    ]


async def test_an_empty_query_still_browses_through_the_registry_lane() -> None:
    supabase = _Supabase()

    await fetch_registry_lane(supabase, query="")  # type: ignore[arg-type]

    assert supabase.calls[0][1]["search_query"] == ""
