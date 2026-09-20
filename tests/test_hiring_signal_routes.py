"""Route-level tests for Hiring Signals P3: the four real FastAPI routes,
driven through `TestClient` (same convention as `test_saved_searches_routes.py`)
over the in-memory Supabase and mocked provider transport in
`hiring_signal_fakes`.

What is pinned here that the service and store tests cannot pin:

- the URL surface, status codes and error envelope a client actually sees;
- the two gates in front of every route -- the verified user (401) and the
  `DISABLE_HIRING_SIGNALS` flag (404 `FEATURE_DISABLED`, read on every
  request);
- request validation (422 `INVALID_INPUT` -- this backend's spelling of the
  contract's `INVALID_REQUEST`: `ErrorCode` has no `INVALID_REQUEST`, and the
  app-wide validation handler already answers every malformed body with
  `INVALID_INPUT`);
- that nothing but the contract's structured fields ever leaves the server: the
  WHOLE search response body is walked, every string leaf must sit at a
  contract path, and no window of any provider title or snippet may appear
  anywhere in it;
- ownership: one user can neither read, create against, nor delete another
  user's data.

Clock. The composed provider fixtures encode post times relative to
`FIXTURE_NOW`, so the service's own clock is frozen to it. The reference that
is patched is `hiring_signal_service.datetime` -- the one `search_application`
actually reads when the route calls it without a `now` (the R3 lesson: patch
the reference the code under test uses, not the one it happens to import
from).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, tzinfo
from typing import Any, Protocol, Self

import httpx
import pytest
from fastapi.testclient import TestClient
from hiring_signal_fakes import (
    APP,
    FIXTURE,
    FIXTURE_NOW,
    HOSTS,
    OTHER_USER,
    USER,
    World,
    credential_row,
)

from between_jobs.api import hiring_signal_saves_store, hiring_signal_service
from between_jobs.api.app import app
from between_jobs.api.app_state import get_hiring_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.hiring_signal_saves_store import MAX_QUERY_LABEL_CHARS, canonical_post_url
from between_jobs.api.hiring_signals import embed_url

SEARCH = f"/applications/{APP}/hiring-signals/search"
SAVES = f"/applications/{APP}/hiring-signals/saves"
OTHER_APP = "30000000-0000-0000-0000-000000000002"
OTHER_SNAPSHOT = "20000000-0000-0000-0000-000000000003"
UNKNOWN_SAVE = "40000000-0000-0000-0000-00000000dead"
ACTIVITY = "7506381452083381426"

ROUTES: list[tuple[str, str, dict[str, str] | None]] = [
    ("POST", SEARCH, None),
    ("POST", SAVES, {"activity_id": ACTIVITY}),
    ("GET", SAVES, None),
    ("DELETE", "/hiring-signals/saves/40000000-0000-0000-0000-000000000001", None),
]


# ── harness ──────────────────────────────────────────────────────────────


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls.fromtimestamp(FIXTURE_NOW.timestamp(), tz=tz or UTC)


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key-not-real")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-real")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "test-secret-not-real")
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS", raising=False)
    monkeypatch.setattr(hiring_signal_service, "datetime", _FrozenDatetime)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Any:
    yield
    app.dependency_overrides.clear()


class Caller:
    """Who the (overridden) auth dependency says is calling. Mutable, so one
    test can act as two different users against the same app."""

    def __init__(self, user_id: str = USER) -> None:
        self.user_id = user_id


def api(world: World, caller: Caller | None = None, *, authenticated: bool = True) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: world.supabase
    app.dependency_overrides[get_hiring_http_client] = lambda: world.http
    if authenticated:
        who = caller or Caller()
        app.dependency_overrides[require_user_id] = lambda: who.user_id
    return TestClient(app)


def add_other_users_application(world: World) -> None:
    """A second application, owned by `OTHER_USER`."""
    world.tables["applications"].rows.append(
        {
            "id": OTHER_APP,
            "user_id": OTHER_USER,
            "job_id": "job-2",
            "active_job_snapshot_id": OTHER_SNAPSHOT,
        }
    )
    world.tables["job_snapshots"].rows.append(
        {
            "id": OTHER_SNAPSHOT,
            "title": "Data Analyst",
            "company_name": "Acme",
            "location_text": None,
        }
    )


class _JsonResponse(Protocol):
    """What `error_of` needs of a response. `TestClient` answers with its own
    (vendored) httpx response type, which is not `httpx.Response`."""

    def json(self) -> Any: ...


def error_of(response: _JsonResponse) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    return error


def untouched(world: World) -> bool:
    """Neither the provider nor any table was reached."""
    return not world.transport.requests and not any(t.calls for t in world.tables.values())


# ── the URL surface ──────────────────────────────────────────────────────


def test_the_contract_routes_are_registered_with_their_status_codes() -> None:
    """Read from the app's own OpenAPI document rather than `app.routes`:
    `include_router` does not necessarily flatten a router's routes into
    `app.routes`, but the OpenAPI document is the surface a client sees."""
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    found = {
        (method.upper(), path): sorted(code for code in item["responses"] if code.startswith("2"))
        for path, methods in paths.items()
        if "hiring-signals" in path
        for method, item in methods.items()
    }
    assert found == {
        ("POST", "/applications/{application_id}/hiring-signals/search"): ["200"],
        # 201 for a new save; the 200 of an already-saved post is chosen at runtime
        ("POST", "/applications/{application_id}/hiring-signals/saves"): ["201"],
        ("GET", "/applications/{application_id}/hiring-signals/saves"): ["200"],
        ("DELETE", "/hiring-signals/saves/{save_id}"): ["204"],
        # P4, the standalone tab
        ("GET", "/hiring-signals/status"): ["200"],
        ("POST", "/hiring-signals/search"): ["200"],
        ("POST", "/hiring-signals/searches"): ["201"],  # 200 for an equivalent one, at runtime
        ("GET", "/hiring-signals/searches"): ["200"],
        ("DELETE", "/hiring-signals/searches/{search_id}"): ["204"],
        ("POST", "/hiring-signals/saves"): ["201"],  # 200 for one already saved, at runtime
        ("GET", "/hiring-signals/saves"): ["200"],
    }


# ── the two gates ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_requires_authentication(
    method: str, path: str, body: dict[str, str] | None
) -> None:
    world = World()
    client = api(world, authenticated=False)

    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert error_of(response)["code"] == "AUTH_REQUIRED"
    assert untouched(world)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize("value", ["1", "true", "0", "false", "anything"])
def test_every_route_is_a_feature_disabled_404_when_the_flag_is_set_to_anything(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, str] | None, value: str
) -> None:
    """The DISABLE_* convention is "set to any non-empty value" (`0` and
    `false` included), and the flag is checked BEFORE authentication: a
    switched-off feature does not confirm to an anonymous caller that it
    exists."""
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", value)
    world = World()
    client = api(world, authenticated=False)

    response = client.request(method, path, json=body)

    assert response.status_code == 404
    error = error_of(response)
    assert error["code"] == "FEATURE_DISABLED"
    assert error["retryable"] is False
    assert untouched(world)


def test_the_flag_is_read_on_every_request_and_empty_means_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    client = api(world)

    assert client.post(SEARCH).status_code == 200
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    assert client.post(SEARCH).status_code == 404
    assert client.get(SAVES).status_code == 404
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "")
    assert client.post(SEARCH).status_code == 200
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS")
    assert client.get(SAVES).status_code == 200


# ── search: request validation ───────────────────────────────────────────


def test_a_bare_post_means_the_default_window() -> None:
    response = api(World()).post(SEARCH)
    assert response.status_code == 200
    assert response.json()["freshness"] == "week"


@pytest.mark.parametrize("freshness", ["day", "3days", "week"])
def test_each_freshness_window_is_echoed(freshness: str) -> None:
    world = World()
    response = api(world).post(SEARCH, json={"freshness": freshness})
    assert response.status_code == 200
    assert response.json()["freshness"] == freshness
    assert response.json()["query_label"].endswith(
        {"day": "last 24 hours", "3days": "last 3 days", "week": "last 7 days"}[freshness]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"freshness": "month"},
        {"freshness": "Week"},
        {"freshness": ""},
        {"freshness": 7},
        {"freshness": None},
        {"freshness": ["day"]},
        {"freshness": "week", "query": "site:linkedin.com"},  # no client-supplied query
        {"provider": "brave"},  # no client-chosen provider
        ["week"],
    ],
)
def test_an_invalid_window_or_an_extra_field_is_a_422_and_spends_nothing(body: Any) -> None:
    world = World()
    response = api(world).post(SEARCH, json=body)

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.transport.requests == []


# ── search: not found / setup / provider failure ─────────────────────────


def test_an_unknown_application_is_not_found_and_spends_nothing() -> None:
    world = World()
    response = api(world).post(
        "/applications/30000000-0000-0000-0000-00000000dead/hiring-signals/search"
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"
    assert world.transport.requests == []


def test_someone_elses_application_is_the_same_not_found() -> None:
    world = World()
    response = api(world, Caller(OTHER_USER)).post(SEARCH)

    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"
    assert world.transport.requests == []


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_an_application_id_that_is_not_a_uuid_is_not_found_not_a_server_error(
    method: str,
) -> None:
    world = World()
    client = api(world)
    tail = "search" if method == "POST" else "saves"
    response = client.request(method, f"/applications/not-a-uuid/hiring-signals/{tail}")

    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"
    assert untouched(world)


def test_no_saved_search_key_is_setup_required_in_the_resolvers_shape() -> None:
    world = World(keys={})
    response = api(world).post(SEARCH)

    assert response.status_code == 409
    error = error_of(response)
    assert error["code"] == "SETUP_REQUIRED"
    assert error["capability"] == "hiring_signals"
    assert error["missing"] == ["search_credential"]
    assert error["settings_path"] == "/profile/integrations?capability=hiring_signals"
    assert "Settings" in error["message"]
    assert world.transport.requests == []


def test_a_key_saved_for_something_other_than_search_does_not_count() -> None:
    world = World(keys={})
    world.tables["provider_credentials"].rows.append(
        {
            "user_id": USER,
            "service": "llm",
            "provider": "firecrawl",
            "model": None,
            "base_url": None,
            "scope": None,
            "secret_encrypted": "enc:llm-key",
            "secret_2_encrypted": None,
        }
    )
    response = api(world).post(SEARCH)

    assert response.status_code == 409
    assert error_of(response)["code"] == "SETUP_REQUIRED"
    assert world.transport.requests == []


def test_providers_are_tried_in_order_and_the_first_answer_with_posts_ends_it() -> None:
    world = World(
        keys={"brave": "b-key", "serper": "s-key", "firecrawl": "f-key", "you_com": "y-key"},
        responses={"brave": httpx.Response(500, json={}), "firecrawl": httpx.ReadTimeout("slow")},
    )
    response = api(world).post(SEARCH)

    assert response.status_code == 200
    # brave fails, firecrawl times out, you.com answers with no posts and is the FIRST
    # to answer, serper is asked last and has none either: nobody has posts, so the
    # first answer is the one reported
    assert response.json()["provider"] == "you_com"
    assert world.providers_called == ["brave", "firecrawl", "you_com", "serper"]


def test_every_provider_failing_is_a_retryable_503_that_names_only_the_providers_tried() -> None:
    keys = {
        "brave": "brave-key",
        "serper": "serper-key",
        "firecrawl": "fc-key",
        "you_com": "yc-key",
    }
    world = World(keys=keys, responses={p: httpx.Response(503, json={}) for p in HOSTS})
    response = api(world).post(SEARCH)

    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["details"] == {"providers_tried": ["brave", "firecrawl", "you_com", "serper"]}
    assert world.providers_called == ["brave", "firecrawl", "you_com", "serper"]
    assert len(world.transport.requests) == 4  # one call per provider, no retry
    assert not any(secret in response.text for secret in keys.values())
    assert "site:" not in response.text


def test_an_application_with_no_company_is_a_422() -> None:
    world = World(company="   ")
    response = api(world).post(SEARCH)

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.transport.requests == []


# ── search: what a client is allowed to see ──────────────────────────────

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00")
_AGE = re.compile(r"(just now|\d+ (minute|hour|day|week|month)s? ago)")
_ALLOWED_STRING_PATHS = {
    "provider",
    "freshness",
    "query_label",
    "signals[].activity_id",
    "signals[].post_url",
    "signals[].embed_url",
    "signals[].author_name",
    "signals[].posted_at",
    "signals[].age_hint",
    "signals[].species",
    "signals[].registry_match",
}


def _string_leaves(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [
            leaf
            for key, child in value.items()
            for leaf in _string_leaves(child, f"{path}.{key}" if path else key)
        ]
    if isinstance(value, list):
        return [leaf for child in value for leaf in _string_leaves(child, f"{path}[]")]
    return [(path, value)] if isinstance(value, str) else []


def assert_search_body_is_structured(body: dict[str, Any]) -> None:
    """The whole body is contract fields with contract formats. A free-text
    leaf anywhere else (a snippet, a title, a raw provider field) fails here
    whatever its key is called."""
    assert set(body) == {"provider", "cached", "freshness", "query_label", "signals", "counts"}
    assert set(body["counts"]) == {
        "raw_hits",
        "rejected",
        "duplicates",
        "off_topic_hidden",
        "echoes_hidden",
        "job_seekers_hidden",
        "too_old_hidden",
        "shown",
    }
    assert all(type(n) is int and n >= 0 for n in body["counts"].values())
    assert body["counts"]["shown"] == len(body["signals"])
    assert isinstance(body["cached"], bool)
    assert re.fullmatch(r".+ -- (.+ -- )?last (24 hours|3 days|7 days)", body["query_label"])

    stray = {path for path, _ in _string_leaves(body)} - _ALLOWED_STRING_PATHS
    assert not stray, stray

    for signal in body["signals"]:
        assert set(signal) == {
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
        assert re.fullmatch(r"[0-9]{1,25}", signal["activity_id"])
        assert signal["embed_url"] == embed_url(signal["activity_id"])
        assert re.fullmatch(r"https://www\.linkedin\.com/posts/[^?#\s]+", signal["post_url"])
        assert signal["author_name"] is None or (
            isinstance(signal["author_name"], str) and 0 < len(signal["author_name"]) <= 100
        )
        assert signal["posted_at"] is None or _ISO.fullmatch(signal["posted_at"])
        assert signal["age_hint"] is None or _AGE.fullmatch(signal["age_hint"])
        assert signal["species"] in {"unclassified", "ats_echo", "referral_offer", "hiring_drive"}
        assert signal["comment_count"] is None or type(signal["comment_count"]) is int
        assert signal["role_match"] in (True, False, None)
        assert signal["registry_match"] in ("matched", "possible", "unmatched", None)
        assert isinstance(signal["saved"], bool)


def _windows(text: str, size: int = 30) -> list[str]:
    return [text[i : i + size] for i in range(max(1, len(text) - size + 1))]


def test_the_search_response_is_the_documented_structure() -> None:
    world = World()
    response = api(world).post(SEARCH, json={"freshness": "week"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert_search_body_is_structured(body)
    assert body["provider"] == "firecrawl"
    assert body["cached"] is False
    assert body["query_label"] == "Stripe -- software engineer -- last 7 days"
    assert body["counts"] == {
        "raw_hits": 13,
        "rejected": 2,
        "duplicates": 1,
        "off_topic_hidden": 3,
        "echoes_hidden": 0,
        "job_seekers_hidden": 1,
        "too_old_hidden": 1,
        "shown": 5,
    }
    # ranked: exact-role posts first, then the freshest
    assert [s["role_match"] for s in body["signals"]] == [True, True, True, False, False]


def test_no_provider_title_or_snippet_text_appears_anywhere_in_the_response() -> None:
    """Structural, over the ENTIRE serialized body (raw text and its
    ASCII-escaped re-encoding): not a key check, a content check -- no
    30-character window of any hit's title or snippet, and no trace of the
    provider query."""
    world = World()
    response = api(world).post(SEARCH)
    assert response.status_code == 200

    raw = response.text
    escaped = json.dumps(response.json(), ensure_ascii=True)
    for entry in FIXTURE["data"]["web"]:
        for text in (entry["title"], entry["description"]):
            for window in _windows(text):
                assert window not in raw, window
                assert window not in escaped, window

    sent_query = json.loads(world.transport.requests[0].content)["query"]
    assert sent_query not in raw
    for fragment in ("site:linkedin.com", "we're hiring", "we are hiring", '"stripe"'):
        assert fragment not in raw, fragment


def test_a_leaky_provider_field_would_be_caught_by_the_structure_check() -> None:
    """The checker is not vacuous: the same body with a snippet slipped into a
    signal (or the query into the label) fails it."""
    body = api(World()).post(SEARCH).json()
    assert_search_body_is_structured(body)

    leaked = json.loads(json.dumps(body))
    leaked["signals"][0]["snippet"] = "free text that a provider sent about the post"
    with pytest.raises(AssertionError):
        assert_search_body_is_structured(leaked)

    smuggled = json.loads(json.dumps(body))
    smuggled["signals"][0]["age_hint"] = "2 days ago -- free text that a provider sent"
    with pytest.raises(AssertionError):
        assert_search_body_is_structured(smuggled)

    relabelled = json.loads(json.dumps(body))
    relabelled["query_label"] = 'site:linkedin.com/posts ("we\'re hiring") "stripe"'
    with pytest.raises(AssertionError):
        assert_search_body_is_structured(relabelled)


def test_error_bodies_carry_no_provider_text_either() -> None:
    entries = FIXTURE["data"]["web"]
    world = World(
        keys={"firecrawl": "fc-key"},
        responses={
            "firecrawl": httpx.Response(
                502, json={"detail": entries[0]["description"], "title": entries[0]["title"]}
            )
        },
    )
    response = api(world).post(SEARCH)

    assert response.status_code == 503
    for entry in entries:
        for text in (entry["title"], entry["description"]):
            assert not any(window in response.text for window in _windows(text))


def test_a_second_click_is_served_from_the_cache_and_says_so() -> None:
    world = World()
    client = api(world)

    first = client.post(SEARCH).json()
    second = client.post(SEARCH).json()

    assert (first["cached"], second["cached"]) == (False, True)
    assert len(world.transport.requests) == 1
    assert [s["activity_id"] for s in second["signals"]] == [
        s["activity_id"] for s in first["signals"]
    ]
    assert_search_body_is_structured(second)
    (cache_row,) = world.tables["hiring_signal_query_cache"].rows
    assert cache_row["provider"] == "firecrawl"
    assert {k for hit in cache_row["response_json"]["hits"] for k in hit} == {
        "url",
        "title",
        "snippet",
    }


# ── saves: create, repeat, list, delete ──────────────────────────────────


def test_saving_creates_a_201_then_the_same_request_is_a_200_with_the_same_row() -> None:
    world = World()
    client = api(world)

    first = client.post(SAVES, json={"activity_id": ACTIVITY, "query_label": "Stripe -- x"})
    second = client.post(SAVES, json={"activity_id": ACTIVITY})

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json() == second.json()
    assert set(first.json()) == {"id", "activity_id", "post_url", "embed_url", "created_at"}
    assert first.json()["activity_id"] == ACTIVITY
    assert first.json()["post_url"] == canonical_post_url(ACTIVITY)
    assert first.json()["post_url"] == (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}"
    )
    assert first.json()["embed_url"] == embed_url(ACTIVITY)
    (row,) = world.tables["hiring_signal_saves"].rows
    assert set(row) == {
        "id",
        "created_at",
        "user_id",
        "application_id",
        "url",
        "activity_id",
        "discovered_via_query",
    }
    assert (row["user_id"], row["application_id"]) == (USER, APP)
    assert row["url"] == canonical_post_url(ACTIVITY)
    assert row["discovered_via_query"] == "Stripe -- x"  # the repeat did not overwrite it


def test_a_lost_save_race_is_a_200_with_the_winners_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two clicks look, neither sees a row, both insert: the unique index lets
    one through and the loser must get the winner's row back as a 200."""
    world = World()
    table = world.tables["hiring_signal_saves"]
    table.unique = [lambda r: (r["user_id"], r["application_id"], r["activity_id"])]
    client = api(world)
    winner = client.post(SAVES, json={"activity_id": ACTIVITY})
    assert winner.status_code == 201

    real_find = hiring_signal_saves_store._find
    lookups = 0

    async def blind_first_lookup(*args: Any, **kwargs: Any) -> Any:
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real_find(*args, **kwargs)

    monkeypatch.setattr(hiring_signal_saves_store, "_find", blind_first_lookup)
    loser = client.post(SAVES, json={"activity_id": ACTIVITY})

    assert loser.status_code == 200
    assert loser.json() == winner.json()
    assert len(table.rows) == 1
    assert table.insert_attempts == 2  # the loser really did hit the unique index


