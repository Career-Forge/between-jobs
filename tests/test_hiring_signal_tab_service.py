"""End-to-end tests for Hiring Signals P4's standalone tab search
(`hiring_signal_service.search_tab`) against the same in-memory fakes P3's service
tests use: a Supabase client (`hiring_signal_fakes`) and a mocked httpx transport
that answers as the four providers.

The Firecrawl body is `firecrawl_role_posts.json`, the composed fixture (structure
of real results, text written by hand -- see its README) for `software engineer` in
Bengaluru, whose 20 hits were built so that every bucket has exactly the members
listed in `test_the_composed_fixture_lands_in_the_documented_buckets`. The rest
build their own provider answers out of `hiring_signal_fakes.post_entry`.

What is pinned here: the counts identity under every branch (a parametrized set
and a seeded fuzz), the hard role filter (multi-word, symbols, one word, unicode),
the ranking, the hidden buckets, the bounded ONE-batch registry read, the standalone
saved flag, the shared cache, the provider layer as P3 left it, and that nothing but
structured fields leaves the server.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Sequence
from datetime import timedelta
from typing import Any

import httpx
import pytest
from hiring_signal_fakes import (
    APP,
    FIXTURE_NOW,
    HOSTS,
    OTHER_USER,
    USER,
    World,
    activity_id_at,
    as_client,
    firecrawl_body,
    load_fixture,
    post_entry,
)
from postgrest.exceptions import APIError

from between_jobs.api.errors import ApiError
from between_jobs.api.hiring_signal_cache import cache_key
from between_jobs.api.hiring_signal_registry import (
    MAX_BATCH_REGISTRY_COMPANIES,
    MAX_COMPANIES,
    MAX_POSTINGS,
)
from between_jobs.api.hiring_signal_search import (
    MAX_PROVIDER_CALLS_PER_REQUEST,
    provider_freshness_request,
)
from between_jobs.api.hiring_signal_service import _tab_registry_states
from between_jobs.api.hiring_signal_tab import build_tab_query
from between_jobs.api.hiring_signals import (
    AGGREGATOR_MIN_POSTS,
    Freshness,
    Locale,
    RawSearchHit,
    analyze_pull,
)

ROLE_FIXTURE = load_fixture("firecrawl_role_posts.json")
HIDDEN_BUCKETS = (
    "rejected",
    "duplicates",
    "role_mismatch_hidden",
    "echoes_hidden",
    "job_seekers_hidden",
    "too_old_hidden",
)

TAB_SIGNAL_FIELDS = {
    "activity_id",
    "post_url",
    "embed_url",
    "author_name",
    "posted_at",
    "age_hint",
    "species",
    "comment_count",
    "registry_match",
    "aggregator",
    "saved",
}
TAB_COUNT_FIELDS = {"raw_hits", "shown", *HIDDEN_BUCKETS}
TAB_RESPONSE_FIELDS = {
    "provider",
    "cached",
    "freshness",
    "locale",
    "query_label",
    "signals",
    "counts",
}


def counts_add_up(response: dict[str, Any]) -> None:
    """The contract's identity: every raw hit is shown or in exactly one bucket."""
    c = response["counts"]
    assert set(c) == TAB_COUNT_FIELDS, c
    assert all(isinstance(v, int) and v >= 0 for v in c.values()), c
    assert c["raw_hits"] == c["shown"] + sum(c[b] for b in HIDDEN_BUCKETS), c
    assert c["shown"] == len(response["signals"])


# ── builders ─────────────────────────────────────────────────────────────

_NAMES = (
    "Riley Sample",
    "Jordan Placeholder",
    "Avery Testwell",
    "Casey Fixture",
    "Morgan Example",
    "Quinn Sampleton",
    "Taylor Standin",
    "Rowan Mockford",
)


def person_post(
    n: int,
    text: str,
    *,
    age_hours: float = 30,
    name: str | None = None,
    handle: str | None = None,
    topic: str = "hiring",
) -> dict[str, Any]:
    """One person's post. `n` makes the author, the handle and the activity id
    distinct (so posts never collide); `text` is the post text as an index joins it
    (the provider's age stamp is added). Every name is synthetic."""
    who = name or _NAMES[n % len(_NAMES)]
    slug_handle = handle or f"{who.lower().replace(' ', '-')}-{n:08x}"
    return post_entry(
        f"{slug_handle}_{topic}",
        f"{who}'s Post - LinkedIn",
        f"1 day ago · {text}",
        age_hours=age_hours,
        sequence=1000 + n,
    )


def echo_post(
    n: int, page: str, role: str, place: str = "Bengaluru, Karnataka", *, age_hours: float = 45
) -> dict[str, Any]:
    """LinkedIn's auto-generated job-share post, authored by a company page."""
    return post_entry(
        f"{page.lower().replace(' ', '-')}_hiring",
        f"#hiring | {page} - LinkedIn",
        f"2 days ago · We're #hiring a new {role} in {place}. "
        "Apply today or share this post with your network.",
        age_hours=age_hours,
        sequence=2000 + n,
    )


def nameless_post(n: int, role: str, *, age_hours: float = 28) -> dict[str, Any]:
    """A post whose url names no author (`/posts/activity-<id>`)."""
    entry = post_entry(
        "x",
        f"Hiring: {role} - LinkedIn",
        f"1 day ago \u00b7 Openings for a {role} ... more",
        age_hours=age_hours,
        sequence=3000 + n,
    )
    entry["url"] = entry["url"].replace("/posts/x-activity-", "/posts/activity-")
    return entry


def registry_for(**pages: Sequence[tuple[str, str | None]]) -> dict[str, Any]:
    """World kwargs for a registry that tracks these companies, each with its
    `(title, location)` postings. Keyword names use `_` for a space."""
    companies: list[dict[str, Any]] = []
    postings: list[dict[str, Any]] = []
    for c, (page, listings) in enumerate(pages.items()):
        company_id = f"co-{c}"
        companies.append({"id": company_id, "name": page.replace("_", " ")})
        for p, (title, location) in enumerate(listings):
            postings.append(
                {
                    "id": f"post-{c}-{p}",
                    "company_id": company_id,
                    "title": title,
                    "location": location,
                    "status": "active",
                    "first_seen": "2026-09-10T00:00:00+00:00",
                }
            )
    return {"registry_companies": companies, "registry_postings": postings}


def world_with(*entries: dict[str, Any], **kwargs: Any) -> World:
    return World(responses={"firecrawl": firecrawl_body(*entries)}, **kwargs)


def ids(response: dict[str, Any]) -> list[str]:
    return [s["activity_id"] for s in response["signals"]]


# ── the composed fixture end to end ──────────────────────────────────────


async def test_the_composed_fixture_lands_in_the_documented_buckets() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    assert set(response) == TAB_RESPONSE_FIELDS
    assert response["provider"] == "firecrawl"
    assert response["cached"] is False
    assert response["freshness"] == "3days"
    assert response["locale"] == "india"
    assert response["query_label"] == "software engineer -- Bengaluru -- last 3 days"
    assert response["counts"] == {
        "raw_hits": 20,
        "rejected": 1,  # a /jobs/view/ page
        "duplicates": 1,  # the same post under a second slug
        "role_mismatch_hidden": 1,  # a manual tester
        "echoes_hidden": 0,  # no registry
        "job_seekers_hidden": 1,
        "too_old_hidden": 1,  # four days old
        "shown": 15,
    }
    counts_add_up(response)


async def test_the_week_window_shows_the_four_day_old_post_too() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.WEEK)

    assert response["freshness"] == "week"
    assert response["counts"]["too_old_hidden"] == 0
    assert response["counts"]["shown"] == 16
    counts_add_up(response)


async def test_a_registry_that_tracks_one_echoed_listing_hides_exactly_that_echo() -> None:
    world = World(
        responses={"firecrawl": ROLE_FIXTURE},
        **registry_for(
            Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")],
            Contoso_Robotics=[("Hardware Technician", "Bengaluru, Karnataka")],
        ),
    )
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    assert response["counts"]["echoes_hidden"] == 1
    assert response["counts"]["shown"] == 14
    echoes = {
        s["author_name"]: s["registry_match"]
        for s in response["signals"]
        if s["species"] == "ats_echo"
    }
    # the page the registry has, with another listing: definite. The page it does not
    # have at all: unknown, never "unmatched".
    assert echoes == {"Contoso Robotics": "unmatched", "Fabrikam Systems": None}
    counts_add_up(response)


