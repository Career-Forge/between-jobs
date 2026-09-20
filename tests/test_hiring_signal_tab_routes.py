"""Route-level tests for Hiring Signals P4: the seven real FastAPI routes of the
standalone tab, driven through `TestClient` (same convention as
`test_saved_searches_routes.py` and `test_hiring_signal_routes.py`) over the
in-memory Supabase and mocked provider transport in `hiring_signal_fakes`:

- `GET  /hiring-signals/status`
- `POST /hiring-signals/search`
- `POST` / `GET /hiring-signals/searches`, `DELETE /hiring-signals/searches/{id}`
- `POST` / `GET /hiring-signals/saves` (and the P3 `DELETE /hiring-signals/saves/{id}`,
  which must keep working for both kinds of save)

What is pinned here that the service and store tests cannot pin: the URL surface,
status codes and error envelope a client actually sees; the two gates in front of
every route -- the verified user (401) and the `DISABLE_HIRING_SIGNALS` flag (404
`FEATURE_DISABLED`, before authentication and before the body is read, with the one
exemption of the status route, which REPORTS the flag); request validation (422
`INVALID_INPUT`); that nothing but the contract's structured fields ever leaves the
server; and ownership: one user can neither read, create against, nor delete
another user's data.

Clock. The composed provider fixtures encode post times relative to `FIXTURE_NOW`,
so the service's own clock is frozen to it. The reference that is patched is
`hiring_signal_service.datetime` -- the one `search_tab` actually reads when the
route calls it without a `now` (the R3 lesson: patch the reference the code under
test uses, not the one it happens to import from).
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
    FIXTURE_NOW,
    HOSTS,
    OTHER_USER,
    USER,
    World,
    credential_row,
    firecrawl_body,
    load_fixture,
    post_entry,
)

from between_jobs.api import hiring_signal_service
from between_jobs.api.app import app
from between_jobs.api.app_state import get_hiring_http_client, get_supabase
from between_jobs.api.auth import require_user_id
from between_jobs.api.hiring_signal_saves_store import MAX_QUERY_LABEL_CHARS, canonical_post_url
from between_jobs.api.hiring_signal_searches_store import MAX_SAVED_SEARCHES
from between_jobs.api.hiring_signals import embed_url

ROLE_FIXTURE = load_fixture("firecrawl_role_posts.json")
STATUS = "/hiring-signals/status"
SEARCH = "/hiring-signals/search"
SEARCHES = "/hiring-signals/searches"
SAVES = "/hiring-signals/saves"
APP_SAVES = f"/applications/{APP}/hiring-signals/saves"
UNKNOWN_ID = "40000000-0000-0000-0000-00000000dead"
ZWSP = chr(0x200B)  # a zero-width space, built rather than written (see the feature modules)
ACTIVITY = "7506381452083381426"
BODY = {"query": "software engineer", "location": "Bengaluru", "freshness": "3days"}

GATED_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", SEARCH, BODY),
    ("POST", SEARCHES, {"query": "data engineer"}),
    ("GET", SEARCHES, None),
    ("DELETE", f"{SEARCHES}/{UNKNOWN_ID}", None),
    ("POST", SAVES, {"activity_id": ACTIVITY}),
    ("GET", SAVES, None),
]
"""Every route of the tab that is behind the feature flag."""
ALL_ROUTES = [("GET", STATUS, None), *GATED_ROUTES]


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
    """Who the (overridden) auth dependency says is calling. Mutable, so one test
    can act as two different users against the same app."""

    def __init__(self, user_id: str = USER) -> None:
        self.user_id = user_id


def api(world: World, caller: Caller | None = None, *, authenticated: bool = True) -> TestClient:
    app.dependency_overrides[get_supabase] = lambda: world.supabase
    app.dependency_overrides[get_hiring_http_client] = lambda: world.http
    if authenticated:
        who = caller or Caller()
        app.dependency_overrides[require_user_id] = lambda: who.user_id
    return TestClient(app)


def role_world(**kwargs: Any) -> World:
    return World(responses={"firecrawl": ROLE_FIXTURE}, **kwargs)


class _JsonResponse(Protocol):
    def json(self) -> Any: ...


def error_of(response: _JsonResponse) -> dict[str, Any]:
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    return error


def untouched(world: World) -> bool:
    """Neither the provider nor any table was reached."""
    return not world.transport.requests and not any(t.calls for t in world.tables.values())


# ── the two gates ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_every_route_of_the_tab_requires_authentication(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    world = World()
    client = api(world, authenticated=False)

    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert error_of(response)["code"] == "AUTH_REQUIRED"
    assert untouched(world)


@pytest.mark.parametrize(("method", "path", "body"), GATED_ROUTES)
@pytest.mark.parametrize("value", ["1", "true", "0", "false", "anything"])
def test_every_gated_route_is_a_feature_disabled_404_when_the_flag_is_set_to_anything(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    value: str,
) -> None:
    """`DISABLE_*` means "set to any non-empty value" (`0` and `false` included), and
    the flag is checked BEFORE authentication: a switched-off feature does not
    confirm to an anonymous caller that it exists."""
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", value)
    world = World()
    client = api(world, authenticated=False)

    response = client.request(method, path, json=body)

    assert response.status_code == 404
    error = error_of(response)
    assert error["code"] == "FEATURE_DISABLED"
    assert error["retryable"] is False
    assert untouched(world)


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", SEARCH), ("POST", SEARCHES), ("POST", SAVES)],
)
@pytest.mark.parametrize("payload", ["{not json", chr(0), "[1,2", '{"query": 5}', ""])
def test_a_switched_off_feature_answers_404_before_the_body_is_read(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, payload: str
) -> None:
    """FastAPI reads and validates a body BEFORE it resolves dependencies; the
    router's route class checks the flag before the body is read, so even a body
    that is not JSON (or is invalid for the model) gets the 404, not a 422."""
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    world = World()
    client = api(world, authenticated=False)

    response = client.request(
        method, path, content=payload, headers={"content-type": "application/json"}
    )

    assert response.status_code == 404
    assert error_of(response)["code"] == "FEATURE_DISABLED"
    assert untouched(world)


@pytest.mark.parametrize("path", [SEARCH, SEARCHES])
@pytest.mark.parametrize(
    "payload",
    [
        '{"query": "data \\ud800 engineer"}',  # a lone surrogate, as JSON escapes it
        '{"query": "data engineer", "location": NaN}',
        '{"query": "data engineer", "location": Infinity}',
        '{"query": "data engineer", "freshness": "\\ud800"}',
    ],
)
def test_text_the_response_cannot_encode_is_a_422_not_a_500(path: str, payload: str) -> None:
    """The validation handler used to echo the offending input back in the error body
    and could not encode a lone surrogate or a NaN, so a client's mistake was a bare
    500. These routes take free typed text, which is how it became reachable."""
    client = api(World())
    response = client.post(path, content=payload, headers={"content-type": "application/json"})

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"


def test_a_422_says_where_and_why_and_never_echoes_what_was_sent() -> None:
    marker = "secret-marker-9f3a"
    client = api(World())
    response = client.post(SEARCH, json={"query": "data engineer", "surprise": marker})

    assert response.status_code == 422
    assert marker not in response.text
    (problem,) = error_of(response)["details"]["errors"]
    assert set(problem) == {"type", "loc", "msg"}
    assert problem["loc"] == ["body", "surprise"]

    huge = client.post(SEARCH, json={"query": "a" * 300_000})
    assert huge.status_code == 422
    assert len(huge.content) < 2_000  # not 300 KB echoed back


def test_with_the_feature_on_a_body_that_is_not_json_is_a_422() -> None:
    response = api(World()).post(
        SEARCH, content="{not json", headers={"content-type": "application/json"}
    )
    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"


def test_the_flag_is_read_on_every_request_and_empty_means_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = role_world()
    client = api(world)

    assert client.post(SEARCH, json=BODY).status_code == 200
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    assert client.post(SEARCH, json=BODY).status_code == 404
    assert client.get(SEARCHES).status_code == 404
    assert client.get(SAVES).status_code == 404
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "")
    assert client.post(SEARCH, json=BODY).status_code == 200
    monkeypatch.delenv("DISABLE_HIRING_SIGNALS")
    assert client.get(SAVES).status_code == 200


def test_the_status_route_reports_the_flag_instead_of_being_blocked_by_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = World()
    client = api(world)

    assert client.get(STATUS).json() == {"enabled": True}
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    off = client.get(STATUS)
    assert (off.status_code, off.json()) == (200, {"enabled": False})
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "0")  # any non-empty value is off
    assert client.get(STATUS).json() == {"enabled": False}
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "")
    assert client.get(STATUS).json() == {"enabled": True}
    assert untouched(world)  # it reads an environment variable and nothing else


def test_the_status_route_still_requires_authentication_when_the_feature_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISABLE_HIRING_SIGNALS", "1")
    response = api(World(), authenticated=False).get(STATUS)
    assert response.status_code == 401
    assert error_of(response)["code"] == "AUTH_REQUIRED"


# ── search: request validation ───────────────────────────────────────────


def test_a_search_with_only_a_role_uses_the_default_window_of_three_days() -> None:
    response = api(role_world()).post(SEARCH, json={"query": "software engineer"})

    assert response.status_code == 200
    body = response.json()
    assert body["freshness"] == "3days"
    assert body["locale"] == "global"
    assert body["query_label"] == "software engineer -- last 3 days"


@pytest.mark.parametrize(
    ("freshness", "label"),
    [("day", "last 24 hours"), ("3days", "last 3 days"), ("week", "last 7 days")],
)
def test_each_freshness_window_is_echoed(freshness: str, label: str) -> None:
    response = api(role_world()).post(SEARCH, json={**BODY, "freshness": freshness})

    assert response.status_code == 200
    assert response.json()["freshness"] == freshness
    assert response.json()["query_label"].endswith(label)


@pytest.mark.parametrize(
    ("location", "locale", "expected"),
    [
        ("Bengaluru", None, "india"),
        ("Bengaluru", "global", "global"),
        ("Austin", None, "global"),
        ("Austin", "india", "india"),
        (None, None, "global"),
    ],
)
def test_the_locale_is_derived_from_the_place_unless_the_request_names_one(
    location: str | None, locale: str | None, expected: str
) -> None:
    body: dict[str, Any] = {"query": "software engineer", "location": location, "locale": locale}
    response = api(role_world()).post(SEARCH, json=body)

    assert response.status_code == 200
    assert response.json()["locale"] == expected


@pytest.mark.parametrize(
    "body",
    [
        {},  # no role
        {"query": ""},
        {"query": "   "},
        {"query": "\n\t"},
        {"query": "x" * 201},  # one over the limit
        {"query": "software engineer", "location": "x" * 101},  # one over the limit
        {"query": 5},
        {"query": None},
        {"query": ["software engineer"]},
        {"query": "software engineer", "location": 5},
        {"query": "software engineer", "freshness": "month"},
        {"query": "software engineer", "freshness": 3},
        {"query": "software engineer", "locale": "fr"},
        {"query": "software engineer", "company": "Stripe"},  # no company, ever
        {"query": "software engineer", "provider": "brave"},  # nothing picks a provider
        {"query": "software engineer", "provider_query": 'site:linkedin.com "x"'},
        {"query": "software engineer", "cache": False},
        {"query": "software engineer", "limit": 100},
    ],
)
def test_an_invalid_search_is_a_422_and_spends_and_reads_nothing(body: dict[str, Any]) -> None:
    world = role_world()
    response = api(world).post(SEARCH, json=body)

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert untouched(world)  # no provider call, no credential read


@pytest.mark.parametrize("body", [[], "software engineer", 5, None])
def test_a_search_body_that_is_not_an_object_is_a_422(body: Any) -> None:
    world = role_world()
    response = api(world).post(SEARCH, json=body)
    assert response.status_code == 422
    assert untouched(world)


def test_a_role_of_exactly_two_hundred_characters_is_allowed_and_a_place_of_one_hundred() -> None:
    body = {"query": ("engineer " * 30)[:200].strip(), "location": ("Pune " * 30)[:100].strip()}
    response = api(role_world()).post(SEARCH, json=body)
    assert response.status_code in (200, 422)  # the length gate passes: what is left is the words
    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"query": "!!! ???"},  # nothing to search
        {"query": "a"},  # no word of two characters
        {"query": "x" * 61},  # a word longer than a query term may be
        {"query": "data engineer", "location": "###"},
        {"query": "data engineer", "location": "y" * 61},
    ],
)
def test_text_that_passes_the_length_gate_but_cannot_be_searched_is_a_422(
    body: dict[str, Any],
) -> None:
    world = role_world()
    response = api(world).post(SEARCH, json=body)

    assert response.status_code == 422
    error = error_of(response)
    assert error["code"] == "INVALID_INPUT"
    assert not world.transport.requests
    # the message tells the person what to fix and never echoes what they typed
    assert body["query"] not in error["message"] or len(body["query"]) < 3


def test_a_blank_place_means_no_place() -> None:
    response = api(role_world()).post(SEARCH, json={"query": "software engineer", "location": "  "})
    assert response.status_code == 200
    assert response.json()["query_label"] == "software engineer -- last 3 days"


def test_a_query_that_tries_to_inject_an_operator_reaches_the_provider_as_words() -> None:
    world = role_world()
    response = api(world).post(
        SEARCH,
        json={
            "query": 'software engineer") OR site:evil.example ("x',
            "location": 'Pune") -site:a',
        },
    )

    assert response.status_code == 200
    (sent,) = world.transport.json_bodies()
    assert sent["query"].count("site:") == 1
    assert sent["query"].count('"') % 2 == 0


# ── search: SETUP_REQUIRED, providers, errors ────────────────────────────


def test_no_saved_search_key_is_setup_required_in_the_resolvers_shape() -> None:
    world = World(keys={})
    response = api(world).post(SEARCH, json=BODY)

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
    row = credential_row("firecrawl", "llm-key", user_id=USER)
    world.tables["provider_credentials"].rows.append({**row, "service": "llm"})
    response = api(world).post(SEARCH, json=BODY)
    assert response.status_code == 409
    assert error_of(response)["code"] == "SETUP_REQUIRED"


def test_another_users_key_is_not_this_users_key() -> None:
    world = World(keys={})
    world.tables["provider_credentials"].rows.append(
        credential_row("firecrawl", "theirs", user_id=OTHER_USER)
    )
    response = api(world).post(SEARCH, json=BODY)
    assert response.status_code == 409
    assert world.transport.requests == []


def test_every_provider_failing_is_a_retryable_503_that_names_only_the_providers_tried() -> None:
    keys = {
        "brave": "brave-key",
        "serper": "serper-key",
        "firecrawl": "fc-key",
        "you_com": "yc-key",
    }
    world = World(keys=keys, responses={p: httpx.Response(503, json={}) for p in HOSTS})
    response = api(world).post(SEARCH, json=BODY)

    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "PROVIDER_UNAVAILABLE"
    assert error["retryable"] is True
    assert error["details"] == {"providers_tried": ["brave", "firecrawl", "you_com", "serper"]}
    assert len(world.transport.requests) == 4  # one call per provider, no retry
    assert not any(secret in response.text for secret in keys.values())
    assert "site:" not in response.text


def test_providers_are_tried_in_order_and_the_first_answer_with_posts_ends_it() -> None:
    world = World(
        keys={"brave": "b-key", "serper": "s-key", "firecrawl": "f-key", "you_com": "y-key"},
        responses={"brave": httpx.Response(500, json={}), "firecrawl": httpx.ReadTimeout("slow")},
    )
    response = api(world).post(SEARCH, json=BODY)

    assert response.status_code == 200
    # brave fails, firecrawl times out, you.com answers with no posts and is the FIRST
    # to answer, serper is asked last and has none either: nobody has posts, so the
    # first answer is the one reported
    assert response.json()["provider"] == "you_com"
    assert world.providers_called == ["brave", "firecrawl", "you_com", "serper"]


def test_a_failing_first_provider_falls_back_to_the_next_that_answers() -> None:
    world = World(
        keys={"brave": "b", "firecrawl": "f"},
        responses={"brave": httpx.Response(500, json={}), "firecrawl": ROLE_FIXTURE},
    )
    response = api(world).post(SEARCH, json=BODY)

    assert response.status_code == 200
    assert response.json()["provider"] == "firecrawl"
    assert world.providers_called == ["brave", "firecrawl"]


def test_a_you_com_only_user_gets_an_honest_empty_answer_not_an_error() -> None:
    response = api(World(keys={"you_com": "yc"})).post(SEARCH, json=BODY)
    assert response.status_code == 200
    body = response.json()
    assert (body["provider"], body["signals"]) == ("you_com", [])
    assert set(body["counts"].values()) == {0}


# ── search: what a client is allowed to see ──────────────────────────────

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00")
_AGE = re.compile(r"(just now|\d+ (minute|hour|day|week|month)s? ago)")
_ALLOWED_STRING_PATHS = {
    "provider",
    "freshness",
    "locale",
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
_COUNT_FIELDS = {
    "raw_hits",
    "rejected",
    "duplicates",
    "role_mismatch_hidden",
    "echoes_hidden",
    "job_seekers_hidden",
    "too_old_hidden",
    "shown",
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


def assert_tab_body_is_structured(body: dict[str, Any]) -> None:
    """The whole body is contract fields with contract formats. A free-text leaf
    anywhere else (a snippet, a title, a raw provider field) fails here whatever its
    key is called."""
    assert set(body) == {
        "provider",
        "cached",
        "freshness",
        "locale",
        "query_label",
        "signals",
        "counts",
    }
    assert set(body["counts"]) == _COUNT_FIELDS
    counts = body["counts"]
    assert all(type(n) is int and n >= 0 for n in counts.values())
    assert counts["shown"] == len(body["signals"])
    assert counts["raw_hits"] == counts["shown"] + sum(
        counts[k] for k in _COUNT_FIELDS - {"raw_hits", "shown"}
    )
    assert isinstance(body["cached"], bool)
    assert body["locale"] in {"india", "global"}
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
            "registry_match",
            "aggregator",
            "saved",
        }
        assert re.fullmatch(r"[1-9][0-9]{0,24}", signal["activity_id"])
        assert signal["embed_url"] == embed_url(signal["activity_id"])
        assert re.fullmatch(r"https://www\.linkedin\.com/posts/[^?#\s]+", signal["post_url"])
        assert signal["author_name"] is None or (
            isinstance(signal["author_name"], str) and 0 < len(signal["author_name"]) <= 100
        )
        assert signal["posted_at"] is None or _ISO.fullmatch(signal["posted_at"])
        assert signal["age_hint"] is None or _AGE.fullmatch(signal["age_hint"])
        assert signal["species"] in {"unclassified", "ats_echo", "referral_offer", "hiring_drive"}
        assert signal["comment_count"] is None or type(signal["comment_count"]) is int
        assert signal["registry_match"] in ("possible", "unmatched", None)
        assert signal["aggregator"] in (True, False, None)
        assert isinstance(signal["saved"], bool)


def _windows(text: str, size: int = 30) -> list[str]:
    return [text[i : i + size] for i in range(max(1, len(text) - size + 1))]


def test_the_search_response_is_the_documented_structure() -> None:
    world = role_world()
    response = api(world).post(SEARCH, json=BODY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert_tab_body_is_structured(body)
    assert body["provider"] == "firecrawl"
    assert body["cached"] is False
    assert body["freshness"] == "3days"
    assert body["locale"] == "india"
    assert body["query_label"] == "software engineer -- Bengaluru -- last 3 days"
    assert body["counts"] == {
        "raw_hits": 20,
        "rejected": 1,
        "duplicates": 1,
        "role_mismatch_hidden": 1,
        "echoes_hidden": 0,
        "job_seekers_hidden": 1,
        "too_old_hidden": 1,
        "shown": 15,
    }
    # ranked: non-aggregator accounts first, then the freshest
    flags = [s["aggregator"] is True for s in body["signals"]]
    assert flags == sorted(flags) and flags.count(True) == 5


_SENTINEL = "ZQXTITLE"
_HIRING_TEXT = "1 day ago \u00b7 We're hiring a software engineer in Bengaluru ... message me"


def _title_shaped_posts() -> list[dict[str, Any]]:
    """One post per title shape the parser reads an author out of, each with a
    sentinel in the part of the title that is POST TEXT and never in the url's
    handle. Every one of them has the role in its snippet, so all are shown."""
    entries = [
        # <Name>'s Post: the marker makes everything before it "the name"
        post_entry(
            "meera-fixture-91d3c0aa_x",
            f"{_SENTINEL} Hiring Backend Engineers Meera Fixture's Post - LinkedIn",
            _HIRING_TEXT,
            sequence=11,
        ),
        # <content> posted
        post_entry(
            "priya-testwell-2f7a91c3_x",
            f"{_SENTINEL} Hiring Backend Software Engineers Bengaluru posted",
            _HIRING_TEXT,
            sequence=12,
        ),
        # #hiring | <last segment>: trusted because the segment before is a hashtag block
        post_entry(
            "arjun-placeholder_x",
            f"#hiring | {_SENTINEL} Software Engineer Openings At Northwind",
            _HIRING_TEXT,
            sequence=13,
        ),
        # <content> | <last segment> | N comments: trusted because a count follows it
        post_entry(
            "kabir-sample-c0a81e55_x",
            f"We are hiring | {_SENTINEL} Team Openings | 12 comments",
            _HIRING_TEXT,
            sequence=14,
        ),
        # <Name> - <topic>: one word of the first part in the handle is enough for the parser
        post_entry(
            "jane-doe-1a2b3c4d_x",
            f"{_SENTINEL} Jane Doe - Software Engineer",
            _HIRING_TEXT,
            sequence=15,
        ),
        # a url that names no author: nothing to corroborate a name against
        post_entry(
            "x", f"#hiring | {_SENTINEL} Northwind Labs - LinkedIn", _HIRING_TEXT, sequence=16
        ),
        # the control: a real name, in the same shape as the first
        post_entry(
            "diya-sampleton-84f0b6e1_x",
            "Diya Sampleton's Post - LinkedIn",
            _HIRING_TEXT,
            sequence=17,
        ),
    ]
    entries[5]["url"] = entries[5]["url"].replace("/posts/x-activity-", "/posts/activity-")
    return entries


def test_no_title_text_reaches_the_response_through_the_author_field() -> None:
    """Every title shape the parser takes an author out of, with a sentinel in the
    post's own title text: it must never come back, in `author_name` or anywhere.
    The parser trusts some of these shapes without checking the url, so the
    response's author is only ever a name that agrees with the handle."""
    world = World(responses={"firecrawl": firecrawl_body(*_title_shaped_posts())})
    response = api(world).post(SEARCH, json={"query": "software engineer", "freshness": "week"})

    assert response.status_code == 200
    body = response.json()
    assert body["counts"]["shown"] == 7  # every shape is really in the response
    assert _SENTINEL.lower() not in response.text.lower()
    authors = [s["author_name"] for s in body["signals"]]
    assert authors.count(None) == 6
    assert [a for a in authors if a is not None] == ["Diya Sampleton"]


