"""The scrubber behind error reporting (sentry_scrub.py), run over hostile events.

Every value below that looks like a secret is made up. The tests pin what the module
promises -- whole parts of an event are dropped by shape, secret-named keys lose their
values, known secret shapes are replaced in free text, a hostile event cannot make the
scrub slow or crash it, and anything it cannot understand is dropped rather than sent --
and, just as deliberately, say in one place (`test_regex_scrubbing_is_not_perfect`) what
it does not promise."""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from between_jobs.api.errors import ApiError
from between_jobs.api.sentry_scrub import (
    _MESSAGE_OMITTED_MODULES,
    FILTERED,
    MAX_DEPTH,
    MAX_KEY_CHARS,
    MAX_NODES,
    MAX_SOURCE_LINES,
    MAX_TOTAL_CHARS,
    MAX_USER_ID_CHARS,
    MESSAGE_OMITTED,
    before_send,
    is_client_error,
    is_secret_key,
    scrub_breadcrumb,
    scrub_event,
    scrub_text,
    scrub_url,
)

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwiZW1haWwiOiJhQGIuY29tIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
OPENROUTER_KEY = "sk-or-v1-" + "0123456789abcdef" * 4
ANTHROPIC_KEY = "sk-ant-api03-" + "AbCdEfGh_-1234567890" * 3
OPENAI_KEY = "sk-proj-" + "AbCdEfGh1234567890" * 3
TELEGRAM_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFake12"
EMAIL = "jane.doe+jobs@example.com"
# Google shapes, each a prefix and obviously fake repeated filler (long enough for its
# pattern), so no secret scanner reads this file as holding a real key.
GOOGLE_CLIENT_SECRET = "GOCSPX-" + "AbCdEfGh1234567890_-" * 2
GOOGLE_API_KEY = "AIza" + "SyAbCdEfGh1234567890_-aBcDeFgHiJ"
GOOGLE_ACCESS_TOKEN = "ya29." + "a0AbCdEfGh1234567890_-" * 2
GOOGLE_REFRESH_TOKEN = "1//" + "0gAbCdEfGh1234567890_-" * 2

SECRETS = [
    JWT,
    OPENROUTER_KEY,
    ANTHROPIC_KEY,
    OPENAI_KEY,
    TELEGRAM_TOKEN,
    EMAIL,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_API_KEY,
    GOOGLE_ACCESS_TOKEN,
    GOOGLE_REFRESH_TOKEN,
]


def _everywhere(event: object) -> str:
    return json.dumps(event, ensure_ascii=False)


# --- free text ----------------------------------------------------------------


@pytest.mark.parametrize("secret", SECRETS)
def test_known_secret_shapes_are_replaced_in_free_text(secret: str) -> None:
    for text in (
        f"failed for {secret}",
        f"{secret}",
        f"Authorization: Bearer {secret}",
        f"line one\n{secret}\nline three",
        f"(wrapped {secret})",
    ):
        assert secret not in scrub_text(text)


def test_a_telegram_bot_token_inside_an_api_url_is_replaced() -> None:
    assert TELEGRAM_TOKEN not in scrub_text(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    )


def test_bearer_tokens_and_secret_pairs_are_replaced() -> None:
    assert "abc.def-ghi" not in scrub_text("header was Bearer abc.def-ghi")
    assert "hunter2" not in scrub_text('{"password": "hunter2", "ok": 1}')
    assert "tok123" not in scrub_text("api_key=tok123&x=1")


def test_urls_lose_credentials_query_and_fragment() -> None:
    cleaned = scrub_url("https://user:pw@example.com/cb?code=abc&state=xyz#frag")
    assert cleaned == "https://example.com/cb"
    assert scrub_url("https://example.com/a/b") == "https://example.com/a/b"


def test_a_jwt_cut_before_its_last_segment_is_still_replaced() -> None:
    head, payload, _signature = JWT.split(".")
    assert "eyJzdWIi" not in scrub_text(f"{head}.{payload[:20]}")
    assert "eyJzdWIi" not in scrub_text(f"{head}.{payload}.")


def test_text_is_cut_before_it_is_scanned() -> None:
    cleaned = scrub_text("x" * 100_000)
    assert len(cleaned) < 2_000


def test_regex_scrubbing_is_not_perfect() -> None:
    """What it cannot know: a name, a sentence, a company. This test exists so the limit is
    written down where it is checked, not just in a docstring."""
    assert scrub_text("Jane Doe applied to Acme") == "Jane Doe applied to Acme"


