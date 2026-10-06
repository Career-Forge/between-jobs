"""The product-event log (api/product_events.py): the closed lists it shares with the database,
the guarantee that recording one never fails or slows the request that caused it, and what a
`tracked` block records.

What each route records is tested next to the route (test_discovery_routes.py,
test_prepare_application_route.py, test_resume_export_routes.py, test_extension_routes.py,
test_telegram_prepare_callback.py). What the real database does with the rows (grants, CHECK
constraints, cascades) is tests/integration/test_local_product_events.py, and that file is also
where the privacy promise (no column that could hold a URL or text) and the Python/SQL parity of
the closed lists are enforced against the live catalog of a database built from every migration.

The two closed-list and column-set checks in THIS file read only the migration that created the
table, so they describe the table as first created. They cannot see a later `alter table`, and
the repo forbids editing an applied migration, so a tripwire fails the moment any other migration
touches the table (see `_REVIEWED_ALTERATIONS`).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError
from postgrest.types import ReturnMethod

from between_jobs.api import product_events
from between_jobs.api.app import handle_api_error
from between_jobs.api.errors import ApiError
from between_jobs.api.job_registry_adapters import ADAPTERS
from between_jobs.api.product_events import (
    ATS_TYPES,
    CAPABILITIES,
    EVENTS,
    OUTCOMES,
    ats_type_of_url,
    capability_or_none,
    emit_event,
    emit_setup_required,
    flush,
    tracked,
)
from between_jobs.api.product_events import write_event as real_write_event
from between_jobs.api.search_providers import _TIER_1_PATTERNS

_ROOT = Path(__file__).parent.parent
_MIGRATIONS = _ROOT / "supabase" / "migrations"
_MIGRATION = next(_MIGRATIONS.glob("*_create_product_events_and_tester_enrollments.sql"))
_USER = "00000000-0000-0000-0000-0000000000a1"


# -- the closed lists the database and the code share ----------------------------------------

_REVIEWED_ALTERATIONS: frozenset[str] = frozenset()
"""Migrations (file names) other than the creating one that change `product_events`, each added
only after the live-catalog tests in tests/integration/test_local_product_events.py were updated
for what it changed. While this is non-empty the checks below that read only the creating
migration are skipped: they would describe a table that no longer exists."""

_creating_migration_is_current = pytest.mark.skipif(
    bool(_REVIEWED_ALTERATIONS),
    reason="a later migration changed product_events; the live-catalog tests are the guard",
)

_TABLE_CHANGE = re.compile(
    r"\b(?:alter\s+table|drop\s+table|(?:add|drop|alter)\s+(?:column|constraint)|rename)\b",
    re.IGNORECASE,
)


def _statements_that_change_the_event_table(sql: str) -> list[str]:
    """The statements in `sql` that alter, drop or rename the table, one of its columns or one
    of its constraints. Deliberately loose (any statement that names product_events and uses one
    of those verbs): a false alarm costs a minute, a miss costs the privacy guard."""
    sql = re.sub(r"--[^\n]*", "", sql)
    return [
        statement.strip()
        for statement in sql.split(";")
        if "product_events" in statement and _TABLE_CHANGE.search(statement)
    ]


def test_no_later_migration_has_changed_the_event_table_without_the_guards_being_updated() -> None:
    """The column-set and CHECK-parity checks below read only the creating migration. A change
    shipped as a newer migration (the only way to change an applied one) would pass them
    untouched, so this fails first and says what to do."""
    altered = {
        path.name: statements[0][:100]
        for path in sorted(_MIGRATIONS.glob("*.sql"))
        if path != _MIGRATION
        and path.name not in _REVIEWED_ALTERATIONS
        and (statements := _statements_that_change_the_event_table(path.read_text()))
    }

    assert altered == {}, (
        f"product_events was changed by {sorted(altered)}: update the column-set and CHECK-parity "
        "guards in tests/integration/test_local_product_events.py (they read the live catalog, "
        "so they see the change), then add the file to _REVIEWED_ALTERATIONS in this module"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "alter table public.product_events add column page_url text;",
        "alter table only public.product_events drop constraint product_events_event_check;",
        "ALTER TABLE IF EXISTS public.product_events\n  ADD COLUMN extra jsonb;",
        "alter table public.other drop constraint product_events_fk;",
        "drop table public.product_events;",
        "alter table public.product_events rename column n_a to something;",
    ],
)
def test_the_tripwire_notices_a_change_to_the_event_table(sql: str) -> None:
    """Guards the guard: each of these shapes must be seen in a later migration."""
    assert _statements_that_change_the_event_table(f"-- a comment\n{sql}\nselect 1;") != []


@pytest.mark.parametrize(
    "sql",
    [
        "update public.product_events set user_id = p_target_user_id where user_id = p_source;",
        "-- alter table public.product_events add column x text;\nselect 1;",
        "alter table public.other add column page_url text;",
        "create index on public.product_events (event, created_at);",
    ],
)
def test_the_tripwire_ignores_what_is_not_a_change_to_the_event_table(sql: str) -> None:
    assert _statements_that_change_the_event_table(sql) == []


def _migration_sql() -> str:
    return re.sub(r"--[^\n]*", "", _MIGRATION.read_text())


def _table_body(sql: str, table: str) -> str:
    """The text between the parentheses of `create table public.<table> (...)`."""
    start = sql.index(f"create table public.{table}")
    open_paren = sql.index("(", start)
    depth = 0
    for i in range(open_paren, len(sql)):
        depth += {"(": 1, ")": -1}.get(sql[i], 0)
        if depth == 0:
            return sql[open_paren + 1 : i]
    raise AssertionError("unbalanced parenthesis in the migration")


def _check_list(table: str, column: str) -> set[str]:
    body = _table_body(_migration_sql(), table)
    match = re.search(
        rf"\b{column}\s+text\s+(?:not\s+null\s+)?check\s*\(\s*{column}\s+in\s*\((.*?)\)\s*\)",
        body,
        re.DOTALL,
    )
    assert match is not None, f"no `{column} in (...)` CHECK on {table}"
    values = re.findall(r"'([^']*)'", match.group(1))
    assert len(values) == len(set(values)), f"{table}.{column} lists a value twice"
    return set(values)


@_creating_migration_is_current
@pytest.mark.parametrize(
    ("column", "python"),
    [
        ("event", EVENTS),
        ("outcome", OUTCOMES),
        ("capability", CAPABILITIES),
        ("ats_type", ATS_TYPES),
    ],
)
def test_each_closed_list_in_python_is_the_one_in_the_migration(
    column: str, python: frozenset[str]
) -> None:
    """The Literals in product_events.py and the CHECKs in the migration are two copies of one
    list. A value only in Python makes every such insert fail its CHECK (and fail open, so
    silently); a value only in SQL is a value nothing can send. (This reads the creating
    migration only; the live-catalog twin in tests/integration/test_local_product_events.py
    reads what the database actually enforces, whichever migration last changed it.)"""
    assert _check_list("product_events", column) == set(python)


def test_the_enrollment_cohorts_and_seniority_bands_are_the_ones_the_program_defined() -> None:
    assert _check_list("tester_enrollments", "role_cohort") == {
        # eight core cohorts, which gate the program
        "data_analyst",
        "data_engineer",
        "data_scientist",
        "ai_ml_engineer",
        "software_engineer",
        "frontend_engineer",
        "devops_sre",
        "qa_sdet",
        # two stretch cohorts, reported but not gating
        "product_manager",
        "business_analyst",
    }
    assert _check_list("tester_enrollments", "seniority") == {
        "new_grad",
        "early_career",
        "mid",
        "senior",
        "lead_plus",
    }


def _capability_keys_used_by_the_backend() -> set[str]:
    """Every capability key a handler resolves a credential for or names in an error: a string
    literal passed as `capability=...`, or assigned to a module constant whose name ends in
    CAPABILITY."""
    found: set[str] = set()
    for path in sorted((_ROOT / "src" / "between_jobs").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.keyword)
                and node.arg == "capability"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                found.add(node.value.value)
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and any(
                    isinstance(t, ast.Name) and t.id.endswith("CAPABILITY") for t in node.targets
                )
            ):
                found.add(node.value.value)
    # `default` is the fallback preference every capability inherits, not a capability a
    # request is ever about (credentials_routes writes it; nothing resolves it by name).
    found.discard("default")
    return found


def test_the_capability_list_is_exactly_what_the_backend_uses() -> None:
    """Fails when a route starts naming a capability the log does not know (its setup_required
    events would be recorded with no capability), or when the list keeps one nothing uses."""
    used = _capability_keys_used_by_the_backend()
    assert len(used) >= 10, "the scan found almost nothing, so it is not scanning"
    assert used == set(CAPABILITIES)


def test_every_applicant_tracking_system_the_backend_knows_can_be_recorded() -> None:
    """The registry's adapters and the URL classifier are where an ATS name comes from; the list
    must include every one, or a posting on it is recorded as 'unknown'."""
    classifier = {
        label.removeprefix("ats:") for _, label in _TIER_1_PATTERNS if label.startswith("ats:")
    }
    assert set(ADAPTERS) <= set(ATS_TYPES)
    assert classifier <= set(ATS_TYPES)
    assert all(re.fullmatch(r"[a-z]+", name) for name in ATS_TYPES)


def _column_definitions(body: str) -> dict[str, str]:
    """{column: type} from a create-table body: split at the commas that are not inside a
    parenthesis (a CHECK's list has commas of its own), skip the table-level constraints."""
    parts: list[str] = []
    depth, current = 0, ""
    for char in body:
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        current += char
    parts.append(current)
    columns: dict[str, str] = {}
    for part in parts:
        tokens = part.split()
        if tokens and tokens[0] not in {"constraint", "check", "primary", "foreign", "unique"}:
            columns[tokens[0]] = tokens[1]
    return columns


@_creating_migration_is_current
def test_the_event_table_has_no_column_that_could_hold_a_url_or_text() -> None:
    """The privacy promise, as a test: product_events has exactly these columns, none of them
    free text, a JSON blob or binary. The only text columns are closed lists (each has a CHECK
    `in (...)`), so nothing but a known name can ever be stored in one. (As first created; the
    same promise is enforced against the live catalog in
    tests/integration/test_local_product_events.py, which a later `alter table` cannot dodge.)"""
    columns = _column_definitions(_table_body(_migration_sql(), "product_events"))

    assert columns == {
        "id": "uuid",
        "user_id": "uuid",
        "event": "text",
        "capability": "text",
        "application_id": "uuid",
        "ats_type": "text",
        "outcome": "text",
        "n_a": "integer",
        "n_b": "integer",
        "duration_ms": "integer",
        "created_at": "timestamptz",
    }
    for name, kind in columns.items():
        if kind == "text":
            assert _check_list("product_events", name), f"{name} is free text"
    # and no column name even suggests the things that must never be kept
    suggestive = r"url|path|host|domain|ip_|agent|body|content|payload|json"
    assert [name for name in columns if re.search(suggestive, name)] == []


def test_the_column_scan_would_notice_a_free_text_column() -> None:
    """Guards the guard: a url or a blob added to the table must change what the scan sees."""
    body = "id uuid primary key, event text check (event in ('a', 'b')), page_url text, extra jsonb"
    assert _column_definitions(body) == {
        "id": "uuid",
        "event": "text",
        "page_url": "text",
        "extra": "jsonb",
    }


# -- the helpers -----------------------------------------------------------------------------


def test_a_known_capability_is_kept_and_an_unknown_one_becomes_none() -> None:
    assert capability_or_none("job_scoring") == "job_scoring"
    assert capability_or_none("a_capability_added_tomorrow") is None
    assert capability_or_none(None) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123?gh_src=tracking", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://jobs.lever.co/acme/abc-def", "lever"),
        ("https://jobs.ashbyhq.com/acme/uuid", "ashby"),
        ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/NYC/Engineer_R1", "workday"),
        ("https://careers.example.com/jobs/1", None),  # a company's own page: not an ATS we know
        ("https://www.linkedin.com/jobs/view/1", None),  # an aggregator, not an ATS
        ("", None),
        (None, None),
    ],
)
def test_the_ats_of_a_posting_url_is_its_name_or_unknown(
    url: str | None, expected: str | None
) -> None:
    assert ats_type_of_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # a long but real URL: the host decides, and it is at the front
        (
            "https://acme.wd5.myworkdayjobs.com/en-US/External/job/" + "New-York-NY/" * 20 + "R1",
            "workday",
        ),
        # tracking junk far past anything that matters
        ("https://boards.greenhouse.io/acme/jobs/1?" + "x" * 5000, "greenhouse"),
        # the system's name appearing only after the part that is looked at is not a match
        ("https://careers.example.com/" + "a" * 5000 + "jobs.lever.co", None),
    ],
)
def test_only_the_front_of_a_long_url_decides_its_ats(url: str, expected: str | None) -> None:
    assert ats_type_of_url(url) == expected


