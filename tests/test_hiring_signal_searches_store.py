"""Tests for the standalone tab's saved-search store
(`hiring_signal_searches_store`, P4): what a user typed, stored normalized, the
same search never twice, at most 25 per user, owner-only, and race-safe.

Runs against the in-memory fake whose unique key mirrors the migration's index --
`(user_id, lower(query), coalesce(lower(location), ''))` -- so an insert that
collides really raises SQLSTATE 23505, the way Postgres does.

The two races are simulated at the DATA level, not by patching the store's own
functions (the lesson of the R3 bug: patch the reference the code actually uses,
or better, do not patch it at all):

- the duplicate race -- the loser looked, saw nothing, and by the time it inserted
  somebody else had (`blind_first_look` makes the first look empty), so the insert
  hits the unique violation and the winner's row is returned;
- the cap race -- two creates both saw 24 rows, both inserted, and the table now
  has 26: the store's verification counts again after its insert, finds more than
  25, deletes its OWN row and refuses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hiring_signal_fakes import (
    FakeSupabase,
    FakeTable,
    as_client,
    blind_first_look,
    unique_violation,
)
from postgrest.exceptions import APIError

from between_jobs.api.hiring_signal_searches_store import (
    MAX_SAVED_SEARCHES,
    HiringSignalSearchLimitReached,
    HiringSignalSearchNotFound,
    HiringSignalSearchSaveRaced,
    create_search,
    delete_search,
    list_searches,
    to_saved_search,
)
from between_jobs.api.hiring_signal_tab import InvalidSearchInput

USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["user_id"], row["query"].lower(), (row.get("location") or "").lower())


def _world(rows: list[dict[str, Any]] | None = None) -> tuple[Any, FakeTable]:
    table = FakeTable(rows, unique=[_key])
    return as_client(FakeSupabase({"hiring_signal_searches": table})), table


def _row(
    n: int, query: str, location: str | None = None, *, user: str = USER, minutes: int | None = None
) -> dict[str, Any]:
    return {
        "id": f"60000000-0000-0000-0000-{n:012d}",
        "user_id": user,
        "query": query,
        "location": location,
        "created_at": (T0 + timedelta(minutes=n if minutes is None else minutes)).isoformat(),
    }


def _full(user: str = USER, count: int = MAX_SAVED_SEARCHES) -> list[dict[str, Any]]:
    return [_row(i, f"role number {i}", user=user) for i in range(count)]


# ── creating and normalizing ─────────────────────────────────────────────


async def test_a_new_search_is_stored_and_returned_in_the_api_shape() -> None:
    supabase, table = _world()

    saved, created = await create_search(supabase, USER, query="data engineer", location="Pune")

    assert created is True
    (row,) = table.rows
    assert (row["user_id"], row["query"], row["location"]) == (USER, "data engineer", "Pune")
    assert set(saved) == {"id", "query", "location", "created_at"}  # no user id, no more
    assert saved["id"] == row["id"] and saved["created_at"] == row["created_at"]
    assert (saved["query"], saved["location"]) == ("data engineer", "Pune")
    # only the typed words are stored: nothing that could make a background job run it
    assert set(row) == {"id", "user_id", "query", "location", "created_at"}


async def test_a_search_without_a_place_stores_a_null_location() -> None:
    supabase, table = _world()
    saved, _ = await create_search(supabase, USER, query="data engineer")
    assert saved["location"] is None and table.rows[0]["location"] is None


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
async def test_a_blank_location_is_stored_as_null_not_as_an_empty_string(
    blank: str | None,
) -> None:
    supabase, table = _world()
    await create_search(supabase, USER, query="data engineer", location=blank)
    assert table.rows[0]["location"] is None


async def test_what_is_stored_is_the_cleaned_text_and_a_rerun_sends_exactly_it() -> None:
    zwsp = chr(0x200B)
    supabase, table = _world()

    saved, _ = await create_search(
        supabase, USER, query=f"  Data{zwsp}   Engineer\t\n", location="  Bengaluru,   Karnataka  "
    )

    assert (table.rows[0]["query"], table.rows[0]["location"]) == (
        "Data Engineer",
        "Bengaluru, Karnataka",
    )
    assert (saved["query"], saved["location"]) == ("Data Engineer", "Bengaluru, Karnataka")


@pytest.mark.parametrize(
    ("query", "location"),
    [
        ("", None),
        ("   ", None),
        ("!!!", None),
        ("a", None),  # no word a click could filter on
        ("x" * 201, None),
        ("data engineer", "x" * 101),
        ("data engineer", "###"),
        ("%", None),
    ],
)
async def test_text_that_cannot_be_searched_is_refused_and_writes_nothing(
    query: str, location: str | None
) -> None:
    supabase, table = _world()
    with pytest.raises(InvalidSearchInput):
        await create_search(supabase, USER, query=query, location=location)
    assert table.calls == []  # not even a look


@pytest.mark.parametrize("bad", [None, 5, ["a"], b"engineer"])
async def test_a_role_that_is_not_text_is_refused(bad: object) -> None:
    supabase, table = _world()
    with pytest.raises(InvalidSearchInput):
        await create_search(supabase, USER, query=bad)
    assert table.calls == []


async def test_the_documented_maximum_lengths_are_accepted() -> None:
    supabase, table = _world()
    await create_search(supabase, USER, query="engineer " * 22, location="Pune " * 20)
    assert len(table.rows[0]["query"]) <= 200 and len(table.rows[0]["location"]) <= 100


# ── the same search, once ────────────────────────────────────────────────


async def test_the_same_search_twice_is_one_row_and_the_second_says_it_existed() -> None:
    supabase, table = _world()
    first, created_first = await create_search(
        supabase, USER, query="data engineer", location="Pune"
    )
    second, created_second = await create_search(
        supabase, USER, query="data engineer", location="Pune"
    )

    assert (created_first, created_second) == (True, False)
    assert second == first  # the existing row, unchanged
    assert len(table.rows) == 1


@pytest.mark.parametrize(
    ("query", "location"),
    [
        ("DATA ENGINEER", "Pune"),
        ("data engineer", "PUNE"),
        ("Data   Engineer", "Pune"),
        ("  data engineer  ", "  pune  "),
        ("Data\tEngineer", "Pune"),
    ],
)
async def test_case_and_whitespace_variants_collapse_to_one_search(
    query: str, location: str
) -> None:
    supabase, table = _world()
    first, _ = await create_search(supabase, USER, query="data engineer", location="Pune")

    again, created = await create_search(supabase, USER, query=query, location=location)

    assert created is False
    assert again == first
    assert len(table.rows) == 1


@pytest.mark.parametrize(
    ("query", "location"),
    [
        ("data engineer", None),  # no place is not the same search as a place
        ("data engineer", "Chicago"),
        ("data engineers", "Pune"),  # a different word
        ("data engineer II", "Pune"),
    ],
)
async def test_a_different_role_or_place_is_a_different_search(
    query: str, location: str | None
) -> None:
    supabase, table = _world()
    await create_search(supabase, USER, query="data engineer", location="Pune")

    _, created = await create_search(supabase, USER, query=query, location=location)

    assert created is True
    assert len(table.rows) == 2


async def test_no_place_is_one_search_however_it_was_typed() -> None:
    supabase, table = _world()
    await create_search(supabase, USER, query="data engineer")
    for blank in (None, "", "  "):
        _, created = await create_search(supabase, USER, query="Data Engineer", location=blank)
        assert created is False
    assert len(table.rows) == 1


async def test_a_typed_percent_underscore_or_star_is_never_a_wildcard() -> None:
    """The duplicate look is done in Python, not with a `LIKE`: a typed `%` or `_`
    must not answer `already saved` with somebody else's search."""
    supabase, table = _world([_row(1, "data engineer", "Pune")])

    created_flags = []
    for typed in ("data%", "d_ta engineer", "data*", "data engineer%", "%engineer"):
        _, created = await create_search(supabase, USER, query=typed, location="Pune")
        created_flags.append(created)

    assert created_flags == [True] * 5  # a `LIKE` would have called the first four `data engineer`
    assert len(table.rows) == 6


