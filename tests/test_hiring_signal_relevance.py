"""Tests for Hiring Signals P3's relevance rules (`hiring_signal_relevance`):
the company filter, role match, the recency window and the ranking key.

The company filter is exercised on `firecrawl_company_posts.json` -- a
composed fixture with the structure of real Firecrawl results, in which each
noise shape the real capture contained (a skills list, a news line, an
`ex-Company` pitch) appears once. The README next to it says how it was
made and what the real-data precision/recall numbers were.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hiring_signal_fakes import FIXTURE_NOW, load_fixture

from between_jobs.api.hiring_signal_company import company_names
from between_jobs.api.hiring_signal_relevance import (
    MAX_AUTHOR_WORDS,
    OPENING_CHARS,
    age_hint,
    display_author,
    is_job_seeker_post,
    is_too_old,
    mentions_company,
    opening,
    rank_key,
    role_match,
    shown_author,
    visible_posted_at,
)
from between_jobs.api.hiring_signals import (
    HiringSignal,
    RawSearchHit,
    RelativeAge,
    parse_hit,
)
from between_jobs.api.search_providers import firecrawl_entries, firecrawl_hit_fields


def _hits() -> list[RawSearchHit]:
    body = load_fixture("firecrawl_company_posts.json")
    return [RawSearchHit(*firecrawl_hit_fields(entry)) for entry in firecrawl_entries(body)]


def _signal(hit: RawSearchHit) -> HiringSignal:
    parsed = parse_hit(hit)
    assert isinstance(parsed, HiringSignal), parsed
    return parsed


def _fixture_by_snippet_start(fragment: str) -> RawSearchHit:
    matches = [h for h in _hits() if fragment in h.snippet]
    assert matches, fragment
    return matches[0]


# ── the opening of a snippet ─────────────────────────────────────────────


def test_opening_drops_the_age_stamp_and_stops_at_the_first_elision() -> None:
    assert opening("2 days ago · We're hiring at Acme ... skills: Python") == "We're hiring at Acme"
    assert opening("Just now · Acme is hiring … more") == "Acme is hiring"
    assert opening("1 hour ago · one") == "one"


def test_opening_of_a_snippet_with_no_elision_is_its_first_characters() -> None:
    text = "word " * 200
    assert opening(text) == text[:OPENING_CHARS].strip()


def test_opening_only_strips_a_leading_stamp() -> None:
    assert opening("Hiring now. 2 days ago · not a stamp here") == (
        "Hiring now. 2 days ago · not a stamp here"
    )


# ── the company filter ───────────────────────────────────────────────────


def test_the_company_filter_on_the_composed_fixture_matches_the_documented_verdicts() -> None:
    company = company_names("Stripe")
    verdicts: dict[str, bool] = {}
    for hit in _hits():
        parsed = parse_hit(hit)
        if isinstance(parsed, HiringSignal):
            verdicts[hit.snippet] = mentions_company(company, hit=hit, signal=parsed)
    on_topic = {snippet for snippet, kept in verdicts.items() if kept}
    off_topic = {snippet for snippet, kept in verdicts.items() if not kept}
    # in the title, in the opening, by the company page, and the known limit (#7)
    assert any("Staff Software Engineer to build money-movement" in s for s in on_topic)
    assert any("Openings at Stripe include a Software Engineer Intern" in s for s in on_topic)
    assert any("My team at Stripe is hiring a Data Analyst" in s for s in on_topic)
    assert any("We're #hiring a new Software Engineer in Dublin" in s for s in on_topic)
    # a mention only AFTER the opening (skills list, news line) or a different company
    assert any("Full-stack developer, five years" in s for s in off_topic)
    assert any("The infrastructure layer is consolidating" in s for s in off_topic)
    assert any("Acme Corp is looking for a Software Engineer" in s for s in off_topic)


def test_a_mention_only_after_the_opening_does_not_count() -> None:
    hit = _fixture_by_snippet_start("Python | Django | Stripe")
    assert not mentions_company(company_names("Stripe"), hit=hit, signal=_signal(hit))


def test_the_known_limit_an_ex_company_pitch_inside_the_opening_is_kept() -> None:
    """Documented, not endorsed: `an ex-Stripe engineer` is in the first
    sentence, so the position rule keeps this recruiter pitch. It is the one
    real-data false positive shape the rule cannot separate without reading
    the sentence (see the module docstring's limits)."""
    hit = _fixture_by_snippet_start("an ex-Stripe engineer")
    assert mentions_company(company_names("Stripe"), hit=hit, signal=_signal(hit))


def test_the_company_in_the_title_counts_wherever_the_snippet_is() -> None:
    hit = RawSearchHit(
        url="https://www.linkedin.com/posts/jane-doe-1a2b3c_hiring-activity-7506381452083381426-AbCd",
        title="Acme Careers | Staff Engineer | Jane Doe",
        snippet="2 days ago · nothing relevant here ... more",
    )
    assert mentions_company(company_names("Acme"), hit=hit, signal=_signal(hit))


def test_the_site_name_suffix_never_matches_a_company_called_linkedin() -> None:
    hit = RawSearchHit(
        url="https://www.linkedin.com/posts/jane-doe-1a2b3c_topic-activity-7506381452083381426-AbCd",
        title="Jane Doe's Post - LinkedIn",
        snippet="2 days ago · an unrelated post about cooking ... more",
    )
    assert not mentions_company(company_names("LinkedIn"), hit=hit, signal=_signal(hit))
    doubled = RawSearchHit(hit.url, "Some post - LinkedIn - LinkedIn", hit.snippet)
    assert not mentions_company(company_names("LinkedIn"), hit=doubled, signal=_signal(doubled))


def test_a_company_page_author_counts_by_name_or_by_handle() -> None:
    by_handle = RawSearchHit(
        url="https://www.linkedin.com/posts/acme_hiring-activity-7506381452083381426-AbCd",
        title="#hiring | Somebody - LinkedIn",
        snippet="2 days ago · We are growing ... more",
    )
    assert mentions_company(company_names("Acme"), hit=by_handle, signal=_signal(by_handle))
    concatenated = RawSearchHit(
        url="https://www.linkedin.com/posts/advancedmicrodevices_hiring-activity-7506381452083381426-AbCd",
        title="Post",
        snippet="2 days ago · We are growing ... more",
    )
    assert mentions_company(
        company_names("Advanced Micro Devices"),
        hit=concatenated,
        signal=_signal(concatenated),
    )


def test_a_multi_word_company_must_appear_in_order_and_whole() -> None:
    def hit_for(text: str) -> RawSearchHit:
        return RawSearchHit(
            url="https://www.linkedin.com/posts/jane-doe-1a2b3c_x-activity-7506381452083381426-AbCd",
            title="Jane Doe's Post - LinkedIn",
            snippet=f"2 days ago · {text}",
        )

    company = company_names("General Magic")
    good = hit_for("General Magic is hiring engineers")
    assert mentions_company(company, hit=good, signal=_signal(good))
    for text in (
        "Magic General is hiring",
        "General manager wanted, magic skills",
        "Generalmagics",
    ):
        bad = hit_for(text)
        assert not mentions_company(company, hit=bad, signal=_signal(bad)), text


def test_a_company_name_is_matched_as_whole_words_not_substrings() -> None:
    hit = RawSearchHit(
        url="https://www.linkedin.com/posts/jane-doe-1a2b3c_x-activity-7506381452083381426-AbCd",
        title="Jane Doe's Post - LinkedIn",
        snippet="2 days ago · Stripes and stripeless designs are hiring interest",
    )
    assert not mentions_company(company_names("Stripe"), hit=hit, signal=_signal(hit))


def test_an_empty_company_matches_nothing() -> None:
    hit = _hits()[0]
    assert not mentions_company(company_names(""), hit=hit, signal=_signal(hit))
    assert not mentions_company(None, hit=hit, signal=_signal(hit))


def test_matching_is_case_and_accent_insensitive_in_the_way_casefold_is() -> None:
    hit = RawSearchHit(
        url="https://www.linkedin.com/posts/jane-doe-1a2b3c_x-activity-7506381452083381426-AbCd",
        title="Jane Doe's Post - LinkedIn",
        snippet="2 days ago · STRIPE IS HIRING",
    )
    assert mentions_company(company_names("Stripe"), hit=hit, signal=_signal(hit))


# ── role match ───────────────────────────────────────────────────────────


def _text_hit(text: str) -> RawSearchHit:
    return RawSearchHit(url="https://example.com/x", title="", snippet=text)


def test_role_match_is_unknown_without_role_terms() -> None:
    assert role_match([_text_hit("anything")], ()) is None


def test_role_match_true_false_and_plural() -> None:
    terms = ("software engineer",)
    assert role_match([_text_hit("We need a Software Engineer")], terms) is True
    assert role_match([_text_hit("We need software engineers!")], terms) is True
    assert role_match([_text_hit("We need a data analyst")], terms) is False
    assert role_match([_text_hit("software and engineering leads")], terms) is False


def test_role_match_is_true_when_any_copy_of_the_post_matches() -> None:
    terms = ("software engineer",)
    copies = [_text_hit("cut off before the role ..."), _text_hit("hiring a software engineer")]
    assert role_match(copies, terms) is True


def test_role_match_handles_symbol_roles() -> None:
    assert role_match([_text_hit("hiring a .NET developer")], (".net developer",)) is True
    assert role_match([_text_hit("hiring a C++ engineer")], ("c++ engineer",)) is True
    assert role_match([_text_hit("hiring a C engineer")], ("c++ engineer",)) is False


# ── recency ──────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 19, 18, 0, 0, tzinfo=UTC)


def test_the_window_boundary_is_exact() -> None:
    week = timedelta(days=7)
    assert not is_too_old(NOW - week, now=NOW, window_days=7)
    assert is_too_old(NOW - week - timedelta(seconds=1), now=NOW, window_days=7)
    assert not is_too_old(NOW - timedelta(hours=23, minutes=59), now=NOW, window_days=1)
    assert is_too_old(NOW - timedelta(hours=24, minutes=1), now=NOW, window_days=1)
    assert is_too_old(NOW - timedelta(days=3, minutes=1), now=NOW, window_days=3)


def test_an_unknown_post_time_is_never_too_old() -> None:
    assert not is_too_old(None, now=NOW, window_days=1)


def test_a_post_time_in_the_future_beyond_clock_skew_is_unknown() -> None:
    assert visible_posted_at(NOW + timedelta(minutes=5), now=NOW) == NOW + timedelta(minutes=5)
    assert visible_posted_at(NOW + timedelta(days=2), now=NOW) is None
    assert visible_posted_at(None, now=NOW) is None
    assert visible_posted_at(NOW - timedelta(days=400), now=NOW) == NOW - timedelta(days=400)


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (RelativeAge(3, "day"), "3 days ago"),
        (RelativeAge(1, "day"), "1 day ago"),
        (RelativeAge(5, "hour"), "5 hours ago"),
        (RelativeAge(2, "week"), "2 weeks ago"),
        (RelativeAge(0, "minute"), "just now"),
        (None, None),
    ],
)
def test_age_hint(age: RelativeAge | None, text: str | None) -> None:
    assert age_hint(age) == text


