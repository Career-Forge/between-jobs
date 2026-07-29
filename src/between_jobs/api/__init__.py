"""The FastAPI orchestrator -- "the brain" (master plan §3.1).

Channels (Telegram, web) are dumb renderers that hit this same app; nothing
in here is channel-specific. Sprint 2.1 (master plan Phase 2) is the bare
spine only: no agent runtime, no event bus, no Telegram bridge yet -- just
the Supabase-backed session store those pieces will attach to.
"""