def test_the_label_is_stored_cleaned_and_capped_and_is_not_echoed() -> None:
    world = World()
    label = "Acme\x00 --\u202e role\n\t" + "x" * 500
    response = api(world).post(SAVES, json={"activity_id": ACTIVITY, "query_label": label})

    assert response.status_code == 201
    (row,) = world.tables["hiring_signal_saves"].rows
    stored = row["discovered_via_query"]
    assert len(stored) == MAX_QUERY_LABEL_CHARS
    assert stored.startswith("Acme -- role x")
    assert not any(ch in stored for ch in "\x00\u202e\n\t")
    assert "query_label" not in response.json()


@pytest.mark.parametrize("label", [None, "", "   "])
def test_an_empty_label_is_stored_as_null(label: str | None) -> None:
    world = World()
    api(world).post(SAVES, json={"activity_id": ACTIVITY, "query_label": label})
    assert world.tables["hiring_signal_saves"].rows[0]["discovered_via_query"] is None


@pytest.mark.parametrize(
    "activity_id",
    [
        "",
        "abc",
        "12a4",
        "1 2",
        "-1",
        "1.5",
        "0x10",
        "\u0661\u0662\u0663\u0664",  # Arabic-Indic digits: str.isdigit() accepts them
        "\uff11\uff12\uff13",  # full-width digits
        "9" * 26,
        f"{ACTIVITY}\n",
        f"{ACTIVITY}?x=1",
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}",
        12345,
        None,
        [ACTIVITY],
    ],
)
def test_an_activity_id_that_is_not_ascii_digits_is_a_422_and_writes_nothing(
    activity_id: Any,
) -> None:
    world = World()
    response = api(world).post(SAVES, json={"activity_id": activity_id})

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_saves"].rows == []
    assert world.tables["hiring_signal_saves"].insert_attempts == 0