def test_no_provider_title_or_snippet_text_appears_anywhere_in_the_response() -> None:
    """Structural, over the ENTIRE serialized body (raw text and its ASCII-escaped
    re-encoding): not a key check, a content check -- no 30-character window of any
    hit's title or snippet, and no trace of the provider query."""
    world = role_world()
    response = api(world).post(SEARCH, json=BODY)
    assert response.status_code == 200

    raw = response.text
    escaped = json.dumps(response.json(), ensure_ascii=True)
    for entry in ROLE_FIXTURE["data"]["web"]:
        for text in (entry["title"], entry["description"]):
            for window in _windows(text):
                assert window not in raw, window
                assert window not in escaped, window

    sent_query = json.loads(world.transport.requests[0].content)["query"]
    assert sent_query not in raw
    for fragment in ("site:linkedin.com", "we're hiring", "immediate joiners", "notice period"):
        assert fragment not in raw.lower(), fragment


def test_a_leaky_provider_field_would_be_caught_by_the_structure_check() -> None:
    """The checker is not vacuous: the same body with a snippet slipped into a signal
    (or the query into the label) fails it."""
    body = api(role_world()).post(SEARCH, json=BODY).json()
    assert_tab_body_is_structured(body)

    leaked = json.loads(json.dumps(body))
    leaked["signals"][0]["snippet"] = "free text that a provider sent about the post"
    with pytest.raises(AssertionError):
        assert_tab_body_is_structured(leaked)

    smuggled = json.loads(json.dumps(body))
    smuggled["signals"][0]["age_hint"] = "2 days ago -- free text that a provider sent"
    with pytest.raises(AssertionError):
        assert_tab_body_is_structured(smuggled)

    relabelled = json.loads(json.dumps(body))
    relabelled["query_label"] = 'site:linkedin.com/posts ("we\'re hiring") "software engineer"'
    with pytest.raises(AssertionError):
        assert_tab_body_is_structured(relabelled)

    unbalanced = json.loads(json.dumps(body))
    unbalanced["counts"]["shown"] -= 1
    with pytest.raises(AssertionError):
        assert_tab_body_is_structured(unbalanced)