async def test_the_same_search_by_another_user_is_a_separate_row() -> None:
    supabase, table = _world()
    _, mine = await create_search(supabase, USER, query="data engineer", location="Pune")
    _, theirs = await create_search(supabase, OTHER_USER, query="data engineer", location="Pune")

    assert (mine, theirs) == (True, True)
    assert sorted(r["user_id"] for r in table.rows) == sorted([USER, OTHER_USER])


async def test_a_capital_sigma_is_folded_the_way_the_database_folds_it() -> None:
    """Python's `str.lower()` reads a capital sigma at the end of a word as a final
    sigma, Postgres's `lower()` does not (it maps each character on its own). The
    store folds per character, so two searches the index calls equal are equal here."""
    sigma_word = "".join(chr(c) for c in (0x39F, 0x394, 0x39F, 0x3A3))  # a Greek word in capitals
    stored_form = "".join(chr(c) for c in (0x3BF, 0x3B4, 0x3BF, 0x3C3))  # per-character lower()
    supabase, table = _world([_row(1, f"{stored_form} engineer")])

    _, created = await create_search(supabase, USER, query=f"{sigma_word} engineer")

    assert created is False
    assert len(table.rows) == 1


# ── the duplicate race ───────────────────────────────────────────────────


async def test_a_lost_race_returns_the_winners_row_with_created_false() -> None:
    winner = _row(1, "data engineer", "Pune")
    supabase, table = _world([winner])
    blind_first_look(table)  # the loser's first look does not see the winner yet

    saved, created = await create_search(supabase, USER, query="Data Engineer", location="pune")

    assert created is False
    assert saved["id"] == winner["id"]
    assert len(table.rows) == 1
    assert table.insert_attempts == 1  # it really did try, and the unique index refused


