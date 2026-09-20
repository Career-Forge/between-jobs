"""Tests for Hiring Signals P3's application -> query derivation
(`hiring_signal_query`): the role-term rule, the locale rule, the company
clean-up, and the query/label built from them.

The role-term cases marked "real title" are job titles as real postings write
them -- the rule was checked against titles like these (see the module
docstring) rather than against titles invented to fit it. Titles are all that
is kept here; nothing links one to an employer.
"""

from __future__ import annotations

import time

import pytest

from between_jobs.api.hiring_signal_query import (
    NoCompanyError,
    build_application_query,
    clean_display_text,
    company_query_name,
    derive_role_terms,
    locale_for_location,
    role_family_term,
    role_phrase_variants,
)
from between_jobs.api.hiring_signals import Freshness, Locale

# ── role terms ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # real titles
        ("Staff Software Engineer", "software engineer"),
        ("Forward Deployed AI Engineer", "forward deployed ai engineer"),
        ("AI Engineer", "ai engineer"),
        ("Applied Machine Learning", "applied machine learning"),
        (
            "Applied Research Scientist - AI Models & Agents",
            "applied research scientist",
        ),
        (
            "Machine Learning Engineer - Inference Optimization & Acceleration",
            "machine learning engineer",
        ),
        ("Agentic AI Engineer", "agentic ai engineer"),
        ("Software Engineer", "software engineer"),
        # everything after a spaced separator is team/location/programme
        ("Software Engineer, Payments Infrastructure", "software engineer"),
        ("Product Manager | Growth", "product manager"),
        ("Data Engineer @ Somewhere", "data engineer"),
        ("Backend Engineer / Platform", "backend engineer"),
        ("Engineer: Payments", "engineer"),
        # brackets are dropped anywhere
        ("Senior Software Engineer II (Payments) - Remote", "software engineer"),
        ("Designer [Contract] (Hybrid)", "designer"),
        # leading level words, singly and stacked
        ("Senior Staff Software Engineer", "software engineer"),
        ("Sr. Data Scientist", "data scientist"),
        ("Principal Engineer", "engineer"),
        ("Associate Product Manager", "product manager"),
        ("Junior Developer", "developer"),
        # trailing level markers
        ("Software Engineer II", "software engineer"),
        ("Software Engineer III", "software engineer"),
        ("Machine Learning Engineer L5", "machine learning engineer"),
        ("Software Engineer Level 2", "software engineer"),
        ("SDE 2", "sde"),
        # level phrases
        ("Software Engineer, New Grad", "software engineer"),
        ("Entry-Level Analyst", "analyst"),
        ("Mid Level Backend Developer", "backend developer"),
        # work-mode words
        ("Remote Software Engineer", "software engineer"),
        ("Full-time Hybrid Data Analyst", "data analyst"),
        # symbols in a role survive
        ("Sr. .NET Developer", ".net developer"),
        ("C++ Engineer", "c++ engineer"),
        ("Full-Stack Engineer", "full-stack engineer"),
        ("AI/ML Engineer", "ai/ml engineer"),
        # level words in the MIDDLE are part of the role, and so is Intern
        ("Engineering Lead", "engineering lead"),
        ("Sales Associate", "sales associate"),
        ("Software Engineering Intern", "software engineering intern"),
    ],
)
def test_derive_role_terms(title: str, expected: str) -> None:
    assert derive_role_terms(title) == (expected,)


@pytest.mark.parametrize("title", ["", "   ", "Senior", "Sr. Staff", "(Remote)", None, 7, []])
def test_derive_role_terms_returns_nothing_when_no_role_can_be_read(title: object) -> None:
    assert derive_role_terms(title) == ()


def test_derive_role_terms_falls_through_when_the_first_segment_is_only_level_words() -> None:
    assert derive_role_terms("Senior - Software Engineer") == ("software engineer",)


def test_derive_role_terms_is_case_insensitive_about_level_words() -> None:
    assert derive_role_terms("SENIOR SOFTWARE ENGINEER") == ("software engineer",)
    assert derive_role_terms("senior software engineer iii") == ("software engineer",)


def test_derive_role_terms_is_fast_on_hostile_titles() -> None:
    hostile = [
        "(" * 5000,
        "a " * 5000,
        "Senior " * 2000,
        ", " * 3000,
        " - " * 2000,
        "I " * 3000,
        "\u00a0" * 5000,
    ]
    start = time.perf_counter()
    for title in hostile:
        derive_role_terms(title)
    assert time.perf_counter() - start < 1.0


def test_role_family_term_is_the_head_noun_only_for_known_role_nouns() -> None:
    assert role_family_term("software engineer") == "engineer"
    assert role_family_term("forward deployed ai engineer") == "engineer"
    assert role_family_term("applied research scientist") == "scientist"
    assert role_family_term("applied machine learning") is None
    assert role_family_term("engineer") is None  # one word: no separate family
    assert role_family_term("") is None