# ── ranking ──────────────────────────────────────────────────────────────


def test_rank_key_puts_exact_role_first_then_newest_then_input_order() -> None:
    older = NOW - timedelta(days=3)
    newer = NOW - timedelta(days=1)
    items = [
        ("mismatch-new", rank_key(role_match_value=False, posted_at=newer, position=0)),
        ("unknown-role-new", rank_key(role_match_value=None, posted_at=newer, position=1)),
        ("match-old", rank_key(role_match_value=True, posted_at=older, position=2)),
        ("match-new", rank_key(role_match_value=True, posted_at=newer, position=3)),
        ("match-unknown-time", rank_key(role_match_value=True, posted_at=None, position=4)),
        ("tie-a", rank_key(role_match_value=False, posted_at=older, position=5)),
        ("tie-b", rank_key(role_match_value=False, posted_at=older, position=6)),
    ]
    assert [name for name, key in sorted(items, key=lambda i: i[1])] == [
        "match-new",
        "match-old",
        "match-unknown-time",
        "mismatch-new",
        "unknown-role-new",
        "tie-a",
        "tie-b",
    ]


def test_fixture_now_is_the_documented_reference_instant() -> None:
    assert FIXTURE_NOW == NOW


# ── the company's page vs a person or firm whose name contains it (YH-5) ──