async def test_the_response_and_every_signal_carry_exactly_the_contract_fields() -> None:
    response = await World(responses={"firecrawl": ROLE_FIXTURE}).tab_search(
        "software engineer", "Bengaluru", Freshness.THREE_DAYS
    )
    assert response["signals"]
    for signal in response["signals"]:
        assert set(signal) == TAB_SIGNAL_FIELDS, signal
        assert signal["embed_url"] == (
            f"https://www.linkedin.com/embed/feed/update/urn:li:activity:{signal['activity_id']}"
        )
        assert signal["post_url"].startswith("https://www.linkedin.com/posts/")
        assert "?" not in signal["post_url"] and "#" not in signal["post_url"]
        assert signal["activity_id"].isascii() and signal["activity_id"].isdigit()
        assert not signal["activity_id"].startswith("0")
        assert signal["species"] in {"unclassified", "ats_echo", "referral_offer", "hiring_drive"}
        assert signal["registry_match"] in {None, "unmatched", "possible"}
        assert signal["aggregator"] in {True, False, None}
        assert signal["saved"] is False
        assert signal["posted_at"] is None or signal["posted_at"].endswith("+00:00")


async def test_a_signal_says_what_the_index_and_the_id_say() -> None:
    response = await World(responses={"firecrawl": ROLE_FIXTURE}).tab_search(
        "software engineer", "Bengaluru", Freshness.THREE_DAYS
    )
    by_author = {s["author_name"]: s for s in response["signals"] if s["author_name"]}

    priya = by_author["Priya Testwell"]
    assert priya["posted_at"] == (FIXTURE_NOW - timedelta(hours=38)).isoformat()
    assert priya["age_hint"] == "1 day ago"
    assert priya["species"] == "unclassified"
    assert priya["aggregator"] is False
    assert by_author["Tanvi Testwell"]["species"] == "referral_offer"
    walk_in = next(s for s in response["signals"] if s["species"] == "hiring_drive")
    assert walk_in["author_name"] is None and walk_in["aggregator"] is False


async def test_the_job_seeker_species_is_never_exposed() -> None:
    """A `job_seeker` post is hidden or plain: the contract has no such species."""
    body = firecrawl_body(
        person_post(1, "Open to work: three years of Kotlin ... #OpenToWork", age_hours=20),
        person_post(2, "We are hiring a software engineer ... I'm looking for the right fit"),
    )
    response = await World(responses={"firecrawl": body}).tab_search("software engineer")
    assert {s["species"] for s in response["signals"]} <= {
        "unclassified",
        "ats_echo",
        "referral_offer",
        "hiring_drive",
    }


# ── the counts identity ──────────────────────────────────────────────────