def test_a_missing_activity_id_is_a_422() -> None:
    world = World()
    response = api(world).post(SAVES, json={"query_label": "x"})
    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_saves"].rows == []


@pytest.mark.parametrize("field", ["url", "post_url", "author_name", "title", "text", "snippet"])
def test_a_client_supplied_url_author_title_or_text_is_refused_not_ignored(field: str) -> None:
    """The server rebuilds the address from the id and stores nothing else, so
    a request that tries to supply any of them is a 422 rather than a silently
    dropped extra."""
    world = World()
    response = api(world).post(
        SAVES, json={"activity_id": ACTIVITY, field: "https://www.linkedin.com/posts/jane-doe_x"}
    )

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_saves"].rows == []


def test_saving_against_an_unknown_or_foreign_application_is_not_found_and_writes_nothing() -> None:
    world = World()
    add_other_users_application(world)
    client = api(world)

    for application_id in (OTHER_APP, "30000000-0000-0000-0000-00000000dead"):
        response = client.post(
            f"/applications/{application_id}/hiring-signals/saves", json={"activity_id": ACTIVITY}
        )
        assert response.status_code == 404
        assert error_of(response)["code"] == "NOT_FOUND"
    assert world.tables["hiring_signal_saves"].rows == []
    assert world.tables["hiring_signal_saves"].insert_attempts == 0


