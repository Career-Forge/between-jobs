"""The product-event log and the tester enrollment, against a real local stack.

What the fast suite cannot show: that the tables have exactly the grants and policies they were
meant to (a user token reads no event and writes no enrollment; not even the service role can
change or remove an event), that every CHECK accepts what the code sends and refuses what it
should not, that deleting a user or an application leaves what it should, that a link between two
accounts moves the events and settles the enrollment, and that the real routes write real rows.

It is also where two promises are enforced against the live catalog, so that a later migration
(the only way to change an applied one) cannot get past them: the table has no column that could
hold a URL, a path or free text, and every closed list in api/product_events.py is exactly the
CHECK list the table enforces. The fast suite reads only the migration that created the table.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from postgrest.exceptions import APIError

from between_jobs.api import product_events
from between_jobs.api.product_events import (
    ATS_TYPES,
    CAPABILITIES,
    EVENTS,
    OUTCOMES,
    write_event,
)

from .conftest import World
from .cross_tenant.harness import Pair, Tenant, make_tenant

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

_ALL_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


# -- the grants and the policies ---------------------------------------------------------------


async def _privileges(world: World, table: str, role: str) -> set[str]:
    rows = await world.pg.fetch(
        "select p from unnest($1::text[]) p where has_table_privilege($2, $3, p)",
        list(_ALL_PRIVILEGES),
        role,
        f"public.{table}",
    )
    return {r["p"] for r in rows}


@pytest.mark.parametrize(
    ("table", "role", "expected"),
    [
        # the backend appends and reads events; nothing may change or remove one
        ("product_events", "service_role", {"SELECT", "INSERT"}),
        ("product_events", "authenticated", set()),
        ("product_events", "anon", set()),
        # the backend writes enrollments (including withdrawing); a tester may read their own
        ("tester_enrollments", "service_role", {"SELECT", "INSERT", "UPDATE"}),
        ("tester_enrollments", "authenticated", {"SELECT"}),
        ("tester_enrollments", "anon", set()),
    ],
)
async def test_each_role_has_exactly_the_privileges_intended(
    tenant_world: World, table: str, role: str, expected: set[str]
) -> None:
    assert await _privileges(tenant_world, table, role) == expected


@pytest.mark.parametrize("table", ["product_events", "tester_enrollments"])
async def test_nothing_is_granted_to_public(tenant_world: World, table: str) -> None:
    """The special role every role inherits from: prod's defaults never gave it a table, and
    neither may a migration."""
    granted = await tenant_world.pg.fetchval(
        """
        select count(*) from pg_class c, aclexplode(c.relacl) a
        where c.oid = $1::regclass and a.grantee = 0
        """,
        f"public.{table}",
    )
    assert granted == 0


async def test_row_level_security_is_on_and_the_policies_are_the_ones_designed(
    tenant_world: World,
) -> None:
    rls = await tenant_world.pg.fetch(
        """
        select relname, relrowsecurity from pg_class
        where oid in ('public.product_events'::regclass, 'public.tester_enrollments'::regclass)
        """
    )
    assert {r["relname"]: r["relrowsecurity"] for r in rls} == {
        "product_events": True,
        "tester_enrollments": True,
    }
    policies = await tenant_world.pg.fetch(
        """
        select tablename, policyname, cmd, qual from pg_policies
        where schemaname = 'public' and tablename in ('product_events', 'tester_enrollments')
        """
    )
    # product_events: no policy at all. tester_enrollments: one, a select of one's own row.
    assert [(p["tablename"], p["policyname"], p["cmd"], p["qual"]) for p in policies] == [
        (
            "tester_enrollments",
            "tester_enrollments_select_own",
            "SELECT",
            "(auth.uid() = user_id)",
        )
    ]


# -- the live catalog: the privacy promise and the closed lists, whichever migration changed them -


_EVENT_COLUMNS = {
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
    "created_at": "timestamp with time zone",
}


async def _live_columns(world: World) -> dict[str, str]:
    rows = await world.pg.fetch(
        """
        select column_name, data_type from information_schema.columns
        where table_schema = 'public' and table_name = 'product_events'
        """
    )
    return {r["column_name"]: r["data_type"] for r in rows}


# What the database prints for `check (col in ('a', 'b'))`.
_LIST_CHECK = re.compile(r"CHECK \(\((\w+) = ANY \(ARRAY\[(.*)\]\)\)\)")


async def _live_check_lists(world: World) -> dict[str, list[str]]:
    """{column: the values of its `col in (...)` CHECK} as the live table enforces them."""
    rows = await world.pg.fetch(
        """
        select pg_get_constraintdef(oid) as definition from pg_constraint
        where conrelid = 'public.product_events'::regclass and contype = 'c'
        """
    )
    lists: dict[str, list[str]] = {}
    for row in rows:
        match = _LIST_CHECK.fullmatch(row["definition"])
        if match is None:
            continue  # a numeric bound or a cross-column rule, not a closed list
        column, body = match.group(1), match.group(2)
        assert column not in lists, f"{column} has two closed-list CHECKs"
        lists[column] = re.findall(r"'([^']*)'::text", body)
    return lists


async def test_the_event_table_has_no_column_that_could_hold_a_url_or_text(
    tenant_world: World,
) -> None:
    """The privacy promise, as a test, against the table as the database really has it after
    every migration: exactly these columns, none a free-text, JSON or binary type. A text column
    is allowed only because it carries a closed-list CHECK, so nothing but a known name can ever
    be stored in one. Adding a column in any later migration fails this until a person has read
    what it can hold."""
    columns = await _live_columns(tenant_world)

    assert columns == _EVENT_COLUMNS
    check_lists = await _live_check_lists(tenant_world)
    for name, kind in columns.items():
        if kind == "text":
            assert check_lists.get(name), f"{name} is a text column with no closed-list CHECK"
    # and no column name even suggests the things that must never be kept
    suggestive = r"url|path|host|domain|ip_|agent|body|content|payload|json"
    assert [name for name in columns if re.search(suggestive, name)] == []


@pytest.mark.parametrize(
    ("column", "python"),
    [
        ("event", EVENTS),
        ("outcome", OUTCOMES),
        ("capability", CAPABILITIES),
        ("ats_type", ATS_TYPES),
    ],
)
async def test_each_closed_list_in_python_is_the_one_the_live_table_enforces(
    tenant_world: World, column: str, python: frozenset[str]
) -> None:
    """The Literals in api/product_events.py and the table's CHECKs are two copies of one list.
    A value only in Python makes every such insert fail its CHECK (and, because recording fails
    open, silently); a value only in SQL is a value nothing can send. Read from the live catalog,
    so widening a list in a later migration is checked the same way as the original."""
    live = (await _live_check_lists(tenant_world)).get(column)

    assert live is not None, f"no closed-list CHECK on product_events.{column}"
    assert len(live) == len(set(live)), f"{column} lists a value twice"
    assert set(live) == set(python)


async def test_the_catalog_reader_would_notice_what_it_exists_to_notice() -> None:
    """Guards the guard: the pattern reads the shape the database prints, and ignores the rest."""
    printed = "CHECK ((outcome = ANY (ARRAY['ok'::text, 'partial'::text])))"
    match = _LIST_CHECK.fullmatch(printed)
    assert match is not None and match.group(1) == "outcome"
    assert re.findall(r"'([^']*)'::text", match.group(2)) == ["ok", "partial"]
    assert _LIST_CHECK.fullmatch("CHECK ((n_a >= 0))") is None
    assert _LIST_CHECK.fullmatch("CHECK (((event <> 'x'::text) OR (n_b <= n_a)))") is None


# -- what a signed-in user's token can do through PostgREST --------------------------------------


class _Rest:
    def __init__(self, stack: dict[str, str]) -> None:
        self.base = f"{stack['API_URL']}/rest/v1"
        self.anon = stack["ANON_KEY"]

    def headers(self, token: str | None = None) -> dict[str, str]:
        return {
            "apikey": self.anon,
            "Authorization": f"Bearer {token or self.anon}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }


async def _enroll(world: World, user_id: str, **overrides: Any) -> None:
    await (
        world.sb.table("tester_enrollments")
        .insert(
            {
                "user_id": user_id,
                "role_cohort": "data_analyst",
                "seniority": "mid",
                "needs_sponsorship": None,
                "consent_version": "2026-10-test",
                "consented_at": datetime.now(UTC).isoformat(),
                **overrides,
            }
        )
        .execute()
    )


async def test_a_user_token_reads_no_event_and_writes_none(
    tenant_world: World, tenant_pair: Pair, local_stack: dict[str, str]
) -> None:
    a = tenant_pair.a
    await (
        tenant_world.sb.table("product_events")
        .insert({"user_id": a.user_id, "event": "copy_panel_opened"})
        .execute()
    )
    rest = _Rest(local_stack)
    url = f"{rest.base}/product_events"

    async with httpx.AsyncClient(timeout=20) as http:
        for who, token in (("A", a.token), ("anon", None)):
            read = await http.get(url, params={"select": "*"}, headers=rest.headers(token))
            # refused outright (no privilege), not an empty list that would hide the table
            assert read.status_code in {401, 403}, f"{who}: {read.status_code} {read.text[:200]}"
            write = await http.post(
                url,
                json={"user_id": a.user_id, "event": "copy_panel_opened"},
                headers=rest.headers(token),
            )
            assert write.status_code in {401, 403}, f"{who}: {write.status_code}"
            change = await http.patch(
                url,
                params={"user_id": f"eq.{a.user_id}"},
                json={"n_a": 1},
                headers=rest.headers(token),
            )
            assert change.status_code in {401, 403}, f"{who}: {change.status_code}"
            remove = await http.delete(
                url, params={"user_id": f"eq.{a.user_id}"}, headers=rest.headers(token)
            )
            assert remove.status_code in {401, 403}, f"{who}: {remove.status_code}"

    rows = (
        await tenant_world.sb.table("product_events")
        .select("id")
        .eq("user_id", a.user_id)
        .execute()
    ).data
    assert rows, "the event the stranger tried to remove is gone"


async def test_a_tester_reads_only_their_own_enrollment_and_changes_nothing(
    tenant_world: World, local_stack: dict[str, str]
) -> None:
    a = await make_tenant(tenant_world, "A")
    b = await make_tenant(tenant_world, "B")
    await _enroll(tenant_world, a.user_id, role_cohort="qa_sdet", needs_sponsorship=True)
    await _enroll(tenant_world, b.user_id, role_cohort="devops_sre")
    rest = _Rest(local_stack)
    url = f"{rest.base}/tester_enrollments"

    async with httpx.AsyncClient(timeout=20) as http:
        mine = await http.get(url, params={"select": "*"}, headers=rest.headers(a.token))
        assert mine.status_code == 200
        assert [r["user_id"] for r in mine.json()] == [a.user_id]
        assert mine.json()[0]["role_cohort"] == "qa_sdet"
        theirs = await http.get(
            url, params={"select": "*", "user_id": f"eq.{b.user_id}"}, headers=rest.headers(a.token)
        )
        assert theirs.status_code == 200 and theirs.json() == []

        # no write of any kind: not an insert (even for oneself), not an update, not a delete
        insert = await http.post(
            url,
            json={
                "user_id": a.user_id,
                "role_cohort": "data_analyst",
                "seniority": "mid",
                "consent_version": "x",
                "consented_at": datetime.now(UTC).isoformat(),
            },
            headers=rest.headers(a.token),
        )
        assert insert.status_code in {401, 403}
        update = await http.patch(
            url,
            params={"user_id": f"eq.{a.user_id}"},
            json={"role_cohort": "data_scientist", "withdrawn_at": None},
            headers=rest.headers(a.token),
        )
        assert update.status_code in {401, 403}
        steal = await http.patch(
            url,
            params={"user_id": f"eq.{b.user_id}"},
            json={"role_cohort": "data_scientist"},
            headers=rest.headers(a.token),
        )
        assert steal.status_code in {401, 403}
        delete = await http.delete(
            url, params={"user_id": f"eq.{a.user_id}"}, headers=rest.headers(a.token)
        )
        assert delete.status_code in {401, 403}

        # and a caller with only the public key sees nothing of it
        anonymous = await http.get(url, params={"select": "*"}, headers=rest.headers())
        assert anonymous.status_code in {401, 403}

    rows = (
        await tenant_world.sb.table("tester_enrollments")
        .select("user_id, role_cohort")
        .in_("user_id", [a.user_id, b.user_id])
        .execute()
    ).data
    assert {(r["user_id"], r["role_cohort"]) for r in rows} == {
        (a.user_id, "qa_sdet"),
        (b.user_id, "devops_sre"),
    }


async def test_not_even_the_service_role_can_change_or_remove_an_event(
    tenant_world: World,
) -> None:
    user = await tenant_world.web_user()
    row = (
        await tenant_world.sb.table("product_events")
        .insert({"user_id": user, "event": "discover_search", "n_a": 3})
        .execute()
    ).data[0]

    with pytest.raises(APIError) as update:
        await (
            tenant_world.sb.table("product_events")
            .update({"n_a": 99})
            .eq("id", row["id"])
            .execute()
        )
    with pytest.raises(APIError) as delete:
        await tenant_world.sb.table("product_events").delete().eq("id", row["id"]).execute()

    assert (update.value.code, delete.value.code) == ("42501", "42501")  # insufficient_privilege
    kept = (
        await tenant_world.sb.table("product_events").select("n_a").eq("id", row["id"]).execute()
    ).data
    assert kept == [{"n_a": 3}]


# -- the CHECK constraints ---------------------------------------------------------------------


def _valid_event_row(user_id: str, event: str) -> dict[str, Any]:
    row: dict[str, Any] = {"user_id": user_id, "event": event}
    if event == "extension_fill":
        row.update({"n_a": 5, "n_b": 4})  # a fill must say how many fields it tried and filled
    return row


@pytest.mark.parametrize("event", sorted(EVENTS))
async def test_the_database_accepts_every_event_the_code_can_send(
    tenant_world: World, event: str
) -> None:
    user = await tenant_world.web_user()

    row = (
        await tenant_world.sb.table("product_events")
        .insert(_valid_event_row(user, event))
        .execute()
    ).data[0]

    assert row["event"] == event
    assert row["id"] and row["created_at"]


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("outcome", sorted(OUTCOMES)),
        ("capability", sorted(CAPABILITIES)),
        ("ats_type", sorted(ATS_TYPES)),
    ],
)
async def test_the_database_accepts_every_value_of_every_closed_list(
    tenant_world: World, column: str, values: list[str]
) -> None:
    user = await tenant_world.web_user()
    rows = [{"user_id": user, "event": "discover_search", column: value} for value in values]

    inserted = (await tenant_world.sb.table("product_events").insert(rows).execute()).data

    assert sorted(r[column] for r in inserted) == values


@pytest.mark.parametrize(
    "bad",
    [
        {"event": "page_view"},
        {"event": ""},
        {"outcome": "success"},
        {"capability": "something_else"},
        {"ats_type": "https://jobs.lever.co/acme"},
        {"ats_type": "lever.co"},
        {"ats_type": "Lever"},
        {"n_a": -1},
        {"n_b": -1},
        {"duration_ms": -1},
        {"n_a": 2**31},  # past what an integer holds
    ],
    ids=lambda bad: f"{next(iter(bad))}={next(iter(bad.values()))!r}",
)
async def test_the_database_refuses_what_the_lists_and_ranges_do_not_allow(
    tenant_world: World, bad: dict[str, Any]
) -> None:
    user = await tenant_world.web_user()

    with pytest.raises(APIError) as refused:
        await (
            tenant_world.sb.table("product_events")
            .insert({"user_id": user, "event": "discover_search", **bad})
            .execute()
        )

    assert refused.value.code in {"23514", "22003"}  # check_violation, numeric_value_out_of_range


@pytest.mark.parametrize(
    "bad",
    [
        {"n_a": 3, "n_b": 4},  # filled more than attempted
        {"n_a": 3},  # a fill with no filled count
        {"n_b": 3},  # a fill with no attempted count
        {},  # a fill with neither
    ],
    ids=["filled>attempted", "no filled", "no attempted", "no counts"],
)
async def test_a_fill_event_must_carry_consistent_counts(
    tenant_world: World, bad: dict[str, Any]
) -> None:
    user = await tenant_world.web_user()

    with pytest.raises(APIError) as refused:
        await (
            tenant_world.sb.table("product_events")
            .insert({"user_id": user, "event": "extension_fill", **bad})
            .execute()
        )

    assert refused.value.code == "23514"


async def test_an_event_needs_a_real_user(tenant_world: World) -> None:
    with pytest.raises(APIError) as refused:
        await (
            tenant_world.sb.table("product_events")
            .insert({"user_id": str(uuid.uuid4()), "event": "discover_search"})
            .execute()
        )

    assert refused.value.code == "23503"  # foreign_key_violation


@pytest.mark.parametrize(
    "bad",
    [
        {"role_cohort": "growth_hacker"},
        {"role_cohort": ""},
        {"seniority": "intern"},
        {"consent_version": ""},
        {"consent_version": "v" * 41},
    ],
)
async def test_the_enrollment_refuses_what_the_programs_lists_do_not_allow(
    tenant_world: World, bad: dict[str, Any]
) -> None:
    user = await tenant_world.web_user()

    with pytest.raises(APIError) as refused:
        await _enroll(tenant_world, user, **bad)

    assert refused.value.code == "23514"


async def test_every_cohort_and_seniority_band_of_the_program_is_accepted(
    tenant_world: World,
) -> None:
    cohorts = [
        "data_analyst",
        "data_engineer",
        "data_scientist",
        "ai_ml_engineer",
        "software_engineer",
        "frontend_engineer",
        "devops_sre",
        "qa_sdet",
        "product_manager",
        "business_analyst",
    ]
    bands = ["new_grad", "early_career", "mid", "senior", "lead_plus"]
    for index, cohort in enumerate(cohorts):
        await _enroll(
            tenant_world,
            await tenant_world.web_user(),
            role_cohort=cohort,
            seniority=bands[index % 5],
        )


async def test_the_sponsorship_answer_is_optional_and_null_means_unknown(
    tenant_world: World,
) -> None:
    asked_no = await tenant_world.web_user()
    asked_yes = await tenant_world.web_user()
    not_asked = await tenant_world.web_user()
    await _enroll(tenant_world, asked_no, needs_sponsorship=False)
    await _enroll(tenant_world, asked_yes, needs_sponsorship=True)
    await _enroll(tenant_world, not_asked, needs_sponsorship=None)

    rows = (
        await tenant_world.sb.table("tester_enrollments")
        .select("user_id, needs_sponsorship")
        .in_("user_id", [asked_no, asked_yes, not_asked])
        .execute()
    ).data

    assert {r["user_id"]: r["needs_sponsorship"] for r in rows} == {
        asked_no: False,
        asked_yes: True,
        not_asked: None,
    }


async def test_a_user_is_enrolled_once(tenant_world: World) -> None:
    user = await tenant_world.web_user()
    await _enroll(tenant_world, user)

    with pytest.raises(APIError) as refused:
        await _enroll(tenant_world, user, role_cohort="qa_sdet")

    assert refused.value.code == "23505"


async def test_the_service_role_can_withdraw_an_enrollment_but_not_delete_the_row(
    tenant_world: World,
) -> None:
    user = await tenant_world.web_user()
    await _enroll(tenant_world, user)
    when = datetime.now(UTC).isoformat()

    await (
        tenant_world.sb.table("tester_enrollments")
        .update({"withdrawn_at": when})
        .eq("user_id", user)
        .execute()
    )
    with pytest.raises(APIError) as delete:
        await tenant_world.sb.table("tester_enrollments").delete().eq("user_id", user).execute()

    assert delete.value.code == "42501"
    kept = (
        await tenant_world.sb.table("tester_enrollments")
        .select("withdrawn_at")
        .eq("user_id", user)
        .execute()
    ).data
    assert kept[0]["withdrawn_at"] is not None


# -- what deleting a user or an application leaves ---------------------------------------------


async def test_deleting_a_user_deletes_their_events_and_enrollment_and_nobody_elses(
    tenant_world: World,
) -> None:
    world = tenant_world
    leaving, staying = await world.web_user(), await world.web_user()
    for user in (leaving, staying):
        await _enroll(world, user, needs_sponsorship=True)
        await (
            world.sb.table("product_events")
            .insert(
                [
                    {"user_id": user, "event": "prepare_finished", "outcome": "ok", "n_a": 0},
                    {
                        "user_id": user,
                        "event": "discover_search",
                        "outcome": "ok",
                        "n_a": 3,
                        "n_b": 2,
                    },
                ]
            )
            .execute()
        )
    counts = await world.counts(leaving)
    assert (counts["public.product_events"], counts["public.tester_enrollments"]) == (2, 1)

    await world.sb.auth.admin.delete_user(leaving)

    for table in ("product_events", "tester_enrollments"):
        gone = (await world.sb.table(table).select("user_id").eq("user_id", leaving).execute()).data
        kept = (await world.sb.table(table).select("user_id").eq("user_id", staying).execute()).data
        assert gone == [], f"{table} kept a deleted user's rows"
        assert kept, f"{table} lost somebody else's rows"


async def test_deleting_an_application_unlinks_its_events_and_keeps_them(
    tenant_world: World,
) -> None:
    world = tenant_world
    user = await world.web_user()
    job = await world.job()
    application = await world.application(user, job)
    other = await world.application(user, await world.job())
    await (
        world.sb.table("product_events")
        .insert(
            [
                {"user_id": user, "event": "prepare_finished", "application_id": application["id"]},
                {"user_id": user, "event": "prepare_finished", "application_id": other["id"]},
            ]
        )
        .execute()
    )

    await world.sb.table("applications").delete().eq("id", application["id"]).execute()

    rows = (
        await world.sb.table("product_events")
        .select("application_id")
        .eq("user_id", user)
        .execute()
    ).data
    assert sorted(r["application_id"] or "" for r in rows) == sorted(["", other["id"]])


async def test_the_row_counts_the_deletion_check_uses_include_both_tables(
    tenant_world: World,
) -> None:
    world = tenant_world
    user = await world.web_user()
    await _enroll(world, user)
    await (
        world.sb.table("product_events")
        .insert({"user_id": user, "event": "copy_panel_opened"})
        .execute()
    )

    counts = await world.counts(user)

    assert counts["public.product_events"] == 1
    assert counts["public.tester_enrollments"] == 1


# -- the account link --------------------------------------------------------------------------


async def _drained(world: World, user_id: str) -> dict[str, int]:
    counts = await world.counts(user_id)
    counts.pop("artifact_versions.storage_key", None)
    counts.pop("storage.objects", None)
    return counts


async def test_a_link_moves_the_sources_events_to_the_target_and_drops_its_enrollment(
    tenant_world: World,
) -> None:
    world = tenant_world
    src = await world.telegram_user()
    target = await world.web_user()
    await (
        world.sb.table("product_events")
        .insert(
            [
                {"user_id": src.id, "event": "prepare_finished", "outcome": "ok", "n_a": 0},
                {
                    "user_id": src.id,
                    "event": "discover_search",
                    "outcome": "ok",
                    "n_a": 4,
                    "n_b": 3,
                },
            ]
        )
        .execute()
    )
    await (
        world.sb.table("product_events")
        .insert({"user_id": target, "event": "artifact_downloaded", "outcome": "ok", "n_a": 1})
        .execute()
    )
    # Not something a Telegram-only account can do in practice, but if one ever did, the
    # target's own consent record is the one that stands.
    await _enroll(world, src.id, role_cohort="qa_sdet", consent_version="from-the-source")
    await _enroll(world, target, role_cohort="data_analyst", consent_version="from-the-target")

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    assert result["summary"]["product_events"] == 2
    assert await _drained(world, src.id) == {}
    moved = (
        await world.sb.table("product_events").select("event").eq("user_id", target).execute()
    ).data
    assert sorted(r["event"] for r in moved) == [
        "artifact_downloaded",
        "discover_search",
        "prepare_finished",
    ]
    enrollment = (
        await world.sb.table("tester_enrollments").select("*").eq("user_id", target).execute()
    ).data
    assert [(e["role_cohort"], e["consent_version"]) for e in enrollment] == [
        ("data_analyst", "from-the-target")
    ]


async def test_a_link_keeps_no_enrollment_the_target_did_not_already_have(
    tenant_world: World,
) -> None:
    """Said plainly so nobody is surprised: the source's enrollment is deleted, not moved, even
    when the target has none. Enrollment is recorded on the web, so a source account that is
    only a Telegram identity has none to lose."""
    world = tenant_world
    src = await world.telegram_user()
    target = await world.web_user()
    await _enroll(world, src.id)

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    assert await _drained(world, src.id) == {}
    assert (
        await world.sb.table("tester_enrollments").select("user_id").eq("user_id", target).execute()
    ).data == []


async def test_events_that_name_an_application_that_collapses_follow_it_to_the_targets(
    tenant_world: World,
) -> None:
    """Both accounts track the same job, so the source's application is folded into the
    target's. An event that named the source's must end up naming the target's: the merge
    refuses to delete a row anything still points at, even through a set-null key."""
    world = tenant_world
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    src_application = await world.application(src.id, job)
    target_application = await world.application(target, job)
    await (
        world.sb.table("product_events")
        .insert(
            {
                "user_id": src.id,
                "event": "prepare_finished",
                "application_id": src_application["id"],
                "outcome": "ok",
            }
        )
        .execute()
    )

    result = await world.link(src.subject, await world.mint(target), src.id)

    assert result["ok"] is True
    events = (
        await world.sb.table("product_events")
        .select("user_id, application_id")
        .eq("event", "prepare_finished")
        .eq("application_id", target_application["id"])
        .execute()
    ).data
    assert events == [{"user_id": target, "application_id": target_application["id"]}]
    assert await _drained(world, src.id) == {}


# -- the real routes write real rows -----------------------------------------------------------


async def _events_for(world: World, user_id: str) -> list[dict[str, Any]]:
    await product_events.flush()  # the routes record in the background
    return list(
        (
            await world.sb.table("product_events")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at")
            .execute()
        ).data
    )


async def test_a_fill_report_through_the_real_app_is_one_real_row(
    tenant_client: httpx.AsyncClient, tenant_world: World
) -> None:
    tenant: Tenant = await make_tenant(tenant_world, "A")
    job = await tenant_world.job()
    application = await tenant_world.application(tenant.user_id, job)

    response = await tenant_client.post(
        "/extension/fill-outcome",
        json={
            "ats_type": "greenhouse",
            "application_id": application["id"],
            "fields_attempted": 11,
            "fields_filled": 9,
            "outcome": "partial",
        },
        headers=tenant.headers,
    )

    assert response.status_code == 204
    (row,) = await _events_for(tenant_world, tenant.user_id)
    assert {k: v for k, v in row.items() if k not in {"id", "created_at"}} == {
        "user_id": tenant.user_id,
        "event": "extension_fill",
        "capability": None,
        "application_id": application["id"],
        "ats_type": "greenhouse",
        "outcome": "partial",
        "n_a": 11,
        "n_b": 9,
        "duration_ms": None,
    }


async def test_a_refused_fill_report_leaves_no_row(
    tenant_client: httpx.AsyncClient, tenant_world: World
) -> None:
    tenant = await make_tenant(tenant_world, "A")
    body = {"ats_type": "lever", "fields_attempted": 3, "fields_filled": 3, "outcome": "ok"}

    unknown_application = await tenant_client.post(
        "/extension/fill-outcome",
        json={**body, "application_id": str(uuid.uuid4())},
        headers=tenant.headers,
    )
    carries_a_value = await tenant_client.post(
        "/extension/fill-outcome",
        json={**body, "value": "jane@example.com"},
        headers=tenant.headers,
    )
    no_token = await tenant_client.post("/extension/fill-outcome", json=body)

    assert (unknown_application.status_code, carries_a_value.status_code) == (404, 422)
    assert no_token.status_code == 401
    assert await _events_for(tenant_world, tenant.user_id) == []


async def test_a_search_that_stops_on_setup_records_the_attempt_and_the_setup_step(
    tenant_client: httpx.AsyncClient, tenant_world: World
) -> None:
    """No profile has been imported, so /discover answers 409 SETUP_REQUIRED. Through the real
    authentication and error handler that is two events: the search attempt, and the one
    `setup_required` event every route's setup error produces."""
    tenant = await make_tenant(tenant_world, "A")

    response = await tenant_client.get(
        "/discover", params={"q": "data analyst"}, headers=tenant.headers
    )

    assert response.status_code == 409
    events = {e["event"]: e for e in await _events_for(tenant_world, tenant.user_id)}
    assert sorted(events) == ["discover_search", "setup_required"]  # one of each, in no set order
    for event in events.values():
        assert (event["outcome"], event["capability"]) == ("setup_required", "profile")
    assert events["discover_search"]["duration_ms"] is not None
    assert events["setup_required"]["duration_ms"] is None