async def test_the_violation_is_recognized_by_its_sqlstate_and_no_other_error_is() -> None:
    """The unique violation is the ONE error that means "somebody saved it first". A
    different failure must never be reported as "already saved": with an equivalent
    row really there to be found, swallowing it would answer 200 for a write that
    failed. (With an empty table the bare re-raise would hide a missing check, so
    the row is there, and the loser's first look is blind to it -- as in a real race.)"""
    winner = _row(1, "data engineer", "Pune")
    supabase, table = _world([winner])
    blind_first_look(table)

    def boom(payload: dict[str, Any]) -> dict[str, Any]:
        raise APIError({"message": "boom", "code": "23514", "details": None, "hint": None})

    table.insert_row = boom  # type: ignore[method-assign]
    with pytest.raises(APIError) as e:
        await create_search(supabase, USER, query="Data Engineer", location="pune")
    assert e.value.code == "23514"
    assert len(table.rows) == 1  # nothing was written, nothing was invented


async def test_a_violation_whose_winner_is_gone_by_the_reread_is_raised_not_invented() -> None:
    supabase, table = _world()

    def always_violates(payload: dict[str, Any]) -> dict[str, Any]:
        raise unique_violation()

    table.insert_row = always_violates  # type: ignore[method-assign]
    with pytest.raises(APIError) as e:
        await create_search(supabase, USER, query="data engineer")
    assert e.value.code == "23505"


# ── the cap ──────────────────────────────────────────────────────────────


async def test_the_cap_is_twenty_five() -> None:
    assert MAX_SAVED_SEARCHES == 25


async def test_the_twenty_fifth_search_is_saved_and_the_twenty_sixth_is_refused() -> None:
    supabase, table = _world(_full(count=MAX_SAVED_SEARCHES - 1))

    _, created = await create_search(supabase, USER, query="the last one that fits")
    assert created is True and len(table.rows) == MAX_SAVED_SEARCHES

    attempts = table.insert_attempts
    with pytest.raises(HiringSignalSearchLimitReached):
        await create_search(supabase, USER, query="one too many")
    assert len(table.rows) == MAX_SAVED_SEARCHES  # nothing was dropped to make room
    assert table.insert_attempts == attempts  # and no insert was even tried


async def test_at_the_cap_saving_an_existing_search_again_is_still_fine() -> None:
    rows = _full()
    supabase, table = _world(rows)

    saved, created = await create_search(supabase, USER, query=rows[3]["query"].upper())

    assert created is False
    assert saved["id"] == rows[3]["id"]
    assert len(table.rows) == MAX_SAVED_SEARCHES


