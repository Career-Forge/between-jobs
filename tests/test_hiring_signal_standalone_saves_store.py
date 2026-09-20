"""Tests for the STANDALONE side of Hiring Signals' saved-post store
(`hiring_signal_saves_store` with `application_id=None`, P4): a post saved from the
Hiring signals tab, which belongs to no application.

Standalone and per-application saves are one table and two namespaces. What these
tests pin is that the namespaces never bleed into each other -- in creating
(a post can be saved from both places, and those are two rows), in the duplicate
check, in listing, and in the `saved` flag -- and that the standalone side has the
same guarantees as the other (a pointer and nothing else, idempotent under a
race, owner-only). The per-application behavior itself is P3's and is tested in
`test_hiring_signal_saves_store.py`, which this file leaves untouched.

The in-memory table mirrors the migration's two partial unique indexes: one over
`(user, application, activity)` for a save with an application and one over
`(user, activity)` for a save without one. (`NULL`s are distinct in a plain unique
index, so without the second one the same standalone post could be saved twice --
the reason the migration has both.)
"""

from __future__ import annotations

from typing import Any

import pytest
from hiring_signal_fakes import FakeSupabase, FakeTable, as_client, blind_first_look
from postgrest.exceptions import APIError

from between_jobs.api.hiring_signal_saves_store import (
    HiringSignalSaveNotFound,
    canonical_post_url,
    create_save,
    delete_save,
    list_saves,
    saved_activity_ids,
)

USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
APP = "30000000-0000-0000-0000-000000000001"
OTHER_APP = "30000000-0000-0000-0000-000000000002"
A = "7506381452083381426"
B = "7506381452083381427"
C = "7506381452083381428"


def _unique_keys() -> list[Any]:
    return [
        lambda r: (
            (r["user_id"], r["application_id"], r["activity_id"])
            if r.get("application_id") is not None
            else None
        ),
        lambda r: (r["user_id"], r["activity_id"]) if r.get("application_id") is None else None,
    ]


def _world(rows: list[dict[str, Any]] | None = None) -> tuple[Any, FakeTable]:
    table = FakeTable(rows, unique=_unique_keys())
    return as_client(FakeSupabase({"hiring_signal_saves": table})), table


def _row(user: str, activity: str, application: str | None, created_at: str) -> dict[str, Any]:
    return {
        "id": f"50000000-0000-0000-0000-{activity[-12:]}",
        "user_id": user,
        "application_id": application,
        "url": canonical_post_url(activity),
        "activity_id": activity,
        "discovered_via_query": None,
        "created_at": created_at,
    }


# ── creating ─────────────────────────────────────────────────────────────


async def test_a_standalone_save_is_a_pointer_with_no_application() -> None:
    supabase, table = _world()

    saved, created = await create_save(
        supabase, USER, None, activity_id=A, query_label="data engineer -- Pune -- last 3 days"
    )

    assert created is True
    (row,) = table.rows
    assert row["application_id"] is None
    assert row["user_id"] == USER
    assert row["activity_id"] == A
    assert row["url"] == canonical_post_url(A)
    assert row["discovered_via_query"] == "data engineer -- Pune -- last 3 days"
    assert set(row) == {
        "id",
        "created_at",
        "user_id",
        "application_id",
        "url",
        "activity_id",
        "discovered_via_query",
    }
    # the API shape is the per-application one, exactly: no application in it
    assert set(saved) == {"id", "activity_id", "post_url", "embed_url", "created_at"}
    assert saved["post_url"] == canonical_post_url(A)


async def test_saving_the_same_standalone_post_twice_is_one_row_and_created_false() -> None:
    supabase, table = _world()
    first, created_first = await create_save(
        supabase, USER, None, activity_id=A, query_label="first"
    )
    second, created_second = await create_save(
        supabase, USER, None, activity_id=A, query_label="second"
    )

    assert (created_first, created_second) == (True, False)
    assert second == first
    assert len(table.rows) == 1
    assert table.rows[0]["discovered_via_query"] == "first"  # not overwritten


async def test_the_database_alone_stops_a_second_standalone_row() -> None:
    """Without the partial index for `application_id IS NULL`, two NULLs would be
    two rows. The fake's second key is that index: the raw insert is refused."""
    _, table = _world([_row(USER, A, None, "2026-09-18T00:00:00+00:00")])
    with pytest.raises(APIError) as e:
        table.insert_row({"user_id": USER, "application_id": None, "activity_id": A})
    assert e.value.code == "23505"
    table.insert_row({"user_id": OTHER_USER, "application_id": None, "activity_id": A})  # fine