def test_error_bodies_carry_no_provider_text_either() -> None:
    entries = ROLE_FIXTURE["data"]["web"]
    world = World(
        keys={"firecrawl": "fc-key"},
        responses={
            "firecrawl": httpx.Response(
                502, json={"detail": entries[0]["description"], "title": entries[0]["title"]}
            )
        },
    )
    response = api(world).post(SEARCH, json=BODY)

    assert response.status_code == 503
    for entry in entries:
        for text in (entry["title"], entry["description"]):
            assert not any(window in response.text for window in _windows(text))


def test_a_second_click_is_served_from_the_cache_and_says_so() -> None:
    world = role_world()
    client = api(world)

    first = client.post(SEARCH, json=BODY).json()
    second = client.post(SEARCH, json=BODY).json()

    assert (first["cached"], second["cached"]) == (False, True)
    assert len(world.transport.requests) == 1
    assert [s["activity_id"] for s in second["signals"]] == [
        s["activity_id"] for s in first["signals"]
    ]
    assert_tab_body_is_structured(second)
    (row,) = world.tables["hiring_signal_query_cache"].rows
    assert {k for hit in row["response_json"]["hits"] for k in hit} == {"url", "title", "snippet"}


def test_a_search_writes_nothing_but_its_cache_row() -> None:
    """Pull-only and read-only: it never saves a search, a post, or anything else."""
    world = role_world()
    api(world).post(SEARCH, json=BODY)

    assert world.tables["hiring_signal_searches"].rows == []
    assert world.tables["hiring_signal_saves"].rows == []
    assert len(world.tables["hiring_signal_query_cache"].rows) == 1


