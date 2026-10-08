"""What an upstream service's error answer may add to this API's error message
(upstream_errors.py): its own error text, and for a validation list only where and why."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from between_jobs.api.upstream_errors import (
    MAX_DETAIL_CHARS,
    MAX_ISSUE_MESSAGE_CHARS,
    MAX_ISSUES,
    MAX_LOC_PART_CHARS,
    UNRECOGNISED,
    upstream_error_detail,
)

SECRET_INPUT = {"name": "Pat Smith", "phone": "+1 201 555 0187"}


def _json(body: Any, status: int = 422) -> httpx.Response:
    # `content`, not `json=`: httpx treats json=None as "no body", and a body of `null` is a case.
    return httpx.Response(status, content=json.dumps(body).encode())


def test_a_string_detail_is_kept() -> None:
    assert upstream_error_detail(_json({"detail": "Couldn't draft a bullet."})) == (
        "Couldn't draft a bullet."
    )


def test_a_string_message_is_kept_when_there_is_no_detail() -> None:
    assert upstream_error_detail(_json({"error": "IngestError", "message": "bad template"})) == (
        "bad template"
    )


def test_a_detail_wins_over_a_message() -> None:
    assert upstream_error_detail(_json({"detail": "from detail", "message": "from message"})) == (
        "from detail"
    )


@pytest.mark.parametrize("key", ["detail", "message"])
def test_a_long_string_is_cut(key: str) -> None:
    assert len(upstream_error_detail(_json({key: "x" * 5_000}))) == MAX_DETAIL_CHARS


def test_a_validation_list_gives_where_and_why_for_each_problem() -> None:
    answer = {
        "detail": [
            {"type": "missing", "loc": ["body", "a"], "msg": "Field required", "input": {}},
            {"type": "int_type", "loc": ["body", "items", 3, "count"], "msg": "Not an int"},
        ]
    }
    assert upstream_error_detail(_json(answer)) == (
        "body.a: Field required; body.items.3.count: Not an int"
    )


def test_input_ctx_and_url_are_never_read() -> None:
    answer = {
        "detail": [
            {
                "type": "value_error",
                "loc": ["body", "x"],
                "msg": "Bad value",
                "input": SECRET_INPUT,
                "ctx": {"error": "Pat Smith is not allowed"},
                "url": "https://errors.pydantic.dev/2/v/value_error",
            }
        ]
    }
    text = upstream_error_detail(_json(answer))
    assert text == "body.x: Bad value"
    for hidden in ("Pat Smith", "555 0187", "pydantic.dev", "value_error"):
        assert hidden not in text


def test_only_the_first_few_problems_are_listed() -> None:
    answer = {"detail": [{"loc": ["body", f"f{i}"], "msg": "m"} for i in range(50)]}
    text = upstream_error_detail(_json(answer))
    assert text.count("; ") == MAX_ISSUES - 1
    assert "f4" in text and "f5" not in text


def test_each_part_of_a_location_and_each_message_is_cut() -> None:
    answer = {"detail": [{"loc": ["body", "k" * 500], "msg": "m" * 500}]}
    text = upstream_error_detail(_json(answer))
    assert "k" * MAX_LOC_PART_CHARS in text and "k" * (MAX_LOC_PART_CHARS + 1) not in text
    assert "m" * MAX_ISSUE_MESSAGE_CHARS in text and "m" * (MAX_ISSUE_MESSAGE_CHARS + 1) not in text


def test_the_whole_text_is_cut() -> None:
    answer = {"detail": [{"loc": ["body", "k" * 40, "j" * 40], "msg": "m" * 80} for _ in range(5)]}
    assert len(upstream_error_detail(_json(answer))) <= MAX_DETAIL_CHARS


def test_only_strings_and_whole_numbers_count_as_parts_of_a_location() -> None:
    answer = {"detail": [{"loc": ["body", True, None, {"name": "Pat"}, ["x"], 2.5, 7], "msg": "m"}]}
    assert upstream_error_detail(_json(answer)) == "body.7: m"


@pytest.mark.parametrize(
    "entry",
    [
        "a string",
        5,
        None,
        [],
        {},
        {"loc": "body.x"},
        {"loc": [], "msg": 5},
        {"msg": None, "input": SECRET_INPUT},
    ],
)
def test_entries_that_say_nothing_usable_are_skipped(entry: Any) -> None:
    text = upstream_error_detail(_json({"detail": [entry, {"loc": ["body", "ok"], "msg": "m"}]}))
    assert text == "body.ok: m"


def test_an_entry_with_only_a_message_or_only_a_location_still_says_something() -> None:
    answer = {"detail": [{"msg": "Just why"}, {"loc": ["body", "just_where"]}]}
    assert upstream_error_detail(_json(answer)) == "Just why; body.just_where"


def test_a_list_with_nothing_usable_falls_back_to_the_message_and_is_never_printed() -> None:
    assert upstream_error_detail(_json({"detail": [5], "message": "fallback"})) == "fallback"
    assert upstream_error_detail(_json({"detail": []})) == UNRECOGNISED
    echo = {"detail": [{"input": SECRET_INPUT}, {"ctx": SECRET_INPUT}, "Pat Smith"]}
    text = upstream_error_detail(_json(echo))
    assert text == UNRECOGNISED
    assert "Pat Smith" not in text


def test_an_answer_of_another_shape_is_shown_cut_as_it_always_was() -> None:
    """Neither service produces one; the cut keeps a stray gateway page or a stray body
    short."""
    assert upstream_error_detail(_json({"error": "CompileError"}, 422)) == (
        "{'error': 'CompileError'}"
    )
    assert len(upstream_error_detail(_json({"x": "y" * 1_000}))) == MAX_DETAIL_CHARS
    assert upstream_error_detail(_json({"detail": 5, "message": None})) == (
        "{'detail': 5, 'message': None}"
    )


def test_a_json_string_body_is_kept_cut() -> None:
    assert upstream_error_detail(_json("plain words")) == "plain words"
    assert len(upstream_error_detail(_json("w" * 1_000))) == MAX_DETAIL_CHARS


def test_a_body_that_is_not_json_is_cut_text() -> None:
    response = httpx.Response(400, text="Bad gateway " + "x" * 1_000)
    text = upstream_error_detail(response)
    assert text.startswith("Bad gateway") and len(text) == MAX_DETAIL_CHARS