async def test_a_generation_attempt_is_recorded_only_once_the_application_is_the_callers(
    tenant_client: httpx.AsyncClient, tenant_world: World
) -> None:
    owner = await make_tenant(tenant_world, "A")
    stranger = await make_tenant(tenant_world, "B")
    job = await tenant_world.job()
    application = await tenant_world.application(owner.user_id, job)
    body = {"idempotency_key": "k" * 20}

    foreign = await tenant_client.post(
        f"/applications/{application['id']}/prepare", json=body, headers=stranger.headers
    )
    own = await tenant_client.post(
        f"/applications/{application['id']}/prepare", json=body, headers=owner.headers
    )

    assert foreign.status_code == 404
    assert own.status_code == 409  # no profile yet: SETUP_REQUIRED, after the ownership check
    assert await _events_for(tenant_world, stranger.user_id) == []
    events = {e["event"]: e for e in await _events_for(tenant_world, owner.user_id)}
    assert sorted(events) == ["prepare_finished", "setup_required"]
    assert events["prepare_finished"]["application_id"] == application["id"]
    assert events["setup_required"]["application_id"] is None
    for event in events.values():
        assert (event["outcome"], event["capability"]) == ("setup_required", "profile")


# -- failing open against the real database ----------------------------------------------------


async def test_a_row_the_database_refuses_is_reported_and_logged_never_raised(
    tenant_world: World, caplog: pytest.LogCaptureFixture
) -> None:
    user = await tenant_world.web_user()

    with caplog.at_level(logging.WARNING, logger=product_events.logger.name):
        bad_value = await write_event(tenant_world.sb, {"user_id": user, "event": "page_view"})
        no_user = await write_event(
            tenant_world.sb, {"user_id": str(uuid.uuid4()), "event": "discover_search"}
        )
        fine = await write_event(tenant_world.sb, {"user_id": user, "event": "discover_search"})

    assert (bad_value, no_user, fine) == (False, False, True)
    codes = [r.ctx["error_code"] for r in caplog.records if hasattr(r, "ctx")]
    assert codes == ["23514", "23503"]
    assert "page_view" not in caplog.text  # the refused value is not quoted back