def test_the_saved_flag_follows_this_users_standalone_saves_across_search_save_and_delete() -> None:
    world = role_world()
    client = api(world)

    first = client.post(SEARCH, json=BODY).json()
    target = first["signals"][0]["activity_id"]
    assert not any(s["saved"] for s in first["signals"])

    saved = client.post(SAVES, json={"activity_id": target})
    assert saved.status_code == 201
    after = client.post(SEARCH, json=BODY).json()
    assert {s["activity_id"] for s in after["signals"] if s["saved"]} == {target}

    assert client.delete(f"{SAVES}/{saved.json()['id']}").status_code == 204
    gone = client.post(SEARCH, json=BODY).json()
    assert not any(s["saved"] for s in gone["signals"])


def test_the_saved_flag_never_crosses_users_or_applications() -> None:
    world = role_world()
    caller = Caller()
    client = api(world, caller)
    target = client.post(SEARCH, json=BODY).json()["signals"][0]["activity_id"]

    caller.user_id = OTHER_USER  # somebody else saves it
    assert client.post(SAVES, json={"activity_id": target}).status_code == 201
    caller.user_id = USER
    # and one of my own, but for an application
    assert client.post(APP_SAVES, json={"activity_id": target}).status_code == 201

    body = client.post(SEARCH, json=BODY).json()
    assert not any(s["saved"] for s in body["signals"])  # neither counts on the tab


