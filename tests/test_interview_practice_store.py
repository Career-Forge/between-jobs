"""Tests for interview-practice-session persistence (InterviewForge R3,
interviewforge-v1.md)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.interview_practice import AnswerFeedback, PracticeQuestion
from between_jobs.api.interview_practice_store import (
    InterviewSessionNotFound,
    complete_session,
    create_session,
    get_next_unanswered_question,
    get_questions_for_session,
    get_session,
    list_sessions,
    record_answer,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_SESSION_ID = "80000000-0000-0000-0000-000000000001"
_QUESTION_ID = "90000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def is_(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[Any] = []
        self.update_calls: list[dict[str, Any]] = []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        self.insert_calls.append(data)
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)

    def update(self, data: dict[str, Any]) -> _ChainBuilder:
        self.update_calls.append(data)
        rows = [{**self.select_rows[0], **data}] if self.select_rows else []
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        sessions: _FakeTable | None = None,
        questions: _FakeTable | None = None,
    ) -> None:
        self.interview_sessions = sessions or _FakeTable(
            select_rows=[], insert_row={"id": _SESSION_ID, "application_id": _APPLICATION_ID}
        )
        self.interview_session_questions = questions or _FakeTable(select_rows=[])

    def table(self, name: str) -> Any:
        return {
            "interview_sessions": self.interview_sessions,
            "interview_session_questions": self.interview_session_questions,
        }[name]


def _question(**overrides: Any) -> PracticeQuestion:
    base: PracticeQuestion = {
        "question": "Tell me about a time you led a project.",
        "type": "behavioral",
        "target_skill": "leadership",
        "grounded_in": None,
    }
    return {**base, **overrides}  # type: ignore[typeddict-item]


def _feedback(**overrides: Any) -> AnswerFeedback:
    base: AnswerFeedback = {
        "score": 7,
        "structure_feedback": "Good.",
        "specificity_feedback": "Fine.",
        "star_coverage": {"situation": True, "task": True, "action": True, "result": False},
    }
    return {**base, **overrides}  # type: ignore[typeddict-item]


async def test_create_session_inserts_the_session_and_its_questions() -> None:
    supabase = _FakeSupabaseClient()

    result = await create_session(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        resume_evidence="Led a team of 5.",
        registry_entry_id=None,
        questions=[_question(), _question(question="Q2")],
    )

    assert result["session"]["id"] == _SESSION_ID
    assert len(supabase.interview_sessions.insert_calls) == 1
    assert supabase.interview_sessions.insert_calls[0]["company_name"] == "Acme"
    assert len(supabase.interview_session_questions.insert_calls) == 1
    inserted_questions = supabase.interview_session_questions.insert_calls[0]
    assert [q["ordinal"] for q in inserted_questions] == [0, 1]
    assert inserted_questions[0]["session_id"] == _SESSION_ID


async def test_create_session_with_no_questions_skips_the_questions_insert() -> None:
    supabase = _FakeSupabaseClient()

    await create_session(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        resume_evidence="",
        registry_entry_id=None,
        questions=[],
    )

    assert supabase.interview_session_questions.insert_calls == []


async def test_get_session_raises_when_not_found() -> None:
    supabase = _FakeSupabaseClient(sessions=_FakeTable(select_rows=[]))

    with pytest.raises(InterviewSessionNotFound):
        await get_session(supabase, _USER_ID, _SESSION_ID)  # type: ignore[arg-type]


async def test_get_session_returns_the_row_when_found() -> None:
    row = {"id": _SESSION_ID, "user_id": _USER_ID}
    supabase = _FakeSupabaseClient(sessions=_FakeTable(select_rows=[row]))

    result = await get_session(supabase, _USER_ID, _SESSION_ID)  # type: ignore[arg-type]

    assert result == row


async def test_list_sessions_returns_the_rows() -> None:
    rows = [{"id": _SESSION_ID, "application_id": _APPLICATION_ID}]
    supabase = _FakeSupabaseClient(sessions=_FakeTable(select_rows=rows))

    result = await list_sessions(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]

    assert result == rows


async def test_get_next_unanswered_question_returns_none_when_all_answered() -> None:
    supabase = _FakeSupabaseClient(questions=_FakeTable(select_rows=[]))

    result = await get_next_unanswered_question(supabase, _SESSION_ID)  # type: ignore[arg-type]

    assert result is None


async def test_get_next_unanswered_question_returns_the_top_row() -> None:
    row = {"id": _QUESTION_ID, "ordinal": 0}
    supabase = _FakeSupabaseClient(questions=_FakeTable(select_rows=[row]))

    result = await get_next_unanswered_question(supabase, _SESSION_ID)  # type: ignore[arg-type]

    assert result == row


async def test_record_answer_updates_the_question_row() -> None:
    row = {"id": _QUESTION_ID, "session_id": _SESSION_ID, "ordinal": 0}
    supabase = _FakeSupabaseClient(questions=_FakeTable(select_rows=[row]))

    result = await record_answer(
        supabase,  # type: ignore[arg-type]
        _QUESTION_ID,
        answer_text="My answer.",
        feedback=_feedback(),
    )

    assert result["answer_text"] == "My answer."
    assert result["score"] == 7
    assert result["feedback"]["star_coverage"]["situation"] is True
    assert result["answered_at"] is not None


async def test_complete_session_updates_status_and_completed_at() -> None:
    row = {"id": _SESSION_ID, "status": "in_progress"}
    supabase = _FakeSupabaseClient(sessions=_FakeTable(select_rows=[row]))

    result = await complete_session(supabase, _SESSION_ID)  # type: ignore[arg-type]

    assert result["status"] == "completed"
    assert result["completed_at"] is not None


async def test_get_questions_for_session_returns_the_rows() -> None:
    rows = [{"id": _QUESTION_ID, "ordinal": 0}]
    supabase = _FakeSupabaseClient(questions=_FakeTable(select_rows=rows))

    result = await get_questions_for_session(supabase, _SESSION_ID)  # type: ignore[arg-type]

    assert result == rows