def test_a_huge_url_is_never_handed_whole_to_the_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classifier's patterns take time quadratic in an unbroken run of word characters, and
    the URL is whatever a user pasted. Deterministic twin of the timing test below."""
    seen: list[int] = []

    def spy(url: str) -> None:
        seen.append(len(url))

    monkeypatch.setattr(product_events, "ats_label_for_url", spy)

    assert ats_type_of_url("https://" + "a" * 100_000) is None
    assert seen == [product_events._MAX_CLASSIFIED_URL_CHARS]


@pytest.mark.parametrize("filler", ["a", "-", "1", "_"])
def test_an_unbroken_run_of_word_characters_in_a_url_cannot_stall_the_loop(filler: str) -> None:
    """Uncapped, this takes about a second per 8,000 characters and grows with the square of the
    length (a minute and a half for 100,000); capped it is a millisecond."""
    started = time.monotonic()

    assert ats_type_of_url("https://" + filler * 30_000) is None

    assert time.monotonic() - started < 0.5


# -- recording never fails or slows the request ----------------------------------------------


class _FakeSupabase:
    """Only what the writer touches: `table(...).insert(row, returning=...).execute()`."""

    def __init__(self, *, error: BaseException | None = None, hang: bool = False) -> None:
        self.error = error
        self.hang = hang
        self.inserted: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self._table = ""
        self._row: dict[str, Any] = {}
        self._kwargs: dict[str, Any] = {}

    def table(self, name: str) -> _FakeSupabase:
        self._table = name
        return self

    def insert(self, row: dict[str, Any], **kwargs: Any) -> _FakeSupabase:
        self._row, self._kwargs = row, kwargs
        return self

    async def execute(self) -> SimpleNamespace:
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        self.inserted.append((self._table, self._row, self._kwargs))
        return SimpleNamespace(data=[])


@pytest.fixture
def real_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Puts the real writer back (the suite's default is a no-op) and shortens its timeout."""
    monkeypatch.setattr(product_events, "write_event", real_write_event)
    monkeypatch.setattr(product_events, "_INSERT_TIMEOUT_SECONDS", 0.05)