# ── saved searches ───────────────────────────────────────────────────────


def test_saving_a_search_is_a_201_and_the_same_request_again_is_a_200_with_the_same_row() -> None:
    world = World()
    client = api(world)
    payload = {"query": "data engineer", "location": "Pune"}

    first = client.post(SEARCHES, json=payload)
    second = client.post(SEARCHES, json=payload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(world.tables["hiring_signal_searches"].rows) == 1
    body = first.json()
    assert set(body) == {"id", "query", "location", "created_at"}
    assert (body["query"], body["location"]) == ("data engineer", "Pune")
    assert re.fullmatch(r"[0-9a-f-]{36}", body["id"])
    assert _ISO.fullmatch(body["created_at"]) or body["created_at"]


@pytest.mark.parametrize(
    "variant",
    [
        {"query": "DATA ENGINEER", "location": "pune"},
        {"query": "  Data   Engineer  ", "location": " PUNE "},
    ],
)
def test_case_and_whitespace_variants_are_the_same_search_and_answer_200(
    variant: dict[str, str],
) -> None:
    world = World()
    client = api(world)
    first = client.post(SEARCHES, json={"query": "data engineer", "location": "Pune"})

    again = client.post(SEARCHES, json=variant)

    assert again.status_code == 200
    assert again.json() == first.json()
    assert len(world.tables["hiring_signal_searches"].rows) == 1


def test_no_place_and_a_blank_place_are_the_same_search() -> None:
    world = World()
    client = api(world)
    first = client.post(SEARCHES, json={"query": "data engineer"})
    blank = client.post(SEARCHES, json={"query": "data engineer", "location": "  "})
    null = client.post(SEARCHES, json={"query": "data engineer", "location": None})

    assert first.status_code == 201
    assert (blank.status_code, null.status_code) == (200, 200)
    assert first.json()["location"] is None
    assert len(world.tables["hiring_signal_searches"].rows) == 1


def test_a_stored_search_is_the_cleaned_text_and_nothing_else() -> None:
    world = World()
    body = api(world).post(
        SEARCHES, json={"query": f"  Data{ZWSP}   Engineer ", "location": " Pune,   Maharashtra "}
    )
    assert body.status_code == 201
    (row,) = world.tables["hiring_signal_searches"].rows
    assert (row["query"], row["location"]) == ("Data Engineer", "Pune, Maharashtra")
    assert set(row) == {"id", "user_id", "query", "location", "created_at"}


def test_the_list_is_newest_first_and_only_this_users_searches() -> None:
    world = World()
    caller = Caller()
    client = api(world, caller)
    for query in ("first role", "second role", "third role"):
        assert client.post(SEARCHES, json={"query": query}).status_code == 201
    # time moves on between the three (the fake stamps rows with the wall clock)
    for offset, row in enumerate(world.tables["hiring_signal_searches"].rows):
        row["created_at"] = f"2026-09-18T12:0{offset}:00+00:00"
    caller.user_id = OTHER_USER
    client.post(SEARCHES, json={"query": "somebody else's role"})
    caller.user_id = USER

    listed = client.get(SEARCHES)

    assert listed.status_code == 200
    assert set(listed.json()) == {"searches"}
    assert [s["query"] for s in listed.json()["searches"]] == [
        "third role",
        "second role",
        "first role",
    ]


def test_an_empty_list_is_an_empty_list() -> None:
    response = api(World()).get(SEARCHES)
    assert (response.status_code, response.json()) == (200, {"searches": []})


def test_delete_is_a_204_with_no_body_and_a_second_delete_is_a_404() -> None:
    world = World()
    client = api(world)
    made = client.post(SEARCHES, json={"query": "data engineer"}).json()

    first = client.delete(f"{SEARCHES}/{made['id']}")
    second = client.delete(f"{SEARCHES}/{made['id']}")

    assert (first.status_code, first.content) == (204, b"")
    assert second.status_code == 404
    assert error_of(second)["code"] == "NOT_FOUND"
    assert world.tables["hiring_signal_searches"].rows == []


def test_another_user_can_neither_see_nor_delete_my_search_and_the_answer_is_a_plain_404() -> None:
    world = World()
    caller = Caller()
    client = api(world, caller)
    mine = client.post(SEARCHES, json={"query": "data engineer"}).json()

    caller.user_id = OTHER_USER
    assert client.get(SEARCHES).json() == {"searches": []}
    foreign = client.delete(f"{SEARCHES}/{mine['id']}")
    unknown = client.delete(f"{SEARCHES}/{UNKNOWN_ID}")

    assert foreign.status_code == unknown.status_code == 404
    assert error_of(foreign)["code"] == error_of(unknown)["code"] == "NOT_FOUND"
    # indistinguishable: the same message shape, with only the id differing
    assert error_of(foreign)["message"].replace(mine["id"], "X") == error_of(unknown)[
        "message"
    ].replace(UNKNOWN_ID, "X")
    assert len(world.tables["hiring_signal_searches"].rows) == 1

    caller.user_id = USER
    assert client.delete(f"{SEARCHES}/{mine['id']}").status_code == 204


def test_the_same_search_by_two_users_is_two_rows() -> None:
    world = World()
    caller = Caller()
    client = api(world, caller)
    assert client.post(SEARCHES, json={"query": "data engineer"}).status_code == 201
    caller.user_id = OTHER_USER
    assert client.post(SEARCHES, json={"query": "data engineer"}).status_code == 201
    assert len(world.tables["hiring_signal_searches"].rows) == 2


_FULLWIDTH_ZERO = chr(0xFF10)
_CANONICAL = "30000000-0000-0000-0000-00000000abcd"
_NON_CANONICAL_IDS = [
    "not-a-uuid",
    "-".join(_FULLWIDTH_ZERO * n for n in (8, 4, 4, 4, 12)),  # full-width digits
    "{" + _CANONICAL + "}",
    "urn:uuid:" + _CANONICAL,
    _CANONICAL.replace("-", ""),
    _CANONICAL + " ",
    "0x" + _CANONICAL[2:],
]


@pytest.mark.parametrize("bad_id", _NON_CANONICAL_IDS)
def test_a_saved_search_id_that_is_not_a_canonical_uuid_is_a_404_that_never_reaches_the_database(
    bad_id: str,
) -> None:
    world = World()
    response = api(world).delete(f"{SEARCHES}/{bad_id}")

    assert response.status_code == 404
    assert error_of(response)["code"] == "NOT_FOUND"
    assert untouched(world)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 201},
        {"query": "data engineer", "location": "y" * 101},
        {"query": 5},
        {"query": "data engineer", "location": 5},
        {"query": "data engineer", "freshness": "day"},  # a saved search stores no window
        {"query": "data engineer", "locale": "india"},  # ... no locale
        {"query": "data engineer", "is_active": True},  # ... and nothing that could run it
        {"query": "data engineer", "user_id": OTHER_USER},
        {"query": "data engineer", "id": UNKNOWN_ID},
    ],
)
def test_an_invalid_saved_search_is_a_422_and_writes_nothing(body: dict[str, Any]) -> None:
    world = World()
    response = api(world).post(SEARCHES, json=body)

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert untouched(world)


