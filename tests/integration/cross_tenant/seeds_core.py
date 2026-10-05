"""Seeders nearly every case builds on: a profile version, a job, an application.

Every user-visible string a seeder writes goes through `ctx.mark`, so the suite can look for
it in a stranger's responses."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .harness import Ctx, Tenant, seeder


@seeder("profile_version", tables=("profile_versions",))
async def seed_profile_version(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    name = ctx.mark(tenant, ctx.tag("Pat"))
    headline = ctx.mark(tenant, ctx.tag("Headline"))
    row = await ctx.world.profile_version(
        tenant.user_id, canonical={"name": name, "headline": headline}
    )
    return {"id": row["id"], "name": name, "headline": headline, "row": row}


@seeder("career_fact", tables=("career_facts",))
async def seed_career_fact(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    version = await ctx.need("profile_version", tenant)
    value = ctx.mark(tenant, ctx.tag("Skill"))
    row = await ctx.world.fact(
        tenant.user_id, version["id"], pointer=f"/skills/{ctx.tag('s')}", value=value
    )
    return {"id": row["id"], "value": value, "profile_version_id": version["id"], "row": row}


@seeder("job", tables=("jobs", "job_snapshots"))
async def seed_job(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    """A job and its snapshot. `jobs` and `job_snapshots` are shared, not owned by anyone; what
    is private is the application that points at them."""
    company = ctx.mark(tenant, ctx.tag("Co"))
    title = ctx.mark(tenant, ctx.tag("Title"))
    description = ctx.mark(tenant, ctx.tag("Description"))
    url = f"https://example.com/{ctx.tag('job')}"
    job = (
        await ctx.sb.table("jobs").insert({"company_name": company, "canonical_url": url}).execute()
    ).data[0]
    ctx.world.job_ids.append(job["id"])
    snapshot = (
        await ctx.sb.table("job_snapshots")
        .insert(
            {
                "job_id": job["id"],
                "source_url": url,
                "title": title,
                "company_name": company,
                "description_text": description,
                "structured_json": {},
                "content_hash": ctx.tag("snap"),
                "fetched_at": datetime.now(UTC).isoformat(),
                "source_kind": "manual",
            }
        )
        .execute()
    ).data[0]
    return {
        "job_id": job["id"],
        "snapshot_id": snapshot["id"],
        "company": company,
        "title": title,
        "description": description,
        "url": url,
    }


@seeder("application", tables=("applications",))
async def seed_application(ctx: Ctx, tenant: Tenant) -> dict[str, Any]:
    job = await ctx.need("job", tenant)
    row = await ctx.world.application(tenant.user_id, (job["job_id"], job["snapshot_id"]))
    return {"id": row["id"], "job": job, "row": row}
