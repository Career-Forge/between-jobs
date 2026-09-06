"""Persistence for `application_status_proposals` (Gmail reply/status
parsing). R3 (`gmail_reply_checker.py`) writes the row and reads it back
only to auto-apply or publish it; R4 (this module's frontend surface,
`today_routes.py`) is the real reason a human ever resolves one directly
-- Accept applies the proposed stage change the same way the poller's own
auto-apply does, Dismiss just records the decision, and both retire the
Today item that surfaced it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from supabase import AsyncClient

STAGE_MAP: dict[str, str] = {
    "assessment.received": "screening",
    "interview.requested": "interviewing",
    "interview.scheduled": "interviewing",
    "application.rejected": "rejected",
    "offer.received": "offer",
}
"""Only 4 of the classifier's 8 proposed types correspond to an actual
NEW Kanban stage (K1's real, enforced vocabulary: saved/applied/
screening/interviewing/offer/rejected/withdrawn). `application.
acknowledged` and `recruiter.replied` are real signals but don't move the
pipeline forward to any specific new stage -- there's nothing for
"Accept" to apply for those two (or for `unknown`), so callers use this
map's absence of a key as the signal that only "Dismiss" makes sense.
Shared between `gmail_reply_checker.py`'s own auto-apply path and
`today_routes.py`'s human Accept action -- the same mapping either way,
whether a machine or a person decided the confidence was high enough."""


class StatusProposalNotFound(Exception):
    """A proposal id (or the today_item it's linked from) doesn't exist,
    belongs to another user, or (an adversarial review's own addition)
    is no longer `status='pending'` by the time a write tries to resolve
    it -- deliberately indistinguishable from the caller's side. Route
    handlers give the common case (a stale click after the proposal was
    already resolved) its own clearer CONFLICT error before ever reaching
    this exception; this one is the rare-race backstop underneath it."""


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


async def get_status_proposal_for_today_item(
    supabase: AsyncClient, user_id: str, today_item_id: str
) -> dict[str, Any]:
    """The reverse lookup `today_routes.py`'s Accept/Dismiss actions need
    -- a user only ever has the Today item's own id client-side, not the
    proposal's. Two queries (the thin link table, then the real proposal
    row), matching this codebase's own no-embedded-join convention rather
    than a single hand-rolled join."""
    link_result = (
        await supabase.table("today_item_status_proposals")
        .select("*")
        .eq("today_item_id", today_item_id)
        .execute()
    )
    if not link_result.data:
        raise StatusProposalNotFound(today_item_id)
    link_row = cast(dict[str, Any], link_result.data[0])
    return await get_status_proposal(
        supabase, user_id, cast(str, link_row["application_status_proposal_id"])
    )


async def resolve_status_proposal(
    supabase: AsyncClient, user_id: str, proposal_id: str, *, status: str
) -> dict[str, Any]:
    """The write half of Accept/Dismiss -- records the human's decision.
    `status` is `'accepted'`/`'dismissed'` (never `'pending'`, the only
    other CHECK-allowed value, which nothing should ever resolve TO).

    The update is scoped to `status='pending'` -- an adversarial review
    found the original unconditional update let a proposal already
    resolved one way be silently flipped the other way (including
    re-triggering a real Kanban stage change after a human had already
    dismissed it, or erasing a recorded acceptance), with no signal to
    either caller that anything unusual happened. Matching zero rows now
    means "not pending anymore," raised as the same `StatusProposalNot
    Found` the caller already handles -- callers that want a clearer,
    non-404 message for the common case (a stale click on an already-
    resolved item) check `proposal["status"]` themselves before calling
    this, same as `today_routes.py` does."""
    result = (
        await supabase.table("application_status_proposals")
        .update({"status": status, "resolved_at": datetime.now(UTC).isoformat()})
        .eq("id", proposal_id)
        .eq("user_id", user_id)
        .eq("status", "pending")
        .execute()
    )
    if not result.data:
        raise StatusProposalNotFound(proposal_id)
    return cast(dict[str, Any], result.data[0])


async def dismiss_other_pending_proposals(
    supabase: AsyncClient, user_id: str, application_id: str, *, except_proposal_id: str
) -> None:
    """Once one proposal for an application is accepted (by a human here,
    or by `gmail_reply_checker.py`'s own auto-apply), any OTHER still-
    `'pending'` proposal for the SAME application is superseded -- the
    Kanban stage has already moved based on the most recently acted-on
    signal. An adversarial review found that leaving an older sibling
    proposal sitting `'pending'` was a real, if narrow, risk: `change_
    application_stage` has no compare-and-swap against the application's
    current status (K1's own deliberate "membership-only, not a gated
    state machine" design, applied consistently everywhere else in this
    codebase too), so accepting that older proposal later could silently
    regress a stage that had already moved forward. Auto-dismissing its
    siblings here closes that without adding a new, inconsistent
    exception to `change_stage`'s own established contract."""
    await (
        supabase.table("application_status_proposals")
        .update({"status": "dismissed", "resolved_at": datetime.now(UTC).isoformat()})
        .eq("application_id", application_id)
        .eq("user_id", user_id)
        .eq("status", "pending")
        .neq("id", except_proposal_id)
        .execute()
    )
