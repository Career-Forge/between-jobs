"""Tests for browser-extension.md E3c's field-map loader --
`get_latest_field_map`. Deliberately NOT fail-open (unlike
`company_tiers.py`/`geo_gazetteer.py`): a confirmed-absent map (zero
rows) and a genuine backend error must stay distinguishable, since the
caller turns the former into a clean 404 and lets the latter propagate."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from between_jobs.api.ats_field_maps import get_latest_field_map


class _FakeQueryBuilder:
    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._all_rows = rows
        self._raise_error = raise_error
        self._eq_field: str | None = None
        self._eq_value: Any = None
        self._order_desc = False
        self._limit: int | None = None

    def select(self, *_: Any, **__: Any) -> _FakeQueryBuilder:
        return self

    def eq(self, field: str, value: Any) -> _FakeQueryBuilder:
        self._eq_field = field
        self._eq_value = value
        return self

    def order(self, _field: str, *, desc: bool = False) -> _FakeQueryBuilder:
        self._order_desc = desc
        return self

    def limit(self, n: int) -> _FakeQueryBuilder:
        self._limit = n
        return self

    async def execute(self) -> SimpleNamespace:
        if self._raise_error:
            raise RuntimeError("connection refused")
        rows = self._all_rows
        if self._eq_field is not None:
            rows = [r for r in rows if r.get(self._eq_field) == self._eq_value]
        rows = sorted(rows, key=lambda r: r["version"], reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]], *, raise_error: bool = False) -> None:
        self._rows = rows
        self._raise_error = raise_error

    def table(self, _name: str) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._rows, raise_error=self._raise_error)


_LEVER_V1 = {
    "ats_type": "lever",
    "version": 1,
    "schema": "ats-field-map/v1",
    "payload_canonical": '{"ats_type":"lever","version":1}',
    "signature_b64": "sig1",
    "signing_key_id": "test-key",
}
_LEVER_V2 = {**_LEVER_V1, "version": 2, "signature_b64": "sig2"}
_GREENHOUSE_V1 = {**_LEVER_V1, "ats_type": "greenhouse", "signature_b64": "sig-gh"}


async def test_returns_none_when_no_map_is_published() -> None:
    supabase = _FakeSupabase([])

    row = await get_latest_field_map(supabase, "lever")  # type: ignore[arg-type]

    assert row is None


async def test_returns_the_only_row_when_one_version_exists() -> None:
    supabase = _FakeSupabase([_LEVER_V1])

    row = await get_latest_field_map(supabase, "lever")  # type: ignore[arg-type]

    assert row is not None
    assert row["version"] == 1


async def test_returns_the_highest_version_when_several_are_published() -> None:
    supabase = _FakeSupabase([_LEVER_V1, _LEVER_V2])

    row = await get_latest_field_map(supabase, "lever")  # type: ignore[arg-type]

    assert row is not None
    assert row["version"] == 2
    assert row["signature_b64"] == "sig2"


async def test_filters_by_ats_type() -> None:
    supabase = _FakeSupabase([_LEVER_V1, _GREENHOUSE_V1])

    row = await get_latest_field_map(supabase, "greenhouse")  # type: ignore[arg-type]

    assert row is not None
    assert row["ats_type"] == "greenhouse"


async def test_a_genuine_backend_error_propagates_rather_than_returning_none() -> None:
    supabase = _FakeSupabase([], raise_error=True)

    with pytest.raises(RuntimeError):
        await get_latest_field_map(supabase, "lever")  # type: ignore[arg-type]