async def test_the_writer_inserts_one_row_and_asks_for_nothing_back(real_writer: None) -> None:
    supabase = _FakeSupabase()

    stored = await real_write_event(supabase, {"user_id": _USER, "event": "discover_search"})  # type: ignore[arg-type]

    assert stored is True
    ((table, row, kwargs),) = supabase.inserted
    assert table == "product_events"
    assert row == {"user_id": _USER, "event": "discover_search"}
    assert kwargs == {"returning": ReturnMethod.minimal}


async def test_a_failing_insert_is_logged_and_reported_never_raised(
    real_writer: None, caplog: pytest.LogCaptureFixture
) -> None:
    error = APIError(
        {
            "message": "new row violates check constraint, Key (user_id)=(secret-looking) ...",
            "code": "23514",
            "hint": None,
            "details": None,
        }
    )

    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        stored = await real_write_event(
            _FakeSupabase(error=error),  # type: ignore[arg-type]
            {"user_id": _USER, "event": "extension_fill"},
        )

    assert stored is False
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    ctx = record.ctx  # type: ignore[attr-defined]
    assert ctx == {
        "event": "extension_fill",
        "error_type": "postgrest.exceptions.APIError",
        "error_code": "23514",
    }
    # the database's message can quote the row, so it is never logged
    assert "secret-looking" not in caplog.text


