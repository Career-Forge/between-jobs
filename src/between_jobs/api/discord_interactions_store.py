"""The one place that knows `claim_discord_interaction` and its two companions exist: the
Discord counterpart of `telegram_updates_store`.

Discord does not retry an interaction the way Telegram redelivers an update, but a signed request
can still arrive twice: a network layer that repeats a request, or someone replaying a request they
captured. A repeat must not create a second job, application or paid resume generation, so the
webhook claims each interaction's id (the snowflake Discord gives it, kept as text) before it does
any work.

Dedup is a safety net, never a gate, as it is for Telegram: if the claim cannot be made (the
function is missing because the migration has not been applied, a timeout, an answer that is not
one of the three) the interaction is processed as it would have been. Dropping a person's command
because the bookkeeping failed is worse than the repeat this exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from supabase import AsyncClient

logger = logging.getLogger(__name__)

ClaimState = Literal["claimed", "done", "in_progress"]
_STATES = ("claimed", "done", "in_progress")


async def claim_interaction(
    supabase: AsyncClient, interaction_id: str, *, lease_seconds: int
) -> ClaimState | None:
    """'claimed': this delivery owns the interaction -- process it, then `complete_interaction`
    (or, if it was never started, `release_interaction`). 'done': an earlier delivery finished
    it. 'in_progress': an earlier delivery claimed it and has not finished within its lease.
    None: could not tell -- process it, and do not complete or release anything."""
    try:
        result = await supabase.rpc(
            "claim_discord_interaction",
            {"p_interaction_id": interaction_id, "p_lease_seconds": lease_seconds},
        ).execute()
    except Exception:
        logger.warning(
            "could not claim a discord interaction; processing without dedup", exc_info=True
        )
        return None
    answer = result.data
    if isinstance(answer, str) and answer in _STATES:
        return cast(ClaimState, answer)
    logger.warning("claim_discord_interaction gave an unrecognised answer; processing it anyway")
    return None


async def complete_interaction(supabase: AsyncClient, interaction_id: str) -> None:
    """Records the interaction as done, so a later repeat is dropped. Best effort: if this fails
    the claim simply expires and a repeat would be processed again."""
    try:
        await supabase.rpc(
            "complete_discord_interaction", {"p_interaction_id": interaction_id}
        ).execute()
    except Exception:
        logger.warning("could not mark a discord interaction complete", exc_info=True)


async def release_interaction(supabase: AsyncClient, interaction_id: str) -> None:
    """Gives up an unfinished claim so the interaction can be claimed again. Best effort."""
    try:
        await supabase.rpc(
            "release_discord_interaction", {"p_interaction_id": interaction_id}
        ).execute()
    except Exception:
        logger.warning("could not release a discord interaction claim", exc_info=True)
