"""Persistence for events warm-path runs (outreach-contactfinder.md
Phase D) -- per-user, immutable-run shape, same precedent as
`company_intel_store.py`/`contact_research_store.py`.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient

from .warm_path_events import WarmPathEvent


async def create_run(
    supabase: AsyncClient,
    user_id: str,
    *,
    application_id: str,
    company_name: str,
    events: list[WarmPathEvent],
    providers_used: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    run_result = (
        await supabase.table("warm_path_runs")
        .insert(
            {
                "user_id": user_id,
                "application_id": application_id,
                "company_name": company_name,
                "providers_used": providers_used,
                "warnings": warnings,
            }
        )
        .execute()
    )
    run = cast(dict[str, Any], run_result.data[0])

    if events:
        await (
            supabase.table("warm_path_events")
            .insert(
                [
                    {
                        "run_id": run["id"],
                        "event_name": e["event_name"],
                        "event_url": e["event_url"],
                        "event_date": e["event_date"],
                        "location": e["location"],
                        "certainty": e["certainty"],
                        "speaker_name": e["speaker_name"],
                        "speaker_title": e["speaker_title"],
                        "talk_topic": e["talk_topic"],
                        "source_title": e["source_title"],
                        "source_snippet": e["source_snippet"],
                    }
                    for e in events
                ]
            )
            .execute()
        )

    return run


async def get_latest_run(
    supabase: AsyncClient, user_id: str, application_id: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table("warm_path_runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast(dict[str, Any], result.data[0])


async def get_events_for_run(supabase: AsyncClient, run_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table("warm_path_events")
        .select("*")
        .eq("run_id", run_id)
        .order("certainty")
        .execute()
    )
    return cast(list[dict[str, Any]], result.data)
