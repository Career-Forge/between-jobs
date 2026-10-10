"""The experience and projects stage (engines/generic/body.py) against a scripted model that
misbehaves in every way the failure policy is meant to survive.

The policy: one repair call listing what was wrong, then the candidate's own bullet in place of
whatever still fails, always a document, every rejected sentence reported as an "unsupported
claim" warning.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from generic_engine_fakes import (
    CREDENTIAL,
    SNAPSHOT,
    STEP0_ANSWER,
    ScriptedModel,
    body_answer,
    echo_body,
    offered_entries,
    profile,
)

from between_jobs.api.engine_contract import Step0Result
from between_jobs.engines.generic.body import (
    MAX_CLAIM_WARNINGS,
    MAX_REWRITE_CHARS,
    BodyResult,
    write_body,
)
from between_jobs.engines.generic.job import combine_terms, prepare_job
from between_jobs.engines.generic.llm import ModelSession, parse_object
from between_jobs.engines.generic.plan import Plan, build_plan, choose_shape
from between_jobs.engines.generic.sources import Corpus, build_corpus
from between_jobs.engines.generic.text import FIRST_PERSON, strip_list_marker

_NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _plan(corpus: Corpus, pages: int | None = None) -> Plan:
    job = prepare_job(SNAPSHOT)
    read = combine_terms(corpus, job, Step0Result.model_validate(json.loads(STEP0_ANSWER)))
    return build_plan(corpus, read, choose_shape(corpus, pages, "balanced", _NOW))


async def _run(
    model: ScriptedModel, profile_changes: dict[str, Any] | None = None
) -> tuple[BodyResult, Corpus, Plan]:
    corpus = build_corpus(profile(**(profile_changes or {})))
    job = prepare_job(SNAPSHOT)
    read = combine_terms(corpus, job, Step0Result.model_validate(json.loads(STEP0_ANSWER)))
    plan = _plan(corpus)
    result = await write_body(ModelSession(CREDENTIAL, model), job, read, plan)
    return result, corpus, plan


def _texts(result: BodyResult, pointer: str) -> list[str]:
    return [bullet.text for bullet in result.bullets[pointer]]


def _original(corpus: Corpus, pointer: str, number: int) -> str:
    return corpus.by_pointer[pointer].bullets[number]


def _good_answer(user: str) -> str:
    return echo_body(user)


def _with_one_bullet_changed(pointer: str, number: int, text: str) -> Any:
    """An answer that is faithful except that one bullet is replaced by `text`."""

    def answer(user: str) -> str:
        entries = json.loads(echo_body(user))
        for entry in entries["entries"]:
            if entry["pointer"] == pointer:
                entry["bullets"][number]["text"] = text
        return json.dumps(entries)

    return answer


# -- the good path ---------------------------------------------------------------------------


async def test_a_faithful_answer_costs_one_call_and_reports_nothing() -> None:
    model = ScriptedModel.faithful()

    result, corpus, plan = await _run(model)

    assert model.count("body") == 1
    assert result.claim_warnings == [] and result.notes == []
    for offer in plan.experience:
        assert len(result.bullets[offer.source.pointer]) >= offer.minimum
        assert len(result.bullets[offer.source.pointer]) <= offer.maximum
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)


async def test_the_prompt_offers_each_entry_by_pointer_with_numbered_bullets_and_limits() -> None:
    model = ScriptedModel.faithful()
    _result, corpus, plan = await _run(model)

    offered = offered_entries(model.last("body").user)

    assert set(offered) == {o.source.pointer for o in (*plan.experience, *plan.projects)}
    assert offered["/experience/0"] == list(corpus.by_pointer["/experience/0"].bullets)
    assert "minimum 2, maximum 5 bullets" in model.last("body").user


async def test_the_prompt_lists_an_entrys_tools_and_metrics_but_not_its_dates() -> None:
    model = ScriptedModel.faithful()
    await _run(model)

    first = model.last("body").user.split("[/experience/0]")[1].split("[/experience/1]")[0]
    facts = next(line for line in first.splitlines() if "tools, metrics and facts" in line)
    assert facts == (
        "  tools, metrics and facts: Senior Software Engineer; Northwind Labs; Remote; Python; "
        "Go; Kafka; PostgreSQL; Kubernetes; AWS; p95 latency -40%; $120K annual savings"
    )


async def test_a_rewording_that_keeps_every_fact_is_used_and_not_reported() -> None:
    reworded = "Cut p95 API latency 40% across 12 services by batching requests in Python and Go"
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, reworded)])

    result, *_ = await _run(model)

    assert _texts(result, "/experience/0")[0] == reworded
    assert result.claim_warnings == []
    assert model.count("body") == 1


async def test_json_in_a_code_fence_and_prose_around_it_is_read_without_a_repair() -> None:
    fenced = lambda user: "```json\n" + echo_body(user) + "\n```"  # noqa: E731
    chatty = lambda user: "Here is the answer:\n" + echo_body(user) + "\nHope that helps!"  # noqa: E731
    for reply in (fenced, chatty):
        model = ScriptedModel.faithful(body=[reply])

        result, *_ = await _run(model)

        assert model.count("body") == 1
        assert result.notes == [] and result.claim_warnings == []


# -- fabrication: repaired once, then replaced ------------------------------------------------

_INVENTED = [
    ("number", "Cut p95 API latency 45% across 12 services using Python", '"45%"'),
    ("tool", "Cut p95 API latency 40% across 12 services using Docker", '"Docker"'),
    ("employer", "Cut p95 API latency 40% across 12 services for Google", '"Google"'),
    ("job term", "Cut p95 API latency 40% across 12 services with a dbt layer", '"dbt"'),
    ("leadership", "Led the effort to cut p95 API latency 40% across 12 services", '"led"'),
    ("markup", r"Cut latency 40% \input{/etc/passwd} across 12 services", "'\\\\'"),
    ("link", "Cut latency 40% across 12 services, see https://evil.example.com", "link"),
]


@pytest.mark.parametrize(("kind", "bad_text", "mention"), _INVENTED, ids=[c[0] for c in _INVENTED])
async def test_an_invented_fact_is_repaired_once_and_the_repair_is_used(
    kind: str, bad_text: str, mention: str
) -> None:
    fixed = "Cut p95 API latency 40% across 12 services with batching in Python"
    model = ScriptedModel.faithful(
        body=[
            _with_one_bullet_changed("/experience/0", 0, bad_text),
            _with_one_bullet_changed("/experience/0", 0, fixed),
        ]
    )

    result, *_ = await _run(model)

    assert model.count("body") == 2
    assert _texts(result, "/experience/0")[0] == fixed
    (warning,) = result.claim_warnings
    assert warning.startswith("unsupported claim (removed before output):")
    assert bad_text[:40] in warning and mention in warning
    assert "[/experience/0]" in warning
    # the repair call was shown what was wrong, as data
    repair_prompt = model.calls[-1].user
    assert "<problems>" in repair_prompt and "/experience/0 bullet 1" in repair_prompt
    assert "<previous_answer>" in repair_prompt


@pytest.mark.parametrize(("kind", "bad_text", "mention"), _INVENTED, ids=[c[0] for c in _INVENTED])
async def test_an_invented_fact_that_survives_the_repair_is_replaced_by_the_original_bullet(
    kind: str, bad_text: str, mention: str
) -> None:
    again = _with_one_bullet_changed("/experience/0", 0, bad_text)
    model = ScriptedModel.faithful(body=[again, again])

    result, corpus, *_ = await _run(model)

    assert model.count("body") == 2  # one repair, never a third call
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert [w.split("):")[0] for w in result.claim_warnings] == [
        "unsupported claim (removed before output",
        "unsupported claim (replaced with your original wording",
    ]
    for emitted in _texts(result, "/experience/0"):
        assert "Docker" not in emitted and "Google" not in emitted and "45%" not in emitted


async def test_a_fact_is_checked_against_its_own_entry_not_the_whole_profile() -> None:
    """Airflow is in the profile, but it belongs to the second job: a bullet of the first job
    may not claim it."""
    bad = "Cut p95 API latency 40% across 12 services orchestrated with Airflow"
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, bad)] * 2)

    result, corpus, *_ = await _run(model)

    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert any("Airflow" in w for w in result.claim_warnings)


async def test_a_bullet_may_lean_on_its_entrys_own_tools_even_if_the_bullet_text_does_not() -> None:
    text = "Cut p95 API latency 40% across 12 services on AWS with PostgreSQL"
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, text)])

    result, *_ = await _run(model)

    assert _texts(result, "/experience/0")[0] == text and model.count("body") == 1


async def test_a_bullet_built_from_two_sources_may_use_either_ones_facts() -> None:
    def merged(user: str) -> str:
        entries = json.loads(echo_body(user))
        for entry in entries["entries"]:
            if entry["pointer"] == "/experience/0":
                entry["bullets"] = [
                    {
                        "from": [0, 1],
                        "text": "Cut p95 latency 40% across 12 services and processed 2M Kafka "
                        "events per day",
                    }
                ]
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[merged])

    result, *_ = await _run(model)

    assert model.count("body") == 1 and result.claim_warnings == []


# -- structure: pointers, sources, duplicates, shape -----------------------------------------


def _answer_with(extra_entry: dict[str, Any]) -> Any:
    def answer(user: str) -> str:
        entries = json.loads(echo_body(user))
        entries["entries"].append(extra_entry)
        return json.dumps(entries)

    return answer


async def test_a_pointer_that_was_never_offered_is_a_problem_and_its_entry_is_dropped() -> None:
    ghost = {"pointer": "/experience/9", "bullets": [{"from": [0], "text": "Ran Mars operations"}]}
    model = ScriptedModel.faithful(body=[_answer_with(ghost), _answer_with(ghost)])

    result, *_ = await _run(model)

    assert model.count("body") == 2
    assert "/experience/9" not in result.bullets
    assert 'the pointer "/experience/9" is not one of the entries given' in model.calls[-1].user
    assert result.claim_warnings == []  # a bad pointer is a format fault, not a claim


async def test_a_pointer_that_is_not_a_string_is_a_problem_too() -> None:
    weird = {"pointer": {"x": 1}, "bullets": []}
    model = ScriptedModel.faithful(body=[_answer_with(weird), echo_body])

    await _run(model)

    assert model.count("body") == 2


async def test_a_pointer_written_without_its_leading_slash_is_still_that_pointer() -> None:
    def sloppy(user: str) -> str:
        entries = json.loads(echo_body(user))
        for entry in entries["entries"]:
            entry["pointer"] = " " + entry["pointer"].lstrip("/") + " "
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[sloppy])

    result, *_ = await _run(model)

    assert model.count("body") == 1 and result.notes == [] and result.claim_warnings == []


async def test_a_pointer_used_twice_keeps_the_first_use_without_a_repair() -> None:
    duplicate = {
        "pointer": "/experience/0",
        "bullets": [
            {"from": [5], "text": "Introduced contract tests that caught 15 breaking changes"}
        ],
    }
    model = ScriptedModel.faithful(body=[_answer_with(duplicate)])

    result, *_ = await _run(model)

    assert model.count("body") == 1
    # the second use of the pointer named a bullet the first did not show: it is ignored
    assert all("contract tests" not in t for t in _texts(result, "/experience/0"))


async def test_a_source_bullet_used_twice_is_shown_once() -> None:
    def twice(user: str) -> str:
        entries = json.loads(echo_body(user))
        for entry in entries["entries"]:
            if entry["pointer"] == "/experience/0":
                entry["bullets"][1] = {
                    "from": [0],
                    "text": "Reduced p95 API latency by 40% across 12 services",
                }
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[twice])

    result, *_ = await _run(model)

    shown = _texts(result, "/experience/0")
    assert model.count("body") == 1
    assert sum("p95" in t for t in shown) == 1


@pytest.mark.parametrize(
    "bad_from",
    [[99], [-1], [], "x", [True], [0, 1, 2], None, [0.5]],
    ids=["too big", "negative", "empty", "string", "bool", "three sources", "missing", "float"],
)
async def test_a_bullet_that_does_not_cite_a_valid_source_is_a_problem(bad_from: Any) -> None:
    def broken(user: str) -> str:
        entries = json.loads(echo_body(user))
        for entry in entries["entries"]:
            if entry["pointer"] == "/experience/0":
                entry["bullets"][0] = {"from": bad_from, "text": "Reduced latency a lot"}
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[broken, broken])

    result, *_ = await _run(model)

    assert model.count("body") == 2
    assert "must list one or two of the entry" in model.calls[-1].user
    assert "Reduced latency a lot" not in _texts(result, "/experience/0")
    assert len(_texts(result, "/experience/0")) >= 2


async def test_a_bullet_over_the_length_limit_is_a_problem_but_not_a_claim() -> None:
    long_text = "Reduced p95 API latency by 40% across 12 services " + "using Python " * 30
    model = ScriptedModel.faithful(
        body=[_with_one_bullet_changed("/experience/0", 0, long_text)] * 2
    )

    result, corpus, *_ = await _run(model)

    assert model.count("body") == 2
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert result.claim_warnings == []


async def test_a_long_bullet_of_the_candidates_own_is_not_rejected_for_its_length() -> None:
    """The length limit is for what the model writes. Echoing the candidate's own wording back
    unchanged is not the model overreaching, whatever the length."""
    long_own = ("Reduced latency " + "by tuning every service in the fleet " * 7).strip()
    model = ScriptedModel.faithful()

    result, *_ = await _run(
        model,
        {"experience": [{**profile()["experience"][0], "bullets": [long_own, "Wrote a runbook"]}]},
    )

    assert len(long_own) > 260
    assert model.count("body") == 1 and result.claim_warnings == []
    assert _texts(result, "/experience/0")[0] == long_own


async def test_more_bullets_than_an_entrys_maximum_are_cut_to_the_maximum() -> None:
    def everything(user: str) -> str:
        offered = offered_entries(user)
        entries = {
            pointer: [([i], text) for i, text in enumerate(texts)]
            for pointer, texts in offered.items()
        }
        return body_answer(entries)

    model = ScriptedModel.faithful(body=[everything])

    result, _, plan = await _run(model)

    for offer in plan.experience:
        assert len(result.bullets[offer.source.pointer]) == offer.maximum


async def test_fewer_bullets_than_the_floor_are_topped_up_with_the_candidates_own() -> None:
    def one_each(user: str) -> str:
        offered = offered_entries(user)
        return body_answer({pointer: [([0], texts[0])] for pointer, texts in offered.items()})

    model = ScriptedModel.faithful(body=[one_each])

    result, corpus, plan = await _run(model)

    for offer in plan.experience:
        assert len(result.bullets[offer.source.pointer]) == offer.minimum
    topped_up = result.bullets["/experience/0"][1]
    assert topped_up.original is True
    assert topped_up.text in corpus.by_pointer["/experience/0"].bullets


# -- an answer that cannot be read ------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   ",
        "I cannot help with that.",
        "{not json",
        '["a", "list"]',
        '{"entries": "none"}',
        "null",
    ],
    ids=["empty", "blank", "prose", "truncated", "list", "wrong shape", "null"],
)
async def test_an_unreadable_answer_costs_one_repair_then_falls_back_to_the_candidates_bullets(
    garbage: str,
) -> None:
    model = ScriptedModel.faithful(body=[garbage, garbage])

    result, _, plan = await _run(model)

    assert model.count("body") == 2
    assert result.claim_warnings == []
    assert any("could not be used" in note for note in result.notes)
    # the section is still complete: every kept job has its minimum, in the candidate's words
    for offer in plan.experience:
        shown = result.bullets[offer.source.pointer]
        assert len(shown) >= offer.minimum
        assert all(b.original for b in shown)
        assert all(b.text in offer.source.bullets for b in shown)
    assert result.project_order  # projects fall back to the plan's own ranking


async def test_an_unreadable_first_answer_that_the_repair_fixes_is_used() -> None:
    model = ScriptedModel.faithful(body=["not json at all", echo_body])

    result, *_ = await _run(model)

    assert model.count("body") == 2 and result.notes == []


async def test_a_provider_failure_is_not_retried_here() -> None:
    from between_jobs.api.errors import ApiError

    model = ScriptedModel.faithful(body=[ApiError("PROVIDER_UNAVAILABLE", "down", retryable=True)])

    with pytest.raises(ApiError) as raised:
        await _run(model)

    assert raised.value.code == "PROVIDER_UNAVAILABLE"
    assert model.count("body") == 1


# -- pins ---------------------------------------------------------------------------------------


def _pin(entry: dict[str, Any], minimum: int | None) -> dict[str, Any]:
    return {**entry, "pin": {"mandatory": True, "min_bullets": minimum}}


async def test_a_rewrite_that_drops_a_pinned_entry_gets_it_back_with_its_minimum_bullets() -> None:
    base = profile()
    changes = {
        "experience": [
            base["experience"][0],
            _pin(base["experience"][1], 3),
            base["experience"][2],
        ],
        "projects": [_pin(base["projects"][1], 1), base["projects"][0]],
    }

    def without_the_pins(user: str) -> str:
        entries = json.loads(echo_body(user))
        entries["entries"] = [
            e for e in entries["entries"] if e["pointer"] not in {"/experience/1", "/projects/0"}
        ]
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[without_the_pins])

    result, *_ = await _run(model, changes)

    assert len(result.bullets["/experience/1"]) == 3  # the pin's minimum, in the candidate's words
    assert all(b.original for b in result.bullets["/experience/1"])
    assert "/projects/0" in result.project_order  # the pinned project (the plan ranks it first)
    assert result.bullets["/projects/0"]


async def test_a_pinned_project_beyond_the_model_limit_is_still_shown() -> None:
    base = profile()
    projects = [dict(base["projects"][0], name=f"p{i}") for i in range(5)]
    projects[4] = _pin(projects[4], None)

    def three_projects(user: str) -> str:
        offered = offered_entries(user)
        chosen = [p for p in offered if p.startswith("/projects/") and p != "/projects/4"][:4]
        return body_answer({p: [([0], offered[p][0])] for p in chosen})

    model = ScriptedModel.faithful(body=[three_projects])

    result, _, plan = await _run(model, {"projects": projects})

    assert plan.project_limit >= 2
    assert "/projects/4" in result.project_order


async def test_the_model_chooses_which_projects_and_in_what_order_up_to_the_limit() -> None:
    def second_then_first_then_extra(user: str) -> str:
        offered = offered_entries(user)
        return body_answer(
            {
                "/projects/1": [([0], offered["/projects/1"][0])],
                "/projects/0": [([0], offered["/projects/0"][0])],
                **{p: [([0], t[0])] for p, t in offered.items() if p.startswith("/experience/")},
            }
        )

    model = ScriptedModel.faithful(body=[second_then_first_then_extra])

    result, *_ = await _run(model)

    assert result.project_order == ["/projects/1", "/projects/0"]


async def test_the_model_may_choose_no_projects() -> None:
    def experience_only(user: str) -> str:
        offered = offered_entries(user)
        return body_answer({p: [([0], t[0])] for p, t in offered.items() if "experience" in p})

    model = ScriptedModel.faithful(body=[experience_only])

    result, *_ = await _run(model)

    assert result.project_order == []


async def test_the_parse_helper_reads_what_models_actually_send() -> None:
    assert parse_object('{"a": 1}') == {"a": 1}
    assert parse_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_object('Sure!\n{"a": 1}\nBye') == {"a": 1}
    assert parse_object("[1, 2]") is None
    assert parse_object("") is None
    assert parse_object("{" * 100000) is None


@pytest.mark.parametrize("digits", [4301, 5000, 100000])
def test_an_integer_too_long_to_convert_is_an_unusable_answer_not_an_error(digits: int) -> None:
    """Python refuses to turn more than 4300 digits into an int, with a plain `ValueError` that
    is not a `JSONDecodeError`. A model that rambles in digits must not take the request down."""
    big = "9" * digits
    for raw in (
        '{"a": ' + big + "}",
        '```json\n{"a": ' + big + "}\n```",
        big,
        'Sure: {"entries": [' + big + "]} bye",
    ):
        assert parse_object(raw) is None
    assert parse_object('{"a": ' + "9" * 4300 + "}") is not None  # the limit itself still parses


@pytest.mark.parametrize("which", ["first", "both"])
async def test_a_degenerate_run_of_digits_in_the_bullets_answer_is_repaired_like_any_junk(
    which: str,
) -> None:
    junk = (
        '{"entries": [{"pointer": "/experience/0", "bullets": [{"from": [' + "9" * 5000 + "]}]}]}"
    )
    model = ScriptedModel.faithful(body=[junk, junk if which == "both" else echo_body])

    result, _, plan = await _run(model)

    assert model.count("body") == 2
    assert result.bullets["/experience/0"]
    if which == "both":
        assert any("could not be used" in note for note in result.notes)
        for offer in plan.experience:
            assert all(b.original for b in result.bullets[offer.source.pointer])
    else:
        assert result.notes == []


async def test_only_the_most_relevant_ten_bullets_are_shown_and_only_those_can_be_cited() -> None:
    twelve = [f"Did task number {n} with Python" for n in range(10)] + [
        "Operated Kafka and Snowflake for the platform",  # relevant: must be shown
        "Wrote an unrelated memo",
    ]
    changes = {"experience": [{**profile()["experience"][0], "bullets": twelve}]}
    model = ScriptedModel.faithful()
    _result, _, plan = await _run(model, changes)

    offered = offered_entries(model.last("body").user)
    shown_numbers = list(plan.experience[0].shown)

    assert len(offered["/experience/0"]) == 10 == len(shown_numbers)
    assert 10 in shown_numbers  # the relevant bullet outranks the filler
    unseen = next(n for n in range(12) if n not in shown_numbers)

    def cites_an_unseen_bullet(user: str) -> str:
        entries = json.loads(echo_body(user))
        entries["entries"][0]["bullets"][0] = {"from": [unseen], "text": "Did something"}
        return json.dumps(entries)

    again = ScriptedModel.faithful(body=[cites_an_unseen_bullet, cites_an_unseen_bullet])
    await _run(again, changes)

    assert again.count("body") == 2  # citing a bullet it was not shown is a problem to repair


async def test_angle_brackets_are_entities_in_the_prompt_and_plain_again_in_the_answer() -> None:
    """The prompt turns `<` and `>` into entities so text cannot forge a tag, and leaves `&`
    alone so the model is not handed "R&amp;D" to copy. A model that copies an entity anyway is
    understood: the bullet comes out with the character it meant."""
    own = "Kept p95 latency < 50ms and > 99.9% uptime for R&D tooling"
    changes = {"experience": [{**profile()["experience"][0], "bullets": [own, "Wrote a runbook"]}]}

    def copies_the_entities(user: str) -> str:
        entries = json.loads(echo_body(user))
        return json.dumps(entries)

    model = ScriptedModel.faithful(body=[copies_the_entities])
    result, *_ = await _run(model, changes)

    prompt = model.last("body").user
    assert "latency &lt; 50ms and &gt; 99.9% uptime for R&D tooling" in prompt
    assert "&amp;" not in prompt
    assert model.count("body") == 1 and result.claim_warnings == []
    assert _texts(result, "/experience/0")[0] == own  # decoded, so it equals the candidate's own


# -- structure the model adds is code's to take off; markup it adds is a claim -----------------

_PLAIN = "Cut p95 API latency 40% across 12 services with batching in Python"


@pytest.mark.parametrize(
    "marker", ["- ", "* ", "+ ", "\u2022 ", "\u2023 ", "\u25cf ", "1. ", "2) ", "- - "]
)
async def test_a_list_marker_in_front_of_a_bullet_is_taken_off_without_a_repair(
    marker: str,
) -> None:
    model = ScriptedModel.faithful(
        body=[_with_one_bullet_changed("/experience/0", 0, marker + _PLAIN)]
    )

    result, *_ = await _run(model)

    assert model.count("body") == 1 and result.claim_warnings == [] and result.notes == []
    assert _texts(result, "/experience/0")[0] == _PLAIN


async def test_a_bullet_that_starts_with_a_number_is_not_mistaken_for_a_numbered_item() -> None:
    reworded = "40% faster p95 API latency across 12 services after batching in Python and Go"
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, reworded)])

    result, *_ = await _run(model)

    assert model.count("body") == 1 and result.claim_warnings == []
    assert _texts(result, "/experience/0")[0] == reworded


@pytest.mark.parametrize("text", ["40% faster", "3.5x faster", "-40% latency", "2024 was busy"])
def test_text_that_only_looks_like_a_list_marker_is_left_alone(text: str) -> None:
    assert strip_list_marker(text) == text


def test_a_list_marker_is_taken_off_at_most_twice() -> None:
    assert strip_list_marker("- - - x") == "- x"


@pytest.mark.parametrize(
    "emphasised",
    [
        "**Reduced** p95 API latency by 40% across 12 services",
        "_Reduced_ p95 API latency by 40% across 12 services",
        "Reduced p95 API latency by *40%* across 12 services",
    ],
)
async def test_markdown_emphasis_is_a_markup_claim_repaired_once_then_replaced(
    emphasised: str,
) -> None:
    again = _with_one_bullet_changed("/experience/0", 0, emphasised)
    model = ScriptedModel.faithful(body=[again, again])

    result, corpus, *_ = await _run(model)

    assert model.count("body") == 2
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert any("markup" in w or "character" in w for w in result.claim_warnings)
    assert all("*" not in t and "_" not in t for t in _texts(result, "/experience/0"))


async def test_an_asterisk_or_underscore_the_source_really_has_is_not_markup() -> None:
    own = "Renamed snake_case columns and fixed the C* index across 12 tables"
    changes = {"experience": [{**profile()["experience"][0], "bullets": [own, "Wrote a runbook"]}]}
    model = ScriptedModel.faithful()

    result, *_ = await _run(model, changes)

    assert model.count("body") == 1 and result.claim_warnings == []
    assert _texts(result, "/experience/0")[0] == own


@pytest.mark.parametrize(
    ("text", "mention"),
    [
        ("Owned the p95 API latency reduction across 12 services", '"owned"'),
        ("Owns the p95 API latency work in Python and Go", '"owns"'),
        ("Own the p95 API latency work in Python and Go", '"own"'),
        ("Manage the request batching in Python and Go", '"manage"'),
        ("Oversee the request batching in Python and Go", '"oversee"'),
        ("Principal engineer on the request batching in Python and Go", '"principal"'),
        ("Cut p95 API latency 40% across 12 services as the team's go-to expert", '"expert"'),
        ("Cut p95 API latency 40% across 12 services as a senior contributor", '"senior"'),
    ],
)
async def test_a_raised_role_ownership_or_level_is_repaired_once_then_replaced(
    text: str, mention: str
) -> None:
    again = _with_one_bullet_changed("/experience/0", 0, text)
    model = ScriptedModel.faithful(body=[again, again])
    base = profile()["experience"][0]
    # the entry's own title is a plain "Engineer": it claims neither a level nor a role
    changes = {"experience": [{**base, "title": "Engineer"}, *profile()["experience"][1:]]}

    result, corpus, *_ = await _run(model, changes)

    assert model.count("body") == 2
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert any(mention in w for w in result.claim_warnings), result.claim_warnings


@pytest.mark.parametrize("verb", ["Lead", "Manage", "Oversee", "Own", "Mentor"])
async def test_a_current_roles_present_tense_leadership_verb_is_not_a_free_upgrade(
    verb: str,
) -> None:
    """The prompt licenses the present tense for a current job, so "Lead", "Manage" and "Own"
    are what a model writes there. The source says the candidate "Wrote" the runbook."""
    upgraded = f"{verb} the on-call runbook used by a team of 8 engineers"
    again = _with_one_bullet_changed("/experience/0", 4, upgraded)
    model = ScriptedModel.faithful(body=[again, again])

    result, corpus, *_ = await _run(model)

    assert model.count("body") == 2  # one repair, never a third call
    assert upgraded not in _texts(result, "/experience/0")
    assert _original(corpus, "/experience/0", 4) in _texts(result, "/experience/0")
    assert any("claims a role" in w and verb.lower() in w.lower() for w in result.claim_warnings)


@pytest.mark.parametrize(
    ("source", "text"),
    [
        (
            "Owned the p95 latency work across 12 services",
            "Owns the p95 latency work across 12 services",
        ),
        ("Principal engineer on the batching work", "Principal engineer on the request batching"),
        ("Led the batching work across 12 services", "Manage the batching work across 12 services"),
    ],
)
async def test_the_same_word_is_fine_when_the_source_bullet_already_claims_it(
    source: str, text: str
) -> None:
    base = profile()["experience"][0]
    changes = {
        "experience": [{**base, "title": "Engineer", "bullets": [source, "Wrote a runbook"]}]
    }
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, text)])

    result, *_ = await _run(model, changes)

    assert model.count("body") == 1 and result.claim_warnings == []
    assert _texts(result, "/experience/0")[0] == text


async def test_a_first_person_bullet_is_a_format_problem_repaired_once() -> None:
    first_person = "I reduced p95 API latency by 40% across 12 services"
    again = _with_one_bullet_changed("/experience/0", 0, first_person)
    model = ScriptedModel.faithful(body=[again, again])

    result, corpus, *_ = await _run(model)

    assert model.count("body") == 2
    assert "uses the first person" in model.calls[-1].user
    assert _texts(result, "/experience/0")[0] == _original(corpus, "/experience/0", 0)
    assert result.claim_warnings == []  # a format fault, not a claim


async def test_the_candidates_own_first_person_bullet_is_theirs_to_have_written() -> None:
    own = "Presented my research on streaming latency to 30 colleagues"
    changes = {"experience": [{**profile()["experience"][0], "bullets": [own, "Wrote a runbook"]}]}
    model = ScriptedModel.faithful()

    result, *_ = await _run(model, changes)

    assert model.count("body") == 1 and _texts(result, "/experience/0")[0] == own


@pytest.mark.parametrize("pronoun", ["I", "my", "My", "me", "mine"])
def test_every_first_person_word_is_found_and_io_and_maine_are_not(pronoun: str) -> None:
    assert FIRST_PERSON.search(f"Said {pronoun} work")
    assert not FIRST_PERSON.search("Tuned Kafka I/O in Portland, ME and PRIME")


# -- exact sizes ---------------------------------------------------------------------------------


def _of_length(n: int) -> str:
    text = _PLAIN
    while len(text) + 4 <= n:
        text += " api"
    return text + "x" * (n - len(text)) if len(text) < n else text


@pytest.mark.parametrize(
    ("length", "accepted"),
    [(260, True), (261, False)],  # written out: the limit is 260
)
async def test_a_rewrite_of_exactly_the_longest_allowed_length_is_accepted_and_one_more_is_not(
    length: int, accepted: bool
) -> None:
    assert MAX_REWRITE_CHARS == 260
    text = _of_length(length)
    assert len(text) == length
    model = ScriptedModel.faithful(body=[_with_one_bullet_changed("/experience/0", 0, text)] * 2)

    result, *_ = await _run(model)

    assert model.count("body") == (1 if accepted else 2)
    assert (text in _texts(result, "/experience/0")) is accepted
    if not accepted:
        assert f"longer than {MAX_REWRITE_CHARS} characters" in model.calls[-1].user


async def test_the_claim_warnings_are_capped() -> None:
    base = profile()
    jobs = [
        {
            **base["experience"][0],
            "company": f"Co{n}",
            "start_date": f"{2010 + n}-01",
            "end_date": f"{2011 + n}-01",
            "is_current": False,
            "bullets": [f"Wrote part {n}.{k}" for k in range(6)],
        }
        for n in range(7)
    ]

    def all_fabricated(user: str) -> str:
        offered = offered_entries(user)
        return body_answer(
            {
                p: [([i], f"{t} for Google") for i, t in enumerate(texts)]
                for p, texts in offered.items()
            }
        )

    model = ScriptedModel.faithful(body=[all_fabricated, all_fabricated])

    result, *_ = await _run(model, {"experience": jobs, "projects": []})

    assert len(result.claim_warnings) == MAX_CLAIM_WARNINGS == 30
    assert all("Google" in w for w in result.claim_warnings)