@pytest.mark.parametrize(
    "body",
    [
        {"query": "!!! ???"},
        {"query": "a"},
        {"query": "x" * 61},
        {"query": "data engineer", "location": "###"},
    ],
)
def test_text_that_cannot_be_searched_cannot_be_saved_either(body: dict[str, Any]) -> None:
    """Every saved search is one a click can run."""
    world = World()
    response = api(world).post(SEARCHES, json=body)

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_searches"].rows == []


def test_the_twenty_sixth_saved_search_is_a_409_conflict_with_a_message_the_ui_can_show() -> None:
    world = World()
    client = api(world)
    for i in range(MAX_SAVED_SEARCHES):
        assert client.post(SEARCHES, json={"query": f"role number {i}"}).status_code == 201

    response = client.post(SEARCHES, json={"query": "one too many"})

    assert response.status_code == 409
    error = error_of(response)
    assert error["code"] == "CONFLICT"
    assert error["retryable"] is False
    assert str(MAX_SAVED_SEARCHES) in error["message"]
    assert "Delete one" in error["message"]
    assert len(world.tables["hiring_signal_searches"].rows) == MAX_SAVED_SEARCHES  # never dropped


def test_a_save_that_lost_a_race_is_a_retryable_conflict_that_does_not_claim_a_full_list() -> None:
    """A rival's save took the last slot a moment earlier and this one was taken back.
    The user may have 20 searches: the message must not say they have 25 (BC-8),
    and it is marked retryable because asking again is what works."""
    world = World()
    table = world.tables["hiring_signal_searches"]
    real_insert = table.insert_row
    state = {"rival_pending": True}

    def insert_then_rival(payload: dict[str, Any]) -> dict[str, Any]:
        row = real_insert(payload)
        if state["rival_pending"]:  # a rival's insert lands right after ours
            state["rival_pending"] = False
            for i in range(MAX_SAVED_SEARCHES):  # the table is now over the cap
                real_insert({"user_id": USER, "query": f"rival {i}", "location": None})
        return row

    table.insert_row = insert_then_rival  # type: ignore[method-assign]
    response = api(world).post(SEARCHES, json={"query": "mine"})

    assert response.status_code == 409
    error = error_of(response)
    assert error["code"] == "CONFLICT"
    assert error["retryable"] is True
    assert str(MAX_SAVED_SEARCHES) not in error["message"]
    assert "again" in error["message"]
    assert "Delete" not in error["message"]