def _hit(handle: str, title: str, snippet: str) -> RawSearchHit:
    return RawSearchHit(
        url=f"https://www.linkedin.com/posts/{handle}_x-activity-7506381452083381426-AbCd",
        title=title,
        snippet=f"2 days ago · {snippet}",
    )


def test_a_persons_surname_is_not_a_mention_of_the_company() -> None:
    hit = _hit("jordan-block-4b1c9e02", "Jordan Block's Post - LinkedIn", "We are hiring ... more")
    assert not mentions_company(company_names("Block"), hit=hit, signal=_signal(hit))


def test_a_handle_that_merely_contains_the_company_is_not_the_company() -> None:
    hit = _hit("jordan-block-4b1c9e02", "Untitled", "We are hiring engineers ... more")
    assert not mentions_company(company_names("Block"), hit=hit, signal=_signal(hit))


@pytest.mark.parametrize("handle", ["block", "block-inc", "block-careers"])
def test_the_companys_own_handle_is_the_company(handle: str) -> None:
    hit = _hit(handle, "Untitled", "We are hiring engineers ... more")
    assert mentions_company(company_names("Block"), hit=hit, signal=_signal(hit))


@pytest.mark.parametrize("author", ["Block", "Block Careers", "Block, Inc."])
def test_an_author_that_is_the_company_page_counts_even_when_nothing_else_does(
    author: str,
) -> None:
    """The handle is not the company's and the snippet never names it: the
    AUTHOR NAME is the only rule that can match here."""
    hit = _hit("some-handle-1a2b3c", f"#hiring | {author} - LinkedIn", "We are hiring ... more")
    signal = _signal(hit)
    assert signal.author_name == author
    assert mentions_company(company_names("Block"), hit=hit, signal=signal)


