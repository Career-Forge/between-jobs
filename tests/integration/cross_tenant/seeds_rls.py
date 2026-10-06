"""Seeders for tables no route case happens to fill, so the row-level-security suite has a real
row of each user's own to guard (see `test_local_rls_isolation.py`). Routes never need these;
they exist so that "B cannot read A's row" is never true merely because A has no row."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .harness import Ctx, Tenant, seeder


@seeder("rls_capability_preference", tables=("capability_preferences",))
async def seed_capability_preference(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    model = ctx.mark(tenant, ctx.tag("model"))
    row = (
        await ctx.sb.table("capability_preferences")
        .insert(
            {
                "user_id": tenant.user_id,
                "capability": ctx.tag("cap"),
                "execution_mode": "byok",
                "provider": "openrouter",
                "model": model,
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "model": model}


@seeder("rls_session", tables=("sessions",))
async def seed_session(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    note = ctx.mark(tenant, ctx.tag("session"))
    row = (
        await ctx.sb.table("sessions")
        .insert({"user_id": tenant.user_id, "context": {"note": note}})
        .execute()
    ).data[0]
    return {"id": row["id"], "note": note}


@seeder("rls_working_set", tables=("working_sets",))
async def seed_working_set(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    note = ctx.mark(tenant, ctx.tag("working-set"))
    row = (
        await ctx.sb.table("working_sets")
        .insert(
            {
                "user_id": tenant.user_id,
                "kind": "search_results",
                "source_channel": "web",
                "items": [{"note": note}],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "note": note}


@seeder("rls_channel_identity", tables=("channel_identities",))
async def seed_channel_identity(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    subject = f"9{uuid.uuid4().int % 10**11:011d}"
    await ctx.world.identity(tenant.user_id, "telegram", subject)
    return {"subject": subject}


@seeder("rls_link_code", tables=("link_codes",))
async def seed_link_code(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    return {"code": await ctx.world.mint(tenant.user_id)}


@seeder("rls_rate_limit_counter", tables=("api_rate_limits",))
async def seed_rate_limit_counter(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A counter row, made by the real function the way the backend makes one. It is
    service-role-only (no policy at all), so no route reads it; the account-deletion drill fills
    every seeder, which is what makes "deleting the account leaves no counter behind" a checked
    claim."""
    bucket = ctx.tag("rl")
    await ctx.sb.rpc(
        "claim_rate_limit_slot",
        {
            "p_user_id": tenant.user_id,
            "p_bucket": bucket,
            "p_window_seconds": 3600,
            "p_max_requests": 10,
        },
    ).execute()
    return {"bucket": bucket}


@seeder("rls_tester_enrollment", tables=("tester_enrollments",))
async def seed_tester_enrollment(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """An enrollment row, written with the service role the way the API writes one. The tester
    can read it (the one policy on the table) and nothing else, which the RLS suite checks. The
    optional sponsorship answer is filled in, so the account-deletion drill has the sensitive
    column to prove gone."""
    version = ctx.mark(tenant, ctx.tag("consent"))
    row = (
        await ctx.sb.table("tester_enrollments")
        .insert(
            {
                "user_id": tenant.user_id,
                "role_cohort": "data_analyst",
                "seniority": "mid",
                "needs_sponsorship": True,
                "consent_version": version,
                "consented_at": datetime.now(UTC).isoformat(),
            }
        )
        .execute()
    ).data[0]
    return {"user_id": row["user_id"], "consent_version": version}


@seeder("rls_product_event", tables=("product_events",))
async def seed_product_event(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A product event that names one of the tenant's applications, so the account-deletion drill
    covers both foreign keys on the table (the user's cascade and the application's set-null).
    The table is service-role-only (no policy at all): no route and no user token reads it."""
    app = await ctx.need("application", tenant)
    row = (
        await ctx.sb.table("product_events")
        .insert(
            {
                "user_id": tenant.user_id,
                "event": "prepare_finished",
                "application_id": app["id"],
                "ats_type": "lever",
                "outcome": "ok",
                "n_a": 0,
                "duration_ms": 1234,
            }
        )
        .execute()
    ).data[0]
    return {"id": row["id"], "application_id": app["id"]}