# --- keys -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        # Every whole word the rule knows, written out here (not read from the module: a list
        # derived from it shrinks together with a deleted entry and the test stays green).
        "addr",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "email",
        "emails",
        "ip",
        "jwt",
        "key",
        "keys",
        "passwd",
        "password",
        "passwords",
        "phone",
        "phones",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        # One compound written as a single word per ending the rule knows.
        "myapikey",
        "sessioncookie",
        "useremail",
        "userpassword",
        "userphone",
        "clientsecret",
        "csrftoken",
        # camelCase whose last word is not an ending of its own: only the split finds them.
        "sessionKey",
        "privateKey",
        "client_secret",
        "access_token",
        "accessToken",
        "refreshToken",
        "Password",
        "api_key",
        "apiKey",
        "x-api-key",
        "Authorization",
        "X-Telegram-Bot-Api-Secret-Token",
        "user_email",
        "userEmail",
        "contact-email",
        "phone_number",
        "set-cookie",
        "ip_address",
        "remote_addr",
        "\uff54\uff4f\uff4b\uff45\uff4e",  # "token" in fullwidth letters: normalised first
    ],
)
def test_secret_looking_keys_are_secret(key: str) -> None:
    assert is_secret_key(key)


def test_values_under_plural_compound_and_camel_case_secret_keys_are_filtered() -> None:
    out = scrub_event(
        {"extra": {"tokens": "x", "sessionKey": "y", "useremail": "z", "cookies": "c", "jwt": "j"}}
    )
    assert out == {
        "extra": {
            "tokens": FILTERED,
            "sessionKey": FILTERED,
            "useremail": FILTERED,
            "cookies": FILTERED,
            "jwt": FILTERED,
        }
    }


@pytest.mark.parametrize(
    "key",
    [
        "monkey",
        "tokenizers",
        "keyboard",
        "worker",
        "route",
        "request_id",
        "consecutive_failures",
        "zip",
    ],
)
def test_ordinary_keys_are_not(key: str) -> None:
    assert not is_secret_key(key)


def test_values_under_secret_keys_are_filtered_at_any_depth_and_whatever_their_type() -> None:
    event = {
        "extra": {
            "password": "hunter2",
            "nested": {"deep": [{"api_key": "abc", "fine": "ok"}], "Authorization": ["Bearer x"]},
            "email": {"a": 1},
            "phone": 5551234,
            "monkey": "kept",
        },
        "contexts": {"app": {"client_secret": "s3cret", "name": "api"}},
        "tags": {"token": "t", "worker": "outbox"},
    }
    out = scrub_event(event)
    assert out is not None
    assert out["extra"]["password"] == FILTERED
    assert out["extra"]["nested"]["deep"][0] == {"api_key": FILTERED, "fine": "ok"}
    assert out["extra"]["nested"]["Authorization"] == FILTERED
    assert out["extra"]["email"] == FILTERED
    assert out["extra"]["phone"] == FILTERED
    assert out["extra"]["monkey"] == "kept"
    assert out["contexts"]["app"] == {"client_secret": FILTERED, "name": "api"}
    assert out["tags"] == {"token": FILTERED, "worker": "outbox"}


def test_a_secret_in_a_key_name_is_scrubbed_too() -> None:
    out = scrub_event({"extra": {f"for {OPENROUTER_KEY}": 1}})
    assert out is not None
    assert OPENROUTER_KEY not in _everywhere(out)


# --- request, user ---------------------------------------------------------------


def _request_event() -> dict[str, Any]:
    return {
        "request": {
            "url": f"https://api.example.com/applications/abc?code=oauthcode&email={EMAIL}",
            "method": "post",
            "query_string": f"code=oauthcode&email={EMAIL}",
            "data": {"resume_text": "My whole resume", "api_key": OPENROUTER_KEY},
            "cookies": {"session": "abc"},
            "env": {"REMOTE_ADDR": "203.0.113.9", "SERVER_NAME": "x"},
            "headers": {
                "Authorization": f"Bearer {JWT}",
                "authorization": "Bearer other",
                "Cookie": "sb-access-token=abc",
                "Set-Cookie": "a=b",
                "apikey": OPENROUTER_KEY,
                "X-Api-Key": OPENROUTER_KEY,
                "x-rapidapi-key": "k",
                "X-Telegram-Bot-Api-Secret-Token": "shh",
                "X-Forwarded-For": "203.0.113.9",
                "X-Real-Ip": "203.0.113.9",
                "Forwarded": "for=203.0.113.9",
                "X-Extension-Version": "1.0",
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Origin": "chrome-extension://abcdefgh",
                "X-Request-ID": "0123456789abcdef",
            },
        }
    }