def test_a_company_page_named_in_its_own_possessive_title_counts() -> None:
    """`<Page>'s Post - LinkedIn` is LinkedIn's title for a page's posts. Nothing
    else names the company here (the handle is not the company's and the text
    never says it): the page's name in the title is the only way it matches --
    through the possessive, which used to fold into `acmes`."""
    hit = _hit("acme-hq-9f2c1a", "Acme's Post - LinkedIn", "We are growing fast ... more")
    signal = _signal(hit)
    assert signal.author_name == "Acme"
    assert mentions_company(company_names("Acme"), hit=hit, signal=signal)
    assert not mentions_company(company_names("Stripe"), hit=hit, signal=signal)


def test_a_possessive_mention_in_the_opening_is_a_mention() -> None:
    hit = _hit(
        "riley-sample-0d7b41e9",
        "Riley Sample's Post - LinkedIn",
        "Stripe's payments team is hiring ... more",
    )
    assert mentions_company(company_names("Stripe"), hit=hit, signal=_signal(hit))


def test_the_authors_name_is_taken_out_of_the_title_before_the_title_is_read() -> None:
    """`Square One Search Partners` is the AUTHOR here (its handle says so), not
    something the title says about the company `Square`."""
    hit = _hit(
        "square-one-search-partners-99",
        "Square One Search Partners - LinkedIn",
        "We are hiring for a client ... more",
    )
    signal = _signal(hit)
    assert signal.author_name == "Square One Search Partners"
    assert not mentions_company(company_names("Square"), hit=hit, signal=signal)


def test_a_company_named_in_the_title_beside_a_person_still_counts() -> None:
    hit = _hit(
        "jane-doe-1a2b3c",
        "Acme Careers | Staff Engineer | Jane Doe",
        "nothing relevant here ... more",
    )
    assert mentions_company(company_names("Acme"), hit=hit, signal=_signal(hit))