def test_role_phrase_variants_pluralize_only_the_last_plain_word() -> None:
    assert role_phrase_variants("software engineer") == ("software engineer", "software engineers")
    assert role_phrase_variants("data analyst") == ("data analyst", "data analysts")
    assert role_phrase_variants("secretary") == ("secretary", "secretaries")
    assert role_phrase_variants("business") == ("business", "businesses")
    assert role_phrase_variants("c++") == ("c++",)
    assert role_phrase_variants("ai/ml") == ("ai/ml",)
    assert role_phrase_variants("") == ()


# ── locale ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "location",
    [
        "Bangalore, India",
        "Bengaluru, Karnataka, India",
        "Pune",
        "Mumbai, Maharashtra",
        "New Delhi",
        "Gurugram, Haryana",
        "Hyderabad (Hybrid)",
        "India",
        "REMOTE - INDIA",
    ],
)
def test_locale_is_india_for_india_and_its_metros(location: str) -> None:
    assert locale_for_location(location) is Locale.INDIA


@pytest.mark.parametrize(
    "location",
    ["Toronto, ON, Canada", "Remote", "Austin, Texas", "London, GB", "", None, 3, "Indianapolis"],
)
def test_locale_is_global_for_everything_else_including_missing(location: object) -> None:
    assert locale_for_location(location) is Locale.GLOBAL


@pytest.mark.parametrize(
    "location",
    [
        # the standalone tab types a place freely: cities and states the first list missed
        "Bhopal",
        "Patna, Bihar",
        "Thane",
        "Mohali",
        "Goa",
        "Kanpur",
        "Vizag",
        "Bombay",
        "Nashik, Maharashtra",
        "Gandhinagar",
        "Karnataka",
        "Maharashtra",
        "Tamil Nadu",
        "Telangana",
        "Kerala",
        "Andhra Pradesh",
        "Mangalore",
    ],
)
def test_locale_is_india_for_the_cities_and_states_a_person_types_into_the_tab(
    location: str,
) -> None:
    """The wording changes what a provider returns (on 2026-09-20 the India terms
    appeared in most hits of Bengaluru pulls and the global ones in fewer than half; an
    observation from captures kept outside the repository, not a guarantee), so a metro
    the list did not know was quietly searched with the wrong words."""
    assert locale_for_location(location) is Locale.INDIA


@pytest.mark.parametrize(
    "location",
    ["Indianapolis", "Goad Hill", "Bihari Street, Toronto", "Thanet, Kent", "Austin, Texas"],
)
def test_the_wider_india_list_still_matches_whole_words_only(location: str) -> None:
    assert locale_for_location(location) is Locale.GLOBAL


# ── company ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("Stripe", "Stripe"),
        ("Stripe, Inc.", "Stripe"),
        ("Advanced Micro Devices, Inc.", "Advanced Micro Devices"),
        ("  General   Magic ", "General Magic"),
        ("Acme Corp", "Acme"),
        ("AMD", "AMD"),
        ("Co", "Co"),  # nothing else is left of it: the name stays
        ("Group", "Group"),
        # the display form, not the registry's normalized one: `&`, dots and
        # apostrophes are what a post says (BC-1 / YH-3)
        ("AT&T Inc.", "AT&T"),
        ("Procter & Gamble", "Procter & Gamble"),
        ("Amazon.com Services LLC", "Amazon.com Services"),
        ("McDonald's Corporation", "McDonald's"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("JPMorgan Chase & Co.", "JPMorgan Chase"),
        ("Advanced Micro Devices (AMD)", "Advanced Micro Devices"),
        ("Goldman Sachs Group, Inc.", "Goldman Sachs"),
        ("", ""),
        (None, ""),
        (12, ""),
    ],
)
def test_company_query_name(company: object, expected: str) -> None:
    assert company_query_name(company) == expected


def test_company_query_name_is_shortened_at_a_word_boundary_to_what_the_builder_accepts() -> None:
    name = company_query_name("Northwind " * 30)
    assert 0 < len(name) <= 60
    assert not name.endswith(" ")
    assert all(word == "Northwind" for word in name.split())


# ── the query and its label ──────────────────────────────────────────────


def test_build_application_query_asks_for_the_role_and_its_family() -> None:
    aq = build_application_query(
        company="Stripe, Inc.",
        title="Senior Software Engineer II (Payments) - Remote",
        location="Remote",
        freshness=Freshness.WEEK,
    )
    assert aq.query.query.startswith("site:linkedin.com/posts (")
    assert '"Stripe"' in aq.query.query
    assert '("software engineer" OR "engineer")' in aq.query.query
    assert aq.role_terms == ("software engineer",)
    assert aq.query_role_terms == ("software engineer", "engineer")
    assert aq.company == "Stripe"
    assert aq.query.locale is Locale.GLOBAL
    assert aq.query.freshness is Freshness.WEEK


