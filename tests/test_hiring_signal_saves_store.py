"""Tests for Hiring Signals P3's saved-post store (`hiring_signal_saves_store`):
id-only pointers, idempotent creation arbitrated by the unique index,
ownership, ordering. Runs against the in-memory fake, whose unique keys mirror
the migration's two partial unique indexes."""

from __future__ import annotations

from typing import Any

import pytest
from hiring_signal_fakes import FakeSupabase, FakeTable, as_client, unique_violation
from postgrest.exceptions import APIError

from between_jobs.api import hiring_signal_saves_store as store
from between_jobs.api.hiring_signal_saves_store import (
    MAX_QUERY_LABEL_CHARS,
    HiringSignalSaveNotFound,
    canonical_post_url,
    clean_query_label,
    create_save,
    delete_save,
    is_valid_activity_id,
    list_saves,
    saved_activity_ids,
    to_saved_post,
)

USER = "00000000-0000-0000-0000-000000000001"
OTHER_USER = "00000000-0000-0000-0000-000000000002"
APP = "30000000-0000-0000-0000-000000000001"
OTHER_APP = "30000000-0000-0000-0000-000000000002"
ACTIVITY = "7506381452083381426"


def _unique_keys() -> list[Any]:
    """The migration's two partial unique indexes."""
    return [
        lambda r: (
            (r["user_id"], r["application_id"], r["activity_id"])
            if r.get("application_id") is not None
            else None
        ),
        lambda r: (r["user_id"], r["activity_id"]) if r.get("application_id") is None else None,
    ]


def _supabase(rows: list[dict[str, Any]] | None = None) -> tuple[FakeSupabase, FakeTable]:
    table = FakeTable(rows, unique=_unique_keys())
    return FakeSupabase({"hiring_signal_saves": table}), table


# ── ids and addresses ────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", ACTIVITY, "9" * 25])
def test_ascii_digit_ids_are_valid(value: str) -> None:
    assert is_valid_activity_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "12a",
        " 123",
        "123\n",
        "-1",
        "1.5",
        "9" * 26,
        "0",  # a real id never starts with 0, and 0007... is the same number as 7...
        "0" + ACTIVITY,
        "\u0661\u0662\u0663",  # Arabic-Indic digits: str.isdigit() accepts these
        chr(0xFF11) + chr(0xFF12),  # full-width digits
        "\u00b2",  # superscript two
        None,
        123,
    ],
)
def test_anything_but_ascii_digits_is_not_a_valid_id(value: object) -> None:
    assert not is_valid_activity_id(value)
    if isinstance(value, str):
        with pytest.raises(ValueError):
            canonical_post_url(value)


def test_the_canonical_address_is_id_only() -> None:
    assert canonical_post_url(ACTIVITY) == (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}"
    )


def test_the_api_shape_rebuilds_addresses_and_ignores_a_stored_slug_url() -> None:
    row = {
        "id": "row-1",
        "activity_id": ACTIVITY,
        "url": "https://www.linkedin.com/posts/jane-doe_a-topic-activity-1-x",
        "created_at": "2026-09-19T18:00:00+00:00",
        "user_id": USER,
        "discovered_via_query": "Acme -- role",
    }
    assert to_saved_post(row) == {
        "id": "row-1",
        "activity_id": ACTIVITY,
        "post_url": f"https://www.linkedin.com/feed/update/urn:li:activity:{ACTIVITY}",
        "embed_url": f"https://www.linkedin.com/embed/feed/update/urn:li:activity:{ACTIVITY}",
        "created_at": "2026-09-19T18:00:00+00:00",
    }


def test_labels_are_cleaned_and_capped() -> None:
    assert clean_query_label("Acme\x00 --\u202e role\n\t") == "Acme -- role"
    assert clean_query_label("x" * 1000) == "x" * MAX_QUERY_LABEL_CHARS
    assert clean_query_label("   ") is None
    assert clean_query_label(None) is None
    assert clean_query_label(12) is None


# ── creating ─────────────────────────────────────────────────────────────


