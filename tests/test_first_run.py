"""The first-run checklist the chat's `/learn` shows (`first_run`).

Two halves. The rules -- which of the five steps an account has done -- are run against
`tests/shared/first_run_steps.json`, the same table `web/src/lib/firstRunShared.test.ts` runs
against the web app's copy of them, so the two cannot drift apart. And the facts are read through
the stores, each filtered by the verified user, with a fact that cannot be read becoming unknown
and never a guess."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.first_run import (
    STEP_IDS,
    STEP_LABELS,
    STEP_PAGES,
    ApplicationFacts,
    FirstRunFacts,
    derive_first_run,
    load_first_run_facts,
)

_TABLE = json.loads((Path(__file__).parent / "shared" / "first_run_steps.json").read_text())
_USER = "00000000-0000-0000-0000-000000000001"


def _facts_of(case: dict[str, Any]) -> FirstRunFacts:
    facts = case["facts"]
    applications = facts["applications"]
    return FirstRunFacts(
        profile=facts["profile"],
        model_key=facts["model_key"],
        saved_searches=facts["saved_searches"],
        applications=(
            None
            if applications is None
            else ApplicationFacts(
                count=applications["count"],
                from_discover=applications["from_discover"],
                with_resume=applications["with_resume"],
            )
        ),
    )


# -- the rules, against the table both implementations share -----------------------------------


def test_the_shared_table_is_worth_running() -> None:
    names = [case["name"] for case in _TABLE["cases"]]
    assert len(names) >= 15 and len(set(names)) == len(names)
    assert _TABLE["step_order"] == list(STEP_IDS)


@pytest.mark.parametrize("case", _TABLE["cases"], ids=[c["name"] for c in _TABLE["cases"]])
def test_every_account_in_the_shared_table_comes_out_as_the_table_says(
    case: dict[str, Any],
) -> None:
    view = derive_first_run(_facts_of(case), case["searched"])

    assert [step.id for step in view.steps] == _TABLE["step_order"]
    assert {step.id: step.status for step in view.steps} == case["steps"]
    assert view.done_count == case["done_count"]
    assert (view.next_step.id if view.next_step else None) == case["next_step"]


def test_the_steps_are_named_and_placed_as_the_shared_table_says() -> None:
    assert _TABLE["labels"] == STEP_LABELS
    assert _TABLE["pages"] == STEP_PAGES
    view = derive_first_run(_facts_of(_TABLE["cases"][0]), False)
    assert {step.id: step.label for step in view.steps} == _TABLE["labels"]
    assert {step.id: step.page for step in view.steps} == _TABLE["pages"]


def test_without_the_browsers_memory_a_chat_never_says_the_first_search_is_todo() -> None:
    """The one place the chat cannot match the web: a search a person ran in their browser
    leaves nothing the chat can read. Whatever else the account holds, `searched=None` never
    produces 'todo' for it."""
    for case in _TABLE["cases"]:
        view = derive_first_run(_facts_of(case), None)
        first_search = next(s for s in view.steps if s.id == "first_search")
        assert first_search.status in {"done", "unknown"}, case["name"]


# -- reading the facts through the stores -------------------------------------------------------


class _Query:
    def __init__(self, table: _Table) -> None:
        self._table = table
        self.filters: list[tuple[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _Query:
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self.filters.append((column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> _Query:
        self.filters.append((column, tuple(values)))
        return self

    def order(self, *_: Any, **__: Any) -> _Query:
        return self

    def limit(self, *_: Any, **__: Any) -> _Query:
        return self

    def is_(self, *_: Any, **__: Any) -> _Query:
        return self

    @property
    def not_(self) -> _Query:
        return self

    async def execute(self) -> SimpleNamespace:
        self._table.queries.append(self.filters)
        if self._table.error is not None:
            raise self._table.error
        return SimpleNamespace(data=self._table.rows)


class _Table:
    def __init__(self, rows: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.queries: list[list[tuple[str, Any]]] = []

    def select(self, *_: Any, **__: Any) -> _Query:
        return _Query(self)


class _HonestAboutIn(_Table):
    """A table that answers an `in_` filter the way a database does: only the rows whose value
    is among the ones asked about come back. The plain `_Table` hands back its rows whatever was
    asked, so a lookup built from the wrong ids would still find what it expected."""

    def select(self, *_: Any, **__: Any) -> _Query:
        return _InQuery(self)


class _InQuery(_Query):
    async def execute(self) -> SimpleNamespace:
        table = self._table
        table.queries.append(self.filters)
        asked = [values for column, values in self.filters if column == "artifact_id"]
        rows = [row for row in table.rows if all(row["artifact_id"] in values for values in asked)]
        return SimpleNamespace(data=rows)


class _Supabase:
    def __init__(self, **tables: _Table) -> None:
        self.tables = {
            "profile_versions": _Table([]),
            "provider_credentials": _Table([]),
            "saved_searches": _Table([]),
            "applications": _Table([]),
            "artifact_versions": _Table([]),
        }
        self.tables.update(tables)

    def table(self, name: str) -> _Table:
        return self.tables[name]


async def test_a_brand_new_account_has_nothing_it_could_be_credited_with() -> None:
    facts = await load_first_run_facts(_Supabase(), _USER)  # type: ignore[arg-type]

    assert facts == FirstRunFacts(
        profile=False,
        model_key=False,
        saved_searches=0,
        applications=ApplicationFacts(count=0, from_discover=0, with_resume=0),
    )


async def test_the_facts_are_what_the_accounts_rows_say() -> None:
    resume_of_a = artifact_id_for("app-a", "resume")
    # Every channel has a different number of rows, so counting the wrong one cannot give the
    # right total by luck: two from Discover, one from the bot, one from the web, one with none.
    supabase = _Supabase(
        profile_versions=_Table([{"id": "v1", "activated_at": "2026-10-01T00:00:00Z"}]),
        provider_credentials=_Table(
            [
                {"service": "llm", "provider": "openrouter", "is_validated": True},
            ]
        ),
        saved_searches=_Table([{"id": "s1"}, {"id": "s2"}]),
        applications=_Table(
            [
                {"id": "app-a", "source_channel": "discover"},
                {"id": "app-b", "source_channel": "discover"},
                {"id": "app-c", "source_channel": "telegram"},
                {"id": "app-d", "source_channel": "web"},
                {"id": "app-e", "source_channel": None},
            ]
        ),
        artifact_versions=_Table([{"artifact_id": resume_of_a}]),
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts == FirstRunFacts(
        profile=True,
        model_key=True,
        saved_searches=2,
        applications=ApplicationFacts(count=5, from_discover=2, with_resume=1),
    )


@pytest.mark.parametrize("channel", ["telegram", "web", "extension", None])
async def test_an_application_from_anywhere_but_discover_does_not_show_a_search_was_run(
    channel: str | None,
) -> None:
    """The first-search step rests on an application tracked FROM Discover. One pasted on the
    web, sent over a chat or added by the extension says nothing about a search, so an account
    holding only those, and no saved search, is not credited with one."""
    supabase = _Supabase(
        applications=_Table(
            [
                {"id": "app-a", "source_channel": channel},
                {"id": "app-b", "source_channel": channel},
            ]
        )
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.applications is not None
    assert facts.applications.count == 2 and facts.applications.from_discover == 0
    first_search = next(s for s in derive_first_run(facts, None).steps if s.id == "first_search")
    assert first_search.status == "unknown"  # never "done"; and never "todo" without the browser


async def test_one_application_from_discover_shows_a_search_was_run() -> None:
    supabase = _Supabase(
        applications=_Table(
            [
                {"id": "app-a", "source_channel": "telegram"},
                {"id": "app-b", "source_channel": "discover"},
            ]
        )
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.applications is not None and facts.applications.from_discover == 1
    first_search = next(s for s in derive_first_run(facts, None).steps if s.id == "first_search")
    assert first_search.status == "done"


async def test_a_resume_counts_only_for_the_application_it_was_made_for() -> None:
    """The lookup asks about the RESUME of each of the account's own applications, and what comes
    back is matched against those. A cover letter, or a resume of an application that is not the
    account's, is not one of its resumes."""
    resume_ids = sorted(artifact_id_for(a, "resume") for a in ("app-a", "app-b"))
    supabase = _Supabase(
        applications=_Table(
            [
                {"id": "app-a", "source_channel": "web"},
                {"id": "app-b", "source_channel": "web"},
            ]
        ),
        artifact_versions=_HonestAboutIn(
            [
                {"artifact_id": artifact_id_for("app-a", "resume")},
                {"artifact_id": artifact_id_for("app-b", "cover_letter")},
                {"artifact_id": artifact_id_for("app-elsewhere", "resume")},
            ]
        ),
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.applications is not None and facts.applications.with_resume == 1
    ((asked,),) = [supabase.table("artifact_versions").queries]
    assert ("artifact_id", tuple(resume_ids)) in asked  # one id per application, in order


async def test_a_model_key_that_was_never_validated_is_not_a_model_key() -> None:
    supabase = _Supabase(
        provider_credentials=_Table(
            [
                {"service": "llm", "provider": "openrouter", "is_validated": False},
                {"service": "llm", "provider": "other", "is_validated": None},
            ]
        )
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.model_key is False


@pytest.mark.parametrize("validated_first", [True, False])
async def test_one_validated_key_among_unvalidated_ones_is_a_model_key(
    validated_first: bool,
) -> None:
    """Rows are unique per provider, so a person can hold a validated key next to one that was
    never validated; the validated one is what counts, wherever it sits in the list."""
    rows: list[dict[str, Any]] = [
        {"service": "llm", "provider": "openrouter", "is_validated": True},
        {"service": "llm", "provider": "other", "is_validated": False},
        {"service": "llm", "provider": "another", "is_validated": None},
    ]
    supabase = _Supabase(
        provider_credentials=_Table(rows if validated_first else list(reversed(rows)))
    )

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.model_key is True


async def test_every_read_is_for_the_verified_user_and_the_keys_are_only_the_models() -> None:
    supabase = _Supabase(applications=_Table([{"id": "app-a", "source_channel": "web"}]))

    await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    for name in ("profile_versions", "provider_credentials", "saved_searches", "applications"):
        queries = supabase.table(name).queries
        assert queries, name
        assert all(("user_id", _USER) in filters for filters in queries), name
    assert ("service", "llm") in supabase.table("provider_credentials").queries[0]
    # the resume lookup is scoped to the user too, and asks only about this user's applications
    ((resume_filters,),) = [supabase.table("artifact_versions").queries]
    assert ("user_id", _USER) in resume_filters
    assert ("artifact_id", (artifact_id_for("app-a", "resume"),)) in resume_filters


async def test_a_fact_that_cannot_be_read_is_unknown_and_leaves_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supabase = _Supabase(
        saved_searches=_Table([], error=ConnectionError("database down: secret-row-detail")),
        profile_versions=_Table([{"id": "v1", "activated_at": "2026-10-01T00:00:00Z"}]),
    )

    with caplog.at_level(logging.WARNING, logger="between_jobs.api.first_run"):
        facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]

    assert facts.saved_searches is None
    assert facts.profile is True
    assert facts.model_key is False and facts.applications is not None
    (record,) = caplog.records
    assert record.ctx == {"fact": "saved_searches"}  # type: ignore[attr-defined]
    assert record.exc_info is not None
    view = derive_first_run(facts, None)
    assert next(s for s in view.steps if s.id == "first_search").status == "unknown"


async def test_when_the_applications_cannot_be_read_three_steps_are_unknown() -> None:
    supabase = _Supabase(applications=_Table([], error=TimeoutError("slow")))

    facts = await load_first_run_facts(supabase, _USER)  # type: ignore[arg-type]
    view = derive_first_run(facts, None)

    assert facts.applications is None
    assert {s.id: s.status for s in view.steps} == {
        "profile": "todo",
        "model_key": "todo",
        "first_search": "unknown",
        "track_job": "unknown",
        "generate_resume": "unknown",
    }
    assert view.next_step is not None and view.next_step.id == "profile"