@pytest.mark.parametrize("error", [RuntimeError("boom"), ConnectionError("down"), KeyError("x")])
async def test_any_ordinary_exception_from_the_insert_is_swallowed(
    real_writer: None, error: Exception
) -> None:
    assert await real_write_event(_FakeSupabase(error=error), {"event": "x"}) is False  # type: ignore[arg-type]


async def test_a_client_that_is_not_even_a_client_is_swallowed_too(real_writer: None) -> None:
    assert await real_write_event(object(), {"event": "x"}) is False  # type: ignore[arg-type]


async def test_a_hung_insert_is_cut_off_at_the_timeout(
    real_writer: None, caplog: pytest.LogCaptureFixture
) -> None:
    started = time.monotonic()

    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        # the outer bound only makes a regression fail in seconds instead of hanging the suite
        stored = await asyncio.wait_for(
            real_write_event(_FakeSupabase(hang=True), {"event": "discover_search"}),  # type: ignore[arg-type]
            timeout=5.0,
        )

    assert stored is False
    assert time.monotonic() - started < 1.0
    assert caplog.records[0].ctx["error_type"] == "builtins.TimeoutError"  # type: ignore[attr-defined]


def test_the_timeout_is_two_seconds_in_production() -> None:
    assert product_events._INSERT_TIMEOUT_SECONDS == 2.0


_HTTPX_DEFAULT_POOL = 100
"""httpx's default `max_connections`, the pool the supabase client shares between every query."""


def test_the_in_flight_cap_is_twenty_in_production() -> None:
    """Every other test sets the cap to 2; this is the number that ships."""
    assert product_events.MAX_IN_FLIGHT == 20


def test_hung_inserts_can_never_take_more_than_half_of_the_connection_pool() -> None:
    """If the table stalls, up to MAX_IN_FLIGHT inserts each hold a pooled connection for the
    whole insert timeout. Staying at or under half the pool leaves real queries a connection."""
    assert product_events.MAX_IN_FLIGHT <= _HTTPX_DEFAULT_POOL // 2