async def test_another_users_searches_do_not_count_against_the_cap() -> None:
    supabase, table = _world(_full(user=OTHER_USER))

    _, created = await create_search(supabase, USER, query="mine")

    assert created is True
    assert len(table.rows) == MAX_SAVED_SEARCHES + 1


async def test_deleting_one_makes_room_for_another() -> None:
    rows = _full()
    supabase, table = _world(rows)
    await delete_search(supabase, USER, rows[0]["id"])

    _, created = await create_search(supabase, USER, query="a fresh one")

    assert created is True and len(table.rows) == MAX_SAVED_SEARCHES


async def test_two_creates_that_race_for_the_last_slot_never_leave_more_than_the_cap() -> None:
    """Both requests counted 24 and both inserted: the table has 26. The store
    counts again after its own insert, sees 26 > 25, deletes ITS OWN row and refuses --
    so the requests cannot both keep their slot, and the cap holds once they finish."""

    class RacingTable(FakeTable):
        rival_pending = True

        def insert_row(self, payload: dict[str, Any]) -> dict[str, Any]:
            row = super().insert_row(payload)
            if self.rival_pending:  # the other request's insert lands right after ours
                self.rival_pending = False
                super().insert_row(_row(900, "the rival's search"))
            return row

    table = RacingTable(_full(count=MAX_SAVED_SEARCHES - 1), unique=[_key])
    supabase = as_client(FakeSupabase({"hiring_signal_searches": table}))

    with pytest.raises(HiringSignalSearchSaveRaced):
        await create_search(supabase, USER, query="my search")

    assert len(table.rows) == MAX_SAVED_SEARCHES  # the rival kept its slot, mine is gone
    queries = {r["query"] for r in table.rows}
    assert "the rival's search" in queries and "my search" not in queries


async def test_the_overshoot_delete_removes_only_the_callers_own_row() -> None:
    """The cleanup is by id AND user, so it cannot take anyone else's row."""

    class RacingTable(FakeTable):
        rival_pending = True

        def insert_row(self, payload: dict[str, Any]) -> dict[str, Any]:
            row = super().insert_row(payload)
            if self.rival_pending:
                self.rival_pending = False
                super().insert_row(_row(901, "rival", user=USER))
            return row

    table = RacingTable(
        [*_full(count=MAX_SAVED_SEARCHES - 1), _row(950, "other", user=OTHER_USER)], unique=[_key]
    )
    supabase = as_client(FakeSupabase({"hiring_signal_searches": table}))

    with pytest.raises(HiringSignalSearchLimitReached):
        await create_search(supabase, USER, query="mine")

    deletes = [filters for op, filters in table.calls if op == "delete"]
    assert len(deletes) == 1
    assert {(c, v) for op, c, v in deletes[0] if op == "eq" and c == "user_id"} == {
        ("user_id", USER)
    }
    assert any(r["user_id"] == OTHER_USER for r in table.rows)  # the other user's row is intact


# ── listing ──────────────────────────────────────────────────────────────


async def test_listing_is_newest_first_and_only_the_callers_rows() -> None:
    supabase, _ = _world(
        [
            _row(1, "oldest"),
            _row(3, "newest"),
            _row(2, "middle", "Pune"),
            _row(9, "someone else's", user=OTHER_USER),
        ]
    )

    listed = await list_searches(supabase, USER)

    assert [s["query"] for s in listed] == ["newest", "middle", "oldest"]
    assert [s["location"] for s in listed] == [None, "Pune", None]
    assert all(set(s) == {"id", "query", "location", "created_at"} for s in listed)
    assert [s["query"] for s in await list_searches(supabase, OTHER_USER)] == ["someone else's"]


async def test_listing_with_no_searches_is_empty() -> None:
    supabase, _ = _world()
    assert await list_searches(supabase, USER) == []


async def test_listing_leaves_out_a_row_that_is_not_a_well_formed_search() -> None:
    good = _row(1, "data engineer")
    supabase, _ = _world(
        [
            good,
            {**_row(2, "x"), "query": 5},  # a role that is not text
            {**_row(3, "x"), "location": 7},  # a place that is not text
            {k: v for k, v in _row(4, "no id").items() if k != "id"},
        ]
    )

    listed = await list_searches(supabase, USER)

    assert [s["id"] for s in listed] == [good["id"]]  # never a 500, never listed