def test_a_title_whose_author_slot_is_a_sentence_is_read_as_title_text() -> None:
    """The parser returns the last pipe segment as the author after a hashtag
    block, even when it is post text; a string that is not name-shaped is not
    taken out of the title, so a company named in it still counts."""
    hit = _hit(
        "someone-1a2b3c",
        "#hiring #python | Acme needs a senior Python engineer to rebuild billing, remote OK",
        "nothing relevant here ... more",
    )
    assert mentions_company(company_names("Acme"), hit=hit, signal=_signal(hit))


def test_a_title_with_a_bidi_override_inside_a_name_still_cannot_smuggle_the_company_in() -> None:
    title = "Jordan " + chr(0x202E) + "Block" + chr(0x200B) + "'s Post - LinkedIn"
    hit = _hit("jordan-block-4b1c9e02", title, "We are hiring ... more")
    assert not mentions_company(company_names("Block"), hit=hit, signal=_signal(hit))


def test_the_known_limit_an_ordinary_word_company_still_matches_the_word_in_the_opening() -> None:
    """Documented, not endorsed (see the module docstring): telling `Target:
    20 hires by June` from a post about the company needs reading the sentence."""
    hit = _hit(
        "riley-sample-0d7b41e9", "Riley Sample's Post - LinkedIn", "Target: 20 hires ... more"
    )
    assert mentions_company(company_names("Target"), hit=hit, signal=_signal(hit))


# ── display_author (LP-1) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Jane Doe",
        "Stripe",
        "Amazon Web Services (AWS)",
        "Ernst & Young",
        "J.P. Morgan",
        "O'Brien Consulting",
        "Ludwig van Beethoven",
        "Anne-Marie de la Cruz",
        "eBay",
        "楽天",
        "Bank of America",
        "The Bill & Melinda Gates Foundation",
        "Stripe, Inc.",
    ],
)
def test_display_author_keeps_what_reads_as_a_name(name: str) -> None:
    assert display_author(name) == name


@pytest.mark.parametrize(
    "not_a_name",
    [
        "Stripe needs a senior Python engineer to rebuild billing, remote OK",
        "We are hiring a Software Engineer in Dublin",  # a sentence, no punctuation at all
        "hiring now",
        "jane doe",  # all lowercase: unknown rather than guessed
        "Hiring 10 engineers",
        "Open roles: Python | Go",
        "Apply at jobs@example.com",
        "#hiring",
        "One Two Three Four Five Six Seven",  # more than MAX_AUTHOR_WORDS words
        "",
        None,
        42,
    ],
)
def test_display_author_drops_what_is_not_a_name(not_a_name: object) -> None:
    assert display_author(not_a_name) is None


def test_display_author_word_limit_is_the_documented_one() -> None:
    assert MAX_AUTHOR_WORDS == 6
    assert display_author("One Two Three Four Five Six") == "One Two Three Four Five Six"


def test_display_author_strips_control_format_and_bidi_characters() -> None:
    dirty = "Jane " + chr(0x202E) + "Doe" + chr(0x200B) + chr(0x0007)
    assert display_author(dirty) == "Jane Doe"


# ── job seekers: the opening decides (YH-4) ──────────────────────────────


def test_a_marker_in_the_opening_is_a_job_seekers_post() -> None:
    hit = RawSearchHit("u", "T", "2 days ago · I'm looking for my next role in ML ... more")
    assert is_job_seeker_post([hit])


def test_a_marker_only_after_the_first_elision_is_someone_elses_comment() -> None:
    hit = RawSearchHit(
        "u", "T", "2 days ago · Acme is hiring a Data Analyst ... Priya S. | #OpenToWork | Python"
    )
    assert not is_job_seeker_post([hit])


def test_any_copy_whose_opening_says_it_makes_the_post_a_job_seekers() -> None:
    hiring = RawSearchHit("u", "T", "2 days ago · Acme is hiring ... more")
    seeker = RawSearchHit("u", "T", "2 days ago · #OpenToWork | Python developer ... more")
    assert is_job_seeker_post([hiring, seeker])


# ── role match: unknown stays unknown (YH-9) ─────────────────────────────