def test_listing_is_newest_first_and_only_this_users_saves_for_this_application() -> None:
    world = World()
    add_other_users_application(world)
    rows = [
        ("a", USER, APP, "111", "2026-09-19T10:00:00+00:00"),
        ("b", USER, APP, "222", "2026-09-19T12:00:00+00:00"),
        ("c", USER, APP, "333", "2026-09-19T11:00:00+00:00"),
        ("other-app", USER, OTHER_APP, "444", "2026-09-19T13:00:00+00:00"),
        ("other-user", OTHER_USER, OTHER_APP, "555", "2026-09-19T14:00:00+00:00"),
    ]
    for save_id, user, application, activity, created in rows:
        world.tables["hiring_signal_saves"].rows.append(
            {
                "id": save_id,
                "user_id": user,
                "application_id": application,
                "activity_id": activity,
                "url": "https://www.linkedin.com/posts/ignored-slug",
                "created_at": created,
            }
        )

    response = api(world).get(SAVES)

    assert response.status_code == 200
    saves = response.json()["saves"]
    assert [s["id"] for s in saves] == ["b", "c", "a"]
    assert all(
        set(s) == {"id", "activity_id", "post_url", "embed_url", "created_at"} for s in saves
    )
    # the address is rebuilt from the id, never read back from the stored url column
    assert {s["post_url"] for s in saves} == {canonical_post_url(n) for n in ("111", "222", "333")}