def test_the_api_shape_of_a_row_leaves_out_everything_but_the_four_fields() -> None:
    assert to_saved_search(
        {"id": "i", "user_id": USER, "query": "q", "location": None, "created_at": "t", "x": 1}
    ) == {"id": "i", "query": "q", "location": None, "created_at": "t"}
    for bad in (
        {"id": "i", "query": 5, "location": None, "created_at": "t"},
        {"id": "i", "query": "q", "location": 5, "created_at": "t"},
        {"query": "q", "location": None, "created_at": "t"},
    ):
        with pytest.raises((KeyError, TypeError)):
            to_saved_search(bad)


# ── deleting ─────────────────────────────────────────────────────────────


async def test_delete_removes_the_owners_search() -> None:
    mine = _row(1, "data engineer")
    supabase, table = _world([mine, _row(2, "other search")])

    await delete_search(supabase, USER, mine["id"])

    assert [r["query"] for r in table.rows] == ["other search"]


async def test_deleting_twice_is_a_not_found_the_second_time() -> None:
    mine = _row(1, "data engineer")
    supabase, _ = _world([mine])
    await delete_search(supabase, USER, mine["id"])
    with pytest.raises(HiringSignalSearchNotFound):
        await delete_search(supabase, USER, mine["id"])


async def test_a_foreign_or_unknown_id_is_the_same_not_found_and_deletes_nothing() -> None:
    theirs = _row(1, "theirs", user=OTHER_USER)
    supabase, table = _world([theirs, _row(2, "mine")])

    with pytest.raises(HiringSignalSearchNotFound):
        await delete_search(supabase, USER, theirs["id"])
    with pytest.raises(HiringSignalSearchNotFound):
        await delete_search(supabase, USER, "60000000-0000-0000-0000-00000000dead")

    assert len(table.rows) == 2
    assert any(r["id"] == theirs["id"] for r in table.rows)


async def test_a_deleted_search_can_be_saved_again() -> None:
    supabase, table = _world()
    saved, _ = await create_search(supabase, USER, query="data engineer")
    await delete_search(supabase, USER, saved["id"])

    _, created = await create_search(supabase, USER, query="data engineer")

    assert created is True and len(table.rows) == 1


async def test_every_query_the_store_makes_is_filtered_by_the_user() -> None:
    """The service-role client bypasses row-level security, so these filters ARE the
    isolation between users."""
    supabase, table = _world(_full(count=3))
    saved, _ = await create_search(supabase, USER, query="another one")
    await list_searches(supabase, USER)
    await delete_search(supabase, USER, saved["id"])

    for op, filters in table.calls:
        if op == "insert":
            continue
        assert ("eq", "user_id", USER) in filters, (op, filters)


async def test_a_lost_race_is_not_reported_as_a_full_list() -> None:
    """The verification refusal and the pre-count refusal are different exceptions:
    the first says a rival's save was in progress (the user may be far under the cap),
    the second that the user really has the maximum. The first is still a
    `HiringSignalSearchLimitReached` for a caller that does not tell them apart."""

    class RacingTable(FakeTable):
        rival_pending = True

        def insert_row(self, payload: dict[str, Any]) -> dict[str, Any]:
            row = super().insert_row(payload)
            if self.rival_pending:
                self.rival_pending = False
                super().insert_row(_row(900, "rival"))
            return row

    racing = RacingTable(_full(count=MAX_SAVED_SEARCHES - 1), unique=[_key])
    with pytest.raises(HiringSignalSearchLimitReached) as raced:
        await create_search(
            as_client(FakeSupabase({"hiring_signal_searches": racing})), USER, query="data engineer"
        )
    assert type(raced.value) is HiringSignalSearchSaveRaced

    supabase, _ = _world(_full())
    with pytest.raises(HiringSignalSearchLimitReached) as full:
        await create_search(supabase, USER, query="one too many")
    assert type(full.value) is HiringSignalSearchLimitReached  # the plain, truthful one
