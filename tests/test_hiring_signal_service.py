"""End-to-end tests for Hiring Signals P3's per-application search
(`hiring_signal_service.search_application`) against in-memory fakes: a
Supabase client (`hiring_signal_fakes`) and a mocked httpx transport that
answers as the four providers.

The Firecrawl body is `firecrawl_company_posts.json`, the composed fixture
(structure of real results, text written by hand -- see its README) whose 13
hits were built so that every hidden-bucket has exactly the members listed in
`test_the_composed_fixture_lands_in_the_documented_buckets`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from hiring_signal_fakes import (
    APP,
    FIXTURE,
    FIXTURE_NOW,
    HOSTS,
    OTHER_USER,
    USER,
    World,
    as_client,
    firecrawl_body,
    load_fixture,
    post_entry,
    registry_kwargs,
)
from postgrest.exceptions import APIError

from between_jobs.api.errors import ApiError
from between_jobs.api.hiring_signal_cache import cache_key
from between_jobs.api.hiring_signal_query import build_application_query
from between_jobs.api.hiring_signal_search import (
    CALL_TIMEOUT_SECONDS,
    MAX_PROVIDER_CALLS_PER_REQUEST,
    provider_freshness_request,
)
from between_jobs.api.hiring_signal_service import REQUEST_DEADLINE_SECONDS, search_application
from between_jobs.api.hiring_signals import Freshness


def _activity_id(entry_index: int) -> str:
    """The activity id of the fixture hit at `entry_index` (1-based, as
    numbered in the fixture generator's comments)."""
    url: str = FIXTURE["data"]["web"][entry_index - 1]["url"]
    return url.split("activity-")[1][:19]


def _counts_add_up(response: dict[str, Any]) -> None:
    c = response["counts"]
    assert c["raw_hits"] == (
        c["rejected"]
        + c["duplicates"]
        + c["off_topic_hidden"]
        + c["too_old_hidden"]
        + c["job_seekers_hidden"]
        + c["echoes_hidden"]
        + c["shown"]
    ), c
    assert c["shown"] == len(response["signals"])


# ── the composed fixture end to end ──────────────────────────────────────


async def test_the_composed_fixture_lands_in_the_documented_buckets() -> None:
    world = World()
    response = await world.search()

    assert response["provider"] == "firecrawl"
    assert response["cached"] is False
    assert response["freshness"] == "week"
    assert response["query_label"] == "Stripe -- software engineer -- last 7 days"
    assert response["counts"] == {
        "raw_hits": 13,
        "rejected": 2,  # a /jobs/ page and a ugcPost
        "duplicates": 1,  # the same post under a second slug
        "off_topic_hidden": 3,  # a skills list, a news line, another company
        "echoes_hidden": 0,
        "job_seekers_hidden": 1,
        "too_old_hidden": 1,  # nine days old
        "shown": 5,
    }
    _counts_add_up(response)


async def test_signals_are_ranked_exact_role_first_then_newest() -> None:
    response = await World().search()

    # role match true: #2 (30h), #4 (45h), #1 (50h); then the rest by newest:
    # #7 (60h, no role words), #3 (70h, a different role)
    assert [s["activity_id"] for s in response["signals"]] == [
        _activity_id(2),
        _activity_id(4),
        _activity_id(1),
        _activity_id(7),
        _activity_id(3),
    ]
    assert [s["role_match"] for s in response["signals"]] == [True, True, True, False, False]


async def test_a_signal_carries_exactly_the_contract_fields() -> None:
    response = await World().search()
    first = response["signals"][0]

    assert set(first) == {
        "activity_id",
        "post_url",
        "embed_url",
        "author_name",
        "posted_at",
        "age_hint",
        "species",
        "comment_count",
        "role_match",
        "registry_match",
        "saved",
    }
    assert first["activity_id"] == _activity_id(2)
    assert first["embed_url"] == (
        f"https://www.linkedin.com/embed/feed/update/urn:li:activity:{_activity_id(2)}"
    )
    assert first["post_url"].startswith("https://www.linkedin.com/posts/")
    assert "?" not in first["post_url"] and "#" not in first["post_url"]
    assert first["posted_at"] == (FIXTURE_NOW - timedelta(hours=30)).isoformat()
    assert first["age_hint"] == "1 day ago"
    assert first["species"] == "unclassified"
    assert first["role_match"] is True
    assert first["registry_match"] is None  # not an echo
    assert first["saved"] is False
    assert first["author_name"] == "Avery Placeholder"


async def test_the_response_contains_no_provider_text_and_no_query() -> None:
    """Structural: no key that names provider text, and no window of any hit's
    title or snippet (30 characters) survives anywhere in the serialized
    body -- the author's display name is the one allowed piece of a title."""
    response = await World().search()
    blob = json.dumps(response, ensure_ascii=False)

    forbidden_keys = {"snippet", "title", "description", "query", "raw", "text", "body", "markdown"}
    assert not _all_keys(response) & forbidden_keys

    for entry in FIXTURE["data"]["web"]:
        for text in (entry["title"], entry["description"]):
            for start in range(0, max(1, len(text) - 29)):
                assert text[start : start + 30] not in blob, text[start : start + 30]
    assert "site:linkedin.com" not in blob
    assert "we're hiring" not in blob.lower()


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


@pytest.mark.parametrize(
    ("freshness", "param", "too_old", "shown"),
    [
        # 3 days: only the 9-day-old post is out
        (Freshness.THREE_DAYS, "qdr:d3", 1, 5),
        # a day: everything but the (job-seeker) 20h post is older than 24h
        (Freshness.DAY, "qdr:d", 6, 0),
        (Freshness.WEEK, "qdr:w", 1, 5),
    ],
)
async def test_the_window_is_enforced_from_the_decoded_post_time(
    freshness: Freshness, param: str, too_old: int, shown: int
) -> None:
    world = World()
    response = await world.search(freshness)

    assert response["freshness"] == freshness.value
    assert response["counts"]["too_old_hidden"] == too_old
    assert response["counts"]["shown"] == shown
    assert json.loads(world.transport.requests[0].content)["tbs"] == param
    _counts_add_up(response)


async def test_the_window_is_trimmed_even_when_the_provider_returns_wider_posts() -> None:
    """Brave has no confirmed way to say "3 days", so it is asked for a week;
    the extra days must not reach the user."""
    body = {
        "web": {
            "results": [
                {"url": e["url"], "title": e["title"], "description": e["description"]}
                for e in FIXTURE["data"]["web"]
            ]
        }
    }
    world = World(keys={"brave": "b-key"}, responses={"brave": body})
    response = await world.search(Freshness.THREE_DAYS)

    assert world.transport.requests[0].url.params["freshness"] == "pw"
    assert response["counts"]["too_old_hidden"] == 1
    assert response["counts"]["shown"] == 5


# ── saved flag ───────────────────────────────────────────────────────────


async def test_a_post_already_saved_for_this_application_is_marked_saved() -> None:
    saves = [
        {"user_id": USER, "application_id": APP, "activity_id": _activity_id(2)},
        {"user_id": USER, "application_id": "other-app", "activity_id": _activity_id(1)},
        {"user_id": OTHER_USER, "application_id": APP, "activity_id": _activity_id(4)},
    ]
    response = await World(saves=saves).search()

    saved = {s["activity_id"] for s in response["signals"] if s["saved"]}
    assert saved == {_activity_id(2)}


# ── registry matching of LinkedIn's auto-generated posts ─────────────────


async def test_an_echo_of_a_listing_the_registry_tracks_is_hidden_as_a_duplicate() -> None:
    world = World(**registry_kwargs(("Software Engineer", "Dublin, Ireland")))
    response = await world.search()

    assert response["counts"]["echoes_hidden"] == 1
    assert response["counts"]["shown"] == 4
    assert _activity_id(4) not in {s["activity_id"] for s in response["signals"]}
    _counts_add_up(response)


async def test_an_echo_the_registry_does_not_confirm_is_shown_and_labelled() -> None:
    cases: list[tuple[list[tuple[str, str | None]], str]] = [
        ([("Software Engineer", "Seattle, WA")], "unmatched"),  # same title, elsewhere
        ([("Data Analyst", "Dublin")], "unmatched"),  # a different title
        ([("Software Engineer", None)], "possible"),  # same title, location unknown
        ([], "unmatched"),  # nothing tracked
    ]
    for postings, expected in cases:
        response = await World(**registry_kwargs(*postings)).search()
        echo = next(s for s in response["signals"] if s["species"] == "ats_echo")
        assert echo["registry_match"] == expected, postings
        assert response["counts"]["echoes_hidden"] == 0


async def test_a_registry_that_cannot_be_read_leaves_the_match_unknown_not_unmatched() -> None:
    world = World(**registry_kwargs(("Software Engineer", "Dublin")))
    world.tables["job_registry_companies"].fail_with = APIError(
        {"message": "down", "code": "XX000", "details": None, "hint": None}
    )
    response = await world.search()

    echo = next(s for s in response["signals"] if s["species"] == "ats_echo")
    assert echo["registry_match"] is None
    assert response["counts"]["echoes_hidden"] == 0


async def test_the_registry_is_not_consulted_when_there_is_no_echo_to_match() -> None:
    world = World(**registry_kwargs(("Software Engineer", "Dublin")))
    # keep only the non-echo posts
    body = {
        **FIXTURE,
        "data": {"web": [e for e in FIXTURE["data"]["web"] if "#hiring" not in e["title"]]},
    }
    world.responses["firecrawl"] = body
    await world.search()
    assert world.tables["job_registry_companies"].calls == []


# ── provider selection ───────────────────────────────────────────────────


async def test_the_dev_setup_you_com_and_firecrawl_reaches_firecrawl_first() -> None:
    world = World(keys={"you_com": "yc-key", "firecrawl": "fc-key"})
    response = await world.search()

    assert response["provider"] == "firecrawl"
    assert world.providers_called == ["firecrawl"]  # You.com is last, never needed


async def test_a_failing_provider_is_skipped_for_the_next_in_order() -> None:
    world = World(
        keys={"brave": "b", "serper": "s", "firecrawl": "f", "you_com": "y"},
        responses={"brave": httpx.Response(500, json={"error": "x"})},
    )
    response = await world.search()

    # brave fails and firecrawl has the posts; you.com and serper are never reached
    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"


async def test_a_provider_that_answers_with_no_linkedin_posts_falls_through_too() -> None:
    """Brave answers 200 with only a jobs page: not a failure, but no LinkedIn
    post -- the search must go on to a provider that can see LinkedIn."""
    brave_body = {
        "web": {
            "results": [
                {"url": "https://www.linkedin.com/jobs/view/123", "title": "t", "description": "s"},
                {"url": "https://example.com/careers", "title": "t", "description": "s"},
            ]
        }
    }
    world = World(keys={"brave": "b", "firecrawl": "f"}, responses={"brave": brave_body})
    response = await world.search()

    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"
    assert response["counts"]["shown"] == 5


async def test_a_provider_that_returns_posts_ends_the_search_even_if_none_are_on_topic() -> None:
    """The honest empty answer for a company nobody is posting about: the
    index returned LinkedIn posts (so it can see LinkedIn), none of them about
    this company. Another provider would only spend more credits on the same
    question."""
    world = World(keys={"firecrawl": "f", "you_com": "y"}, company="Zorblax Robotics")
    response = await world.search()

    assert world.providers_called == ["firecrawl"]
    assert response["signals"] == []
    assert response["provider"] == "firecrawl"
    assert response["counts"]["off_topic_hidden"] == 10
    assert response["counts"]["shown"] == 0
    _counts_add_up(response)


async def test_a_you_com_only_user_gets_an_honest_empty_answer_not_an_error() -> None:
    world = World(keys={"you_com": "y"})
    response = await world.search()

    assert response["provider"] == "you_com"
    assert response["signals"] == []
    assert response["counts"] == {
        "raw_hits": 0,
        "rejected": 0,
        "duplicates": 0,
        "off_topic_hidden": 0,
        "echoes_hidden": 0,
        "job_seekers_hidden": 0,
        "too_old_hidden": 0,
        "shown": 0,
    }


async def test_when_every_provider_answers_empty_the_last_answer_is_the_result() -> None:
    world = World(
        keys={"firecrawl": "f", "you_com": "y"},
        responses={"firecrawl": load_fixture("firecrawl_empty.json")},
    )
    response = await world.search()

    assert world.providers_called == ["firecrawl", "you_com"]
    assert response["provider"] == "you_com"
    assert response["signals"] == []


async def test_when_every_provider_fails_the_error_is_retryable_provider_unavailable() -> None:
    secrets = {
        "brave": "brave-secret-key",
        "serper": "serper-secret-key",
        "firecrawl": "firecrawl-secret-key",
        "you_com": "youcom-secret-key",
    }
    world = World(keys=secrets, responses={p: httpx.Response(503, json={}) for p in HOSTS})
    with pytest.raises(ApiError) as caught:
        await world.search()

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert caught.value.retryable is True
    assert caught.value.details == {"providers_tried": ["brave", "firecrawl", "you_com", "serper"]}
    rendered = json.dumps(caught.value.to_body())
    assert not any(secret in rendered for secret in secrets.values())
    assert "site:" not in rendered


async def test_a_provider_that_times_out_is_a_failure_and_the_next_is_tried() -> None:
    world = World(
        keys={"brave": "b", "firecrawl": "f"},
        responses={"brave": httpx.ReadTimeout("slow")},
    )
    response = await world.search()
    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"


# ── ceilings ─────────────────────────────────────────────────────────────


async def test_a_request_never_makes_more_than_the_ceiling_of_provider_calls() -> None:
    world = World(
        keys={"brave": "b", "serper": "s", "firecrawl": "f", "you_com": "y"},
        responses={p: httpx.Response(500, json={}) for p in HOSTS},
    )
    with pytest.raises(ApiError):
        await world.search()
    assert len(world.transport.requests) == MAX_PROVIDER_CALLS_PER_REQUEST == 4
    assert sorted(world.providers_called) == sorted(HOSTS)  # one call per provider, no retries


async def test_no_new_provider_call_starts_after_the_request_deadline() -> None:
    # started, checked before brave (inside the budget), checked before firecrawl (past it)
    ticks = iter([0.0, 1.0, REQUEST_DEADLINE_SECONDS + 1.0])

    world = World(
        keys={"brave": "b", "firecrawl": "f"},
        responses={"brave": httpx.Response(500, json={})},
    )
    with pytest.raises(ApiError):
        await world.search(monotonic=lambda: next(ticks))

    assert world.providers_called == ["brave"]  # firecrawl was never started


# ── the cache ────────────────────────────────────────────────────────────


async def test_a_second_search_is_served_from_the_cache_without_a_provider_call() -> None:
    world = World()
    first = await world.search()
    calls_after_first = len(world.transport.requests)
    second = await world.search()

    assert calls_after_first == 1
    assert len(world.transport.requests) == 1  # no second provider call
    assert first["cached"] is False
    assert second["cached"] is True
    assert {**second, "cached": False} == first  # same answer, parsed again from the cache

    (row,) = world.tables["hiring_signal_query_cache"].rows
    assert row["provider"] == "firecrawl"
    assert set(row["response_json"]) == {"v", "hits"}
    assert len(row["response_json"]["hits"]) == 13


async def test_the_cache_key_matches_provider_query_freshness_bucket_and_day() -> None:
    world = World()
    await world.search(Freshness.THREE_DAYS)

    aq = build_application_query(
        company="Stripe",
        title="Software Engineer",
        location="Remote",
        freshness=Freshness.THREE_DAYS,
    )
    request = provider_freshness_request(
        "firecrawl", Freshness.THREE_DAYS, today=FIXTURE_NOW.date()
    )
    expected = cache_key("firecrawl", aq.query.query, request.param, FIXTURE_NOW.date())
    assert [r["query_key"] for r in world.tables["hiring_signal_query_cache"].rows] == [expected]


async def test_windows_that_share_a_provider_parameter_share_a_cache_row() -> None:
    """Serper is asked for a week for both "3 days" and "a week": the second
    search is free, and still trimmed to ITS window."""
    body = {
        "organic": [
            {"link": e["url"], "title": e["title"], "snippet": e["description"]}
            for e in FIXTURE["data"]["web"]
        ]
    }
    world = World(keys={"serper": "s"}, responses={"serper": body})
    week = await world.search(Freshness.WEEK)
    three = await world.search(Freshness.THREE_DAYS)

    assert len(world.transport.requests) == 1
    assert three["cached"] is True
    assert week["counts"]["too_old_hidden"] == 1
    assert three["counts"]["too_old_hidden"] == 1


async def test_an_expired_cache_row_is_a_miss_and_is_overwritten() -> None:
    world = World()
    await world.search()
    row = world.tables["hiring_signal_query_cache"].rows[0]
    row["created_at"] = (FIXTURE_NOW - timedelta(hours=25)).isoformat()

    again = await world.search()

    assert again["cached"] is False
    assert len(world.transport.requests) == 2
    assert len(world.tables["hiring_signal_query_cache"].rows) == 1
    assert (
        world.tables["hiring_signal_query_cache"].rows[0]["created_at"] == FIXTURE_NOW.isoformat()
    )


async def test_a_day_window_is_not_served_an_answer_older_than_its_own_ttl() -> None:
    """An answer 5 hours old is missing a fifth of the 24 hours it was asked
    about, so the 24-hour window (a 3-hour ttl) buys a fresh one -- while inside
    the 24-hour ceiling that the row itself may live to."""
    world = World()
    await world.search(Freshness.DAY)
    row = world.tables["hiring_signal_query_cache"].rows[0]
    row["created_at"] = (FIXTURE_NOW - timedelta(hours=5)).isoformat()

    again = await world.search(Freshness.DAY)

    assert again["cached"] is False
    assert len(world.transport.requests) == 2


async def test_the_week_window_is_still_served_the_same_five_hour_old_answer() -> None:
    world = World()
    await world.search(Freshness.WEEK)
    row = world.tables["hiring_signal_query_cache"].rows[0]
    row["created_at"] = (FIXTURE_NOW - timedelta(hours=5)).isoformat()

    again = await world.search(Freshness.WEEK)

    assert again["cached"] is True
    assert len(world.transport.requests) == 1


async def test_the_cache_never_lends_a_provider_the_user_has_no_key_for() -> None:
    """Another user's Firecrawl answer is cached for this very query; a user
    with only a Brave key must not be served it."""
    aq = build_application_query(
        company="Stripe", title="Software Engineer", location="Remote", freshness=Freshness.WEEK
    )
    key = cache_key("firecrawl", aq.query.query, "qdr:w", FIXTURE_NOW.date())
    stale = {
        "query_key": key,
        "provider": "firecrawl",
        "response_json": {"v": 1, "hits": []},
        "created_at": FIXTURE_NOW.isoformat(),
    }
    world = World(keys={"brave": "b"}, cache_rows=[stale])
    response = await world.search()

    assert world.providers_called == ["brave"]
    assert response["provider"] == "brave"


async def test_a_cache_that_cannot_be_read_or_written_never_fails_the_request() -> None:
    world = World()
    world.tables["hiring_signal_query_cache"].fail_with = APIError(
        {"message": "down", "code": "XX000", "details": None, "hint": None}
    )
    response = await world.search()

    assert response["cached"] is False
    assert response["counts"]["shown"] == 5


async def test_an_empty_answer_is_cached_too() -> None:
    world = World(keys={"you_com": "y"})
    await world.search()
    again = await world.search()

    assert len(world.transport.requests) == 1
    assert again["cached"] is True
    assert again["signals"] == []


# ── errors ───────────────────────────────────────────────────────────────


async def test_no_saved_search_key_is_setup_required_in_the_resolvers_shape() -> None:
    world = World(keys={})
    with pytest.raises(ApiError) as caught:
        await world.search()

    err = caught.value
    assert err.code == "SETUP_REQUIRED"
    assert err.capability == "hiring_signals"
    assert err.missing == ["search_credential"]
    assert err.settings_path == "/profile/integrations?capability=hiring_signals"
    assert "Settings" in err.message
    assert world.transport.requests == []


async def test_an_unknown_application_or_someone_elses_is_not_found() -> None:
    world = World()
    for app_id, user_id in [("30000000-0000-0000-0000-00000000dead", USER), (APP, OTHER_USER)]:
        with pytest.raises(ApiError) as caught:
            await search_application(
                as_client(world.supabase),
                world.http,
                user_id,
                app_id,
                freshness=Freshness.WEEK,
                now=FIXTURE_NOW,
            )
        assert caught.value.code == "NOT_FOUND"
    assert world.transport.requests == []


async def test_a_missing_snapshot_is_an_internal_error() -> None:
    world = World()
    world.tables["job_snapshots"].rows.clear()
    with pytest.raises(ApiError) as caught:
        await world.search()
    assert caught.value.code == "INTERNAL_ERROR"


async def test_an_application_with_no_company_is_invalid_input() -> None:
    world = World(company="   ")
    with pytest.raises(ApiError) as caught:
        await world.search()
    assert caught.value.code == "INVALID_INPUT"
    assert world.transport.requests == []


# ── role and locale ──────────────────────────────────────────────────────


async def test_with_no_derivable_role_role_match_is_unknown_and_order_is_by_freshness() -> None:
    response = await World(title="Senior").search()

    assert response["query_label"] == "Stripe -- last 7 days"
    assert {s["role_match"] for s in response["signals"]} == {None}
    posted = [s["posted_at"] for s in response["signals"]]
    assert posted == sorted(posted, reverse=True)


async def test_an_india_location_changes_the_vocabulary_that_is_queried() -> None:
    world = World(location="Bangalore, India")
    await world.search()
    assert '"immediate joiners"' in json.loads(world.transport.requests[0].content)["query"]


async def test_the_query_sent_to_the_provider_is_never_in_the_response() -> None:
    world = World()
    response = await world.search()
    sent = json.loads(world.transport.requests[0].content)["query"]
    assert sent.startswith("site:linkedin.com/posts")
    assert sent not in json.dumps(response)


# ── the hard line, end to end ────────────────────────────────────────────


@pytest.mark.parametrize("provider", list(HOSTS))
async def test_a_full_search_only_ever_contacts_provider_hosts(provider: str) -> None:
    """The fixture's results are all linkedin.com posts, jobs pages and links;
    a whole search (parse, filters, registry, saved flag, ranking) must never
    turn any of them into a request."""
    entries = FIXTURE["data"]["web"]
    bodies = {
        "firecrawl": FIXTURE,
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
        **registry_kwargs(("Software Engineer", "Dublin")),
    )

    response = await world.search()

    assert response["counts"]["raw_hits"] == 13
    assert set(world.transport.hosts) == {HOSTS[provider]}
    assert not any(
        h.endswith("linkedin.com") or h.endswith("lnkd.in") for h in world.transport.hosts
    )


# ── conservation, across scenarios ───────────────────────────────────────


@pytest.mark.parametrize("company", ["Stripe", "Acme", "Zorblax", "Riley", "Avery Placeholder"])
@pytest.mark.parametrize("freshness", list(Freshness))
@pytest.mark.parametrize("registry", [False, True])
async def test_every_hit_lands_in_exactly_one_bucket(
    company: str, freshness: Freshness, registry: bool
) -> None:
    kwargs = registry_kwargs(("Software Engineer", "Dublin")) if registry else {}
    response = await World(company=company, **kwargs).search(freshness)
    _counts_add_up(response)


# ── helpers for the regression tests below ───────────────────────────────

_PERSON_SLUG = "riley-sample-0d7b41e9_hiring"
_PERSON_TITLE = "Riley Sample's Post - LinkedIn"


def _single_post_world(
    company: str,
    text: str,
    *,
    slug: str = _PERSON_SLUG,
    title: str = _PERSON_TITLE,
    role: str = "Software Engineer",
    age_hours: float = 30,
) -> World:
    """A world whose only provider answer is ONE post: `text` is the post
    text as a search index joins it (age stamp, then fragments)."""
    body = firecrawl_body(post_entry(slug, title, f"1 day ago · {text}", age_hours=age_hours))
    return World(company=company, title=role, responses={"firecrawl": body})


def _echo(
    handle: str, page: str, role: str, place: str, *, age_hours: float = 45
) -> dict[str, Any]:
    """LinkedIn's auto-generated job-share post, authored by a company page."""
    return post_entry(
        f"{handle}_hiring",
        f"#hiring | {page} - LinkedIn",
        f"2 days ago · We're #hiring a new {role} in {place}. Apply today or share this post "
        "with your network.",
        age_hours=age_hours,
    )


_NESTLE_NFD = "Nestle" + chr(0x0301)

# ── BC-1 / YH-3 / BC-2: names with `&`, dots, legal suffixes, accents, CJK ──

NAME_CASES = [
    ("AT&T Inc.", "AT&T is hiring a Software Engineer in Dallas ... skills: fiber"),
    ("Procter & Gamble", "We're hiring a Software Engineer at Procter & Gamble ... more"),
    ("Amazon.com Services LLC", "Amazon is hiring a Software Engineer ... more"),
    ("Meta Platforms, Inc.", "Meta is hiring a Software Engineer in Menlo Park ... more"),
    ("Johnson & Johnson", "Johnson & Johnson is hiring a Software Engineer ... more"),
    ("Ernst & Young LLP", "Ernst & Young is hiring a Software Engineer ... more"),
    ("S&P Global", "S&P Global is hiring a Software Engineer ... more"),
    ("McDonald's Corporation", "McDonald's is hiring a Software Engineer ... more"),
    ("Advanced Micro Devices (AMD)", "AMD is hiring a Software Engineer ... more"),
    ("Scale AI", "ScaleAI is hiring a Software Engineer ... more"),
    ("Nestl" + chr(0x00E9), f"{_NESTLE_NFD} is hiring a Software Engineer ... more"),
    ("楽天", "楽天でソフトウェアエンジニアを採用中です ... more"),
    ("字节跳动", "字节跳动招聘算法工程师 ... more"),
]


@pytest.mark.parametrize(("company", "text"), NAME_CASES)
async def test_a_company_with_punctuation_accents_or_a_legal_suffix_finds_its_own_posts(
    company: str, text: str
) -> None:
    response = await _single_post_world(company, text).search()

    assert response["counts"]["off_topic_hidden"] == 0, (company, response["counts"])
    assert response["counts"]["shown"] == 1, (company, response["counts"])
    _counts_add_up(response)


@pytest.mark.parametrize(
    ("company", "phrase"),
    [
        ("AT&T Inc.", '"AT&T"'),
        ("Procter & Gamble", '"Procter & Gamble"'),
        ("Amazon.com Services LLC", '"Amazon.com Services"'),
        ("McDonald's Corporation", '"McDonald\'s"'),
        ("Meta Platforms, Inc.", '"Meta Platforms"'),
    ],
)
async def test_the_provider_is_asked_for_the_name_as_people_write_it(
    company: str, phrase: str
) -> None:
    world = _single_post_world(company, "we are hiring ... more")
    await world.search()

    query = world.transport.json_bodies()[0]["query"]
    assert phrase in query
    assert "atandt" not in query and "amazoncom" not in query and "mcdonalds" not in query


# ── YH-5: a person or another firm named like the company is not it ─────


@pytest.mark.parametrize(
    ("company", "slug", "title", "text"),
    [
        (
            "Block",
            "jordan-block-4b1c9e02_hiring-software-engineer",
            "Jordan Block's Post - LinkedIn",
            "We're hiring a Software Engineer at Northwind Traders in Denver. DM me ... more",
        ),
        (
            "Ford",
            "casey-ford-77a3d1f0_hiring",
            "Casey Ford's Post - LinkedIn",
            "We're hiring a Software Engineer at Contoso in Leeds. Apply through the page ... more",
        ),
        (
            "Square",
            "square-one-search-partners-99_hiring",
            "Square One Search Partners - LinkedIn",
            "We're hiring a Software Engineer for our client in Boston. Apply now ... more",
        ),
    ],
)
async def test_a_person_or_firm_whose_name_contains_the_company_is_not_the_company(
    company: str, slug: str, title: str, text: str
) -> None:
    response = await _single_post_world(company, text, slug=slug, title=title).search()

    assert response["counts"]["off_topic_hidden"] == 1, (company, response["counts"])
    assert response["counts"]["shown"] == 0
    _counts_add_up(response)


async def test_the_companys_own_page_is_the_company_even_when_the_text_never_says_so() -> None:
    """The author-name rule is the ONLY way this post matches: the title is the
    author's name (taken out of the title before it is read), the snippet never
    names the company and the handle is not the company's."""
    response = await _single_post_world(
        "Stripe",
        "We are growing fast and hiring across the board ... more",
        slug="stripe-payments-9f2c1a_hiring",
        title="Stripe - LinkedIn",
    ).search()

    assert response["counts"]["shown"] == 1
    assert response["signals"][0]["author_name"] == "Stripe"


# ── LP-1: an author field carries a name and nothing else ────────────────


async def test_a_sentence_in_the_authors_slot_is_never_sent_as_an_author() -> None:
    title = "#hiring #python | Stripe needs a senior Python engineer to rebuild billing, remote OK"
    response = await _single_post_world(
        "Stripe",
        "Stripe needs a senior Python engineer ... more",
        slug="stripe_hiring",
        title=title,
    ).search()

    assert response["counts"]["shown"] == 1
    assert response["signals"][0]["author_name"] is None
    assert "needs a senior Python engineer to rebuild" not in json.dumps(response)


async def test_bidi_and_zero_width_characters_are_stripped_from_an_author_name() -> None:
    title = "Jane " + chr(0x202E) + "Doe" + chr(0x200B) + "'s Post - LinkedIn"
    response = await _single_post_world(
        "Stripe",
        "Stripe is hiring a Software Engineer ... more",
        slug="jane-doe-1a2b3c_hiring",
        title=title,
    ).search()

    author = response["signals"][0]["author_name"]
    assert author == "Jane Doe"
    assert chr(0x202E) not in json.dumps(response, ensure_ascii=False)
    assert chr(0x200B) not in json.dumps(response, ensure_ascii=False)


# ── YH-4: a commenter's #OpenToWork line does not hide a hiring post ─────


@pytest.mark.parametrize(
    "text",
    [
        "Openings at Stripe: Software Engineer ... Priya S. Data Analyst | #OpenToWork | Python",
        "Stripe has an opening for a Software Engineer on my team ... Sam K. I'm open to work "
        "in payments engineering",
        "Come work with me at Stripe -- Software Engineer role open ... I am actively open to "
        "work, can refer me?",
    ],
)
async def test_a_job_seeker_line_after_the_opening_does_not_hide_a_hiring_post(text: str) -> None:
    response = await _single_post_world("Stripe", text).search()

    assert response["counts"]["job_seekers_hidden"] == 0
    assert response["counts"]["shown"] == 1
    # the contract has no `job_seeker` species: such a post is plainly unclassified
    assert response["signals"][0]["species"] == "unclassified"
    _counts_add_up(response)


async def test_a_post_whose_opening_says_it_is_a_job_seekers_is_still_hidden() -> None:
    response = await _single_post_world(
        "Stripe", "I'm looking for my next role. Stripe was a great team ... more"
    ).search()

    assert response["counts"]["job_seekers_hidden"] == 1
    assert response["counts"]["shown"] == 0


# ── YH-6 / YH-7 / BC-14: registry echo matching ──────────────────────────


def _registry(name: str, *postings: tuple[str, str | None, str]) -> dict[str, Any]:
    return {
        "registry_companies": [{"id": "co-1", "name": name}],
        "registry_postings": [
            {
                "id": f"post-{i}",
                "company_id": "co-1",
                "title": title,
                "location": location,
                "status": status,
                "first_seen": f"2026-09-1{i}T00:00:00+00:00",
            }
            for i, (title, location, status) in enumerate(postings)
        ],
    }


@pytest.mark.parametrize(
    ("company", "page", "handle", "registry_name"),
    [
        ("Scale AI", "Scale AI", "scaleai", "scaleai"),  # the registry stores a slug
        ("Meta", "Meta", "meta", "Meta Platforms, Inc."),  # ... or the legal name
        ("Stripe", "Stripe", "stripe", "STRIPE"),
    ],
)
async def test_an_echo_of_a_listing_the_registry_tracks_under_another_spelling_is_hidden(
    company: str, page: str, handle: str, registry_name: str
) -> None:
    body = firecrawl_body(_echo(handle, page, "Software Engineer", "San Francisco, CA"))
    world = World(
        company=company,
        responses={"firecrawl": body},
        **_registry(registry_name, ("Software Engineer", "San Francisco, CA, USA", "active")),
    )
    response = await world.search()

    assert response["counts"]["echoes_hidden"] == 1, (company, response["counts"])
    assert response["counts"]["shown"] == 0
    _counts_add_up(response)


async def test_an_echo_whose_page_the_registry_cannot_tie_to_a_company_is_unknown() -> None:
    """Nothing in the registry is this page's company (it may well track the
    listing under a name this matching cannot connect): "we cannot tell" is
    `None`, never `unmatched`."""
    body = firecrawl_body(_echo("scaleai", "Scale AI", "Software Engineer", "San Francisco, CA"))
    world = World(company="Scale AI", responses={"firecrawl": body})
    response = await world.search()

    assert response["counts"]["shown"] == 1
    assert response["signals"][0]["species"] == "ats_echo"
    assert response["signals"][0]["registry_match"] is None


async def test_an_echo_of_a_company_the_registry_has_but_not_that_listing_is_unmatched() -> None:
    body = firecrawl_body(_echo("stripe", "Stripe", "Software Engineer", "Dublin"))
    world = World(
        responses={"firecrawl": body},
        **_registry("Stripe, Inc.", ("Data Analyst", "Dublin", "active")),
    )
    response = await world.search()

    assert response["signals"][0]["registry_match"] == "unmatched"


async def test_a_closed_registry_posting_does_not_hide_an_echo() -> None:
    """A closed posting is not "a listing we already track"."""
    body = firecrawl_body(_echo("stripe", "Stripe", "Software Engineer", "Dublin, Ireland"))
    world = World(
        responses={"firecrawl": body},
        **_registry("Stripe, Inc.", ("Software Engineer", "Dublin, Ireland", "closed")),
    )
    response = await world.search()

    assert response["counts"]["echoes_hidden"] == 0
    assert response["signals"][0]["registry_match"] == "unmatched"


async def test_a_country_only_registry_location_does_not_hide_a_city_echo() -> None:
    """The registry says `United States`; the echo says `Austin, Texas`. The
    same title at the same company in another city is another opening, so this
    is `possible`, not a duplicate to hide."""
    body = firecrawl_body(
        _echo("stripe", "Stripe", "Software Engineer", "Austin, Texas, United States")
    )
    world = World(
        responses={"firecrawl": body},
        **_registry("Stripe, Inc.", ("Software Engineer", "United States", "active")),
    )
    response = await world.search()

    assert response["counts"]["echoes_hidden"] == 0
    assert response["signals"][0]["registry_match"] == "possible"


# ── BC-14: the payload never trusts a future-dated id ────────────────────


async def test_a_post_whose_id_decodes_to_a_time_in_the_future_has_no_posted_at() -> None:
    body = firecrawl_body(
        post_entry(
            _PERSON_SLUG, _PERSON_TITLE, "1 day ago · Stripe is hiring ... more", age_hours=-30
        )
    )
    response = await World(responses={"firecrawl": body}).search()

    assert response["counts"]["shown"] == 1
    assert response["signals"][0]["posted_at"] is None


# ── BC-10: an id with a leading zero is not a usable id ──────────────────


async def test_an_activity_id_with_a_leading_zero_is_not_a_usable_post() -> None:
    entry = post_entry(_PERSON_SLUG, _PERSON_TITLE, "1 day ago · Stripe is hiring ... more")
    entry["url"] = entry["url"].replace("-activity-", "-activity-000")
    response = await World(responses={"firecrawl": firecrawl_body(entry)}).search()

    assert response["counts"]["rejected"] == 1
    assert response["counts"]["shown"] == 0
    _counts_add_up(response)


# ── BC-3 / BC-14: a call gets only the time the request has left ─────────


async def test_a_provider_call_gets_only_the_time_left_in_the_request_budget() -> None:
    # the request started at 0; the check before the first call reads 20 s
    ticks = iter([0.0, REQUEST_DEADLINE_SECONDS - 5.0])
    world = World()
    await world.search(monotonic=lambda: next(ticks))

    timeout = world.transport.requests[0].extensions["timeout"]
    assert timeout["read"] == pytest.approx(5.0)
    assert CALL_TIMEOUT_SECONDS > 5.0


async def test_a_provider_call_is_capped_at_the_call_timeout_when_the_budget_is_larger() -> None:
    ticks = iter([0.0, 1.0])
    world = World()
    await world.search(monotonic=lambda: next(ticks))

    assert world.transport.requests[0].extensions["timeout"]["read"] == CALL_TIMEOUT_SECONDS


async def test_the_deadline_boundary_is_exact() -> None:
    at_deadline = iter([0.0, REQUEST_DEADLINE_SECONDS])
    world = World()
    with pytest.raises(ApiError) as caught:
        await world.search(monotonic=lambda: next(at_deadline))
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert world.transport.requests == []  # a call started AT the deadline would be one too many

    just_inside = iter([0.0, REQUEST_DEADLINE_SECONDS - 0.5])
    world = World()
    await world.search(monotonic=lambda: next(just_inside))
    assert len(world.transport.requests) == 1


# ── BC-3 / LP-4: a hostile body is a provider failure, and the next is tried ─


async def test_a_hostile_deeply_nested_body_is_a_failure_and_the_next_provider_is_tried() -> None:
    hostile = httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000)
    world = World(keys={"brave": "b", "firecrawl": "f"}, responses={"brave": hostile})
    response = await world.search()

    assert world.providers_called == ["brave", "firecrawl"]
    assert response["provider"] == "firecrawl"