@pytest.mark.parametrize("freshness", list(Freshness))
@pytest.mark.parametrize("registry", [False, True])
@pytest.mark.parametrize("location", [None, "Bengaluru", "Bengaluru, Karnataka, India", "Austin"])
async def test_every_hit_of_the_composed_fixture_lands_in_exactly_one_bucket(
    freshness: Freshness, registry: bool, location: str | None
) -> None:
    kwargs = (
        registry_for(Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")])
        if registry
        else {}
    )
    world = World(responses={"firecrawl": ROLE_FIXTURE}, **kwargs)
    counts_add_up(await world.tab_search("software engineer", location, freshness))


ROLES = ("software engineer", "data engineer", "c++ developer", "devops", "senior data engineer")
PAGES = ("Northwind Labs", "Contoso Robotics", "Fabrikam Systems", "Litware Cloud", "Adatum Data")
ROLE_WORDS = {
    "software engineer": ("software engineer", "software engineers", "Software Engineer II"),
    "data engineer": ("data engineer", "Data Engineers", "engineer for our data platform"),
    "c++ developer": ("C++ developer", "C++ developers", "c++ developer"),
    "devops": ("DevOps", "devops engineer", "DevOps"),
    "senior data engineer": ("senior data engineer", "Senior Data Engineers"),
}
OTHER_ROLES = ("nurse", "account executive", "product designer", "site reliability manager")


def random_pull(rng: random.Random, role: str) -> list[dict[str, Any]]:
    """A provider answer that exercises every branch of the search at once, in a
    random mix: person posts that match the role and ones that do not, job seekers,
    aggregator bursts, echoes of several pages, duplicates, posts that are not posts,
    posts older than any window, posts from the future, and posts with no author."""
    entries: list[dict[str, Any]] = []
    n = 0

    def nxt() -> int:
        nonlocal n
        n += 1
        return n

    for _ in range(rng.randint(0, 12)):
        entries.append(
            person_post(
                nxt(),
                f"We're hiring {rng.choice(ROLE_WORDS[role])} in Pune ... message me",
                age_hours=rng.choice([1, 5, 20, 30, 50, 70, 100, 160, 300]),
            )
        )
    for _ in range(rng.randint(0, 6)):
        entries.append(
            person_post(
                nxt(),
                f"Hiring a {rng.choice(OTHER_ROLES)} in Pune ... message me",
                age_hours=rng.choice([2, 25, 60]),
            )
        )
    for _ in range(rng.randint(0, 3)):
        entries.append(
            person_post(
                nxt(),
                f"I'm looking for my next opportunity as a {role}. Open to work ... #OpenToWork",
                age_hours=rng.choice([3, 40]),
            )
        )
    if rng.random() < 0.5:  # one job-alert account, over the aggregator threshold
        for _ in range(rng.randint(AGGREGATOR_MIN_POSTS, AGGREGATOR_MIN_POSTS + 3)):
            k = nxt()
            entries.append(
                person_post(
                    k,
                    f"Litware{k} Hiring for {role} Location: Pune ... Apply Link ...",
                    age_hours=rng.choice([2, 12, 40, 66]),
                    name="Daily Tech Job Alerts",
                    handle="jobalerts-daily-3c9d41ab",
                )
            )
    for _ in range(rng.randint(0, 5)):
        entries.append(
            echo_post(
                nxt(),
                rng.choice(PAGES),
                rng.choice([role.title(), role.title(), "Analyst"]),
                age_hours=rng.choice([10, 45]),
            )
        )
    for entry in rng.sample(entries, k=min(len(entries), rng.randint(0, 4))):
        twin = dict(entry)  # the same post under a second slug
        twin["url"] = (
            entry["url"]
            .replace("_hiring-activity-", "_again-activity-")
            .replace("/posts/activity-", "/posts/other-activity-")
        )
        entries.append(twin)
    for url in (
        "https://www.linkedin.com/jobs/view/123456789/",
        "https://example.com/not-linkedin",
        f"https://www.linkedin.com/posts/x_y-ugcPost-{activity_id_at(10, sequence=9)}-AbCd",
    ):
        if rng.random() < 0.4:
            entries.append(
                {"url": url, "title": "Software Engineer - LinkedIn", "description": "x"}
            )
    if rng.random() < 0.4:
        leading = person_post(nxt(), f"Hiring {role} ... x")
        leading["url"] = leading["url"].replace("-activity-", "-activity-000")
        entries.append(leading)
    if rng.random() < 0.4:
        entries.append(person_post(nxt(), f"Hiring {role} ... x", age_hours=-48))  # a future id
    if rng.random() < 0.4:
        entries.append(nameless_post(nxt(), role, age_hours=rng.choice([4, 90])))
    rng.shuffle(entries)
    return entries[:20]  # a provider answers with at most 20 hits


def fuzz_case(seed: int) -> tuple[World, str, str | None, Freshness]:
    rng = random.Random(seed)
    role = rng.choice(ROLES)
    location = rng.choice([None, "Pune", "Bengaluru, Karnataka, India", "Austin"])
    freshness = rng.choice(list(Freshness))
    kwargs: dict[str, Any] = {}
    if rng.random() < 0.8:
        kwargs = registry_for(
            **{
                page.replace(" ", "_"): [
                    (
                        rng.choice([role.title(), role.title(), "Data Analyst"]),
                        "Bengaluru, Karnataka",
                    )
                    for _ in range(rng.randint(0, 2))
                ]
                for page in rng.sample(PAGES, k=rng.randint(1, len(PAGES)))
            }
        )
    world = World(responses={"firecrawl": firecrawl_body(*random_pull(rng, role))}, **kwargs)
    return world, role, location, freshness


SEEDS = range(60)


@pytest.mark.parametrize("seed", SEEDS)
async def test_the_counts_add_up_and_the_ranking_holds_over_random_pulls(seed: int) -> None:
    world, role, location, freshness = fuzz_case(seed)

    response = await world.tab_search(role, location, freshness)

    counts_add_up(response)
    signals = response["signals"]
    assert len(set(ids(response))) == len(signals)  # never the same post twice
    aggregator_flags = [s["aggregator"] is True for s in signals]
    assert aggregator_flags == sorted(aggregator_flags)  # every aggregator after every other
    for flag in (False, True):  # within a class, freshest first, unknown times last
        times = [s["posted_at"] for s in signals if (s["aggregator"] is True) is flag]
        known = [t for t in times if t is not None]
        assert known == sorted(known, reverse=True)
        assert times[: len(known)] == known
    assert json.dumps(response)  # serializable as it stands


async def test_the_random_pulls_really_reach_every_branch() -> None:
    """The fuzz above proves the identity only as far as it reaches: over its seeds
    every bucket is non-empty many times, and the shown set holds each kind of post
    the ranking has to order."""
    seen = dict.fromkeys(TAB_COUNT_FIELDS, 0)
    kinds: set[str] = set()
    for seed in SEEDS:
        world, role, location, freshness = fuzz_case(seed)
        response = await world.tab_search(role, location, freshness)
        for bucket, value in response["counts"].items():
            seen[bucket] += bool(value)
        kinds |= {f"species={s['species']}" for s in response["signals"]} | {
            f"aggregator={s['aggregator']}" for s in response["signals"]
        }
        kinds |= {f"registry={s['registry_match']}" for s in response["signals"]}
        kinds |= (
            {"unknown_time"} if any(s["posted_at"] is None for s in response["signals"]) else set()
        )
    assert min(seen.values()) >= 10, seen
    assert {
        "species=unclassified",
        "species=ats_echo",
        "aggregator=True",
        "aggregator=False",
        "aggregator=None",
        "registry=unmatched",
        "registry=None",
        "unknown_time",
    } <= kinds, kinds


@pytest.mark.parametrize("seed", range(20))
async def test_the_same_pull_gives_the_same_answer_twice_and_from_the_cache(seed: int) -> None:
    rng = random.Random(1000 + seed)
    role = rng.choice(ROLES)
    world = World(responses={"firecrawl": firecrawl_body(*random_pull(rng, role))})

    first = await world.tab_search(role, "Pune", Freshness.WEEK)
    second = await world.tab_search(role, "Pune", Freshness.WEEK)

    assert first["cached"] is False and second["cached"] is True
    assert {**second, "cached": False} == first


# ── the hard role filter ─────────────────────────────────────────────────


def _single(text: str, *, age_hours: float = 30) -> World:
    return world_with(person_post(1, text, age_hours=age_hours))


@pytest.mark.parametrize(
    ("role", "text", "kept"),
    [
        # multi-word: every word, as whole words, in any order and anywhere
        ("data engineer", "We're hiring a data engineer ... message me", True),
        ("data engineer", "Engineer needed for our data team ... message me", True),
        ("data engineer", "We're hiring a data analyst ... message me", False),
        ("data engineer", "We're hiring a database engineer ... message me", False),
        ("software engineer", "Hiring software engineers in Austin ... message me", True),
        ("software engineer", "Hiring Software-Engineer roles ... message me", True),
        ("software engineer", "Hiring a Software Developer ... message me", False),
        # level words are part of what was typed
        ("senior data engineer", "Hiring a Senior Data Engineer ... message me", True),
        ("senior data engineer", "Hiring a Data Engineer ... message me", False),
        # one word
        ("devops", "DevOps engineer wanted ... message me", True),
        ("devops", "Hiring platform engineers ... message me", False),
        # symbols are part of the word
        ("c++ developer", "Hiring a C++ developer with STL ... message me", True),
        ("c++ developer", "Hiring C++/Rust developers ... message me", True),
        ("c++ developer", "Hiring a C developer ... message me", False),
        (".NET developer", "Hiring .NET developers ... message me", True),
        (".NET developer", "Hiring a NET developer ... message me", False),
        ("C# developer", "C# developer needed ... message me", True),
        ("node.js backend", "Node.js backend engineer ... message me", True),
        ("node.js backend", "Node backend engineer ... message me", False),
        # separators in what was typed are gaps, and a post's own separators match them
        ("AI/ML engineer", "AI ML engineer wanted ... message me", True),
        ("AI/ML engineer", "ML engineer wanted ... message me", False),
        ("full-stack developer", "Hiring a Full Stack Developer ... message me", True),
        ("full-stack developer", "Hiring a fullstack developer ... message me", True),
        # case does not matter, in either direction
        ("SOFTWARE ENGINEER", "hiring a software engineer ... message me", True),
        ("software engineer", "HIRING A SOFTWARE ENGINEER ... MESSAGE ME", True),
        # a word is a word: `net` is not `.net`, and a longer word is another word
        ("engineer", "Hiring an engineering manager ... message me", False),
        # the plural of the LAST word only
        ("software engineer", "Hiring software engineers ... message me", True),
        ("software engineers", "Hiring a software engineer ... message me", True),
        # a role typed in the plural also finds the singular (`role_filter_terms`)
        ("data engineers", "Hiring a data engineer ... message me", True),
        ("data engineers", "Hiring data engineers ... message me", True),
        ("data engineers", "Hiring a data analyst ... message me", False),
        ("c++ developers", "Hiring a C++ developer ... message me", True),
    ],
)
async def test_the_role_filter_is_every_word_as_a_whole_word(
    role: str, text: str, kept: bool
) -> None:
    response = await _single(text).tab_search(role, None, Freshness.WEEK)

    assert response["counts"]["role_mismatch_hidden"] == (0 if kept else 1), (role, text)
    assert response["counts"]["shown"] == (1 if kept else 0)
    counts_add_up(response)


async def test_a_role_in_the_index_answer_only_as_a_neighbour_is_hidden_and_counted() -> None:
    """The filter is hard: a hiring post for a different role is not shown, and the
    person is told how many were hidden rather than shown noise."""
    body = firecrawl_body(
        person_post(1, "We're hiring a software engineer ... message me"),
        person_post(2, "We're hiring a data analyst ... message me"),
        person_post(3, "Hiring an account executive ... message me"),
    )
    response = await World(responses={"firecrawl": body}).tab_search("software engineer")
    assert response["counts"]["role_mismatch_hidden"] == 2
    assert response["counts"]["shown"] == 1


async def test_a_role_whose_only_letters_are_one_word_of_one_character_is_refused() -> None:
    with pytest.raises(ApiError) as e:
        await _single("hiring an R developer ... x").tab_search("r")
    assert e.value.code == "INVALID_INPUT"


async def test_a_one_character_word_in_a_role_is_dropped_by_the_shared_predicate() -> None:
    """`R developer` filters on `developer` alone: the shared predicate never tests a
    one-character word (documented in `hiring_signal_tab`), so a post that says
    `developer` without R is kept. The provider was still asked for R."""
    world = _single("Hiring a developer (Python) ... message me")
    response = await world.tab_search("R developer", None, Freshness.WEEK)
    assert response["counts"]["shown"] == 1
    assert response["query_label"].startswith("r developer")


async def test_a_role_in_a_script_without_spaces_is_never_hidden_by_the_filter() -> None:
    """Whether a CJK role is in a post cannot be told by word boundaries, so it is
    unknown, and unknown is kept."""
    role = "".join(chr(c) for c in (0x8F6F, 0x4EF6, 0x5DE5, 0x7A0B, 0x5E08))
    body = firecrawl_body(
        person_post(1, "We are hiring a nurse ... message me"),
        person_post(2, f"Hiring {role} ... message me"),
    )
    response = await World(responses={"firecrawl": body}).tab_search(role)
    assert response["counts"]["role_mismatch_hidden"] == 0
    assert response["counts"]["shown"] == 2


async def test_accents_are_not_folded_in_the_role_filter() -> None:
    """`developpeur` is not `developpeur` with its accent: no fold is invented."""
    developer = "d" + chr(0xE9) + "veloppeur"
    body = firecrawl_body(
        person_post(1, f"Nous recherchons un {developer.title()} ... ecrivez-moi"),
        person_post(2, "Nous recherchons un developpeur ... ecrivez-moi"),
    )
    response = await World(responses={"firecrawl": body}).tab_search(developer)
    assert response["counts"]["shown"] == 1
    assert response["counts"]["role_mismatch_hidden"] == 1


async def test_the_role_may_be_found_in_the_title_as_well_as_the_text() -> None:
    entry = post_entry(
        "riley-sample-0d7b41e9_hiring",
        "Data Engineer opening | Riley Sample - LinkedIn",
        "1 day ago · Join our team in Pune ... message me",
        age_hours=30,
    )
    response = await world_with(entry).tab_search("data engineer")
    assert response["counts"]["shown"] == 1


async def test_a_post_that_names_the_role_in_another_copy_is_kept() -> None:
    """One post can come back as several hits, one cut before the role words."""
    short = post_entry(
        "riley-sample-0d7b41e9_hiring",
        "Riley Sample's Post - LinkedIn",
        "1 day ago · Big news from our team ... more",
        age_hours=30,
        sequence=5,
    )
    long = post_entry(
        "riley-sample-0d7b41e9_hiring-again",
        "Riley Sample's Post - LinkedIn",
        "1 day ago · Big news: we are hiring a data engineer ... more",
        age_hours=30,
        sequence=5,
    )
    response = await world_with(short, long).tab_search("data engineer")
    assert response["counts"]["duplicates"] == 1
    assert response["counts"]["shown"] == 1
    assert response["counts"]["role_mismatch_hidden"] == 0


async def test_location_is_never_a_filter() -> None:
    """The index gives no structured place, so a post about another city is shown
    (the page says so); only the query carries the place."""
    body = firecrawl_body(
        person_post(1, "We're hiring a data engineer in Bengaluru ... message me"),
        person_post(2, "We're hiring a data engineer in Chicago ... message me"),
        person_post(3, "We're hiring a data engineer, fully remote ... message me"),
    )
    response = await World(responses={"firecrawl": body}).tab_search("data engineer", "Bengaluru")
    assert response["counts"]["shown"] == 3
    assert response["counts"]["role_mismatch_hidden"] == 0


# ── hidden buckets ───────────────────────────────────────────────────────


async def test_job_seekers_are_hidden_when_their_opening_says_so() -> None:
    body = firecrawl_body(
        person_post(1, "I'm looking for my next opportunity as a data engineer. #OpenToWork ..."),
        person_post(2, "We're hiring a data engineer ... someone: #OpenToWork Data Engineer at X"),
    )
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")

    assert response["counts"]["job_seekers_hidden"] == 1  # only the OPENING counts
    assert response["counts"]["shown"] == 1
    counts_add_up(response)


@pytest.mark.parametrize(
    ("freshness", "within"),
    [(Freshness.DAY, 1), (Freshness.THREE_DAYS, 4), (Freshness.WEEK, 5)],
)
async def test_the_window_is_enforced_from_the_decoded_post_time(
    freshness: Freshness, within: int
) -> None:
    ages = (10, 30, 60, 70, 100, 200)  # hours old; the windows are 24, 72 and 168 hours
    body = firecrawl_body(
        *(
            person_post(i, "We're hiring a data engineer ... message me", age_hours=age)
            for i, age in enumerate(ages)
        )
    )
    response = await World(responses={"firecrawl": body}).tab_search(
        "data engineer", None, freshness
    )
    assert response["counts"]["shown"] == within
    assert response["counts"]["too_old_hidden"] == len(ages) - within
    counts_add_up(response)


async def test_a_post_with_a_future_id_is_unknown_in_time_not_too_old_or_dropped() -> None:
    response = await world_with(
        person_post(1, "We're hiring a data engineer ... message me", age_hours=-72)
    ).tab_search("data engineer", None, Freshness.DAY)

    assert response["counts"]["shown"] == 1
    assert response["counts"]["too_old_hidden"] == 0
    assert response["signals"][0]["posted_at"] is None


async def test_an_id_with_a_leading_zero_is_rejected_not_shown() -> None:
    entry = person_post(1, "We're hiring a data engineer ... message me")
    entry["url"] = entry["url"].replace("-activity-", "-activity-000")
    response = await world_with(entry).tab_search("data engineer")

    assert response["counts"]["rejected"] == 1
    assert response["counts"]["shown"] == 0
    counts_add_up(response)


async def test_the_same_post_under_two_slugs_is_one_signal_and_one_duplicate() -> None:
    first = person_post(1, "We're hiring a data engineer ... message me")
    second = dict(first, url=first["url"].replace("_hiring-activity-", "_hiring-again-activity-"))
    response = await world_with(first, second).tab_search("data engineer")

    assert response["counts"]["duplicates"] == 1
    assert response["counts"]["shown"] == 1
    counts_add_up(response)


# ── ranking ──────────────────────────────────────────────────────────────


def _aggregator_burst(count: int, *, freshest_hours: float = 2) -> list[dict[str, Any]]:
    return [
        person_post(
            50 + i,
            f"Litware{i} Hiring for Data Engineer Location: Pune ... Apply Link ...",
            age_hours=freshest_hours + i,
            name="Daily Tech Job Alerts",
            handle="jobalerts-daily-3c9d41ab",
        )
        for i in range(count)
    ]


async def test_an_aggregators_posts_rank_below_everyone_even_when_they_are_the_freshest() -> None:
    people = [
        person_post(1, "We're hiring a data engineer ... message me", age_hours=60),
        person_post(2, "We're hiring a data engineer ... message me", age_hours=40),
        person_post(3, "We're hiring a data engineer ... message me", age_hours=50),
    ]
    body = firecrawl_body(*_aggregator_burst(AGGREGATOR_MIN_POSTS), *people)
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")

    flags = [s["aggregator"] for s in response["signals"]]
    assert flags == [False] * 3 + [True] * AGGREGATOR_MIN_POSTS
    hours = [
        round((FIXTURE_NOW - _parse(s["posted_at"])).total_seconds() / 3600)
        for s in response["signals"]
    ]
    assert hours[:3] == [40, 50, 60]  # freshest first among the people
    assert hours[3:] == sorted(hours[3:])  # and among the aggregator's
    assert response["counts"]["shown"] == 3 + AGGREGATOR_MIN_POSTS  # ranked, never hidden


def _parse(iso: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(iso)


async def test_one_post_short_of_the_threshold_is_not_an_aggregator() -> None:
    body = firecrawl_body(*_aggregator_burst(AGGREGATOR_MIN_POSTS - 1))
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")
    assert {s["aggregator"] for s in response["signals"]} == {False}


async def test_the_aggregator_is_counted_over_the_whole_pull_before_the_role_filter() -> None:
    """Five posts from one account, three of them about another role: the account
    is still an aggregator (its two matching posts rank last), because the tag is set
    over the pull, not over what survived."""
    burst = _aggregator_burst(2) + [
        person_post(
            70 + i,
            "Hiring an account executive ... Apply Link ...",
            name="Daily Tech Job Alerts",
            handle="jobalerts-daily-3c9d41ab",
        )
        for i in range(3)
    ]
    person = person_post(1, "We're hiring a data engineer ... message me", age_hours=100)
    response = await World(responses={"firecrawl": firecrawl_body(*burst, person)}).tab_search(
        "data engineer", None, Freshness.WEEK
    )
    assert [s["aggregator"] for s in response["signals"]] == [False, True, True]
    assert response["counts"]["role_mismatch_hidden"] == 3


async def test_a_post_with_no_author_handle_is_not_an_aggregator_but_is_unknown() -> None:
    response = await world_with(nameless_post(1, "data engineer", age_hours=10)).tab_search(
        "data engineer"
    )
    (signal,) = response["signals"]
    assert signal["aggregator"] is None
    assert signal["author_name"] is None


async def test_an_unknown_author_ranks_with_the_people_by_freshness() -> None:
    body = firecrawl_body(
        person_post(1, "We're hiring a data engineer ... message me", age_hours=30),
        nameless_post(2, "data engineer", age_hours=10),
        *_aggregator_burst(AGGREGATOR_MIN_POSTS),
    )
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")
    assert [s["aggregator"] for s in response["signals"]][:2] == [None, False]
    assert all(s["aggregator"] is True for s in response["signals"][2:])


async def test_the_ranking_is_deterministic_and_ties_keep_the_providers_order() -> None:
    same_time = [
        person_post(i, "We're hiring a data engineer ... message me", age_hours=30)
        for i in range(1, 6)
    ]
    forward = await World(responses={"firecrawl": firecrawl_body(*same_time)}).tab_search(
        "data engineer"
    )
    again = await World(responses={"firecrawl": firecrawl_body(*same_time)}).tab_search(
        "data engineer"
    )
    backward = await World(
        responses={"firecrawl": firecrawl_body(*reversed(same_time))}
    ).tab_search("data engineer")

    assert ids(forward) == ids(again)
    assert ids(backward) == list(reversed(ids(forward)))  # a tie: the index's own order


async def test_an_unknown_post_time_sorts_after_every_known_one() -> None:
    body = firecrawl_body(
        person_post(1, "We're hiring a data engineer ... message me", age_hours=-72),  # unknown
        person_post(2, "We're hiring a data engineer ... message me", age_hours=60),
        person_post(3, "We're hiring a data engineer ... message me", age_hours=20),
    )
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")
    assert [s["posted_at"] is None for s in response["signals"]] == [False, False, True]


# ── the registry echo lookup: ONE bounded batch ──────────────────────────


def _companies_calls(world: World) -> list[Any]:
    return world.tables["job_registry_companies"].calls


def _postings_calls(world: World) -> list[Any]:
    return world.tables["job_registry_postings"].calls


def _or_clauses(call: Any) -> list[str]:
    _, filters = call
    (clauses,) = (value for op, _, value in filters if op == "or_ilike")
    return [pattern for _, pattern in clauses]


async def test_with_no_echo_the_registry_is_never_read() -> None:
    world = world_with(
        person_post(1, "We're hiring a data engineer ... message me"),
        **registry_for(Northwind_Labs=[("Data Engineer", "Pune")]),
    )
    await world.tab_search("data engineer")
    assert _companies_calls(world) == [] and _postings_calls(world) == []


async def test_one_echo_is_two_queries_and_hides_it_when_the_registry_tracks_the_listing() -> None:
    world = world_with(
        echo_post(1, "Northwind Labs", "Data Engineer", "Pune, Maharashtra"),
        **registry_for(Northwind_Labs=[("Data Engineer", "Pune, Maharashtra")]),
    )
    response = await world.tab_search("data engineer")

    assert response["counts"]["echoes_hidden"] == 1
    assert response["counts"]["shown"] == 0
    assert len(_companies_calls(world)) == 1 and len(_postings_calls(world)) == 1
    counts_add_up(response)


async def test_a_company_the_registry_lacks_is_one_query_and_the_echo_is_shown_unknown() -> None:
    world = world_with(
        echo_post(1, "Zorblax Dynamics", "Data Engineer"),
        **registry_for(Northwind_Labs=[("Data Engineer", "Pune")]),
    )
    response = await world.tab_search("data engineer")

    assert len(_companies_calls(world)) == 1
    assert _postings_calls(world) == []  # no company matched: nothing to read
    (signal,) = response["signals"]
    assert signal["species"] == "ats_echo" and signal["registry_match"] is None


async def test_many_echoes_of_many_companies_are_still_two_queries() -> None:
    pages = [f"Acme{name} Systems" for name in "ABCDEF"]
    entries = [
        echo_post(i * 3 + j, page, role)
        for i, page in enumerate(pages)
        for j, role in enumerate(["Data Engineer", "Data Engineer II", "Senior Data Engineer"])
    ]
    world = world_with(
        *entries,
        **registry_for(
            **{p.replace(" ", "_"): [("Data Engineer", "Bengaluru, Karnataka")] for p in pages}
        ),
    )
    response = await world.tab_search("data engineer")

    assert response["counts"]["raw_hits"] == 18  # 18 echoes, 6 companies, 3 roles each
    assert len(_companies_calls(world)) == 1  # never one query per echo
    assert len(_postings_calls(world)) == 1
    assert len(_or_clauses(_companies_calls(world)[0])) == len(pages)  # one per DISTINCT page
    # `Data Engineer` is tracked for every page: 6 hidden; the other titles are not
    assert response["counts"]["echoes_hidden"] == 6
    counts_add_up(response)


async def test_the_same_page_is_looked_up_once_however_many_echoes_it_has() -> None:
    entries = [echo_post(i, "Northwind Labs", "Data Engineer") for i in range(6)]
    world = world_with(*entries, **registry_for(Northwind_Labs=[("Data Engineer", "Pune")]))
    await world.tab_search("data engineer")
    assert len(_or_clauses(_companies_calls(world)[0])) == 1


async def test_pages_beyond_the_batch_cap_are_left_unknown_and_shown_never_hidden() -> None:
    """A provider answers with at most 20 hits, so a real pull cannot hold more than
    `MAX_COMPANIES` distinct pages; the cap is a bound that holds whatever a provider
    (or a later change to that ceiling) does, so it is tested on the function directly."""
    count = MAX_COMPANIES + 5
    pages = [f"Acme{i:02d}Corp Systems".replace("0", "o") for i in range(count)]
    hits = [
        RawSearchHit(e["url"], e["title"], e["description"])
        for e in (echo_post(i, page, "Data Engineer", "Pune") for i, page in enumerate(pages))
    ]
    echoes = [s for s in analyze_pull(hits).signals if s.species == "ats_echo"]
    assert len(echoes) == count
    world = World(
        **registry_for(**{p.replace(" ", "_"): [("Data Engineer", "Pune")] for p in pages})
    )

    states = await _tab_registry_states(as_client(world.supabase), echoes)

    assert len(_companies_calls(world)) == 1 and len(_postings_calls(world)) == 1
    assert len(_or_clauses(_companies_calls(world)[0])) == MAX_COMPANIES
    matched = [e.activity_id for e in echoes if states[e.activity_id] == "matched"]
    assert matched == [e.activity_id for e in echoes[:MAX_COMPANIES]]
    # the five past the cap are unknown -- shown, never hidden on a guess
    assert [states[e.activity_id] for e in echoes[MAX_COMPANIES:]] == [None] * 5


async def test_the_registry_read_is_bounded_however_many_companies_share_a_name() -> None:
    names = [f"Acme Systems {i:03d}" for i in range(MAX_BATCH_REGISTRY_COMPANIES + 100)]
    world = world_with(
        echo_post(1, "Acme Systems", "Data Engineer"),
        **registry_for(**{n.replace(" ", "_"): [("Data Engineer", "Pune")] for n in names}),
    )
    response = await world.tab_search("data engineer")

    assert len(_companies_calls(world)) == 1 and len(_postings_calls(world)) == 1
    (in_filter,) = (
        v for op, c, v in _postings_calls(world)[0][1] if op == "in" and c == "company_id"
    )
    assert len(in_filter) <= MAX_BATCH_REGISTRY_COMPANIES
    counts_add_up(response)


async def test_a_name_too_short_to_look_up_is_left_unknown_and_reads_nothing() -> None:
    world = world_with(
        echo_post(1, "AT&T", "Data Engineer", "Dallas, Texas"),
        **registry_for(ATT_Services=[("Data Engineer", "Dallas, Texas")]),
    )
    response = await world.tab_search("data engineer")

    assert _companies_calls(world) == []
    assert response["counts"]["echoes_hidden"] == 0
    assert response["signals"][0]["registry_match"] is None


async def test_a_registry_that_cannot_be_read_leaves_every_echo_shown_and_unknown() -> None:
    world = world_with(
        echo_post(1, "Northwind Labs", "Data Engineer"),
        echo_post(2, "Contoso Robotics", "Data Engineer"),
        **registry_for(Northwind_Labs=[("Data Engineer", "Bengaluru, Karnataka")]),
    )
    world.tables["job_registry_companies"].fail_with = APIError(
        {"message": "down", "code": "XX000", "details": None, "hint": None}
    )
    response = await world.tab_search("data engineer")

    assert response["counts"]["echoes_hidden"] == 0
    assert response["counts"]["shown"] == 2
    assert {s["registry_match"] for s in response["signals"]} == {None}
    counts_add_up(response)


async def test_a_postings_read_that_fails_after_the_companies_read_also_degrades() -> None:
    world = world_with(
        echo_post(1, "Northwind Labs", "Data Engineer"),
        **registry_for(Northwind_Labs=[("Data Engineer", "Bengaluru, Karnataka")]),
    )
    world.tables["job_registry_postings"].fail_with = APIError(
        {"message": "down", "code": "XX000", "details": None, "hint": None}
    )
    response = await world.tab_search("data engineer")

    assert response["counts"]["echoes_hidden"] == 0
    assert response["signals"][0]["registry_match"] is None


@pytest.mark.parametrize(
    ("tracked", "expected"),
    [
        ([("Data Engineer", "Bengaluru, Karnataka")], "hidden"),
        ([("Data Engineer", "Seattle, WA")], "unmatched"),  # the same title, elsewhere
        ([("Data Analyst", "Bengaluru, Karnataka")], "unmatched"),  # another listing
        ([("Data Engineer", None)], "possible"),  # the title, an unknown place
        ([], "unmatched"),  # the company, no open listing
    ],
)
async def test_an_echo_is_hidden_only_when_the_registry_confirms_that_listing(
    tracked: list[tuple[str, str | None]], expected: str
) -> None:
    world = world_with(
        echo_post(1, "Northwind Labs", "Data Engineer", "Bengaluru, Karnataka"),
        **registry_for(Northwind_Labs=tracked),
    )
    response = await world.tab_search("data engineer")

    if expected == "hidden":
        assert response["counts"]["echoes_hidden"] == 1 and response["signals"] == []
    else:
        assert response["counts"]["echoes_hidden"] == 0
        assert response["signals"][0]["registry_match"] == expected
    counts_add_up(response)


async def test_a_closed_registry_posting_does_not_hide_an_echo() -> None:
    kwargs = registry_for(Northwind_Labs=[("Data Engineer", "Bengaluru, Karnataka")])
    kwargs["registry_postings"][0]["status"] = "closed"
    world = world_with(echo_post(1, "Northwind Labs", "Data Engineer"), **kwargs)
    response = await world.tab_search("data engineer")
    assert response["counts"]["echoes_hidden"] == 0


async def test_an_echo_of_another_role_is_role_filtered_before_the_registry_is_asked() -> None:
    world = world_with(
        echo_post(1, "Northwind Labs", "Account Executive"),
        **registry_for(Northwind_Labs=[("Account Executive", "Bengaluru, Karnataka")]),
    )
    response = await world.tab_search("data engineer")

    assert response["counts"]["role_mismatch_hidden"] == 1
    assert _companies_calls(world) == []  # the registry is asked about survivors only


async def test_a_tagline_after_a_dash_in_the_page_name_is_not_part_of_the_company() -> None:
    entry = post_entry(
        "northwind-labs_hiring",
        "#hiring | Northwind Labs - Aerospace and Defence - LinkedIn",
        "2 days ago · We're #hiring a new Data Engineer in Bengaluru, Karnataka. "
        "Apply today or share this post with your network.",
        age_hours=45,
        sequence=7,
    )
    world = world_with(
        entry, **registry_for(Northwind_Labs=[("Data Engineer", "Bengaluru, Karnataka")])
    )
    response = await world.tab_search("data engineer")
    assert response["counts"]["echoes_hidden"] == 1


# ── saved flag: standalone saves only ────────────────────────────────────


def _save(user: str, activity_id: str, application_id: str | None) -> dict[str, Any]:
    return {
        "id": f"9000000{len(activity_id) % 10}-0000-0000-0000-{activity_id[-12:]}",
        "user_id": user,
        "application_id": application_id,
        "activity_id": activity_id,
        "post_url": f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}",
        "discovered_via_query": None,
        "created_at": "2026-09-18T00:00:00+00:00",
    }


async def test_the_saved_flag_reads_standalone_saves_only() -> None:
    entries = [person_post(i, "We're hiring a data engineer ... message me") for i in range(1, 5)]
    aid = [e["url"].split("activity-")[1][:19] for e in entries]
    world = world_with(
        *entries,
        saves=[
            _save(USER, aid[0], None),  # mine, standalone: saved
            _save(USER, aid[1], APP),  # mine, for an application: NOT saved here
            _save(OTHER_USER, aid[2], None),  # somebody else's standalone: not mine
        ],
    )
    response = await world.tab_search("data engineer")

    saved = {s["activity_id"]: s["saved"] for s in response["signals"]}
    assert saved == {aid[0]: True, aid[1]: False, aid[2]: False, aid[3]: False}


async def test_the_saved_flag_costs_one_query_and_none_when_nothing_is_shown() -> None:
    world = world_with(
        *[person_post(i, "We're hiring a data engineer ... message me") for i in range(1, 6)]
    )
    await world.tab_search("data engineer")
    assert len(world.tables["hiring_signal_saves"].calls) == 1

    empty = world_with(person_post(1, "We're hiring a nurse ... message me"))
    await empty.tab_search("data engineer")
    assert empty.tables["hiring_signal_saves"].calls == []


# ── the shared cache ─────────────────────────────────────────────────────


async def test_a_second_search_is_served_from_the_cache_without_a_provider_call() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    first = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)
    second = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    assert len(world.transport.requests) == 1
    assert first["cached"] is False and second["cached"] is True
    assert {**second, "cached": False} == first


