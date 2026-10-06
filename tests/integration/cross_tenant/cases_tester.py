"""Cross-tenant cases for the tester-programme routes.

None of the three takes an id: each acts on the caller's own enrollment row, whose key is the
caller. So the question is not "does a stranger's id 404" but "does what B does reach A's row,
and does B's answer show anything of A's". The row is seeded for A with a marked consent version
(`rls_tester_enrollment`, which also resets it, withdrawal included, every time it is seeded: a
tenant keeps its user across the cases of a module and these routes change the row).

B joins and withdraws with a 200, because B's own row is what they act on: the proof is that A's
row is exactly as it was afterwards, and that nothing of A's is in B's answer."""

from __future__ import annotations

from between_jobs.api.tester_enrollment import TESTER_AGREEMENT_VERSION

from .harness import Case, Ctx, Req

_SEEDED = {"role_cohort": "data_analyst", "seniority": "mid", "needs_sponsorship": True}


async def _a_row_is_as_seeded(ctx: Ctx) -> None:
    seeded = await ctx.need("rls_tester_enrollment", ctx.a)
    rows = (
        await ctx.sb.table("tester_enrollments").select("*").eq("user_id", ctx.a.user_id).execute()
    ).data
    assert len(rows) == 1
    row = rows[0]
    for column, value in _SEEDED.items():
        assert row[column] == value, f"B's request changed A's {column}"
    assert row["consent_version"] == seeded["consent_version"], "B's request changed A's consent"
    assert row["withdrawn_at"] is None, "B's request withdrew A"


async def _read(ctx: Ctx) -> Req:
    await ctx.need("rls_tester_enrollment", ctx.a)
    return Req("GET", "/tester/enrollment")


async def _join(ctx: Ctx) -> Req:
    await ctx.need("rls_tester_enrollment", ctx.a)
    return Req(
        "POST",
        "/tester/enrollment",
        json={
            "role_cohort": "software_engineer",
            "seniority": "senior",
            "needs_sponsorship": False,
            "accept_version": TESTER_AGREEMENT_VERSION,
        },
    )


async def _withdraw(ctx: Ctx) -> Req:
    await ctx.need("rls_tester_enrollment", ctx.a)
    return Req("POST", "/tester/enrollment/withdraw")


CASES = [
    # B reads B's own enrollment (seeded for B, so "none of A's" is a real test): the answer
    # carries B's consent version and not A's.
    Case(
        "GET /tester/enrollment",
        _read,
        kind="list",
        foreign_status=200,
        b_seeds=("rls_tester_enrollment",),
        note="a list of one: the caller's own row",
    ),
    # B joins: B gets a 200 for their own row, and A's row is untouched.
    Case(
        "POST /tester/enrollment",
        _join,
        foreign_status=200,
        unchanged=_a_row_is_as_seeded,
        note="joins as B, never rewrites A's row",
    ),
    # B withdraws their own seeded row; A is still enrolled.
    Case(
        "POST /tester/enrollment/withdraw",
        _withdraw,
        foreign_status=200,
        b_seeds=("rls_tester_enrollment",),
        unchanged=_a_row_is_as_seeded,
        note="withdraws B, never A",
    ),
]