def test_at_the_cap_the_same_search_again_is_still_a_200_and_a_delete_makes_room() -> None:
    world = World()
    client = api(world)
    first = client.post(SEARCHES, json={"query": "role number 0"}).json()
    for i in range(1, MAX_SAVED_SEARCHES):
        client.post(SEARCHES, json={"query": f"role number {i}"})

    assert client.post(SEARCHES, json={"query": "ROLE number 0"}).status_code == 200
    assert client.post(SEARCHES, json={"query": "fresh one"}).status_code == 409
    assert client.delete(f"{SEARCHES}/{first['id']}").status_code == 204
    assert client.post(SEARCHES, json={"query": "fresh one"}).status_code == 201


def test_another_users_searches_do_not_count_against_my_cap() -> None:
    world = World()
    caller = Caller(OTHER_USER)
    client = api(world, caller)
    for i in range(MAX_SAVED_SEARCHES):
        client.post(SEARCHES, json={"query": f"role number {i}"})
    caller.user_id = USER
    assert client.post(SEARCHES, json={"query": "mine"}).status_code == 201


def test_saving_a_search_never_runs_it() -> None:
    """A saved search is the typed words and nothing that runs: saving one makes no
    provider call and reads no key."""
    world = role_world()
    api(world).post(SEARCHES, json={"query": "software engineer", "location": "Bengaluru"})

    assert world.transport.requests == []
    assert world.tables["provider_credentials"].calls == []
    assert world.tables["hiring_signal_query_cache"].calls == []


# ── standalone saves ─────────────────────────────────────────────────────