async def test_a_different_role_or_place_is_a_different_cache_row_and_a_new_call() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)
    await world.tab_search("software engineer", "Pune", Freshness.THREE_DAYS)
    await world.tab_search("data engineer", "Bengaluru", Freshness.THREE_DAYS)
    await world.tab_search("software engineer", "Bengaluru", Freshness.DAY)

    assert len(world.transport.requests) == 4
    assert len(world.tables["hiring_signal_query_cache"].rows) == 4


async def test_the_same_words_typed_differently_are_one_cache_row() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    await world.tab_search("Software Engineer", "Bengaluru", Freshness.THREE_DAYS)
    again = await world.tab_search("  software   ENGINEER ", " bengaluru ", Freshness.THREE_DAYS)

    assert again["cached"] is True
    assert len(world.transport.requests) == 1


async def test_the_cache_key_is_provider_query_freshness_bucket_and_day() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    tq = build_tab_query(
        query="software engineer",
        location="Bengaluru",
        freshness=Freshness.THREE_DAYS,
        locale=None,
    )
    request = provider_freshness_request(
        "firecrawl", Freshness.THREE_DAYS, today=FIXTURE_NOW.date()
    )
    expected = cache_key("firecrawl", tq.query.query, request.param, FIXTURE_NOW.date())
    assert [r["query_key"] for r in world.tables["hiring_signal_query_cache"].rows] == [expected]


