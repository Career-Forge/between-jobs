"""Gmail reply/status parsing R3 (gmail-reply-status-parsing.md) -- the
poller that finally wires R1 (Gmail scope + `get_thread`) and R2 (the
classifier) into something that runs. Same worker shape as
`job_registry_poller.py`/`saved_search_matcher.py`: `run_reply_check_once`/
`run_reply_check_forever`, its own dedicated Supabase client (wired in
app.py), gated by `DISABLE_GMAIL_REPLY_CHECKER`.

One poll per due draft does BOTH jobs R1's own research identified as one
call: `threads.get(thread_id, format="full")` returns every message's
labels/timestamps/bodies in one shot. The EARLIEST `SENT`-labeled message
confirms the draft was actually sent (there is no dedicated "was this
sent" signal anywhere in the Gmail API) -- earliest, not most recent: an
adversarial review found anchoring to the most recent one silently lost a
real reply forever on a thread where the user later sent a second message
from within Gmail directly (not through this platform), since any INBOX
reply that arrived BETWEEN the two SENT messages would fall below a
too-late watermark and never be reconsidered, as the watermark only moves
forward. Once confirmed, that send timestamp is never recomputed from the
thread on a later tick -- the stored `sent_confirmed_at` column is the
one, permanent source of truth. Any `INBOX`-labeled message newer than
that -- and newer than this draft's own watermark, so an already-
classified reply is never reclassified on a later tick -- is a real
reply, fed to `classify_reply`. Only the single newest such message is
classified per tick, not every one that arrived since the last check: one
classification attempt per poll, matching `classify_reply`'s own "no
retry" cost discipline. A user offline long enough to receive two
genuinely separate replies in one gap would only have the more recent one
classified -- a disclosed v1 simplification, not a bug.

A proposal at or above `AUTO_TRACK_THRESHOLD` auto-applies via the
EXISTING `applications_store.change_stage` (Proposal §28.7's own
`accept_proposal(actor="email_monitor")`) -- but only for the 4 proposed
types that actually correspond to a real Kanban stage
(`assessment.received`, `interview.requested`, `interview.scheduled`,
`application.rejected`, `offer.received`). `application.acknowledged` and
`recruiter.replied` are real, evidenced signals worth surfacing but don't
correspond to any NEW stage to move to -- these, like every below-
threshold proposal AND every proposal whose auto-apply attempt itself
failed for any reason, are recorded as `status='pending'` and published
to `event_outbox` for a human to review in the Today feed, never silently
dropped and never forced into a stage that doesn't fit. Falling through
to the review queue on an auto-apply FAILURE (rather than just returning)
means a proposal is never permanently, invisibly stranded -- an
adversarial review found the original "just return" behavior could
orphan a proposal forever with no path to ever surface it. The one
disclosed, accepted trade-off: if `change_stage` itself succeeds but the
follow-up write marking this proposal `status='accepted'` fails
immediately after, both the real stage change AND a redundant pending-
review Today item exist -- confusing but not corrupting, and rare enough
(two back-to-back Supabase calls, the first already having succeeded)
that building a fully atomic RPC for it isn't worth the machinery.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .application_status_classifier import AUTO_TRACK_THRESHOLD, StatusProposal, classify_reply
from .applications_store import change_stage
from .credential_resolver import resolve
from .errors import ApiError
from .gmail_client import (
    GmailMessage,
    GmailOauthConfig,
    get_thread,
    refresh_access_token,
    require_gmail_oauth_config,
)
from .llm_client import generate as llm_generate
from .provider_credentials_store import CredentialNotFound, get_decrypted_credential

_UNIQUE_VIOLATION = "23505"

_DEFAULT_CHECK_INTERVAL_SECONDS = 900.0
"""15 minutes, matching `job_registry_poller`'s own freshness-oriented
cadence rather than `saved_search_matcher`'s 6-hour cost-bound one --
checking a thread is cheap Gmail quota (40 units/call against a 6,000/
min/user cap), not an LLM call every tick. The real LLM cost is already
bounded by how often a genuinely NEW reply shows up, not by polling
frequency."""

_STALENESS_SECONDS = _DEFAULT_CHECK_INTERVAL_SECONDS
"""A draft is "due" once this much time has passed since it was last
checked -- deliberately kept equal to the default poll interval so
`run_reply_check_once` needs no separate interval parameter threaded
through it."""

_MAX_DRAFTS_PER_TICK = 200
"""A disclosed per-tick budget, same precedent as Oracle's 600-postings/
tick and Google's 6-pages/tick ceilings elsewhere in this codebase --
cheap insurance against one tick doing unbounded work, not a real limit
at this project's actual scale."""