def test_a_standalone_save_is_a_201_and_the_same_request_again_is_a_200_with_the_same_row() -> None:
    world = World()
    client = api(world)

    first = client.post(
        SAVES, json={"activity_id": ACTIVITY, "query_label": "data engineer -- Pune"}
    )
    second = client.post(SAVES, json={"activity_id": ACTIVITY, "query_label": "another label"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    body = first.json()
    assert set(body) == {"id", "activity_id", "post_url", "embed_url", "created_at"}
    assert body["post_url"] == canonical_post_url(ACTIVITY)
    assert body["embed_url"] == embed_url(ACTIVITY)
    (row,) = world.tables["hiring_signal_saves"].rows
    assert row["application_id"] is None and row["user_id"] == USER
    assert row["discovered_via_query"] == "data engineer -- Pune"  # the first label stays


def test_a_standalone_save_needs_no_label_and_the_label_is_cleaned_and_capped_never_echoed() -> (
    None
):
    world = World()
    client = api(world)
    bare = client.post(SAVES, json={"activity_id": ACTIVITY})
    assert bare.status_code == 201
    assert world.tables["hiring_signal_saves"].rows[0]["discovered_via_query"] is None

    other = client.post(
        SAVES, json={"activity_id": "7506381452083381427", "query_label": "A\x00" + "b" * 500}
    )
    assert other.status_code == 201
    stored = world.tables["hiring_signal_saves"].rows[1]["discovered_via_query"]
    assert stored == "A" + "b" * (MAX_QUERY_LABEL_CHARS - 1)
    assert "b" * 50 not in other.text  # the label is never echoed


@pytest.mark.parametrize(
    "activity_id",
    ["", "12a", "-1", "1.5", " 123", "0", "00", "01", "0007506381452083381426", "9" * 26, "١٢"],
)
def test_an_activity_id_that_is_not_ascii_digits_without_a_leading_zero_is_a_422(
    activity_id: str,
) -> None:
    world = World()
    response = api(world).post(SAVES, json={"activity_id": activity_id})

    assert response.status_code == 422
    assert error_of(response)["code"] == "INVALID_INPUT"
    assert world.tables["hiring_signal_saves"].rows == []


def test_a_missing_or_non_text_activity_id_is_a_422() -> None:
    client = api(World())
    assert client.post(SAVES, json={}).status_code == 422
    assert client.post(SAVES, json={"activity_id": 7506381452083381426}).status_code == 422
    assert client.post(SAVES, json={"activity_id": None}).status_code == 422


@pytest.mark.parametrize(
    "field",
    ["url", "post_url", "embed_url", "author_name", "title", "text", "snippet", "application_id"],
)
def test_a_client_supplied_url_author_title_text_or_application_is_refused_not_ignored(
    field: str,
) -> None:
    """The server builds the address itself and stores a pointer and nothing else; and
    a standalone save cannot be turned into a per-application one from the client."""
    world = World()
    response = api(world).post(SAVES, json={"activity_id": ACTIVITY, field: "x"})

    assert response.status_code == 422
    assert world.tables["hiring_signal_saves"].rows == []


def test_the_list_holds_only_standalone_saves_and_the_application_list_only_its_own() -> None:
    world = World()
    client = api(world)
    standalone = client.post(SAVES, json={"activity_id": "7506381452083381426"}).json()
    per_app = client.post(APP_SAVES, json={"activity_id": "7506381452083381427"}).json()
    both = client.post(
        SAVES, json={"activity_id": "7506381452083381427"}
    ).json()  # saved in both places

    mine = client.get(SAVES).json()["saves"]
    for_app = client.get(APP_SAVES).json()["saves"]

    assert {s["id"] for s in mine} == {standalone["id"], both["id"]}
    assert {s["id"] for s in for_app} == {per_app["id"]}
    assert both["id"] != per_app["id"]  # the same post saved twice is two saves
    assert {s["activity_id"] for s in mine} == {"7506381452083381426", "7506381452083381427"}


def test_the_standalone_list_is_newest_first() -> None:
    world = World()
    client = api(world)
    for n, activity in enumerate(
        ("7506381452083381426", "7506381452083381427", "7506381452083381428")
    ):
        assert client.post(SAVES, json={"activity_id": activity}).status_code == 201
        world.tables["hiring_signal_saves"].rows[n]["created_at"] = f"2026-09-18T12:0{n}:00+00:00"

    listed = client.get(SAVES).json()["saves"]

    assert [s["activity_id"] for s in listed] == [
        "7506381452083381428",
        "7506381452083381427",
        "7506381452083381426",
    ]


def test_an_empty_saves_list_is_an_empty_list() -> None:
    assert api(World()).get(SAVES).json() == {"saves": []}


def test_delete_works_for_both_kinds_of_save_and_a_second_delete_is_a_404() -> None:
    world = World()
    client = api(world)
    standalone = client.post(SAVES, json={"activity_id": ACTIVITY}).json()
    per_app = client.post(APP_SAVES, json={"activity_id": ACTIVITY}).json()

    for save in (standalone, per_app):
        first = client.delete(f"{SAVES}/{save['id']}")
        assert (first.status_code, first.content) == (204, b"")
        assert client.delete(f"{SAVES}/{save['id']}").status_code == 404
    assert world.tables["hiring_signal_saves"].rows == []


def test_another_user_can_neither_read_create_against_nor_delete_my_standalone_saves() -> None:
    world = World()
    caller = Caller()
    client = api(world, caller)
    mine = client.post(SAVES, json={"activity_id": ACTIVITY}).json()

    caller.user_id = OTHER_USER
    assert client.get(SAVES).json() == {"saves": []}
    assert client.delete(f"{SAVES}/{mine['id']}").status_code == 404
    theirs = client.post(SAVES, json={"activity_id": ACTIVITY})  # the same post is their own save
    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine["id"]

    assert len(world.tables["hiring_signal_saves"].rows) == 2
    caller.user_id = USER
    assert [s["id"] for s in client.get(SAVES).json()["saves"]] == [mine["id"]]


@pytest.mark.parametrize("bad_id", _NON_CANONICAL_IDS)
def test_a_save_id_that_is_not_a_canonical_uuid_is_a_404_that_never_reaches_the_database(
    bad_id: str,
) -> None:
    world = World()
    response = api(world).delete(f"{SAVES}/{bad_id}")
    assert response.status_code == 404
    assert untouched(world)


def test_a_save_writes_nothing_but_its_pointer_row() -> None:
    world = role_world()
    api(world).post(SAVES, json={"activity_id": ACTIVITY, "query_label": "x -- y"})

    assert world.transport.requests == []
    assert [t for t, table in world.tables.items() if table.calls] == ["hiring_signal_saves"]
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