async def test_a_lost_race_for_a_standalone_save_is_the_winners_row_with_created_false() -> None:
    """Both requests looked, neither saw the row, both inserted: the loser's insert
    hits the unique violation and returns the winner's row."""
    winner = _row(USER, A, None, "2026-09-18T00:00:00+00:00")
    supabase, table = _world([winner])
    blind_first_look(table)

    saved, created = await create_save(supabase, USER, None, activity_id=A)

    assert created is False
    assert saved["id"] == winner["id"]
    assert len(table.rows) == 1
    assert table.insert_attempts == 1  # the loser really did try to insert


async def test_only_the_unique_violation_means_already_saved() -> None:
    """A different failure of the insert must surface, not be answered with the row
    that happens to be there (the existing row is found by the re-read, which is
    exactly why swallowing the error would go unnoticed)."""
    winner = _row(USER, A, None, "2026-09-18T00:00:00+00:00")
    supabase, table = _world([winner])
    blind_first_look(table)

    def boom(payload: dict[str, Any]) -> dict[str, Any]:
        raise APIError({"message": "boom", "code": "40001", "details": None, "hint": None})

    table.insert_row = boom  # type: ignore[method-assign]
    with pytest.raises(APIError) as e:
        await create_save(supabase, USER, None, activity_id=A)
    assert e.value.code == "40001"


# ── the two namespaces never bleed into each other ───────────────────────


async def test_the_same_post_saved_standalone_and_for_an_application_is_two_saves() -> None:
    supabase, table = _world()

    _, standalone = await create_save(supabase, USER, None, activity_id=A)
    _, for_app = await create_save(supabase, USER, APP, activity_id=A)
    _, for_other_app = await create_save(supabase, USER, OTHER_APP, activity_id=A)
    _, standalone_again = await create_save(supabase, USER, None, activity_id=A)
    _, for_app_again = await create_save(supabase, USER, APP, activity_id=A)

    assert (standalone, for_app, for_other_app) == (True, True, True)
    assert (standalone_again, for_app_again) == (False, False)
    assert sorted(str(r["application_id"]) for r in table.rows) == sorted(["None", APP, OTHER_APP])


async def test_the_standalone_list_holds_no_per_application_save_and_the_other_way_round() -> None:
    supabase, _ = _world(
        [
            _row(USER, A, None, "2026-09-18T01:00:00+00:00"),
            _row(USER, B, APP, "2026-09-18T02:00:00+00:00"),
            _row(USER, C, None, "2026-09-18T03:00:00+00:00"),
            _row(USER, A, OTHER_APP, "2026-09-18T04:00:00+00:00"),
        ]
    )

    standalone = await list_saves(supabase, USER, None)
    for_app = await list_saves(supabase, USER, APP)
    for_other = await list_saves(supabase, USER, OTHER_APP)

    assert [s["activity_id"] for s in standalone] == [C, A]  # newest first
    assert [s["activity_id"] for s in for_app] == [B]
    assert [s["activity_id"] for s in for_other] == [A]


async def test_a_standalone_listing_never_shows_another_users_saves() -> None:
    supabase, _ = _world(
        [
            _row(USER, A, None, "2026-09-18T01:00:00+00:00"),
            _row(OTHER_USER, B, None, "2026-09-18T02:00:00+00:00"),
        ]
    )
    assert [s["activity_id"] for s in await list_saves(supabase, USER, None)] == [A]
    assert [s["activity_id"] for s in await list_saves(supabase, OTHER_USER, None)] == [B]


async def test_the_saved_flag_reads_the_namespace_it_is_asked_about() -> None:
    supabase, _ = _world(
        [
            _row(USER, A, None, "2026-09-18T01:00:00+00:00"),
            _row(USER, B, APP, "2026-09-18T02:00:00+00:00"),
            _row(OTHER_USER, C, None, "2026-09-18T03:00:00+00:00"),
        ]
    )

    assert await saved_activity_ids(supabase, USER, None, [A, B, C]) == {A}
    assert await saved_activity_ids(supabase, USER, APP, [A, B, C]) == {B}
    assert await saved_activity_ids(supabase, USER, OTHER_APP, [A, B, C]) == set()
    assert await saved_activity_ids(supabase, OTHER_USER, None, [A, B, C]) == {C}


