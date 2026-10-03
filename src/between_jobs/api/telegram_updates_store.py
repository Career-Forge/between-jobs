"""The one place that knows `claim_telegram_update` and its two companions exist (launch
plan P0.9).

Telegram redelivers an update it did not get a 2xx for, and a webhook that runs for a minute
(generating a resume) can make it give up waiting and send the same update again while the
first is still running. The webhook claims each `update_id` before processing it, so the
second delivery is recognised instead of creating a second job, application and paid
generation: answered with a quick 200 if the first finished, and with a non-2xx if the first
is still running (so that Telegram keeps trying if that first delivery's process died).

Dedup is a safety net, never a gate: if the claim cannot be made (the function is missing
because the migration has not been applied, a timeout, an answer that is not a boolean) the
update is processed as it always was. Dropping a user's message because the bookkeeping
failed would be worse than the duplicate this exists to prevent.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from supabase import AsyncClient

logger = logging.getLogger(__name__)


ClaimState = Literal["claimed", "done", "in_progress"]
_STATES = ("claimed", "done", "in_progress")


async def claim_update(
    supabase: AsyncClient, update_id: int, *, lease_seconds: int
) -> ClaimState | None:
    """'claimed': this delivery owns the update -- process it, then `complete_update` (or, on
    failure, `release_update`). 'done': an earlier delivery finished it. 'in_progress': an
    earlier delivery claimed it and has not finished within its lease. None: could not tell --
    process it, and do not complete or release anything.

    The webhook answers 'done' with a 200 and 'in_progress' with a non-2xx, and the difference
    matters: see `claim_telegram_update`."""
    try:
        result = await supabase.rpc(
            "claim_telegram_update", {"p_update_id": update_id, "p_lease_seconds": lease_seconds}
        ).execute()
    except Exception:
        logger.warning("could not claim a telegram update; processing without dedup", exc_info=True)
        return None
    answer = result.data
    if isinstance(answer, str) and answer in _STATES:
        return cast(ClaimState, answer)
    logger.warning("claim_telegram_update gave an unrecognised answer; processing it anyway")
    return None


async def complete_update(supabase: AsyncClient, update_id: int) -> None:
    """Records the update as done, so a later redelivery is dropped. Best effort: if this
    fails the claim simply expires and a redelivery would be processed again."""
    try:
        await supabase.rpc("complete_telegram_update", {"p_update_id": update_id}).execute()
    except Exception:
        logger.warning("could not mark a telegram update complete", exc_info=True)


async def release_update(supabase: AsyncClient, update_id: int) -> None:
    """Gives up an unfinished claim so Telegram's retry of a failed update is processed
    rather than dropped as a duplicate. Best effort: if this fails the retry waits out the
    claim's lease."""
    try:
        await supabase.rpc("release_telegram_update", {"p_update_id": update_id}).execute()
    except Exception:
        logger.warning("could not release a telegram update claim", exc_info=True)
