"""A hand-made stand-in for the Supabase client, with just enough of `tester_enrollments` to run
the enrollment functions, routes and the gate against: select with a column list, eq and is_
filters, upsert with a conflict key (which merges, so a column the payload leaves out, such as
`created_at`, keeps its value) and update. Every other table answers with no rows, whatever it is
asked, so a route that goes on to look something else up gets "not found" instead of an error.

`fail_with` makes every operation on the table raise, to show what a broken lookup does."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from postgrest.exceptions import APIError

USER = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"


class _Query:
    def __init__(
        self, table: FakeEnrollments, op: str, payload: Any = None, columns: str = "*"
    ) -> None:
        self._table = table
        self._op = op
        self._payload = payload
        self._columns = columns
        self._filters: list[tuple[str, str, Any]] = []

    def eq(self, column: str, value: Any) -> _Query:
        self._filters.append(("eq", column, value))
        return self

    def is_(self, column: str, value: Any) -> _Query:
        self._filters.append(("is", column, value))
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for kind, column, value in self._filters:
            if kind == "eq" and row.get(column) != value:
                return False
            if kind == "is" and value == "null" and row.get(column) is not None:
                return False
        return True

    async def execute(self) -> SimpleNamespace:
        table = self._table
        table.operations.append(self._op)
        if table.fail_with is not None:
            raise table.fail_with
        if self._op == "select":
            wanted = [c.strip() for c in self._columns.split(",")]
            rows = [
                {k: v for k, v in row.items() if self._columns == "*" or k in wanted}
                for row in table.rows.values()
                if self._matches(row)
            ]
            return SimpleNamespace(data=rows)
        if self._op == "upsert":
            if table.upsert_error is not None:
                raise table.upsert_error
            payload = dict(self._payload)
            current = table.rows.get(
                payload["user_id"], {"created_at": "2026-10-01T00:00:00+00:00"}
            )
            table.rows[payload["user_id"]] = {**current, **payload}
            return SimpleNamespace(data=[table.rows[payload["user_id"]]])
        if self._op == "update":
            changed = [row for row in table.rows.values() if self._matches(row)]
            for row in changed:
                row.update(self._payload)
            return SimpleNamespace(data=changed)
        raise AssertionError(f"unexpected operation {self._op}")


class FakeEnrollments:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {row["user_id"]: dict(row) for row in rows or []}
        self.operations: list[str] = []
        self.fail_with: Exception | None = None
        self.upsert_error: Exception | None = None

    def select(self, columns: str = "*") -> _Query:
        return _Query(self, "select", columns=columns)

    def upsert(self, payload: dict[str, Any], **_: Any) -> _Query:
        return _Query(self, "upsert", payload)

    def update(self, payload: dict[str, Any]) -> _Query:
        return _Query(self, "update", payload)


class _NoRows:
    """Any other table: every chain ends in an empty result."""

    def __getattr__(self, _name: str) -> Any:
        return self

    def __call__(self, *_: Any, **__: Any) -> Any:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.enrollments = FakeEnrollments(rows)

    def table(self, name: str) -> Any:
        return self.enrollments if name == "tester_enrollments" else _NoRows()


def row(
    user_id: str = USER,
    *,
    version: str,
    withdrawn_at: str | None = None,
    role_cohort: str = "data_analyst",
    seniority: str = "mid",
    needs_sponsorship: bool | None = None,
) -> dict[str, Any]:
    """An enrollment row as the table holds it."""
    return {
        "user_id": user_id,
        "role_cohort": role_cohort,
        "seniority": seniority,
        "needs_sponsorship": needs_sponsorship,
        "consent_version": version,
        "consented_at": "2026-10-06T09:00:00+00:00",
        "withdrawn_at": withdrawn_at,
        "created_at": "2026-10-06T09:00:00+00:00",
    }


def foreign_key_violation() -> APIError:
    return APIError({"message": "violates foreign key constraint", "code": "23503"})