async def test_the_saved_flag_asks_nothing_when_there_is_nothing_to_flag() -> None:
    supabase, table = _world([_row(USER, A, None, "2026-09-18T01:00:00+00:00")])
    assert await saved_activity_ids(supabase, USER, None, []) == set()
    assert table.calls == []


async def test_a_standalone_query_filters_on_is_null_and_never_on_an_application_id() -> None:
    """`= <id>` never matches a NULL, so the standalone side must ask `IS NULL`;
    and the per-application side must never ask it."""

    def selects(table: FakeTable) -> list[set[tuple[str, str]]]:
        return [
            {(op, column) for op, column, _ in filters}
            for kind, filters in table.calls
            if kind == "select"
        ]

    supabase, table = _world()
    await list_saves(supabase, USER, None)
    await saved_activity_ids(supabase, USER, None, [A])
    await create_save(supabase, USER, None, activity_id=A)  # looks first
    assert len(selects(table)) == 3
    for columns in selects(table):
        assert ("is", "application_id") in columns
        assert ("eq", "application_id") not in columns

    table.calls.clear()
    await list_saves(supabase, USER, APP)
    await saved_activity_ids(supabase, USER, APP, [A])
    await create_save(supabase, USER, APP, activity_id=B)
    assert len(selects(table)) == 3
    for columns in selects(table):
        assert ("eq", "application_id") in columns
        assert ("is", "application_id") not in columns


# ── deleting ─────────────────────────────────────────────────────────────


async def test_delete_removes_the_owners_save_of_either_kind_by_id() -> None:
    standalone = _row(USER, A, None, "2026-09-18T01:00:00+00:00")
    per_app = _row(USER, B, APP, "2026-09-18T02:00:00+00:00")
    supabase, table = _world([standalone, per_app])

    await delete_save(supabase, USER, standalone["id"])
    assert [r["id"] for r in table.rows] == [per_app["id"]]
    await delete_save(supabase, USER, per_app["id"])
    assert table.rows == []


async def test_delete_of_someone_elses_or_an_unknown_save_is_the_same_not_found() -> None:
    mine = _row(USER, A, None, "2026-09-18T01:00:00+00:00")
    theirs = _row(OTHER_USER, B, None, "2026-09-18T02:00:00+00:00")
    supabase, table = _world([mine, theirs])

    with pytest.raises(HiringSignalSaveNotFound):
        await delete_save(supabase, USER, theirs["id"])
    with pytest.raises(HiringSignalSaveNotFound):
        await delete_save(supabase, USER, "50000000-0000-0000-0000-00000000dead")
    assert {r["id"] for r in table.rows} == {mine["id"], theirs["id"]}  # nothing was deleted


async def test_a_deleted_standalone_save_can_be_saved_again() -> None:
    supabase, table = _world()
    saved, _ = await create_save(supabase, USER, None, activity_id=A)
    await delete_save(supabase, USER, saved["id"])
    _, created = await create_save(supabase, USER, None, activity_id=A)
    assert created is True
    assert len(table.rows) == 1


# ── refusals ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["12a", "", "0", "0" + A, "١٢"])
async def test_an_invalid_id_is_refused_before_anything_is_read_or_written(bad: str) -> None:
    supabase, table = _world()
    with pytest.raises(ValueError):
        await create_save(supabase, USER, None, activity_id=bad)
    assert table.calls == []


async def test_other_database_errors_on_a_standalone_save_propagate() -> None:
    supabase, table = _world()
    table.fail_with = APIError({"message": "boom", "code": "XX000", "details": None, "hint": None})
    with pytest.raises(APIError):
        await create_save(supabase, USER, None, activity_id=A)


async def test_a_standalone_save_that_vanished_between_violation_and_reread_propagates() -> None:
    """The unique violation says a row exists; if the re-read cannot find it (it was
    deleted in between) the violation is raised rather than invented into a row."""
    supabase, table = _world()

    def always_violates(payload: dict[str, Any]) -> dict[str, Any]:
        raise APIError({"message": "dup", "code": "23505", "details": None, "hint": None})

    table.insert_row = always_violates  # type: ignore[method-assign]
    with pytest.raises(APIError):
        await create_save(supabase, USER, None, activity_id=A)
