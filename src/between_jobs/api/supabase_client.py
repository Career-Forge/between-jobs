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

from supabase import AsyncClient, acreate_client

from .env import require_env


async def create_supabase_client() -> tuple[AsyncClient, str]:
    """Returns the client and the project URL -- callers that also need
    the URL (e.g. to build a JWKS client for auth.py) shouldn't have to
    re-read and re-validate the env var themselves."""
    url = require_env("SUPABASE_URL")
    key = require_env("SUPABASE_SERVICE_ROLE_KEY")
    client = await acreate_client(url, key)
    return client, url