_MAX_CONCURRENT_CHECKS = 5
"""Different drafts share no state (each reads/writes only its own row),
so they don't need to run one at a time -- but each can trigger a real
Gmail API call and, when a new reply is found, a real LLM call, and the
number of due drafts is unbounded. Same reasoning and same bound as
`saved_search_matcher._MAX_CONCURRENT_SEARCHES`."""

_CLASSIFIER_CAPABILITY = "gmail_reply_check"
"""A new `credential_resolver` capability key -- no registry or enum
enforces these, per `resolve()`'s own docstring; falls back to the
"default" capability preference until a user configures this one
specifically, same as `saved_search_matcher`'s own "job_scoring"."""

_STAGE_MAP: dict[str, str] = {
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
pipeline forward to any specific new stage -- auto-apply is structurally
impossible for those two (and for `unknown`, whose confidence is always
0.0 and therefore never reaches `AUTO_TRACK_THRESHOLD` regardless of this
map), so they always go to the pending review queue instead, never
guessed into an ill-fitting stage."""


def _to_epoch_ms(value: str | None) -> int:
    if value is None:
        return 0
    return int(datetime.fromisoformat(value).timestamp() * 1000)


async def _select_due_drafts(supabase: AsyncClient) -> list[dict[str, Any]]:
    cutoff_iso = (datetime.now(UTC) - timedelta(seconds=_STALENESS_SECONDS)).isoformat()
    result = await supabase.rpc(
        "list_drafts_due_for_reply_check",
        {"staleness_cutoff": cutoff_iso, "result_limit": _MAX_DRAFTS_PER_TICK},
    ).execute()
    return cast(list[dict[str, Any]], result.data or [])


async def _mark_checked(supabase: AsyncClient, draft_id: str) -> None:
    await (
        supabase.table("outreach_drafts")
        .update({"last_reply_checked_at": datetime.now(UTC).isoformat()})
        .eq("id", draft_id)
        .execute()
    )


async def _mark_sent_confirmed(supabase: AsyncClient, draft_id: str, sent_at_ms: int) -> None:
    sent_at = datetime.fromtimestamp(sent_at_ms / 1000, tz=UTC).isoformat()
    await (
        supabase.table("outreach_drafts")
        .update({"sent_confirmed_at": sent_at})
        .eq("id", draft_id)
        .execute()
    )


async def _get_access_token(
    http: httpx.AsyncClient,
    oauth_config: GmailOauthConfig,
    credential: dict[str, Any],
    user_id: str,
    tasks: dict[str, asyncio.Task[str]],
) -> str:
    """Single-flights the refresh across every draft due for the same
    user in one tick. An adversarial review found the original plain
    dict-of-strings cache had a real check-then-await-then-set race: two
    concurrently-running bounded tasks for the same user_id (a normal
    case -- one user with two due drafts) would both observe a cache
    miss before either write happened, defeating the "one refresh per
    user per tick" design and doubling real calls to Google's token
    endpoint. Storing the in-flight `asyncio.Task` itself (not its
    eventual result) closes this: the check, task creation, and cache
    write below have no `await` between them, so they're atomic from the
    event loop's perspective -- whichever caller reaches this first wins,
    and every other caller for the same user just awaits that same task."""
    task = tasks.get(user_id)
    if task is None:
        task = asyncio.ensure_future(
            refresh_access_token(
                http,
                refresh_token=credential["secret"],
                client_id=oauth_config["client_id"],
                client_secret=oauth_config["client_secret"],
            )
        )
        tasks[user_id] = task
    return await task


async def _record_proposal(
    supabase: AsyncClient,
    *,
    user_id: str,
    application_id: str,
    outreach_draft_id: str,
    source_gmail_message_id: str,
    proposal: StatusProposal,
) -> None:
    try:
        result = await (
            supabase.table("application_status_proposals")
            .insert(
                {
                    "user_id": user_id,
                    "application_id": application_id,
                    "outreach_draft_id": outreach_draft_id,
                    "proposed_type": proposal["proposed_type"],
                    "confidence": proposal["confidence"],
                    "evidence_spans": proposal["evidence_spans"],
                    "source_gmail_message_id": source_gmail_message_id,
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code == _UNIQUE_VIOLATION:
            return  # already proposed for this exact reply -- re-polled, not new
        raise

    proposal_row = cast(dict[str, Any], result.data[0])
    new_status = _STAGE_MAP.get(proposal["proposed_type"])
    auto_applied = False

    if new_status is not None and proposal["confidence"] >= AUTO_TRACK_THRESHOLD:
        idempotency_key = f"gmail_reply.status_proposal_accepted:{proposal_row['id']}"
        try:
            await change_stage(
                supabase,
                user_id,
                application_id,
                new_status=new_status,
                idempotency_key=idempotency_key,
                actor_type="email_monitor",
            )
            await (
                supabase.table("application_status_proposals")
                .update({"status": "accepted", "resolved_at": datetime.now(UTC).isoformat()})
                .eq("id", proposal_row["id"])
                .execute()
            )
            auto_applied = True
        except Exception:
            # The application this refers to may already be gone/invalid
            # (change_stage's own ApplicationNotFound/InvalidApplication
            # Status), or a transient Postgres/RPC failure hit either
            # call -- either way, fall through to the review queue rather
            # than silently stranding this proposal at 'pending' forever
            # with no event ever published (an adversarial review found
            # the original narrower except left exactly that gap).
            pass

    if not auto_applied:
        await _publish_pending_proposal_event(
            supabase,
            user_id=user_id,
            application_id=application_id,
            outreach_draft_id=outreach_draft_id,
            proposal_row=proposal_row,
        )


async def _publish_pending_proposal_event(
    supabase: AsyncClient,
    *,
    user_id: str,
    application_id: str,
    outreach_draft_id: str,
    proposal_row: dict[str, Any],
) -> None:
    idempotency_key = f"gmail_reply.status_proposal_pending:{proposal_row['id']}"
    try:
        await (
            supabase.table("event_outbox")
            .insert(
                {
                    "user_id": user_id,
                    "aggregate_type": "application_status_proposal",
                    "aggregate_id": proposal_row["id"],
                    "event_type": "gmail_reply.status_proposed.v1",
                    "event_version": 1,
                    "payload": {
                        "application_id": application_id,
                        "outreach_draft_id": outreach_draft_id,
                        "proposed_type": proposal_row["proposed_type"],
                        "confidence": proposal_row["confidence"],
                        "evidence_spans": proposal_row["evidence_spans"],
                    },
                    "idempotency_key": idempotency_key,
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise


async def _check_one_draft(
    http: httpx.AsyncClient,
    supabase: AsyncClient,
    draft: dict[str, Any],
    oauth_config: GmailOauthConfig,
    access_token_tasks: dict[str, asyncio.Task[str]],
) -> None:
    user_id = draft["user_id"]

    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service="oauth", provider="gmail"
        )
    except CredentialNotFound:
        return  # fail open -- no Gmail connection at all

    scope = credential.get("scope") or ""
    if "gmail.readonly" not in scope:
        return  # fail open -- stored credential predates the widened scope; needs reconnect

    try:
        access_token = await _get_access_token(
            http, oauth_config, credential, user_id, access_token_tasks
        )
    except ApiError:
        # Any Gmail-call failure here -- a revoked/expired refresh token
        # (PROVIDER_REJECTED) or a transient network blip talking to
        # Google (PROVIDER_UNAVAILABLE) -- must never crash the whole
        # tick: an adversarial review found the original code only
        # special-cased PROVIDER_REJECTED, letting PROVIDER_UNAVAILABLE
        # propagate out of asyncio.gather and permanently kill this
        # worker's forever-loop task for every user, not just this draft.
        await _mark_checked(supabase, draft["draft_id"])
        return

    try:
        thread = await get_thread(
            http, access_token=access_token, thread_id=draft["gmail_thread_id"]
        )
    except ApiError:
        await _mark_checked(supabase, draft["draft_id"])
        return

    sent_messages = [m for m in thread["messages"] if "SENT" in m["label_ids"]]
    if not sent_messages:
        await _mark_checked(supabase, draft["draft_id"])
        return  # never actually sent (or Gmail hasn't indexed it as SENT yet)

    if draft["sent_confirmed_at"] is None:
        # The EARLIEST SENT message, not the most recent -- see this
        # module's own docstring for why anchoring to the latest one is a
        # real, adversarially-confirmed bug. Computed only once, ever,
        # per draft: every later tick trusts the stored column instead.
        original_sent_message = min(sent_messages, key=lambda m: m["internal_date_ms"])
        await _mark_sent_confirmed(
            supabase, draft["draft_id"], original_sent_message["internal_date_ms"]
        )
        sent_at_ms = original_sent_message["internal_date_ms"]
    else:
        sent_at_ms = _to_epoch_ms(draft["sent_confirmed_at"])

    watermark_ms = max(sent_at_ms, _to_epoch_ms(draft["last_reply_checked_at"]))
    reply_candidates = [
        m
        for m in thread["messages"]
        if "INBOX" in m["label_ids"] and m["internal_date_ms"] > watermark_ms
    ]

    await _mark_checked(supabase, draft["draft_id"])

    if not reply_candidates:
        return

    reply_message: GmailMessage = max(reply_candidates, key=lambda m: m["internal_date_ms"])

    try:
        llm_credential = await resolve(supabase, user_id, capability=_CLASSIFIER_CAPABILITY)
    except ApiError as e:
        if e.code == "SETUP_REQUIRED":
            return  # fail open -- no LLM configured for this capability yet
        raise

    proposal = await classify_reply(
        reply_body_text=reply_message["body_text"],
        company=draft["company_name"] or "Unknown Company",
        role_title=draft["title"] or "Unknown Role",
        llm_api_key=llm_credential.secret,
        llm_model=llm_credential.model,
        llm_base_url=llm_credential.base_url,
        generate=llm_generate,
    )

    await _record_proposal(
        supabase,
        user_id=user_id,
        application_id=draft["application_id"],
        outreach_draft_id=draft["draft_id"],
        source_gmail_message_id=reply_message["id"],
        proposal=proposal,
    )


async def run_reply_check_once(http: httpx.AsyncClient, supabase: AsyncClient) -> int:
    try:
        oauth_config = require_gmail_oauth_config()
    except ApiError:
        return 0  # fail open -- Gmail integration isn't configured on this server at all

    drafts = await _select_due_drafts(supabase)
    if not drafts:
        return 0

    access_token_tasks: dict[str, asyncio.Task[str]] = {}
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

    async def _bounded(draft: dict[str, Any]) -> None:
        async with semaphore:
            await _check_one_draft(http, supabase, draft, oauth_config, access_token_tasks)

    await asyncio.gather(*(_bounded(draft) for draft in drafts))
    return len(drafts)


async def run_reply_check_forever(
    http: httpx.AsyncClient,
    supabase: AsyncClient,
    *,
    poll_interval_seconds: float = _DEFAULT_CHECK_INTERVAL_SECONDS,
) -> None:
    while True:
        await run_reply_check_once(http, supabase)
        await asyncio.sleep(poll_interval_seconds)
