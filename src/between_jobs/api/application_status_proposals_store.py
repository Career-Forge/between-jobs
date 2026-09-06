"""Persistence for `application_status_proposals` (Gmail reply/status
parsing R3, gmail-reply-status-parsing.md). The row itself is written by
`gmail_reply_checker.py`; this module currently exists only for the one
read `digest_listener.py` needs -- confirming a published proposal still
exists before turning it into a Today item. R4 (frontend Accept/Dismiss)
is the next real caller and will extend this file with the write side
(resolving a proposal's `status`), not create a parallel module.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


class StatusProposalNotFound(Exception):
    """A proposal id doesn't exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


async def get_status_proposal(
    supabase: AsyncClient, user_id: str, proposal_id: str
) -> dict[str, Any]:
    """Lets `digest_listener.py` confirm a `gmail_reply.status_proposed.v1`
    event's own proposal still exists before inserting its Today item --
    the same "referenced entity already gone -> skip gracefully" check
    `get_application`/`get_saved_search` already give every other event
    type (the application itself, and therefore this proposal via its own
    `on delete cascade`, can be deleted between the poller publishing the
    event and the outbox worker processing it -- a real but rare race,
    not an error to surface, and not one to let corrupt an FK insert into
    a crash)."""
    result = (
        await supabase.table("application_status_proposals")
        .select("*")
        .eq("id", proposal_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data:
        raise StatusProposalNotFound(proposal_id)
    return cast(dict[str, Any], result.data[0])