def test_an_empty_list_is_an_empty_list() -> None:
    response = api(World()).get(SAVES)
    assert response.status_code == 200
    assert response.json() == {"saves": []}


def test_delete_is_a_204_with_no_body_and_a_second_delete_is_a_404() -> None:
    world = World()
    client = api(world)
    save_id = client.post(SAVES, json={"activity_id": ACTIVITY}).json()["id"]

    first = client.delete(f"/hiring-signals/saves/{save_id}")
    second = client.delete(f"/hiring-signals/saves/{save_id}")

    assert first.status_code == 204
    assert first.content == b""
    assert second.status_code == 404
    assert error_of(second)["code"] == "NOT_FOUND"
    assert world.tables["hiring_signal_saves"].rows == []


@pytest.mark.parametrize("save_id", ["not-a-uuid", UNKNOWN_SAVE])
def test_deleting_an_unknown_or_malformed_id_is_a_404(save_id: str) -> None:
    response = api(World()).delete(f"/hiring-signals/saves/{save_id}")
    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"


# ── ownership and isolation ──────────────────────────────────────────────


def test_another_user_can_neither_read_create_against_nor_delete_my_saves() -> None:
    world = World()
    add_other_users_application(world)
    caller = Caller(USER)
    client = api(world, caller)
    mine = client.post(SAVES, json={"activity_id": ACTIVITY}).json()

    caller.user_id = OTHER_USER
    # my application is a 404 to them, for reading and for writing alike
    read = client.get(SAVES)
    write = client.post(SAVES, json={"activity_id": "111"})
    # deleting my save by its real id is indistinguishable from an unknown id
    delete_mine = client.delete(f"/hiring-signals/saves/{mine['id']}")
    delete_unknown = client.delete(f"/hiring-signals/saves/{UNKNOWN_SAVE}")

    for response in (read, write, delete_mine, delete_unknown):
        assert response.status_code == 404
        assert error_of(response)["code"] == "NOT_FOUND"
    assert error_of(delete_mine)["message"].replace(mine["id"], "<id>") == error_of(delete_unknown)[
        "message"
    ].replace(UNKNOWN_SAVE, "<id>")
    assert [r["id"] for r in world.tables["hiring_signal_saves"].rows] == [mine["id"]]

    # their own application works, and it shows nothing of mine
    own = client.post(
        f"/applications/{OTHER_APP}/hiring-signals/saves", json={"activity_id": ACTIVITY}
    )
    assert own.status_code == 201
    assert own.json()["id"] != mine["id"]  # the same post is a different save per user
    listed = client.get(f"/applications/{OTHER_APP}/hiring-signals/saves").json()["saves"]
    assert [s["id"] for s in listed] == [own.json()["id"]]

    caller.user_id = USER
    assert [s["id"] for s in client.get(SAVES).json()["saves"]] == [mine["id"]]
    assert client.delete(f"/hiring-signals/saves/{own.json()['id']}").status_code == 404