def _keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys_of(v)}
    return set()


async def test_a_cache_row_holds_raw_hits_and_never_a_parsed_opinion() -> None:
    """The cache is shared between the two surfaces, so what it holds must be what
    neither of them owns: the provider's own `{url, title, snippet}` per hit. A
    species, an author, an aggregator tag or a role verdict is a parse, and a parse
    read back later would be an old parser's opinion."""
    world = World(
        responses={"firecrawl": ROLE_FIXTURE},
        **registry_for(Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")]),
    )
    await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    (row,) = world.tables["hiring_signal_query_cache"].rows
    assert set(row["response_json"]) == {"v", "hits"}
    assert len(row["response_json"]["hits"]) == 20
    assert {frozenset(h) for h in row["response_json"]["hits"]} == {
        frozenset({"url", "title", "snippet"})
    }
    banned = {
        "species",
        "author",
        "author_name",
        "author_handle",
        "aggregator",
        "aggregator_source",
        "registry_match",
        "role_match",
        "saved",
        "posted_at",
        "activity_id",
        "counts",
        "signals",
        "echo_role",
        "echo_location",
    }
    assert not _keys_of(row["response_json"]) & banned
    assert set(row) <= {"query_key", "provider", "response_json", "created_at", "fetched_at", "id"}


async def test_a_cache_row_older_than_the_windows_ttl_is_a_miss_and_is_overwritten() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    start = FIXTURE_NOW - timedelta(hours=12)  # 06:00 UTC: every time below is the same UTC day

    def search(hours: float) -> Any:
        return world.tab_search(
            "software engineer",
            "Bengaluru",
            Freshness.THREE_DAYS,
            now=start + timedelta(hours=hours),
        )

    assert (await search(0))["cached"] is False
    (row,) = world.tables["hiring_signal_query_cache"].rows
    key = row["query_key"]
    # five hours later: served (the 3-day window is served for 12 hours)
    assert (await search(5))["cached"] is True
    # thirteen hours later: a miss, and the same key is rewritten in place
    assert (await search(13))["cached"] is False
    assert len(world.transport.requests) == 2
    assert [r["query_key"] for r in world.tables["hiring_signal_query_cache"].rows] == [key]


async def test_a_cache_that_cannot_be_read_or_written_never_fails_the_search() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    world.tables["hiring_signal_query_cache"].fail_with = APIError(
        {"message": "down", "code": "XX000", "details": None, "hint": None}
    )
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)
    assert response["cached"] is False
    assert response["counts"]["shown"] == 15