def test_build_application_query_uses_the_india_vocabulary_for_an_india_location() -> None:
    aq = build_application_query(
        company="Acme",
        title="AI Engineer",
        location="Bangalore, India",
        freshness=Freshness.WEEK,
    )
    assert aq.query.locale is Locale.INDIA
    assert '"immediate joiners"' in aq.query.query


@pytest.mark.parametrize(
    ("freshness", "text"),
    [
        (Freshness.DAY, "last 24 hours"),
        (Freshness.THREE_DAYS, "last 3 days"),
        (Freshness.WEEK, "last 7 days"),
    ],
)
def test_the_label_is_a_human_summary_never_the_provider_query(
    freshness: Freshness, text: str
) -> None:
    aq = build_application_query(
        company="Acme Corp",
        title="Software Engineer",
        location=None,
        freshness=freshness,
    )
    assert aq.label == f"Acme Corp -- software engineer -- {text}"
    assert "site:" not in aq.label and "OR" not in aq.label and '"' not in aq.label


def test_with_no_role_the_query_is_company_wide_and_role_match_is_unknown() -> None:
    aq = build_application_query(
        company="Acme", title="Senior", location=None, freshness=Freshness.WEEK
    )
    assert aq.role_terms == ()
    assert aq.query_role_terms == ("hiring",)
    assert aq.label == "Acme -- last 7 days"
    assert '"Acme"' in aq.query.query


@pytest.mark.parametrize("company", ["", "   ", None, 3, '"()"', "::"])
def test_an_application_with_no_usable_company_is_refused(company: object) -> None:
    with pytest.raises(NoCompanyError):
        build_application_query(
            company=company, title="Software Engineer", location=None, freshness=Freshness.WEEK
        )


def test_a_hostile_title_and_company_cannot_inject_query_operators() -> None:
    aq = build_application_query(
        company='Acme") OR site:example.com ("x',
        title='Software Engineer" OR site:evil.example -"x',
        location="Remote",
        freshness=Freshness.WEEK,
    )
    query = aq.query.query
    assert query.count("site:") == 1  # only the one this module wrote
    assert query.startswith("site:linkedin.com/posts")
    assert query.count('"') % 2 == 0
    assert query.count("(") == query.count(")")


def test_a_very_long_title_still_builds_a_query_within_the_caps() -> None:
    aq = build_application_query(
        company="Acme",
        title="Software Engineer " * 500,
        location=None,
        freshness=Freshness.WEEK,
    )
    assert len(aq.query.query) <= 350


# ── display text ─────────────────────────────────────────────────────────


def test_clean_display_text_removes_control_and_bidi_characters_and_collapses_space() -> None:
    dirty = "Acme\x00 --\u202e evil \u200b\n\t--  role\u2028x"
    assert clean_display_text(dirty, 100) == "Acme -- evil -- role x"


def test_clean_display_text_caps_length_and_ignores_non_strings() -> None:
    assert clean_display_text("a" * 500, 10) == "a" * 10
    assert clean_display_text(None, 10) == ""
    assert clean_display_text(5, 10) == ""


def test_clean_display_text_keeps_letters_of_every_script() -> None:
    assert clean_display_text("  Zoë हिन्दी 中文 ", 50) == ("Zoë हिन्दी 中文")


_HOSTILE_STRINGS = [
    '"',
    "'",
    "()",
    ":",
    "\\",
    "-",
    "- -",
    "site:linkedin.com",
    "a" * 400,
    "a b " * 100,
    "x\x00y\x01z",
    "\U0001f600" * 50,
    "हिन्दी 中文",
    "OR OR OR",
    "AND (site:x) -site:y",
    "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30",
]


@pytest.mark.parametrize("company", _HOSTILE_STRINGS)
@pytest.mark.parametrize("title", _HOSTILE_STRINGS)
@pytest.mark.parametrize("location", [None, "Pune, India", "x" * 400])
def test_no_application_can_make_the_query_builder_raise(
    company: str, title: str, location: str | None
) -> None:
    """Whatever a job description's title and company say, the only thing
    that may go wrong is "no usable company" -- never a `ValueError` out of
    `build_query`'s caps (which would surface as a 500)."""
    try:
        aq = build_application_query(
            company=company, title=title, location=location, freshness=Freshness.WEEK
        )
    except NoCompanyError:
        return
    query = aq.query.query
    assert query.count("site:") == 1
    assert len(query) <= 350
    assert len(query.split()) <= 32