@pytest.mark.parametrize(
    ("code", "logged"),
    [
        ("23514", True),  # a SQLSTATE
        ("PGRST116", True),  # a PostgREST code
        ("x" * 16, True),  # the longest that is kept
        ("x" * 17, False),  # one past it
        ('relation "t" violates (user_id)=(secret-looking)', False),  # free text, not a code
        (23514, False),  # not a string
        (None, False),
    ],
)
async def test_only_a_short_string_code_from_an_error_is_ever_logged(
    real_writer: None, caplog: pytest.LogCaptureFixture, code: object, logged: bool
) -> None:
    """The log line carries the error's type and, when it is a short string, its SQLSTATE. An
    exception's `.code` can be anything, including text that quotes a value."""
    error = RuntimeError("boom")
    error.code = code  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        stored = await real_write_event(_FakeSupabase(error=error), {"event": "extension_fill"})  # type: ignore[arg-type]

    assert stored is False
    (record,) = caplog.records
    ctx = record.ctx  # type: ignore[attr-defined]
    assert ctx["error_type"] == "builtins.RuntimeError"
    assert ctx.get("error_code") == (code if logged else None)
    if not logged:
        assert "error_code" not in ctx
        assert "secret-looking" not in caplog.text


async def test_cancellation_is_never_swallowed(real_writer: None) -> None:
    """`except Exception` must not catch it: a cancelled request has to stay cancelled."""
    with pytest.raises(asyncio.CancelledError):
        await real_write_event(_FakeSupabase(error=asyncio.CancelledError()), {"event": "x"})  # type: ignore[arg-type]