async def test_the_cache_never_lends_a_provider_the_user_has_no_key_for() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)  # firecrawl row

    serper_only = World(
        keys={"serper": "s"}, cache_rows=world.tables["hiring_signal_query_cache"].rows
    )
    serper_only.responses["serper"] = {"organic": []}
    response = await serper_only.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)
    assert response["cached"] is False
    assert serper_only.providers_called == ["serper"]


# ── the provider layer, exactly as P3 left it ────────────────────────────


async def test_the_dev_setup_you_com_and_firecrawl_reaches_firecrawl_first() -> None:
    world = World(keys={"you_com": "yc", "firecrawl": "fc"}, responses={"firecrawl": ROLE_FIXTURE})
    response = await world.tab_search("software engineer", "Bengaluru")
    assert response["provider"] == "firecrawl"
    assert world.providers_called == ["firecrawl"]


async def test_a_failing_provider_is_skipped_for_the_next_in_order() -> None:
    world = World(
        keys={"brave": "b", "serper": "s", "firecrawl": "f", "you_com": "y"},
        responses={"brave": httpx.Response(500, json={"error": "x"}), "firecrawl": ROLE_FIXTURE},
    )
    response = await world.tab_search("software engineer", "Bengaluru")
    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"


async def test_a_provider_that_answers_with_no_linkedin_posts_falls_through_too() -> None:
    brave_body = {
        "web": {
            "results": [
                {
                    "url": "https://example.com/jobs",
                    "title": "Software Engineer jobs",
                    "description": "x",
                }
            ]
        }
    }
    world = World(
        keys={"brave": "b", "firecrawl": "f"},
        responses={"brave": brave_body, "firecrawl": ROLE_FIXTURE},
    )
    response = await world.tab_search("software engineer", "Bengaluru")
    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"