def test_the_request_keeps_only_method_url_without_query_and_a_few_headers() -> None:
    out = scrub_event(_request_event())
    assert out is not None
    request = out["request"]
    assert set(request) == {"url", "method", "headers"}
    assert request["url"] == "https://api.example.com/applications/abc"
    assert request["method"] == "POST"
    assert request["headers"] == {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
        "Origin": "chrome-extension://abcdefgh",
        "X-Request-ID": "0123456789abcdef",
    }
    blob = _everywhere(out)
    for leaked in (JWT, OPENROUTER_KEY, EMAIL, "oauthcode", "My whole resume", "203.0.113.9"):
        assert leaked not in blob


def test_headers_given_as_pairs_are_handled_the_same_way() -> None:
    event = {
        "request": {
            "headers": [
                ["Authorization", "Bearer x"],
                ["User-Agent", "ua"],
                ["bad"],
                5,
                ["a", "b", "c"],
            ]
        }
    }
    out = scrub_event(event)
    assert out == {"request": {"headers": {"User-Agent": "ua"}}}


def test_a_header_value_that_is_a_secret_is_scrubbed_even_on_a_kept_header() -> None:
    out = scrub_event({"request": {"headers": {"Origin": f"https://x.example/?t={JWT}"}}})
    assert out is not None
    assert JWT not in _everywhere(out)


@pytest.mark.parametrize("bad", ["a string", 5, None, ["x"], b"bytes"])
def test_a_request_that_is_not_a_dict_is_dropped(bad: Any) -> None:
    assert scrub_event({"request": bad, "level": "error"}) == {"level": "error"}


def test_the_user_is_reduced_to_at_most_an_opaque_id() -> None:
    event = {
        "user": {
            "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
            "email": EMAIL,
            "ip_address": "1.2.3.4",
        }
    }
    assert scrub_event(event) == {"user": {"id": "7c9e6679-7425-40de-944b-e07fc1f90ae7"}}
    assert scrub_event({"user": {"id": 42, "username": "jane"}}) == {"user": {"id": "42"}}


@pytest.mark.parametrize(
    "user",
    [
        {"email": EMAIL},
        {"ip_address": "1.2.3.4"},
        {"id": EMAIL},
        {"id": "x" * 500},
        {"id": ""},
        {"id": True},
        {"id": ["a"]},
        "jane",
        ["id"],
        None,
    ],
)
def test_a_user_without_a_plain_opaque_id_is_dropped(user: Any) -> None:
    assert scrub_event({"user": user, "level": "error"}) == {"level": "error"}


@pytest.mark.parametrize(
    "uid",
    [
        "jane@corp",  # no top-level domain, so the email pattern misses it: the "@" check
        "jane.doe@corp",
        TELEGRAM_TOKEN,  # a secret shape without an "@": the scrub check
        "sk-or-v1-" + "0123456789abcdef" * 2,  # short enough for the length cap to let it by
    ],
)
def test_a_user_id_that_is_not_an_opaque_id_is_dropped_by_each_check_alone(uid: str) -> None:
    assert len(uid) <= MAX_USER_ID_CHARS
    assert scrub_event({"user": {"id": uid}, "level": "error"}) == {"level": "error"}


# --- messages and exceptions -------------------------------------------------------


def _exception_event(module: str, value: str, type_name: str = "SomeError") -> dict[str, Any]:
    return {
        "exception": {
            "values": [
                {
                    "type": type_name,
                    "value": value,
                    "module": module,
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "app.py",
                                "function": "get_token",
                                "lineno": 3,
                                "context_line": "    raise SomeError(token)",
                                "vars": {"token": OPENROUTER_KEY, "resume": "text"},
                            }
                        ]
                    },
                }
            ]
        }
    }


def test_exception_messages_are_scrubbed_and_frame_variables_never_kept() -> None:
    out = scrub_event(_exception_event("builtins", f"bad {OPENROUTER_KEY} for {EMAIL} and {JWT}"))
    assert out is not None
    (value,) = out["exception"]["values"]
    for secret in (OPENROUTER_KEY, EMAIL, JWT):
        assert secret not in value["value"]
    frame = value["stacktrace"]["frames"][0]
    assert frame["vars"] == FILTERED
    assert frame["function"] == "get_token"
    assert frame["context_line"] == "    raise SomeError(token)"
    assert OPENROUTER_KEY not in _everywhere(out)