async def test_emitting_returns_at_once_even_when_the_database_never_answers(
    real_writer: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(product_events, "_INSERT_TIMEOUT_SECONDS", 0.3)
    supabase = _FakeSupabase(hang=True)

    started = time.monotonic()
    emit_event(supabase, _USER, "discover_search", outcome="ok")  # type: ignore[arg-type]
    elapsed = time.monotonic() - started

    assert elapsed < 0.1, "emit_event waited for the insert"
    assert supabase.inserted == []
    await flush(timeout=2.0)  # the bounded insert finishes (by timing out) and is forgotten
    assert product_events._live_tasks() == []


async def test_emitting_never_raises_whatever_the_client_does(real_writer: None) -> None:
    emit_event(object(), _USER, "discover_search")  # type: ignore[arg-type]
    emit_event(_FakeSupabase(error=RuntimeError("down")), _USER, "extension_fill")  # type: ignore[arg-type]
    await flush(timeout=2.0)


def test_emitting_with_no_running_loop_is_a_logged_no_op_not_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        emit_event(_FakeSupabase(), _USER, "discover_search")  # type: ignore[arg-type]

    assert "not scheduled" in caplog.text


async def test_a_burst_past_the_cap_is_dropped_not_queued(
    real_writer: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(product_events, "MAX_IN_FLIGHT", 2)
    monkeypatch.setattr(product_events, "_INSERT_TIMEOUT_SECONDS", 0.3)
    supabase = _FakeSupabase(hang=True)

    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        for _ in range(5):
            emit_event(supabase, _USER, "discover_search")  # type: ignore[arg-type]

    assert len(product_events._live_tasks()) == 2
    assert caplog.text.count("dropped") == 3
    await flush(timeout=2.0)


async def test_flush_waits_for_the_inserts_in_flight(real_writer: None) -> None:
    supabase = _FakeSupabase()

    emit_event(supabase, _USER, "extension_fill", n_a=3, n_b=2)  # type: ignore[arg-type]
    assert supabase.inserted == []  # scheduled, not yet run
    await flush()

    assert [row["event"] for _t, row, _k in supabase.inserted] == ["extension_fill"]


async def test_a_task_stranded_on_a_closed_loop_does_not_count_against_the_cap() -> None:
    """Tests (and a restarted worker) can leave a task behind on a loop that has been closed;
    it would otherwise sit in the set forever and eat into the cap."""
    closed = asyncio.new_event_loop()
    closed.close()

    class Stranded:  # all the set ever asks of a task is which loop it belongs to
        def get_loop(self) -> asyncio.AbstractEventLoop:
            return closed

    stranded: Any = Stranded()
    product_events._in_flight.add(stranded)
    try:
        assert product_events._live_tasks() == []
        assert stranded not in product_events._in_flight
    finally:
        product_events._in_flight.discard(stranded)


# -- what a row holds ------------------------------------------------------------------------


async def test_only_what_is_set_goes_into_the_row(recorded_events: list[dict[str, Any]]) -> None:
    emit_event(
        None,  # type: ignore[arg-type]
        _USER,
        "extension_fill",
        ats_type="lever",
        outcome="partial",
        n_a=12,
        n_b=9,
    )
    emit_event(None, _USER, "copy_panel_opened")  # type: ignore[arg-type]

    assert recorded_events == [
        {
            "user_id": _USER,
            "event": "extension_fill",
            "ats_type": "lever",
            "outcome": "partial",
            "n_a": 12,
            "n_b": 9,
        },
        {"user_id": _USER, "event": "copy_panel_opened"},
    ]


async def test_counts_are_clamped_into_what_the_column_holds(
    recorded_events: list[dict[str, Any]],
) -> None:
    emit_event(None, _USER, "discover_search", n_a=-4, n_b=2**40, duration_ms=0)  # type: ignore[arg-type]

    (row,) = recorded_events
    assert row["n_a"] == 0
    assert row["n_b"] == 2_147_483_647
    assert row["duration_ms"] == 0  # a zero is a value, not "unset"


# -- tracked ---------------------------------------------------------------------------------


async def test_a_block_that_finishes_records_an_ok_event_with_its_numbers(
    recorded_events: list[dict[str, Any]],
) -> None:
    async with tracked(None, _USER, "discover_search") as event:  # type: ignore[arg-type]
        event.n_a = 40
        event.n_b = 25

    (row,) = recorded_events
    assert row["user_id"] == _USER
    assert row["event"] == "discover_search"
    assert row["outcome"] == "ok"
    assert (row["n_a"], row["n_b"]) == (40, 25)
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
    assert "capability" not in row and "application_id" not in row


async def test_the_block_can_say_it_was_only_partly_done(
    recorded_events: list[dict[str, Any]],
) -> None:
    async with tracked(None, _USER, "prepare_finished") as event:  # type: ignore[arg-type]
        event.outcome = "partial"
        event.application_id = "30000000-0000-0000-0000-000000000001"
        event.ats_type = "greenhouse"

    (row,) = recorded_events
    assert row["outcome"] == "partial"
    assert row["application_id"] == "30000000-0000-0000-0000-000000000001"
    assert row["ats_type"] == "greenhouse"


async def test_the_duration_is_how_long_the_block_took(
    recorded_events: list[dict[str, Any]],
) -> None:
    async with tracked(None, _USER, "discover_search"):  # type: ignore[arg-type]
        await asyncio.sleep(0.05)

    assert 40 <= recorded_events[0]["duration_ms"] < 1000


async def test_a_setup_required_error_is_recorded_with_its_capability_and_still_raised(
    recorded_events: list[dict[str, Any]],
) -> None:
    error = ApiError("SETUP_REQUIRED", "add a key", capability="job_scoring")

    with pytest.raises(ApiError) as caught:
        async with tracked(None, _USER, "discover_search"):  # type: ignore[arg-type]
            raise error

    assert caught.value is error  # the very same exception, untouched
    (row,) = recorded_events
    assert (row["outcome"], row["capability"]) == ("setup_required", "job_scoring")


async def test_a_setup_error_naming_an_unknown_capability_is_still_recorded(
    recorded_events: list[dict[str, Any]],
) -> None:
    with pytest.raises(ApiError):
        async with tracked(None, _USER, "discover_search"):  # type: ignore[arg-type]
            raise ApiError("SETUP_REQUIRED", "x", capability="brand_new_capability")

    (row,) = recorded_events
    assert row["outcome"] == "setup_required"
    assert "capability" not in row


@pytest.mark.parametrize("error", [ApiError("NOT_FOUND", "gone"), RuntimeError("boom")])
async def test_any_other_error_is_recorded_as_failed_and_still_raised(
    recorded_events: list[dict[str, Any]], error: Exception
) -> None:
    with pytest.raises(type(error)) as caught:
        async with tracked(None, _USER, "prepare_finished"):  # type: ignore[arg-type]
            raise error

    assert caught.value is error
    assert recorded_events[0]["outcome"] == "failed"
    assert "capability" not in recorded_events[0]


async def test_a_block_that_has_not_armed_its_event_records_nothing_whatever_happens(
    recorded_events: list[dict[str, Any]],
) -> None:
    async with tracked(None, _USER, "prepare_finished", armed=False):  # type: ignore[arg-type]
        pass
    with pytest.raises(ApiError):
        async with tracked(None, _USER, "prepare_finished", armed=False):  # type: ignore[arg-type]
            raise ApiError("NOT_FOUND", "not yours")
    with pytest.raises(RuntimeError):
        async with tracked(None, _USER, "prepare_finished", armed=False):  # type: ignore[arg-type]
            raise RuntimeError("boom")

    assert recorded_events == []


async def test_a_block_that_arms_itself_part_way_records_from_then_on(
    recorded_events: list[dict[str, Any]],
) -> None:
    with pytest.raises(ApiError):
        async with tracked(None, _USER, "prepare_finished", armed=False) as event:  # type: ignore[arg-type]
            event.armed = True
            raise ApiError("SETUP_REQUIRED", "x", capability="profile")

    assert [r["outcome"] for r in recorded_events] == ["setup_required"]


async def test_a_cancelled_block_records_nothing(recorded_events: list[dict[str, Any]]) -> None:
    started = asyncio.Event()

    async def work() -> None:
        async with tracked(None, _USER, "discover_search"):  # type: ignore[arg-type]
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(work())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorded_events == []


async def test_tracking_never_changes_what_the_block_returns() -> None:
    async def route() -> dict[str, Any]:
        async with tracked(None, _USER, "discover_search") as event:  # type: ignore[arg-type]
            event.n_a = 1
            return {"scored": [1, 2, 3]}

    assert await route() == {"scored": [1, 2, 3]}


async def test_tracking_survives_a_writer_that_blows_up_when_scheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("the writer itself is broken")

    monkeypatch.setattr(product_events, "write_event", explode)

    async with tracked(None, _USER, "discover_search") as event:  # type: ignore[arg-type]
        event.n_a = 1

    with pytest.raises(ApiError):  # and the block's own error is still the one that surfaces
        async with tracked(None, _USER, "discover_search"):  # type: ignore[arg-type]
            raise ApiError("NOT_FOUND", "gone")


# -- the setup_required event ----------------------------------------------------------------


async def test_a_setup_required_error_for_a_signed_in_user_is_one_event(
    recorded_events: list[dict[str, Any]],
) -> None:
    emit_setup_required(
        object(),  # type: ignore[arg-type]
        _USER,
        ApiError("SETUP_REQUIRED", "x", capability="company_intel", missing=["credential"]),
    )

    assert recorded_events == [
        {
            "user_id": _USER,
            "event": "setup_required",
            "capability": "company_intel",
            "outcome": "setup_required",
        }
    ]


async def test_a_setup_error_naming_an_unknown_capability_is_recorded_without_it(
    recorded_events: list[dict[str, Any]],
) -> None:
    """A capability the log does not list yet must not reach the insert: it would fail the
    column's CHECK, be logged and dropped, and the stop would vanish from the cohort reports."""
    emit_setup_required(
        object(),  # type: ignore[arg-type]
        _USER,
        ApiError("SETUP_REQUIRED", "x", capability="brand_new_capability"),
    )

    assert recorded_events == [
        {"user_id": _USER, "event": "setup_required", "outcome": "setup_required"}
    ]


@pytest.mark.parametrize(
    ("client", "user", "code"),
    [
        (object(), _USER, "NOT_FOUND"),  # not a setup error
        (object(), _USER, "AUTH_REQUIRED"),
        (None, _USER, "SETUP_REQUIRED"),  # nothing to record with
        (object(), None, "SETUP_REQUIRED"),  # nobody signed in
    ],
)
async def test_nothing_is_recorded_unless_it_is_a_setup_error_for_a_known_user(
    recorded_events: list[dict[str, Any]], client: object, user: str | None, code: Any
) -> None:
    emit_setup_required(client, user, ApiError(code, "x"))  # type: ignore[arg-type]

    assert recorded_events == []


# -- the hourly cap on setup_required events -------------------------------------------------

_OTHER_USER = "00000000-0000-0000-0000-0000000000b2"
_SETUP_ERROR = ApiError("SETUP_REQUIRED", "x", capability="profile")


@pytest.fixture
def no_in_flight_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests emit dozens of events in a row without ever yielding to the loop, so every
    insert is still pending; the in-flight cap would drop them before the hourly cap is reached."""
    monkeypatch.setattr(product_events, "MAX_IN_FLIGHT", 10_000)


def _stops(recorded: list[dict[str, Any]], user: str) -> int:
    return sum(1 for row in recorded if row["user_id"] == user)


async def test_a_hundred_setup_stops_for_one_user_record_only_the_hourly_cap(
    recorded_events: list[dict[str, Any]], no_in_flight_limit: None
) -> None:
    for _ in range(100):
        emit_setup_required(object(), _USER, _SETUP_ERROR)  # type: ignore[arg-type]

    assert _stops(recorded_events, _USER) == product_events._SETUP_EVENTS_PER_HOUR == 30


async def test_one_users_stops_never_use_up_another_users_cap(
    recorded_events: list[dict[str, Any]], no_in_flight_limit: None
) -> None:
    for _ in range(100):
        emit_setup_required(object(), _USER, _SETUP_ERROR)  # type: ignore[arg-type]
    emit_setup_required(object(), _OTHER_USER, _SETUP_ERROR)  # type: ignore[arg-type]

    assert _stops(recorded_events, _OTHER_USER) == 1


async def test_recording_resumes_when_the_hour_is_up(
    recorded_events: list[dict[str, Any]], no_in_flight_limit: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    # the module's own clock only: patching time.monotonic itself would move the event loop's too
    monkeypatch.setattr(product_events, "time", SimpleNamespace(monotonic=lambda: now[0]))

    for _ in range(40):
        emit_setup_required(object(), _USER, _SETUP_ERROR)  # type: ignore[arg-type]
    assert _stops(recorded_events, _USER) == 30

    now[0] += product_events._SETUP_WINDOW_SECONDS - 1  # still inside the hour
    emit_setup_required(object(), _USER, _SETUP_ERROR)  # type: ignore[arg-type]
    assert _stops(recorded_events, _USER) == 30

    now[0] += 1  # the hour is up: a fresh window, with a full cap again
    for _ in range(40):
        emit_setup_required(object(), _USER, _SETUP_ERROR)  # type: ignore[arg-type]
    assert _stops(recorded_events, _USER) == 60


def test_the_cap_counts_only_stops_that_would_be_recorded() -> None:
    """An error that is not a setup error, a missing user and a missing client take no slot."""
    for _ in range(100):
        emit_setup_required(object(), _USER, ApiError("NOT_FOUND", "x"))  # type: ignore[arg-type]
        emit_setup_required(object(), None, _SETUP_ERROR)  # type: ignore[arg-type]
        emit_setup_required(None, _USER, _SETUP_ERROR)

    assert product_events._setup_window == {}


def test_the_table_of_counted_users_cannot_grow_without_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(product_events, "_SETUP_MAX_USERS", 3)
    free = product_events._setup_slot_free
    window = product_events._SETUP_WINDOW_SECONDS

    assert [free("a", 0.0), free("b", 0.0), free("c", 0.0)] == [True, True, True]
    assert free("d", 1.0) is False  # full of live windows: dropped, not added
    assert len(product_events._setup_window) == 3
    assert free("a", 1.0) is True  # a user already counted is unaffected by the full table

    assert free("d", window + 0.5) is True  # their windows have run out: purged, room again
    assert set(product_events._setup_window) == {"d"}


def test_a_user_whose_window_ran_out_starts_a_new_one_without_growing_the_table() -> None:
    free = product_events._setup_slot_free
    window = product_events._SETUP_WINDOW_SECONDS

    for _ in range(30):
        assert free("a", 0.0) is True
    assert free("a", 1.0) is False
    assert free("a", window) is True
    assert product_events._setup_window == {"a": (window, 1)}


# -- the API error handler: the one central place -------------------------------------------


def _handler_request(user_id: str | None, supabase: object | None) -> Any:
    state = SimpleNamespace(user_id=user_id) if user_id is not None else SimpleNamespace()
    return SimpleNamespace(
        method="GET",
        scope={},
        url=SimpleNamespace(path="/discover"),
        state=state,
        app=SimpleNamespace(state=SimpleNamespace(supabase=supabase)),
    )


async def test_the_error_handler_records_a_setup_required_event_and_answers_as_before(
    recorded_events: list[dict[str, Any]],
) -> None:
    error = ApiError(
        "SETUP_REQUIRED", "Import a resume first.", capability="profile", missing=["profile"]
    )

    response = await handle_api_error(_handler_request(_USER, object()), error)

    assert response.status_code == 409
    assert recorded_events == [
        {
            "user_id": _USER,
            "event": "setup_required",
            "capability": "profile",
            "outcome": "setup_required",
        }
    ]
    # the body is exactly what it always was
    assert json.loads(bytes(response.body)) == error.to_body()


@pytest.mark.parametrize(
    "request_",
    [
        _handler_request(None, object()),  # no signed-in user on the request
        _handler_request(_USER, None),  # no client (a server that has not started)
    ],
)
async def test_the_error_handler_records_nothing_without_a_user_or_a_client(
    recorded_events: list[dict[str, Any]], request_: Any
) -> None:
    response = await handle_api_error(
        request_, ApiError("SETUP_REQUIRED", "x", capability="profile")
    )

    assert response.status_code == 409
    assert recorded_events == []


async def test_the_error_handler_records_nothing_for_other_errors(
    recorded_events: list[dict[str, Any]],
) -> None:
    response = await handle_api_error(_handler_request(_USER, object()), ApiError("NOT_FOUND", "x"))

    assert response.status_code == 404
    assert recorded_events == []


async def test_the_error_handler_answers_even_if_recording_is_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_: object) -> None:
        raise RuntimeError("the writer itself is broken")

    monkeypatch.setattr(product_events, "write_event", explode)

    response = await handle_api_error(
        _handler_request(_USER, object()), ApiError("SETUP_REQUIRED", "x", capability="profile")
    )

    assert response.status_code == 409