async def test_a_you_com_only_user_gets_an_honest_empty_answer_not_an_error() -> None:
    world = World(keys={"you_com": "yc"})
    response = await world.tab_search("software engineer", "Bengaluru")
    assert response["provider"] == "you_com"
    assert response["signals"] == []
    assert response["counts"] == {**dict.fromkeys(TAB_COUNT_FIELDS, 0)}


async def test_a_niche_role_empty_everywhere_reports_the_best_placed_provider() -> None:
    """Found in the browser: a user with Firecrawl AND You.com connected searched a
    niche role, both answered with no posts, and the page said "You.com's index does
    not include LinkedIn posts ... connect one such as Firecrawl" -- advice that was
    wrong for them, because Firecrawl HAD been asked and had nothing. The answer
    that counts is the first (best-placed) provider's, so the response says
    `firecrawl` and the You.com caveat never shows."""
    world = World(
        keys={"you_com": "yc", "firecrawl": "fc"},
        responses={"firecrawl": load_fixture("firecrawl_empty.json")},
    )
    response = await world.tab_search("fpga verification engineer", "San Jose, CA")

    assert world.providers_called == ["firecrawl", "you_com"]
    assert response["provider"] == "firecrawl"
    assert response["signals"] == []
    assert response["counts"] == {**dict.fromkeys(TAB_COUNT_FIELDS, 0)}


async def test_every_provider_failing_is_a_retryable_provider_unavailable() -> None:
    world = World(
        keys={"brave": "b", "serper": "s", "firecrawl": "f", "you_com": "y"},
        responses={p: httpx.Response(500, json={}) for p in HOSTS},
    )
    with pytest.raises(ApiError) as e:
        await world.tab_search("software engineer", "Bengaluru")

    assert e.value.code == "PROVIDER_UNAVAILABLE"
    assert e.value.retryable is True
    assert sorted(e.value.details["providers_tried"]) == sorted(HOSTS)
    assert len(world.transport.requests) == MAX_PROVIDER_CALLS_PER_REQUEST


async def test_no_saved_search_key_is_setup_required_in_the_resolvers_shape() -> None:
    world = World(keys={})
    with pytest.raises(ApiError) as e:
        await world.tab_search("software engineer", "Bengaluru")

    error = e.value
    assert error.code == "SETUP_REQUIRED"
    assert error.capability == "hiring_signals"
    assert error.missing == ["search_credential"]
    assert error.settings_path == "/profile/integrations?capability=hiring_signals"
    assert world.transport.requests == []


async def test_a_key_saved_for_something_other_than_search_does_not_count() -> None:
    world = World(keys={})
    world.tables["provider_credentials"].rows.append(
        {
            "user_id": USER,
            "service": "llm",
            "provider": "firecrawl",
            "model": None,
            "base_url": None,
            "scope": None,
            "secret_encrypted": "enc:k",
            "secret_2_encrypted": None,
        }
    )
    with pytest.raises(ApiError) as e:
        await world.tab_search("software engineer")
    assert e.value.code == "SETUP_REQUIRED"


@pytest.mark.parametrize("provider", list(HOSTS))
async def test_a_full_tab_search_only_ever_contacts_provider_hosts(provider: str) -> None:
    entries = ROLE_FIXTURE["data"]["web"]
    bodies: dict[str, Any] = {
        "firecrawl": ROLE_FIXTURE,
        "brave": {
            "web": {
                "results": [
                    {"url": e["url"], "title": e["title"], "description": e["description"]}
                    for e in entries
                ]
            }
        },
        "serper": {
            "organic": [
                {"link": e["url"], "title": e["title"], "snippet": e["description"]}
                for e in entries
            ]
        },
        "you_com": {
            "results": {
                "web": [
                    {"url": e["url"], "title": e["title"], "snippets": [e["description"]]}
                    for e in entries
                ]
            }
        },
    }
    world = World(
        keys={provider: "k"},
        responses={provider: bodies[provider]},
        **registry_for(Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")]),
    )
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    assert response["counts"]["raw_hits"] == 20
    assert set(world.transport.hosts) == {HOSTS[provider]}
    assert not any(
        h.endswith("linkedin.com") or h.endswith("lnkd.in") for h in world.transport.hosts
    )


# ── what the person typed ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "location"),
    [
        ("", None),
        ("   ", None),
        ("!!! ???", None),
        ("a", None),
        ("x" * 61, None),
        ("data engineer", "###"),
        ("data engineer", "x" * 61),
        ("data " * 50, None),
    ],
)
async def test_text_that_cannot_be_searched_is_refused_before_anything_is_spent(
    query: str, location: str | None
) -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    with pytest.raises(ApiError) as e:
        await world.tab_search(query, location)

    assert e.value.code == "INVALID_INPUT"
    assert world.transport.requests == []
    assert not any(t.calls for t in world.tables.values())  # not even the key lookup


async def test_hostile_text_cannot_add_an_operator_to_the_provider_query() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    await world.tab_search(
        'software engineer") OR site:evil.example ("x', 'Pune") site:evil.example'
    )

    (body,) = world.transport.json_bodies()
    query: str = body["query"]
    assert query.startswith("site:linkedin.com/posts")
    assert query.count("site:") == 1  # only the one the feature adds
    assert query.count('"') % 2 == 0  # every quote it wrote is closed
    assert ":" not in query.removeprefix("site:linkedin.com/posts")  # no operator is left in it


async def test_the_query_sent_to_the_provider_is_never_in_the_response() -> None:
    world = World(responses={"firecrawl": ROLE_FIXTURE})
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)

    (body,) = world.transport.json_bodies()
    sent: str = body["query"]
    blob = json.dumps(response, ensure_ascii=False)
    assert "site:linkedin.com" in sent and "site:" not in blob
    assert sent not in blob
    assert "immediate joiners" not in blob.lower() and "we're hiring" not in blob.lower()


async def test_an_india_place_changes_the_vocabulary_that_is_queried_and_says_so() -> None:
    india = World(responses={"firecrawl": ROLE_FIXTURE})
    global_ = World(responses={"firecrawl": ROLE_FIXTURE})
    forced = World(responses={"firecrawl": ROLE_FIXTURE})
    r_india = await india.tab_search("software engineer", "Bengaluru")
    r_global = await global_.tab_search("software engineer", "Austin")
    r_forced = await forced.tab_search("software engineer", "Bengaluru", locale=Locale.GLOBAL)

    assert (r_india["locale"], r_global["locale"], r_forced["locale"]) == (
        "india",
        "global",
        "global",
    )
    q_india, q_global, q_forced = (
        w.transport.json_bodies()[0]["query"].lower() for w in (india, global_, forced)
    )
    assert "immediate joiners" in q_india and "immediate joiners" not in q_global
    assert "immediate joiners" not in q_forced  # an explicit locale wins over the place


async def test_no_place_is_the_global_vocabulary() -> None:
    response = await World(responses={"firecrawl": ROLE_FIXTURE}).tab_search(
        "software engineer", None, Freshness.THREE_DAYS
    )
    assert response["locale"] == "global"
    assert response["query_label"] == "software engineer -- last 3 days"


# ── nothing but structured fields leaves the server ──────────────────────


def _windows(text: str, size: int = 30) -> Iterable[str]:
    return (text[i : i + size] for i in range(max(1, len(text) - size + 1)))


async def test_no_provider_title_or_snippet_and_no_query_appears_anywhere_in_the_response() -> None:
    world = World(
        responses={"firecrawl": ROLE_FIXTURE},
        **registry_for(Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")]),
    )
    response = await world.tab_search("software engineer", "Bengaluru", Freshness.THREE_DAYS)
    blob = json.dumps(response, ensure_ascii=False)

    forbidden_keys = {"snippet", "title", "description", "query", "raw", "text", "body", "markdown"}
    assert not _keys_of(response) & forbidden_keys
    for entry in ROLE_FIXTURE["data"]["web"]:
        for text in (entry["title"], entry["description"]):
            for window in _windows(text):
                assert window not in blob, window
    # the only free-form strings are the ones the contract names
    strings: list[str] = []

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, list):
            for v in value:
                walk(v, f"{path}[]")
        elif isinstance(value, str):
            strings.append(path)

    walk(response)
    assert set(strings) <= {
        ".provider",
        ".freshness",
        ".locale",
        ".query_label",
        ".signals[].activity_id",
        ".signals[].post_url",
        ".signals[].embed_url",
        ".signals[].author_name",
        ".signals[].posted_at",
        ".signals[].age_hint",
        ".signals[].species",
        ".signals[].registry_match",
    }