def test_source_lines_around_a_frame_are_left_alone_not_garbled() -> None:
    frame = {
        "context_line": "    token = request_id_var.set(request_id)",
        "pre_context": ["def f():", "    api_key = settings.api_key"],
        "post_context": ["x" * 1000, 5, "ok"],
    }
    out = scrub_event({"exception": {"values": [{"stacktrace": {"frames": [frame]}}]}})
    assert out is not None
    kept = out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert kept["context_line"] == "    token = request_id_var.set(request_id)"
    assert kept["pre_context"] == ["def f():", "    api_key = settings.api_key"]
    assert kept["post_context"] == ["x" * 300, "ok"]


def test_odd_source_line_values_are_filtered() -> None:
    out = scrub_event({"context_line": {"a": 1}, "pre_context": None})
    assert out == {"context_line": FILTERED, "pre_context": FILTERED}


def test_the_hostname_and_the_command_line_are_not_sent() -> None:
    out = scrub_event(
        {
            "server_name": "Janes-MacBook-Pro.local",
            "extra": {"sys.argv": ["x", "--key", "y"], "a": 1},
        }
    )
    assert out == {"extra": {"sys.argv": FILTERED, "a": 1}}


def test_a_long_exception_message_is_cut() -> None:
    out = scrub_event(_exception_event("builtins", "word " * 200))
    assert out is not None
    assert len(out["exception"]["values"][0]["value"]) < 400


# The modules whose exception messages are withheld, written out here as well as read from
# the module, so a silent removal (or addition) fails a test instead of passing unnoticed.
_OMITTED_MODULES = (
    "between_jobs.api.errors",
    "gotrue",
    "httpcore",
    "httpx",
    "openai",
    "postgrest",
    "pydantic",
    "pydantic_core",
    "realtime",
    "storage3",
    "supabase",
    "supabase_auth",
    "supabase_functions",
)


def test_the_modules_whose_messages_are_withheld_are_the_ones_written_down() -> None:
    assert sorted(_MESSAGE_OMITTED_MODULES) == sorted(_OMITTED_MODULES)


@pytest.mark.parametrize(
    "module",
    [
        *_OMITTED_MODULES,
        *(f"{name}.sub" for name in _OMITTED_MODULES),
        *_MESSAGE_OMITTED_MODULES,
        "postgrest.exceptions",
        "pydantic_core._pydantic_core",
        "pydantic.errors",
        "supabase_auth.errors",
        "httpcore._exceptions",
    ],
)
def test_messages_of_libraries_that_quote_values_are_not_sent(module: str) -> None:
    out = scrub_event(_exception_event(module, "Key (email)=(someone) already exists; resume: ..."))
    assert out is not None
    assert out["exception"]["values"][0]["value"] == MESSAGE_OMITTED


@pytest.mark.parametrize(
    "module", ["httpxy", "supabasey", "gotrue2", "pydantic_corex", "between_jobs.api.errorsx"]
)
def test_a_module_that_only_starts_like_a_listed_one_keeps_its_message(module: str) -> None:
    out = scrub_event(_exception_event(module, "plain"))
    assert out is not None
    assert out["exception"]["values"][0]["value"] == "plain"


def test_an_api_error_message_is_not_sent_for_a_client_builds_it_from_an_upstream_answer() -> None:
    message = "The resume engine rejected this run: [{'input': {'name': 'Jane Doe'}}]"
    out = scrub_event(_exception_event("between_jobs.api.errors", message, "ApiError"))
    assert out is not None
    assert out["exception"]["values"][0]["value"] == MESSAGE_OMITTED
    assert "Jane Doe" not in _everywhere(out)


@pytest.mark.parametrize(
    "type_name",
    [
        "RequestValidationError",
        "ResponseValidationError",
        "ValidationException",
        "WebSocketRequestValidationError",
    ],
)
def test_fastapi_validation_errors_quote_their_input_so_their_message_is_not_sent(
    type_name: str,
) -> None:
    message = "1 validation error: {'loc': ('response', 0), 'input': ['Led the migration of Jane']}"
    out = scrub_event(_exception_event("fastapi.exceptions", message, type_name))
    assert out is not None
    assert out["exception"]["values"][0]["value"] == MESSAGE_OMITTED


def test_a_fastapi_http_exception_keeps_its_message_because_the_type_not_the_module_decides() -> (
    None
):
    out = scrub_event(
        _exception_event("fastapi.exceptions", "Service unavailable", "HTTPException")
    )
    assert out is not None
    assert out["exception"]["values"][0]["value"] == "Service unavailable"
    # And the same type name in another module is not caught by accident.
    out = scrub_event(_exception_event("someapp.errors", "plain", "ResponseValidationError"))
    assert out is not None
    assert out["exception"]["values"][0]["value"] == "plain"


