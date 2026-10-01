"""The one place that knows `claim_worker_lease` (launch plan P2.19) exists.

Kept apart from `worker_lease.py` so that module -- the keeper and its timing
rules -- imports no Supabase client and can be tested with a plain coroutine.
"""

from __future__ import annotations

from supabase import AsyncClient

_RPC_NAME = "claim_worker_lease"


class LeaseAnswerError(Exception):
    """The function answered, but not with a boolean. Treated as "could not
    ask", never as a refusal: guessing `False` from a malformed answer would put
    a process on standby that no one else is covering."""


async def claim_worker_lease(
    supabase: AsyncClient, worker: str, holder: str, ttl_seconds: int
) -> bool:
    """Takes or renews `worker`'s lease for `holder` for `ttl_seconds`.

    True: `holder` has it. False: someone else does -- a definite answer, not an
    error. Anything that is not exactly one of those (the function missing, a
    timeout, a transport failure, a result that is neither true nor false)
    raises, because "I could not ask" and "I was told no" must stay distinct: the
    first fails closed and is unhealthy, the second is a healthy standby.
    """
    result = await supabase.rpc(
        _RPC_NAME,
        {"p_worker": worker, "p_holder": holder, "p_ttl_seconds": ttl_seconds},
    ).execute()
    # `is`, not truthiness: bool(None) and bool([]) would both read as "no".
    if result.data is True:
        return True
    if result.data is False:
        return False
    raise LeaseAnswerError(f"{_RPC_NAME} returned neither true nor false")