def test_the_saved_flag_follows_this_users_saves_across_search_save_and_delete() -> None:
    world = World()
    caller = Caller(USER)
    client = api(world, caller)

    first = client.post(SEARCH).json()
    target = first["signals"][0]["activity_id"]
    assert not any(s["saved"] for s in first["signals"])

    save = client.post(SAVES, json={"activity_id": target, "query_label": first["query_label"]})
    assert save.status_code == 201
    after = client.post(SEARCH).json()
    assert after["cached"] is True
    assert {s["activity_id"]: s["saved"] for s in after["signals"]}[target] is True
    assert sum(s["saved"] for s in after["signals"]) == 1

    assert client.delete(f"/hiring-signals/saves/{save.json()['id']}").status_code == 204
    assert not any(s["saved"] for s in client.post(SEARCH).json()["signals"])


def test_the_saved_flag_never_crosses_users_or_applications() -> None:
    """USER saves a post against THEIR application; OTHER_USER's search of
    THEIR own application returns that very post (the fixture holds an Acme
    hiring post) and it must not come back marked saved -- neither the user
    nor the application matches."""
    world = World()
    add_other_users_application(world)
    world.tables["provider_credentials"].rows.append(
        credential_row("firecrawl", "other-fc-key", user_id=OTHER_USER)
    )
    acme_post = next(
        e["url"].split("activity-")[1][:19]
        for e in FIXTURE["data"]["web"]
        if "acme-corp-is-looking" in e["url"]
    )
    caller = Caller(USER)
    client = api(world, caller)
    assert client.post(SAVES, json={"activity_id": acme_post}).status_code == 201

    caller.user_id = OTHER_USER
    response = client.post(f"/applications/{OTHER_APP}/hiring-signals/search")

    assert response.status_code == 200
    by_id = {s["activity_id"]: s["saved"] for s in response.json()["signals"]}
    assert acme_post in by_id  # the post really is in this user's results
    assert by_id[acme_post] is False