def test_message_and_logentry_are_scrubbed() -> None:
    event = {
        "message": f"hello {EMAIL}",
        "logentry": {
            "message": "key %s",
            "formatted": f"key {OPENROUTER_KEY}",
            "params": [TELEGRAM_TOKEN],
        },
        "transaction": f"/oauth/{JWT}",
    }
    out = scrub_event(event)
    assert out is not None
    blob = _everywhere(out)
    for secret in (EMAIL, OPENROUTER_KEY, TELEGRAM_TOKEN, JWT):
        assert secret not in blob


def test_exception_in_odd_shapes_does_not_crash_or_leak() -> None:
    odd: list[Any] = ["text", 5, None, [], {"values": "x"}, {"values": [None, 5, {"value": 7}]}]
    for exception in odd:
        out = scrub_event({"exception": exception})
        assert out is not None


# --- breadcrumbs ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "crumb",
    [
        {"type": "http", "category": "httplib", "data": {"url": "https://x.example/?token=abc"}},
        {"category": "httplib", "message": "GET https://x.example"},
        {"category": "httpx"},
        {"category": "subprocess", "data": {"args": ["git", "rev-parse"]}},
        {"type": "query", "message": "select * from users"},
        {"category": "db"},
    ],
)
def test_outbound_request_database_and_subprocess_breadcrumbs_are_dropped(
    crumb: dict[str, Any],
) -> None:
    assert scrub_breadcrumb(crumb) is None


def test_other_breadcrumbs_are_scrubbed() -> None:
    crumb = {
        "type": "log",
        "category": "app",
        "message": f"sent to {EMAIL}",
        "data": {"token": "t", "route": "/x", "body": f"Bearer {JWT}"},
        "level": "info",
    }
    out = scrub_breadcrumb(crumb)
    assert out is not None
    assert out["data"]["token"] == FILTERED
    assert EMAIL not in _everywhere(out)
    assert JWT not in _everywhere(out)
    assert out["level"] == "info"


@pytest.mark.parametrize("crumb", [None, "text", 5, [], ["a"]])
def test_a_breadcrumb_that_is_not_a_dict_is_dropped(crumb: Any) -> None:
    assert scrub_breadcrumb(crumb) is None


def test_breadcrumbs_inside_an_event_follow_the_same_rules_in_both_shapes() -> None:
    crumbs = [
        {"category": "httplib", "data": {"url": "https://x.example"}},
        {"category": "app", "message": f"hi {EMAIL}"},
        "junk",
    ]
    wrapped = scrub_event({"breadcrumbs": {"values": crumbs}})
    bare = scrub_event({"breadcrumbs": crumbs})
    assert wrapped is not None and bare is not None
    assert len(wrapped["breadcrumbs"]["values"]) == 1
    assert len(bare["breadcrumbs"]) == 1
    assert EMAIL not in _everywhere(wrapped) + _everywhere(bare)
    assert scrub_event({"breadcrumbs": 5, "level": "e"}) == {"level": "e"}


# --- hostile inputs -----------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "event", 5, 1.5, [], ["a"], ("a",), b"x", object()])
def test_something_that_is_not_a_dict_cannot_be_scrubbed_so_it_is_dropped(bad: Any) -> None:
    assert scrub_event(bad) is None
    assert before_send(bad, {}) is None


def test_values_that_are_not_json_data_are_filtered_not_stringified() -> None:
    class Weird:
        def __str__(self) -> str:
            raise AssertionError("must not be called")

    out = scrub_event(
        {"extra": {"a": Weird(), "b": b"bytes", "c": {1, 2}, "d": (1, "x"), "e": float("nan")}}
    )
    assert out is not None
    assert out["extra"]["a"] == FILTERED
    assert out["extra"]["b"] == FILTERED
    assert out["extra"]["c"] == [1, 2]
    assert out["extra"]["d"] == [1, "x"]


def test_non_string_keys_are_dropped() -> None:
    out = scrub_event({"extra": {1: "a", None: "b", ("t",): "c", "ok": "d"}, 5: "x"})
    assert out == {"extra": {"ok": "d"}}