async def test_creating_a_save_stores_only_a_pointer() -> None:
    supabase, table = _supabase()

    saved, created = await create_save(
        as_client(supabase),
        USER,
        APP,
        activity_id=ACTIVITY,
        query_label="Acme -- software engineer",
    )

    assert created is True
    (row,) = table.rows
    assert row["user_id"] == USER
    assert row["application_id"] == APP
    assert row["activity_id"] == ACTIVITY
    assert row["url"] == canonical_post_url(ACTIVITY)
    assert row["discovered_via_query"] == "Acme -- software engineer"
    # a pointer and provenance -- nothing else about the post exists to store
    assert set(row) == {
        "id",
        "created_at",
        "user_id",
        "application_id",
        "url",
        "activity_id",
        "discovered_via_query",
    }
    assert saved["activity_id"] == ACTIVITY
    assert saved["post_url"] == canonical_post_url(ACTIVITY)


async def test_creating_the_same_save_twice_is_idempotent() -> None:
    supabase, table = _supabase()
    first, created_first = await create_save(
        as_client(supabase), USER, APP, activity_id=ACTIVITY, query_label="first label"
    )
    second, created_second = await create_save(
        as_client(supabase), USER, APP, activity_id=ACTIVITY, query_label="second label"
    )

    assert (created_first, created_second) == (True, False)
    assert second == first
    assert len(table.rows) == 1
    assert table.rows[0]["discovered_via_query"] == "first label"  # not overwritten


async def test_the_label_is_stored_cleaned_and_capped() -> None:
    supabase, table = _supabase()
    await create_save(
        as_client(supabase), USER, APP, activity_id=ACTIVITY, query_label="A\x00" + "b" * 500
    )
    assert table.rows[0]["discovered_via_query"] == "A" + "b" * (MAX_QUERY_LABEL_CHARS - 1)


async def test_no_label_is_stored_as_null() -> None:
    supabase, table = _supabase()
    await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)
    assert table.rows[0]["discovered_via_query"] is None


async def test_an_invalid_id_is_refused_before_anything_is_written() -> None:
    supabase, table = _supabase()
    with pytest.raises(ValueError):
        await create_save(as_client(supabase), USER, APP, activity_id="12a")
    assert table.calls == []


async def test_the_same_post_can_be_saved_for_different_applications_and_users() -> None:
    supabase, table = _supabase()
    for user, app in [(USER, APP), (USER, OTHER_APP), (OTHER_USER, APP)]:
        _, created = await create_save(as_client(supabase), user, app, activity_id=ACTIVITY)
        assert created is True
    assert len(table.rows) == 3


