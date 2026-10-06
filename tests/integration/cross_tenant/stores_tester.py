"""Store-level stranger cases for the tester-programme enrollment functions.

The API reaches Postgres with the service role, so isolation here is the `user_id` the function
filters or writes with. Each case calls the function as B, then reads A's row back with the
service-role client to prove it did not move, and calls it as A once so an empty answer for B
cannot be an empty answer for everyone."""

from __future__ import annotations

from typing import Any

from between_jobs.api import tester_enrollment

from .harness import Ctx, StoreCase


async def _row(ctx: Ctx, user_id: str) -> dict[str, Any] | None:
    rows = (
        await ctx.sb.table("tester_enrollments").select("*").eq("user_id", user_id).execute()
    ).data
    return dict(rows[0]) if rows else None


async def get_enrollment_row(ctx: Ctx) -> None:
    a = await ctx.need("rls_tester_enrollment", ctx.a)
    b = await ctx.need("rls_tester_enrollment", ctx.b)

    own_b = await tester_enrollment.get_enrollment_row(ctx.sb, ctx.b.user_id)
    own_a = await tester_enrollment.get_enrollment_row(ctx.sb, ctx.a.user_id)

    # Each tenant reads their own row and only that one: the marked consent versions differ.
    assert own_b is not None and own_b["consent_version"] == b["consent_version"]
    assert own_a is not None and own_a["consent_version"] == a["consent_version"]
    assert own_a["consent_version"] != own_b["consent_version"]


async def withdraw(ctx: Ctx) -> None:
    await ctx.need("rls_tester_enrollment", ctx.a)
    await ctx.need("rls_tester_enrollment", ctx.b)

    withdrawn_b = await tester_enrollment.withdraw(ctx.sb, ctx.b.user_id)

    assert withdrawn_b["withdrawn_at"] is not None
    a_after = await _row(ctx, ctx.a.user_id)
    assert a_after is not None and a_after["withdrawn_at"] is None, "B's withdrawal reached A"
    # the control: the same call as A withdraws A
    withdrawn_a = await tester_enrollment.withdraw(ctx.sb, ctx.a.user_id)
    assert withdrawn_a["withdrawn_at"] is not None


async def enroll(ctx: Ctx) -> None:
    a = await ctx.need("rls_tester_enrollment", ctx.a)

    await tester_enrollment.enroll(
        ctx.sb,
        ctx.b.user_id,
        role_cohort="software_engineer",
        seniority="senior",
        needs_sponsorship=False,
    )

    a_after = await _row(ctx, ctx.a.user_id)
    assert a_after is not None
    assert a_after["role_cohort"] == "data_analyst" and a_after["seniority"] == "mid"
    assert a_after["needs_sponsorship"] is True
    assert a_after["consent_version"] == a["consent_version"], "B's enrollment rewrote A's row"
    b_after = await _row(ctx, ctx.b.user_id)
    assert b_after is not None and b_after["role_cohort"] == "software_engineer"
    # the control: the same call as A rewrites A's row
    await tester_enrollment.enroll(
        ctx.sb,
        ctx.a.user_id,
        role_cohort="qa_sdet",
        seniority="lead_plus",
        needs_sponsorship=None,
    )
    a_own = await _row(ctx, ctx.a.user_id)
    assert a_own is not None and a_own["role_cohort"] == "qa_sdet"


STORE_CASES = [
    StoreCase("tester_enrollment.get_enrollment_row", get_enrollment_row),
    StoreCase("tester_enrollment.withdraw", withdraw),
    StoreCase("tester_enrollment.enroll", enroll),
]