def test_a_scrub_that_raises_internally_drops_the_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Exploding(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise RuntimeError("boom")

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("boom")

        def __len__(self) -> int:
            return 1

    assert scrub_event(Exploding()) is None
    assert scrub_event({"extra": Exploding()}) is None
    assert scrub_breadcrumb(Exploding()) is None
    # Said in the log, by type only: the message and the event are not.
    contexts = [r.ctx for r in caplog.records]  # type: ignore[attr-defined]
    assert contexts == [
        {"what": "event", "error_type": "builtins.RuntimeError"},
        {"what": "event", "error_type": "builtins.RuntimeError"},
        {"what": "breadcrumb", "error_type": "builtins.RuntimeError"},
    ]
    assert "boom" not in caplog.text


def test_the_input_is_not_modified() -> None:
    event = (
        _request_event() | _exception_event("builtins", f"x {EMAIL}") | {"user": {"email": EMAIL}}
    )
    before = copy.deepcopy(event)
    scrub_event(event)
    assert event == before


def test_very_deep_nesting_does_not_hit_the_recursion_limit() -> None:
    deep: dict[str, Any] = {}
    cursor = deep
    for _ in range(5_000):
        cursor["child"] = {}
        cursor = cursor["child"]
    cursor["token"] = OPENROUTER_KEY
    out = scrub_event({"extra": deep})
    assert out is not None
    assert OPENROUTER_KEY not in repr(out)
    depth = 0
    node: Any = out["extra"]
    while isinstance(node, dict) and "child" in node:
        node = node["child"]
        depth += 1
    assert depth <= MAX_DEPTH


def test_very_deep_lists_are_cut_too() -> None:
    nested: Any = [OPENROUTER_KEY]
    for _ in range(5_000):
        nested = [nested]
    out = scrub_event({"extra": {"v": nested}})
    assert out is not None


def test_a_huge_event_is_cut_to_a_bounded_size_quickly() -> None:
    event = {
        "extra": {
            "big_string": "a" * 5_000_000,
            "many": [f"item {i} {EMAIL}" for i in range(200_000)],
            "wide": {f"key{i}": "v" for i in range(200_000)},
        },
        "breadcrumbs": [{"category": "app", "message": "m" * 1000} for _ in range(50_000)],
    }
    started = time.perf_counter()
    out = scrub_event(event)
    elapsed = time.perf_counter() - started
    assert out is not None
    assert elapsed < 5.0
    assert EMAIL not in _everywhere(out)
    assert len(_everywhere(out)) < 3_000_000


def _entries(node: Any) -> int:
    """Dict entries plus list items anywhere in `node`."""
    total, pending = 0, [node]
    while pending:
        current = pending.pop()
        children = (
            list(current.values())
            if isinstance(current, dict)
            else current
            if isinstance(current, list)
            else []
        )
        total += len(children)
        pending.extend(children)
    return total


def test_a_huge_list_and_a_huge_dict_are_cut_to_the_node_budget() -> None:
    long_list = {"extra": {"values": list(range(MAX_NODES * 3))}}
    out = scrub_event(long_list)
    assert out is not None
    assert _entries(out) <= MAX_NODES + 10

    wide = {"extra": {chr(0x4E00 + i): i for i in range(MAX_NODES + 1_000)}}  # 1-character keys
    out = scrub_event(wide)
    assert out is not None
    assert _entries(out) <= MAX_NODES + 10


def test_text_past_the_total_budget_is_replaced_not_scanned() -> None:
    out = scrub_event({"extra": {"lines": ["x" * 900 for _ in range(200)]}})
    assert out is not None
    lines = out["extra"]["lines"]
    scanned = [line for line in lines if line != FILTERED]
    assert 0 < len(scanned) <= MAX_TOTAL_CHARS // 900 + 1
    assert FILTERED in lines


def test_entries_past_the_total_budget_are_dropped_from_a_dict() -> None:
    out = scrub_event({"extra": {f"k{i}": "x" * 900 for i in range(200)}})
    assert out is not None
    assert 0 < len(out["extra"]) <= MAX_TOTAL_CHARS // 900 + 1


def test_a_long_run_of_secret_shaped_text_cannot_make_the_scan_slow() -> None:
    event = {"extra": {f"k{i}": "a" * 5_000 + "@" for i in range(100)}}
    started = time.perf_counter()
    assert scrub_event(event) is not None
    assert time.perf_counter() - started < 5.0


def test_unicode_survives_and_secrets_in_it_do_not() -> None:
    event = {
        "message": f"résumé 履歴書 \U0001f4c4 ‮evil‬ {EMAIL} café",
        "extra": {
            "名前": "山田",
            "emoji\U0001f510": TELEGRAM_TOKEN,
            "lone": "bad \ud800 surrogate",
        },
    }
    out = scrub_event(event)
    assert out is not None
    assert "résumé 履歴書 \U0001f4c4" in out["message"]
    assert "café" in out["message"]
    assert EMAIL not in out["message"]
    assert out["extra"]["名前"] == "山田"
    assert TELEGRAM_TOKEN not in out["extra"]["emoji\U0001f510"]


def test_an_email_hidden_after_other_text_in_a_url_is_found() -> None:
    out = scrub_event({"request": {"url": f"https://x.example/users/{EMAIL}/profile"}})
    assert out is not None
    assert EMAIL not in out["request"]["url"]


def test_the_output_is_plain_json() -> None:
    event = (
        _request_event() | _exception_event("builtins", "x") | {"extra": {"a": [1, 2, {"b": None}]}}
    )
    out = scrub_event(event)
    assert out is not None
    json.dumps(out)


# --- what is dropped before scrubbing ---------------------------------------------------


def _api_error_hint(code: Any, cause: BaseException | None = None) -> dict[str, Any]:
    error = ApiError(code, "message")
    return {"exc_info": (type(error), error, None)}


@pytest.mark.parametrize(
    "code", ["INVALID_INPUT", "NOT_FOUND", "AUTH_REQUIRED", "RATE_LIMITED", "FORBIDDEN"]
)
def test_an_api_error_with_a_client_status_is_never_reported(code: Any) -> None:
    assert is_client_error(_api_error_hint(code))
    assert before_send({"level": "error"}, _api_error_hint(code)) is None


@pytest.mark.parametrize(
    "code", ["INTERNAL_ERROR", "RUN_FAILED", "PROVIDER_UNAVAILABLE", "PROVIDER_REJECTED"]
)
def test_an_api_error_with_a_server_status_is_reported(code: Any) -> None:
    assert not is_client_error(_api_error_hint(code))
    assert before_send({"level": "error"}, _api_error_hint(code)) == {"level": "error"}


@pytest.mark.parametrize(
    "hint",
    [
        None,
        {},
        {"exc_info": None},
        {"exc_info": (ValueError, ValueError("x"), None)},
        {"exc_info": "text"},
        {"exc_info": (1, 2)},
        "hint",
    ],
)
def test_anything_else_is_reported(hint: Any) -> None:
    assert not is_client_error(hint)
    assert before_send({"level": "error"}, hint) == {"level": "error"}


def test_before_send_scrubs_what_it_keeps() -> None:
    out = before_send({"message": f"x {EMAIL}", "user": {"email": EMAIL}}, {})
    assert out is not None
    assert EMAIL not in _everywhere(out)


# --- bounds and shape checks that nothing else pins -----------------------------------------


def test_a_hostile_key_is_cut_before_it_is_scanned() -> None:
    started = time.perf_counter()
    out = scrub_event({"extra": {"a" * 100_000 + "@": 1}})
    elapsed = time.perf_counter() - started
    assert out is not None
    [key] = out["extra"]
    assert len(key) <= MAX_KEY_CHARS + len("...[cut]")
    assert elapsed < 0.5


def test_the_characters_of_keys_count_against_the_budget() -> None:
    out = scrub_event({"extra": {f"{'k' * 190}{i}": 1 for i in range(5_000)}})
    assert out is not None
    assert len(out["extra"]) <= MAX_TOTAL_CHARS // 190 + 2


def test_only_a_few_lines_around_a_frame_are_kept() -> None:
    # The literal, as well as the constant: raising the cap is a decision, so it breaks this.
    assert MAX_SOURCE_LINES == 20
    out = scrub_event({"pre_context": ["line"] * 100, "post_context": ["x"] * 100})
    assert out == {"pre_context": ["line"] * 20, "post_context": ["x"] * 20}


def test_a_long_header_value_is_cut() -> None:
    out = scrub_event({"request": {"headers": {"User-Agent": "u" * 5_000}}})
    assert out is not None
    assert len(out["request"]["headers"]["User-Agent"]) <= 500 + len("...[cut]")


def test_only_the_first_two_hundred_headers_are_looked_at() -> None:
    pairs = [[f"X-Filler-{i}", "v"] for i in range(300)] + [["User-Agent", "late"]]
    assert scrub_event({"request": {"headers": pairs}}) == {"request": {"headers": {}}}


@pytest.mark.parametrize(
    "method",
    ["sk-or-v1-abc", "jane@example.com", "GET1", "GET /x", "A" * 17, "", 5, None],
)
def test_a_request_method_that_is_not_a_short_word_is_dropped(method: Any) -> None:
    assert scrub_event({"request": {"method": method}}) == {"request": {}}


def test_a_request_method_of_sixteen_letters_is_kept_in_capitals() -> None:
    assert scrub_event({"request": {"method": "a" * 16}}) == {"request": {"method": "A" * 16}}


@pytest.mark.parametrize("url", ["https://example.com/a#frag", "https://example.com/a#"])
def test_a_fragment_with_no_query_is_cut_from_a_url(url: str) -> None:
    assert scrub_url(url) == "https://example.com/a"


@pytest.mark.parametrize(
    "crumb",
    [
        {"type": "http", "category": "other", "message": "m"},  # dropped by its type alone
        {"category": "query", "message": "select 1"},  # dropped by its category alone
    ],
)
def test_a_breadcrumb_is_dropped_by_its_type_or_its_category_alone(crumb: dict[str, Any]) -> None:
    assert scrub_breadcrumb(crumb) is None


def test_a_short_cut_jwt_head_is_replaced_too() -> None:
    assert "zdWIiOiIx" not in scrub_text("sub claim eyJzdWIiOiIx cut")


# --- what the SDK adds that the key-name filter must not mangle -----------------------------

TRACE_ID = "0123456789abcdef0123456789abcdef"
PUBLIC_KEY = "fedcba9876543210fedcba9876543210"


def _trace_event(dsc: Any) -> dict[str, Any]:
    return {
        "contexts": {
            "trace": {"trace_id": TRACE_ID, "span_id": "0123456789abcdef"}
            | ({} if dsc is None else {"dynamic_sampling_context": dsc}),
            "app": {"client_secret": "s3cret", "name": "api"},
        }
    }


def test_a_dynamic_sampling_context_survives_with_its_public_key() -> None:
    dsc = {
        "trace_id": TRACE_ID,
        "public_key": PUBLIC_KEY,
        "org_id": "123456",
        "environment": "production",
        "release": "abc1234",
        "transaction": "/applications/{application_id}",
        "sample_rate": "1.0",
        "sample_rand": "0.089972",
        "sampled": "true",
    }
    out = scrub_event(_trace_event(dsc))
    assert out is not None
    trace = out["contexts"]["trace"]
    assert trace["dynamic_sampling_context"] == dsc
    assert trace["trace_id"] == TRACE_ID
    # The rest of the contexts is still walked like any other value.
    assert out["contexts"]["app"] == {"client_secret": FILTERED, "name": "api"}


def test_a_dynamic_sampling_context_is_rebuilt_from_its_allowlist_and_scrubbed() -> None:
    dsc = {
        "trace_id": TRACE_ID,
        "public_key": "not a key!",  # not a plain token: dropped
        "transaction": f"/x/{EMAIL}",  # kept, scrubbed
        "release": 5,  # not a string
        "environment": "",  # empty
        "sampled": None,
        "user_segment": "premium",  # not on the list
        "email": EMAIL,
        "token": OPENROUTER_KEY,
        "sample_rate": "x" * 5_000,
    }
    out = scrub_event(_trace_event(dsc))
    assert out is not None
    rebuilt = out["contexts"]["trace"]["dynamic_sampling_context"]
    assert set(rebuilt) == {"trace_id", "transaction", "sample_rate"}
    assert rebuilt["transaction"] == "/x/<email>"
    assert len(rebuilt["sample_rate"]) < 300
    assert EMAIL not in _everywhere(out) and OPENROUTER_KEY not in _everywhere(out)


@pytest.mark.parametrize(
    "dsc", ["junk", 5, ["trace_id"], {}, {"user_segment": "x"}, {"sampled": 1}]
)
def test_a_dynamic_sampling_context_with_nothing_usable_is_left_out(dsc: Any) -> None:
    out = scrub_event(_trace_event(dsc))
    assert out is not None
    assert "dynamic_sampling_context" not in out["contexts"]["trace"]
    assert out["contexts"]["trace"]["trace_id"] == TRACE_ID


def test_the_input_dynamic_sampling_context_is_not_modified() -> None:
    event = _trace_event({"public_key": PUBLIC_KEY, "email": EMAIL})
    before = copy.deepcopy(event)
    scrub_event(event)
    assert event == before


def test_contexts_that_are_not_a_dict_are_still_handled() -> None:
    assert scrub_event({"contexts": "text"}) == {"contexts": "text"}
    assert scrub_event({"contexts": {"trace": "text"}}) == {"contexts": {"trace": "text"}}


def test_the_sdks_annotations_of_removed_values_are_not_sent() -> None:
    meta = {"request": {"headers": {"authorization": {"": {"rem": [["!config", "x"]]}}}}}
    assert scrub_event({"_meta": meta, "level": "error"}) == {"level": "error"}