async def test_an_author_field_is_only_ever_a_name_not_a_stretch_of_the_post() -> None:
    body = firecrawl_body(
        post_entry(
            "riley-sample-0d7b41e9_hiring",
            "#hiring | we are looking for a data engineer to join our growing team today",
            "1 day ago · We're hiring a data engineer ... message me",
            age_hours=30,
        )
    )
    response = await World(responses={"firecrawl": body}).tab_search("data engineer")
    for signal in response["signals"]:
        assert signal["author_name"] is None or len(signal["author_name"].split()) <= 6
        assert signal["author_name"] is None or "looking for" not in signal["author_name"]


async def test_error_bodies_carry_no_provider_text_either() -> None:
    world = World(
        keys={"firecrawl": "fc"},
        responses={"firecrawl": httpx.Response(500, json={"error": "we're hiring secret-marker"})},
    )
    with pytest.raises(ApiError) as e:
        await world.tab_search("software engineer")
    assert "secret-marker" not in json.dumps(e.value.details or {}) + e.value.message


# ── YH-1: where the role is said decides whether a post counts as a match ─

_COMMENTER = "Priya S. Software Engineer @ Acme | #OpenToWork | Python"


async def test_a_role_named_only_in_a_commenters_headline_is_shown_but_ranked_last() -> None:
    """Kept, not hidden -- a genuine `Role: Software Engineer` line also sits after
    the opening -- but it can no longer outrank a post that states the role, however
    fresh it is."""
    late = person_post(1, f"We're hiring for our growing team ... {_COMMENTER}", age_hours=6)
    stated = person_post(2, "We're hiring a software engineer ... message me", age_hours=80)
    response = await world_with(late, stated).tab_search("software engineer")

    # person 2 states the role and is older; person 1 only has a commenter's headline
    assert [s["author_name"] for s in response["signals"]] == [
        "Avery Testwell",
        "Jordan Placeholder",
    ]
    assert response["counts"]["role_mismatch_hidden"] == 0
    assert response["counts"]["shown"] == 2
    counts_add_up(response)


async def test_an_auto_job_share_for_another_role_is_hidden_whatever_a_commenter_says() -> None:
    """The real shape: LinkedIn's `We're #hiring a new <other role>` post, kept by the
    old any-text rule because a later fragment carried the searched role."""
    wrong = post_entry(
        "northwind-labs_hiring",
        "#hiring | Northwind Labs - LinkedIn",
        "2 days ago \u00b7 We're #hiring a new Senior AI Engineer in Pune, Maharashtra. "
        f"Apply today or share this post with your network. ... {_COMMENTER}",
        age_hours=45,
        sequence=2101,
    )
    own = echo_post(2, "Contoso Robotics", "Software Engineer II", "Pune, Maharashtra")
    response = await world_with(wrong, own).tab_search("software engineer")

    assert response["counts"]["role_mismatch_hidden"] == 1
    assert [s["author_name"] for s in response["signals"]] == ["Contoso Robotics"]
    counts_add_up(response)


async def test_the_composed_fixtures_commenter_headline_post_ranks_below_a_stated_role() -> None:
    """Row 8 of `firecrawl_role_posts.json`: no role of its own, the role words come
    from a commenter's headline after the first elision. Shown (the counts documented
    for the fixture hold) and below every non-aggregator post that states the role,
    although it is neither the oldest nor the freshest of them."""
    response = await World(responses={"firecrawl": ROLE_FIXTURE}).tab_search(
        "software engineer", "Bengaluru", Freshness.THREE_DAYS
    )
    assert response["counts"]["shown"] == 15
    people = [s for s in response["signals"] if s["aggregator"] is not True]
    assert people[-1]["author_name"] == "Diya Sampleton"
    above = [s["posted_at"] for s in people[:-1]]
    assert above == sorted(above, reverse=True)  # the stated posts: freshest first
    diya = people[-1]["posted_at"]
    assert min(above) < diya < max(above)  # so it is the tier that puts it last, not its age
    counts_add_up(response)


# ── BC-1: the batch read is narrowed by the roles being asked about ─────


async def test_one_large_employer_cannot_crowd_another_out_of_the_postings_cap() -> None:
    """A real registry has employers with thousands of open postings (Amazon alone had
    12,537). A batch read of "the newest thousand postings of these companies" would
    return only that employer's, and every other page's echo would degrade to
    `unmatched` instead of being hidden as the duplicate it is. The read is narrowed
    by the roles being asked about, so the older listing is still found."""
    big = [
        {
            "id": f"big-{i}",
            "company_id": "co-big",
            "title": f"Warehouse Associate {i}",
            "location": "Pune",
            "status": "active",
            "first_seen": (FIXTURE_NOW - timedelta(seconds=i)).isoformat(),
        }
        for i in range(MAX_POSTINGS + 5)
    ]
    older = {
        "id": "nw-1",
        "company_id": "co-nw",
        "title": "Software Engineer",
        "location": "Bengaluru, Karnataka",
        "status": "active",
        "first_seen": (FIXTURE_NOW - timedelta(days=60)).isoformat(),
    }
    world = World(
        registry_companies=[
            {"id": "co-big", "name": "Bigco Systems"},
            {"id": "co-nw", "name": "Northwind Labs, Inc."},
        ],
        registry_postings=[*big, older],
    )
    hits = [
        RawSearchHit(e["url"], e["title"], e["description"])
        for e in (
            echo_post(1, "Bigco Systems", "Warehouse Associate 7", "Pune"),
            echo_post(2, "Northwind Labs", "Software Engineer", "Bengaluru, Karnataka"),
        )
    ]
    echoes = [s for s in analyze_pull(hits).signals if s.species == "ats_echo"]
    assert len(echoes) == 2

    states = await _tab_registry_states(as_client(world.supabase), echoes)

    by_page = {e.author_name: states[e.activity_id] for e in echoes}
    assert by_page == {"Bigco Systems": "matched", "Northwind Labs": "matched"}
    (postings_call,) = _postings_calls(world)
    patterns = _or_clauses(postings_call)
    assert "%Software%Engineer%" in patterns  # narrowed by the role, not only by company


# ── BC-3: the precedence of the buckets is the documented one ───────────


async def test_an_old_post_about_another_role_counts_as_a_role_mismatch_not_as_too_old() -> None:
    old_other = person_post(1, "We're hiring a data analyst ... message me", age_hours=200)
    response = await world_with(old_other).tab_search(
        "software engineer", None, Freshness.THREE_DAYS
    )
    assert response["counts"]["role_mismatch_hidden"] == 1
    assert response["counts"]["too_old_hidden"] == 0
    counts_add_up(response)


async def test_an_old_job_seeker_post_counts_as_too_old_not_as_a_job_seeker() -> None:
    old_seeker = person_post(
        1, "I'm open to work as a software engineer ... #OpenToWork", age_hours=200
    )
    response = await world_with(old_seeker).tab_search(
        "software engineer", None, Freshness.THREE_DAYS
    )
    assert response["counts"]["too_old_hidden"] == 1
    assert response["counts"]["job_seekers_hidden"] == 0
    counts_add_up(response)


async def test_a_job_seeker_post_about_another_role_counts_as_a_role_mismatch() -> None:
    seeker = person_post(1, "I'm open to work as a data analyst ... #OpenToWork", age_hours=20)
    response = await world_with(seeker).tab_search("software engineer")
    assert response["counts"]["role_mismatch_hidden"] == 1
    assert response["counts"]["job_seekers_hidden"] == 0
    counts_add_up(response)


async def test_a_registry_hidden_echo_is_counted_only_after_the_role_and_age_checks() -> None:
    """The echo is for the searched role but four days old: too old, so the registry
    is never asked about it (and it is not counted as an echo)."""
    old_echo = echo_post(1, "Northwind Labs", "Software Engineer", age_hours=100)
    world = world_with(
        old_echo, **registry_for(Northwind_Labs=[("Software Engineer", "Bengaluru, Karnataka")])
    )
    response = await world.tab_search("software engineer", None, Freshness.THREE_DAYS)
    assert response["counts"]["too_old_hidden"] == 1
    assert response["counts"]["echoes_hidden"] == 0
    assert _companies_calls(world) == []
    counts_add_up(response)
