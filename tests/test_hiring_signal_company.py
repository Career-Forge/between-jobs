"""Tests for `hiring_signal_company`: what a company is CALLED in a query and in
a post. The names here are public companies; every text is written for the test.

The service-level tests in `test_hiring_signal_service.py` run these same names
through a whole search; this file pins the module's own rules, one at a time.

Strings that differ only in an invisible way (a decomposed accent, a
typographic apostrophe) are built from code points, never typed, so the source
says what they are.
"""

from __future__ import annotations

import pytest

from between_jobs.api.hiring_signal_company import (
    company_names,
    fold,
    has_unspaced_script,
    identity_keys_of,
    tokens_of,
)

COMBINING_ACUTE = chr(0x0301)
NESTLE_NFC = "Nestl" + chr(0x00E9)
NESTLE_NFD = "Nestle" + COMBINING_ACUTE
TYPOGRAPHIC_APOSTROPHE = chr(0x2019)
LOREAL = "L'Or" + chr(0x00E9) + "al S.A."

# ── fold: the one comparison form ────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("AT&T", "at and t"),
        ("Procter & Gamble", "procter  and  gamble"),
        # a possessive 's is dropped, so "Stripe's team" is about Stripe ...
        ("McDonald's", "mcdonald"),
        (f"McDonald{TYPOGRAPHIC_APOSTROPHE}s", "mcdonald"),
        ("Stripe's", "stripe"),
        (f"Stripe{TYPOGRAPHIC_APOSTROPHE}s", "stripe"),
        # ... any other apostrophe inside a word is removed, not split on
        ("L'Oreal", "loreal"),
        ("O'Sullivan", "osullivan"),  # `'s` followed by more letters is not a possessive
        ("S.A.", "sa"),
        ("L.L.C.", "llc"),
        (NESTLE_NFC, "nestle"),
        (NESTLE_NFD, "nestle"),
        ("STRASSE", "strasse"),
        ("Straße", "strasse"),  # casefold, not lower
        ("ＡＴ＆Ｔ", "at and t"),  # noqa: RUF001 -- full-width letters (NFKC)
        ("Amazon.com", "amazon.com"),  # a dot inside a word is not an abbreviation
    ],
)
def test_fold(text: str, folded: str) -> None:
    assert fold(text) == folded


def test_fold_leaves_indic_vowel_signs_alone() -> None:
    """Only the Latin/Greek/Cyrillic combining block is stripped; a Devanagari
    vowel sign is part of the letter, and stripping it would change the word."""
    word = "टाटा"
    assert fold(word) == word


def test_tokens_split_a_script_written_without_spaces_from_its_neighbors() -> None:
    assert tokens_of("TikTok在招聘") == ["tiktok", "在招聘"]
    assert tokens_of("Two Sigma, LP") == ["two", "sigma", "lp"]
    assert has_unspaced_script("楽天") and not has_unspaced_script("Rakuten")


# ── the query phrase: the display name, not the registry's normal form ───


@pytest.mark.parametrize(
    ("company", "phrase"),
    [
        ("AT&T Inc.", "AT&T"),
        ("Procter & Gamble", "Procter & Gamble"),
        ("Amazon.com Services LLC", "Amazon.com Services"),
        ("McDonald's Corporation", "McDonald's"),
        ("Meta Platforms, Inc.", "Meta Platforms"),
        ("JPMorgan Chase & Co.", "JPMorgan Chase"),
        ("Johnson & Johnson", "Johnson & Johnson"),
        ("Advanced Micro Devices (AMD)", "Advanced Micro Devices"),
        ("Goldman Sachs Group, Inc.", "Goldman Sachs"),
        ("Stripe, Inc.", "Stripe"),
        ("Co", "Co"),  # never emptied
    ],
)
def test_query_phrase(company: str, phrase: str) -> None:
    names = company_names(company)
    assert names is not None
    assert names.query_name == phrase


@pytest.mark.parametrize("company", ["", "   ", None, 3, '"()"', "::", "—"])
def test_no_company_when_there_is_nothing_to_search_for(company: object) -> None:
    assert company_names(company) is None


# ── whole-word matching over every spelling a post may use ───────────────