# ── BC-8: the flag wins over a body that is not even JSON ────────────────


@pytest.mark.parametrize(("method", "path"), [("POST", SEARCH), ("POST", SAVES)])
@pytest.mark.parametrize("payload", ["{not json", "\u0000", "[1,2"])
def test_a_switched_off_feature_answers_404_even_to_a_body_that_is_not_json(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, payload: str
) -> None:
    """FastAPI reads and validates a body BEFORE it resolves dependencies, so a
    dependency-based flag lost to a 422 here. The router's route class checks it
    before the body is read."""
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    world = World()
    client = api(world, authenticated=False)

    response = client.request(
        method, path, content=payload, headers={"content-type": "application/json"}
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "FEATURE_DISABLED"
    assert untouched(world)


def test_with_the_flag_off_a_body_that_is_not_json_is_still_a_422() -> None:
    world = World()
    response = api(world).post(
        SEARCH, content="{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"


# ── BC-9: only canonical uuids reach the database ────────────────────────

_CANONICAL = "30000000-0000-0000-0000-00000000abcd"
_FULLWIDTH_ZERO = chr(0xFF10)
_NON_CANONICAL_IDS = [
    "-".join(_FULLWIDTH_ZERO * n for n in (8, 4, 4, 4, 12)),  # full-width digits
    "{" + _CANONICAL + "}",
    "urn:uuid:" + _CANONICAL,
    _CANONICAL.replace("-", ""),
    _CANONICAL + " ",
    "0x" + _CANONICAL[2:],
]


@pytest.mark.parametrize("bad_id", _NON_CANONICAL_IDS)
@pytest.mark.parametrize("method", ["POST", "GET"])
def test_a_uuid_spelling_postgres_would_reject_is_a_404_and_never_reaches_it(
    bad_id: str, method: str
) -> None:
    """`uuid.UUID` accepts all of these; Postgres accepts none, and the failure
    would surface as an unstructured 500."""
    world = World()
    tail = "search" if method == "POST" else "saves"
    response = api(world).request(method, f"/applications/{bad_id}/hiring-signals/{tail}")

    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"
    assert untouched(world)


@pytest.mark.parametrize("bad_id", _NON_CANONICAL_IDS)
def test_delete_with_a_non_canonical_uuid_is_a_404_that_never_reaches_the_database(
    bad_id: str,
) -> None:
    world = World()
    response = api(world).delete(f"/hiring-signals/saves/{bad_id}")

    assert response.status_code == 404
    assert untouched(world)


def test_an_uppercase_canonical_uuid_is_still_a_uuid() -> None:
    world = World()
    response = api(world).get(SAVES.replace(APP, APP.upper()))
    assert response.status_code in (200, 404)  # reached the lookup, not the format gate
    assert world.tables["applications"].calls != []


# ── BC-10: one post, one save, whatever the spelling of its id ───────────


@pytest.mark.parametrize("activity_id", ["0007506381452083381426", "0", "00", "01"])
def test_an_activity_id_with_a_leading_zero_is_a_422_and_writes_nothing(
    activity_id: str,
) -> None:
    world = World()
    response = api(world).post(SAVES, json={"activity_id": activity_id})

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_saves"].rows == []


# ── LP-5 / BC-5: a malformed row never makes the list a 500 ──────────────


def test_a_row_that_is_not_a_well_formed_pointer_is_left_out_of_the_list() -> None:
    """The P1 row-level-security insert policy lets a client write any row
    directly; the list must survive one."""
    world = World()
    client = api(world)
    assert client.post(SAVES, json={"activity_id": ACTIVITY}).status_code == 201
    world.tables["hiring_signal_saves"].rows.append(
        {
            "id": "40000000-0000-0000-0000-00000000bad1",
            "user_id": USER,
            "application_id": APP,
            "url": "javascript:alert(1)",
            "activity_id": "abc",
            "source": "linkedin",
            "discovered_via_query": None,
            "created_at": "2026-09-19T18:00:00+00:00",
        }
    )

    response = client.get(SAVES)

    assert response.status_code == 200
    assert [s["activity_id"] for s in response.json()["saves"]] == [ACTIVITY]