async def test_a_lost_race_returns_the_winners_row_with_created_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests looked, neither saw a row, both inserted; the unique
    index let one through. The loser must get the winner's row back, not an
    error."""
    supabase, table = _supabase()
    winner, _ = await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)

    real_find = store._find
    lookups = 0

    async def blind_first_lookup(*args: Any, **kwargs: Any) -> Any:
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real_find(*args, **kwargs)

    monkeypatch.setattr(store, "_find", blind_first_lookup)
    loser, created = await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)

    assert created is False
    assert loser == winner
    assert len(table.rows) == 1
    assert table.insert_attempts == 2  # the loser really did hit the unique index


async def test_a_unique_violation_whose_row_vanished_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supabase, _ = _supabase()
    await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)

    async def never_found(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(store, "_find", never_found)
    with pytest.raises(APIError) as caught:
        await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)
    assert caught.value.code == "23505"


async def test_other_database_errors_propagate() -> None:
    supabase, table = _supabase()
    table.fail_with = APIError({"message": "boom", "code": "XX000", "details": None, "hint": None})
    with pytest.raises(APIError):
        await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)


def test_the_fake_reproduces_a_real_unique_violation_code() -> None:
    assert unique_violation().code == "23505"


# ── listing ──────────────────────────────────────────────────────────────


async def test_listing_returns_the_callers_saves_for_that_application_newest_first() -> None:
    rows = [
        {"id": "a", "user_id": USER, "application_id": APP, "activity_id": "111", "url": "u",
         "created_at": "2026-09-19T10:00:00+00:00"},
        {"id": "b", "user_id": USER, "application_id": APP, "activity_id": "222", "url": "u",
         "created_at": "2026-09-19T12:00:00+00:00"},
        {"id": "c", "user_id": USER, "application_id": APP, "activity_id": "333", "url": "u",
         "created_at": "2026-09-19T11:00:00+00:00"},
        {"id": "other-app", "user_id": USER, "application_id": OTHER_APP, "activity_id": "444",
         "url": "u", "created_at": "2026-09-19T13:00:00+00:00"},
        {"id": "other-user", "user_id": OTHER_USER, "application_id": APP, "activity_id": "555",
         "url": "u", "created_at": "2026-09-19T14:00:00+00:00"},
    ]  # fmt: skip
    supabase, _ = _supabase(rows)

    saves = await list_saves(as_client(supabase), USER, APP)

    assert [s["id"] for s in saves] == ["b", "c", "a"]
    assert all(
        set(s) == {"id", "activity_id", "post_url", "embed_url", "created_at"} for s in saves
    )


async def test_listing_with_no_saves_is_empty() -> None:
    supabase, _ = _supabase()
    assert await list_saves(as_client(supabase), USER, APP) == []


async def test_saved_activity_ids_marks_only_this_users_saves_for_this_application() -> None:
    supabase, table = _supabase()
    await create_save(as_client(supabase), USER, APP, activity_id="111")
    await create_save(as_client(supabase), USER, OTHER_APP, activity_id="222")
    await create_save(as_client(supabase), OTHER_USER, APP, activity_id="333")
    table.calls.clear()

    assert await saved_activity_ids(
        as_client(supabase), USER, APP, ["111", "222", "333", "444"]
    ) == {"111"}
    assert await saved_activity_ids(as_client(supabase), USER, APP, []) == set()
    assert len(table.calls) == 1  # the empty lookup asked the database nothing


# ── deleting ─────────────────────────────────────────────────────────────


async def test_deleting_removes_the_callers_own_save() -> None:
    supabase, table = _supabase()
    saved, _ = await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)

    await delete_save(as_client(supabase), USER, saved["id"])

    assert table.rows == []


async def test_deleting_someone_elses_save_or_an_unknown_id_is_the_same_not_found() -> None:
    supabase, table = _supabase()
    saved, _ = await create_save(as_client(supabase), OTHER_USER, APP, activity_id=ACTIVITY)

    with pytest.raises(HiringSignalSaveNotFound):
        await delete_save(as_client(supabase), USER, saved["id"])
    with pytest.raises(HiringSignalSaveNotFound):
        await delete_save(as_client(supabase), USER, "00000000-0000-0000-0000-00000000dead")
    assert len(table.rows) == 1  # the other user's row is untouched


# ── BC-10 / LP-5: one save per post, and a bad row never breaks the list ──


async def test_the_same_number_spelled_with_leading_zeros_is_refused_not_saved_twice() -> None:
    supabase, table = _supabase()
    await create_save(as_client(supabase), USER, APP, activity_id=ACTIVITY)

    with pytest.raises(ValueError):
        await create_save(as_client(supabase), USER, APP, activity_id="000" + ACTIVITY)

    assert len(table.rows) == 1


async def test_listing_leaves_out_a_row_that_is_not_a_well_formed_pointer() -> None:
    good = {
        "id": "40000000-0000-0000-0000-000000000001",
        "user_id": USER,
        "application_id": APP,
        "url": store.POST_URL_TEMPLATE.format(activity_id=ACTIVITY),
        "activity_id": ACTIVITY,
        "source": "linkedin",
        "discovered_via_query": None,
        "created_at": "2026-09-19T18:00:00+00:00",
    }
    bad = {**good, "id": "40000000-0000-0000-0000-000000000002", "activity_id": "abc"}
    bad_zeros = {
        **good,
        "id": "40000000-0000-0000-0000-000000000003",
        "activity_id": "0" + ACTIVITY,
    }
    supabase, _ = _supabase([good, bad, bad_zeros])

    listed = await list_saves(as_client(supabase), USER, APP)

    assert [row["id"] for row in listed] == [good["id"]]
