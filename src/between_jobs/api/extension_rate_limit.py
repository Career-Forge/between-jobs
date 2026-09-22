"""Per-user rate limiting for `POST /extension/draft-answer` (E6
continuation).

Of the four `/extension/*` routes, this is the only one that spends real
BYOK LLM credit on every call, and the only one with nothing else in
front of it bounding how often it can be called -- the D6 gate and
`is_generation_eligible` (application_answer_generator.py) decide WHETHER
a given question is drafted, never how many draft calls a caller may make
per minute. There is no rate-limiting infrastructure anywhere else in
this backend today, so this is deliberately minimal: one small table, one
atomic Postgres function, no new background worker or dependency.

Threshold reasoning (`_WINDOW_SECONDS` / `_MAX_REQUESTS` below): a person
filling out one real application legitimately drafts several screening
questions within a few minutes, often redrafting the same one after
editing their profile or noticing the label changed. 20 calls per 10
minutes covers that with real headroom (a genuinely unusual number of
redrafts on one sitting) while still bounding a scripted loop -- which
would otherwise have no limit at all -- to a low, fixed multiple of
normal single-session use. Generous on purpose: the goal is bounding
abuse, not policing a careful reader.

The atomic check lives in Postgres (`claim_extension_draft_answer_slot`,
its own migration), not read-then-write in Python, for the same reason
`consume_link_code`'s own rate-limit bookkeeping does: two concurrent
draft-answer calls from the same user must never both read a stale
under-limit count and both be let through. A plain in-memory counter was
considered and rejected -- this backend can run as more than one worker
process, and an in-memory counter is only ever correct within one.
"""

from __future__ import annotations

from supabase import AsyncClient

DRAFT_ANSWER_RATE_LIMIT_WINDOW_SECONDS = 600
DRAFT_ANSWER_RATE_LIMIT_MAX_REQUESTS = 20

_RPC_NAME = "claim_extension_draft_answer_slot"


async def claim_draft_answer_slot(supabase: AsyncClient, user_id: str) -> bool:
    """Atomically claims one of this user's draft-answer calls for the
    current window. Returns False when the caller is already over the
    limit for this window; never raises for that ordinary, expected
    outcome -- the route decides how to surface it (a retryable
    PROVIDER_RATE_LIMITED, not a 5xx)."""
    result = await supabase.rpc(
        _RPC_NAME,
        {
            "p_user_id": user_id,
            "p_window_seconds": DRAFT_ANSWER_RATE_LIMIT_WINDOW_SECONDS,
            "p_max_requests": DRAFT_ANSWER_RATE_LIMIT_MAX_REQUESTS,
        },
    ).execute()
    return bool(result.data)