def test_role_match_is_unknown_when_no_term_is_long_enough_to_test() -> None:
    """A single-character role (`R`, `C`) is dropped by the shared predicate,
    which would then call EVERY post a match."""
    assert role_match([_text_hit("barista wanted")], ("r",)) is None
    assert role_match([_text_hit("barista wanted")], ("c",)) is None


def test_role_match_is_unknown_not_false_for_a_role_in_a_script_without_word_boundaries() -> None:
    post = _text_hit("メルカリではソフトウェアエンジニアを募集しています")
    assert role_match([post], ("ソフトウェアエンジニア",)) is None
    assert role_match([_text_hit("hiring a baker")], ("ソフトウェアエンジニア",)) is None


def test_role_match_true_still_wins_over_unknown() -> None:
    post = _text_hit("募集: ソフトウェアエンジニア, Tokyo")
    assert role_match([post], ("ソフトウェアエンジニア",)) is True


# ── PRIV-1: an author is only sent when it agrees with the url's handle ──


@pytest.mark.parametrize(
    ("name", "handle", "expected"),
    [
        # a person: LinkedIn derives the handle from the name, plus a collision id
        ("Priya Testwell", "priya-testwell-2f7a91c3", "Priya Testwell"),
        ("Priya Testwell", "priyatestwell", "Priya Testwell"),  # run together
        ("Priya Marie Testwell", "priya-testwell-2f7a91c3", None),  # a middle name is not in it
        ("Jose Garcia", "jos\u00e9-garc\u00eda-1a2b3c4d", "Jose Garcia"),  # accents fold
        # a company page: its own name, with or without its descriptors and legal form
        ("Stripe", "stripe", "Stripe"),
        ("Stripe, Inc.", "stripe", "Stripe, Inc."),
        ("Northwind Labs", "northwind", "Northwind Labs"),
        ("Scale AI", "scaleai", "Scale AI"),
        ("Gnani.ai", "gnani-ai", "Gnani.ai"),
        ("gnani.ai", "gnani-ai", None),  # an all-lowercase name is unknown (display_author)
        # a page name carries a tagline after ` - `: post-title text, cut off
        ("Northwind - Cloud Security Platform", "northwind", "Northwind"),
        ("Mitigata\u2122 - Full-Stack Cyber Resilience", "mitigata", "Mitigata"),
        # a stretch of a headline has the SHAPE of a name and is not one
        ("Hiring Backend Software Engineers Bengaluru", "priya-testwell-2f7a91c3", None),
        ("Software Engineer Openings At Northwind", "arjun-placeholder", None),
        ("Urgent Hiring Software Engineers Meera Fixture", "meera-fixture-91d3c0aa", None),
        ("ZQXTITLE Jane Doe", "jane-doe-1a2b3c4d", None),
        # one word of a name agreeing with the handle is not enough
        ("Meera Fixture Openings", "meera-fixture-91d3c0aa", None),
        # a url that names no author has nothing to agree with
        ("Northwind Labs", None, None),
        ("Northwind Labs", "", None),
        # nothing significant in it
        ("The", "the-team", None),
        # not a name at all, whatever the handle says
        ("we are hiring", "we-are-hiring", None),
        (None, "stripe", None),
    ],
)
def test_an_author_is_shown_only_when_it_agrees_with_the_handle(
    name: str | None, handle: str | None, expected: str | None
) -> None:
    assert shown_author(name, handle) == expected


def test_display_author_alone_would_have_shown_every_headline_above() -> None:
    """The shape test is not what the fix replaced but what it now sits behind: the
    same strings pass it, which is the whole reason the handle check exists."""
    for headline in (
        "Hiring Backend Software Engineers Bengaluru",
        "Software Engineer Openings At Northwind",
        "Urgent Hiring Software Engineers Meera Fixture",
    ):
        assert display_author(headline) == headline


def test_a_handles_collision_id_is_not_a_word_of_the_handle() -> None:
    """`2f7a91c3` is LinkedIn's suffix, not a word of the person's name, so a name
    word that is only a run of hex characters does not "agree" with it."""
    assert shown_author("Priya 2f7a91c3", "priya-testwell-2f7a91c3") is None
