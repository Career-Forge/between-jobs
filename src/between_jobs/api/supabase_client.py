"""Supabase client wiring.

Uses the service-role key -- this backend is the only thing that talks to
Postgres directly (master plan §3.2: channels hit the FastAPI orchestrator,
never Supabase directly), so it's a trusted server, not a public client.
RLS (verified live against the `sessions`/`telegram_links` schema before
this code was written) is defense-in-depth for other access paths, e.g. a
future browser-side Supabase Realtime subscription -- it isn't what's
gating this backend's own reads/writes.

The service-role key deliberately never flows through any AI tool call --
Supabase's own management API doesn't expose it programmatically. Set it
yourself in `.env` from the project dashboard (Settings -> API).
"""

from __future__ import annotations

import os

from supabase import AsyncClient, acreate_client


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"missing required env var {name}")
    return value


async def create_supabase_client() -> AsyncClient:
    url = _require_env("SUPABASE_URL")
    key = _require_env("SUPABASE_SERVICE_ROLE_KEY")
    return await acreate_client(url, key)