POSTS = [
    ("AT&T Inc.", "AT&T is hiring a network engineer"),
    ("AT&T Inc.", "Come build with AT & T"),
    ("Procter & Gamble", "We're hiring at Procter & Gamble"),
    ("Procter & Gamble", "Procter and Gamble is hiring"),
    ("Amazon.com Services LLC", "Amazon is hiring"),
    ("Amazon.com Services LLC", "Join Amazon.com today"),
    ("McDonald's Corporation", "McDonald's is hiring"),
    ("McDonald's Corporation", "McDonalds is hiring"),
    ("Meta Platforms, Inc.", "Meta is hiring"),
    ("Meta Platforms, Inc.", "Meta Platforms is hiring"),
    ("Johnson & Johnson", "Johnson & Johnson is hiring"),
    ("JPMorgan Chase & Co.", "JPMorgan Chase is hiring"),
    ("Advanced Micro Devices (AMD)", "AMD is hiring"),
    ("Advanced Micro Devices (AMD)", "Advanced Micro Devices is hiring"),
    ("Scale AI", "ScaleAI is hiring"),
    ("Scale AI", "Scale AI is hiring"),
    ("Dr. Reddy's Laboratories Ltd", "Dr. Reddy's is hiring"),
    (LOREAL, "L'Oreal is hiring"),
    ("S&P Global", "S&P Global is hiring"),
    ("Two Sigma Investments, LP", "Two Sigma is hiring"),
    ("The Home Depot", "Home Depot is hiring"),
    (NESTLE_NFC, f"{NESTLE_NFD} is hiring"),  # decomposed in the post
    (NESTLE_NFD, f"{NESTLE_NFC} is hiring"),  # decomposed in the name
    ("楽天", "楽天で採用中です"),  # no spaces at all
    ("字节跳动", "字节跳动招聘算法工程师"),
    ("TikTok", "TikTok在招聘"),  # a Latin name inside CJK text
    ("Stripe", "STRIPE IS HIRING"),
    ("Stripe", "Stripe's engineers are hiring"),  # possessive
    ("Stripe", f"Stripe{TYPOGRAPHIC_APOSTROPHE}s engineers are hiring"),
    ("Acme, Inc.", "Acme's Post"),  # LinkedIn's own title shape for a page's posts
    ("McDonald's Corporation", "McDonald is hiring"),  # the possessive form of a name too
    ("字节跳动", "字节 跳动 招聘算法工程师"),  # a page that spaces the name out
]


@pytest.mark.parametrize(("company", "post"), POSTS)
def test_a_name_is_found_in_the_ways_a_post_spells_it(company: str, post: str) -> None:
    names = company_names(company)
    assert names is not None
    assert names.found_in(post), (company, post)


@pytest.mark.parametrize(
    ("company", "post"),
    [
        ("Stripe", "Stripes and stripeless designs are hiring interest"),
        ("Stripe", "Stripeless is hiring"),
        ("General Magic", "Magic General is hiring"),
        ("General Magic", "General manager wanted, magic skills"),
        ("Block", "Blockchain engineers wanted"),
        ("AT&T", "At the moment we are hiring"),
        ("Amazon.com Services LLC", "Amazonia is hiring"),
    ],
)
def test_a_name_is_not_found_inside_another_word(company: str, post: str) -> None:
    names = company_names(company)
    assert names is not None
    assert not names.found_in(post), (company, post)


# ── identity: is this page (or registry company) the company? ────────────


@pytest.mark.parametrize(
    "page",
    [
        "Acme",
        "acme",
        "Acme Careers",
        "Acme Jobs",
        "Acme Talent Acquisition",
        "Acme, Inc.",
        "acme-inc",
    ],
)
def test_the_companys_own_page(page: str) -> None:
    names = company_names("Acme")
    assert names is not None
    assert names.is_page_name(page), page


@pytest.mark.parametrize(
    "page",
    ["Jordan Acme", "Acme Valley Staffing", "Riley Acme Recruiting Partners", "Acmeworks", ""],
)
def test_a_person_or_another_firm_whose_name_contains_the_word_is_not_the_page(page: str) -> None:
    names = company_names("Acme")
    assert names is not None
    assert not names.is_page_name(page), page


def test_a_run_together_handle_is_the_page() -> None:
    names = company_names("Advanced Micro Devices")
    assert names is not None
    assert names.is_page_name("advancedmicrodevices")
    assert names.is_page_name("advanced-micro-devices")


def test_registry_identity_ties_a_page_to_a_company_stored_under_a_slug_or_legal_name() -> None:
    assert identity_keys_of("Scale AI") & identity_keys_of("scaleai")
    assert identity_keys_of("Meta") & identity_keys_of("Meta Platforms, Inc.")
    assert identity_keys_of("Stripe") & identity_keys_of("Stripe, Inc.")
    assert identity_keys_of("Aquascape Engineers Pvt Ltd - Aerospace") & identity_keys_of(
        "Aquascape Engineers Pvt Ltd"
    )
    # different companies must not be tied
    assert not identity_keys_of("Scale AI") & identity_keys_of("Scale Computing")
    assert not identity_keys_of("Block") & identity_keys_of("Blockchain Capital")
    assert identity_keys_of("") == frozenset()


def test_registry_words_are_the_shortest_spelling_without_and() -> None:
    names = company_names("Procter & Gamble")
    assert names is not None
    assert names.registry_words == ("procter", "gamble")
    meta = company_names("Meta Platforms, Inc.")
    assert meta is not None
    assert meta.registry_words == ("meta",)
