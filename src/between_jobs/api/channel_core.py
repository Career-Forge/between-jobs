"""The chat bot's business logic, in terms of envelopes and never of a channel's wire format.

`handle_inbound` takes one `InboundMessage` (see `channel_envelope`), resolves the sender to
a user, and answers through a `Renderer`. It knows nothing of Telegram: not its update JSON,
not its HTML, not its client. The one thing it asks of the channel beyond sending is
`Renderer.fetch_attachment`, for an uploaded resume. A new channel is an adapter that
produces `InboundMessage`s and implements `Renderer`; this module does not change.

A message is dispatched on SEVEN things, checked in order:
  1. A document attachment shaped like a .json file -> resume import.
  2. `/link CODE` -> account linking (consumes the code, merges any accumulated data onto
     the target web account). Only in a private chat.
  3. `/unlink` -> detaches this channel account from whatever it currently resolves to.
  4. Text shaped like a JSON payload (`looks_like_json_payload`) -> resume import.
  5. Text shaped like a `Title:`/`Company:` job paste (`looks_like_job_paste`) -> creates
     the same `jobs`/`job_snapshots`/`applications` rows the web's manual-paste form does.
  6. "apply to #N" / "apply #N" / "generate #N" (`parse_apply_reference`) -> resolves the
     index against the working set "list" minted, then runs the same prepare-and-deliver
     flow the "Generate resume" button uses.
  7. Everything else -> the deterministic command classifier (`classify()`): setup help,
     check-resume, track-job help, list applications, or a fallback.

A button tap (`Callback`) is dispatched on its data's prefix: confirm or cancel a resume
preview, generate a resume, mark an application as applied.

A resume import is a template message, then a SEPARATE message carrying the actual JSON,
validated deterministically (profile.py) and staged as a PENDING profile_versions row
(profile_store.py) until the user taps a confirm or cancel button.

Generating a resume is the one slow thing here, and it is DEFERRED: `_start_prepare` does
the quick part inline (the per-user limit, the "this can take a minute" message) and hands
the rest to a `DeferredReplies` task, so the webhook can answer its request at once and
the person gets the resume when it is ready. That module's docstring says what a restart does
to work in flight.

Human actions stay human: the bot drafts and delivers a resume, and offers a button to mark
the application as applied; it never submits anything.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import cast

import httpx
from postgrest.exceptions import APIError

from supabase import AsyncClient

from . import channel_messages as messages
from .applications_store import (
    ApplicationNotFound,
    InvalidApplicationStatus,
    change_stage,
    create_application,
    list_applications,
)
from .body_limit import max_request_body_bytes
from .channel_envelope import (
    AckCallback,
    Attachment,
    AttachmentTooLarge,
    ButtonRows,
    Callback,
    InboundMessage,
    Renderer,
    RichText,
    Say,
    SendDocument,
    rich,
)
from .deferred_reply import DeferredReplies
from .errors import ApiError, log_api_error
from .intents import (
    Intent,
    classify,
    is_unlink_command,
    looks_like_job_paste,
    looks_like_json_payload,
    parse_apply_reference,
    parse_job_paste,
    parse_link_code,
)
from .jobs_store import create_job_from_paste, get_snapshots
from .link_codes_store import consume_link_code, link_schema_ready
from .link_completion import LinkCompletion, finish_link
from .prepare_orchestrator import latest_resume_pdf, run_prepare_application
from .product_events import emit_setup_required
from .profile import ProfileImportError, import_profile
from .profile_store import (
    VersionAlreadyActivated,
    VersionNotFound,
    activate_version,
    create_pending_version,
    delete_pending_version,
    get_active_version,
)
from .rate_limits import rate_limit_error_or_none
from .telegram_identity import (
    is_auto_provisioned_for_subject,
    resolve_or_create_user_id_for_subject,
    unlink_subject,
)
from .working_sets_store import (
    ReferenceOutOfRange,
    WorkingSetExpired,
    create_working_set,
    get_active_working_set,
    resolve_reference,
)

logger = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"

_APPLICATIONS_WORKING_SET_KIND = "applications"


@dataclass(frozen=True)
class _Turn:
    """Everything one inbound message's handling needs, so no handler takes eight arguments.
    Frozen and made of long-lived objects, so a deferred task can keep using it after the
    request that made it has been answered."""

    supabase: AsyncClient
    http: httpx.AsyncClient
    renderer: Renderer
    message: InboundMessage
    user_id: str
    deferred: DeferredReplies

    async def say(self, text: str | RichText, buttons: ButtonRows = ()) -> None:
        """Replies in the conversation. A plain `str` is text, never markup."""
        body = text if isinstance(text, RichText) else rich(text)
        await self.renderer.send(self.message.chat_ref, Say(body, buttons))


def is_slow_request(message: InboundMessage) -> bool:
    """Whether handling this message starts a resume generation: a "Generate resume" tap, or
    "apply to #N". The webhook gives those a longer claim lease."""
    if message.callback is not None:
        return message.callback.data.startswith(messages.PREPARE_PREFIX)
    return parse_apply_reference(message.text) is not None


def _is_json_document(attachment: Attachment) -> bool:
    return (
        attachment.filename.lower().endswith(".json") or attachment.mime_type == "application/json"
    )


def _decode_uploaded_bytes(raw: bytes) -> str:
    # utf-8-sig transparently strips a UTF-8 BOM if present; UTF-16 (the
    # common case for a Windows-Notepad-saved file) is the fallback n8n's
    # own file-upload path had to handle for the same reason.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("utf-16")


async def _handle_json_import(turn: _Turn, raw_text: str, source_kind: str) -> None:
    try:
        imported = import_profile(raw_text)
    except ProfileImportError as e:
        await turn.say(f"❌ {e}")
        return

    version = await create_pending_version(turn.supabase, turn.user_id, imported, source_kind)
    preview = messages.build_preview_message(version, imported.stats, imported.warnings)
    await turn.say(preview, messages.preview_keyboard(version["id"]))


async def _handle_job_paste(turn: _Turn, text: str) -> None:
    """The chat-side equivalent of the web's manual-paste `PasteJobForm`, reusing the exact
    same store functions (`create_job_from_paste`, `create_application`) rather than a second
    job-creation path. The confirmation carries a "Generate resume" button -- the
    `PREPARE_PREFIX` branch of `_handle_callback`."""
    parsed = parse_job_paste(text)
    if isinstance(parsed, list):
        await turn.say("❌ " + " / ".join(parsed))
        return

    job, snapshot = await create_job_from_paste(
        turn.supabase,
        title=parsed["title"],
        company_name=parsed["company_name"],
        description_text=parsed["description_text"],
        canonical_url=parsed["canonical_url"],
        location_text=parsed["location_text"],
    )
    application = await create_application(
        turn.supabase,
        turn.user_id,
        job_id=job["id"],
        active_job_snapshot_id=snapshot["id"],
        source_channel=turn.message.channel,
    )
    await turn.say(
        messages.job_tracked_text(parsed["title"], parsed["company_name"]),
        messages.prepare_keyboard(application["id"]),
    )


def _log_ctx(turn: _Turn) -> dict[str, str | None]:
    """Ids only. The application id is left out of every log line: it came out of a button's
    data, so nothing has checked it is anything but text."""
    return {"update_id": turn.message.update_id, "channel": turn.message.channel}


async def _start_prepare(turn: _Turn, application_id: str) -> None:
    """The "Generate resume" button and "apply to #N" both end here: one prepare flow, two
    ways to reach it.

    Inline, so the person hears back at once and a refusal is a reply like any other: a
    place in the deferred-work registry (else "busy", or "still generating" when this person
    already has a generation running -- one at a time each, keyed on the verified user id so
    one person cannot hold every place), then the per-user "prepare" limit -- the same bucket
    `POST /applications/{id}/prepare` uses, since the bot is no way round the limit on the web --
    then the "this can take a minute" message. Only then is the slow part handed to a
    background task, and the webhook answers its request.

    The place is reserved before the limit is claimed, so a refusal for "busy" spends none of
    the person's hourly budget. It is given back as soon as nothing is going to run, before
    the reply that says so. The window of the limiter's own database round trip still holds
    it, which is short and bounded."""
    slot = await turn.deferred.try_reserve(turn.user_id)
    if slot is None:
        if turn.deferred.holds(turn.user_id):
            logger.info(
                "resume generation refused: this user already has one running",
                extra={"ctx": _log_ctx(turn)},
            )
            await turn.say(messages.ALREADY_GENERATING_TEXT)
        else:
            logger.info(
                "resume generation refused: every deferred-work place is taken",
                extra={"ctx": _log_ctx(turn)},
            )
            await turn.say(messages.BUSY_TEXT)
        return
    handed_over = False
    try:
        limited = await rate_limit_error_or_none(turn.supabase, turn.user_id, "prepare")
        if limited is not None:
            # Nothing will run for this request, so give the place back before the reply's
            # round trip: another person's tap meanwhile is not told "busy" for nothing.
            slot.release()
            await turn.say(f"❌ {limited.message}")
            return
        await turn.say(messages.GENERATING_TEXT)
        turn.deferred.start(
            slot,
            lambda: _prepare_and_deliver(turn, application_id),
            name="prepare",
            context=_log_ctx(turn),
            on_failure=lambda: turn.say(messages.PREPARE_FAILED_TEXT),
        )
        handed_over = True
    finally:
        if not handed_over:
            slot.release()


async def _prepare_and_deliver(turn: _Turn, application_id: str) -> None:
    """The slow part of generating a resume, run as a deferred task: the same
    `run_prepare_application` / `latest_resume_pdf` orchestration the web's `/prepare` and
    `/resume.pdf` routes call, turned into a chat message and a real file instead of a JSON
    body. `ApiError` is caught here rather than left to propagate -- this IS the
    client-facing boundary for a chat-triggered prepare, the same role `errors.py`'s FastAPI
    handler plays for the web route -- and is logged the way that handler logs it, so an
    engine outage seen through the bot leaves the same trace as one seen through the web.
    Anything else is the registry's to catch, log and report (see `deferred_reply`), and is
    told to the person as a failed generation: so nothing after the resume has reached the
    chat may raise, or they would be told it failed when they hold it."""
    try:
        result = await run_prepare_application(
            turn.supabase,
            turn.http,
            turn.user_id,
            application_id,
            idempotency_key=f"{turn.message.channel}:{uuid.uuid4()}",
        )
    except ApiError as e:
        _log_known_failure(turn, e)
        # This is the bot's own error boundary, so the API error handler never sees the error;
        # a person stuck on a setup step here is as much a funnel drop-off as one on the web.
        emit_setup_required(turn.supabase, turn.user_id, e)
        await turn.say(f"❌ {e.message}")
        return

    warnings = cast(list[str], result.get("warnings") or [])
    if result.get("resume") is None:
        warning_text = "\n".join(f"• {w}" for w in warnings) if warnings else "No details given."
        await turn.say(messages.PREPARE_DECLINED_TEXT.format(warnings=warning_text))
        return

    try:
        _version_row, pdf_bytes = await latest_resume_pdf(
            turn.supabase, turn.http, turn.user_id, application_id
        )
    except ApiError as e:
        _log_known_failure(turn, e)
        await turn.say(f"❌ {e.message}")
        return

    score = result.get("final_score")
    score_text = str(int(score)) if score is not None else "--"
    warning_suffix = ("\n" + "\n".join(f"• {w}" for w in warnings)) if warnings else ""
    await turn.renderer.send_document(
        turn.message.chat_ref,
        SendDocument(
            "resume.pdf",
            pdf_bytes,
            caption=messages.PREPARE_SUCCESS_CAPTION.format(
                score=score_text, warnings=warning_suffix
            ),
        ),
    )
    # One tap moves the application from "saved" (its default status, per
    # applications_store._DEFAULT_STATUS) to "applied", the natural next step right after
    # reviewing a freshly generated resume. A separate message rather than buttons on the
    # document itself -- the already-tested shape (a message with a keyboard).
    try:
        await turn.say(
            messages.MARK_APPLIED_PROMPT_TEXT, messages.mark_applied_keyboard(application_id)
        )
    except Exception:
        # The resume is already in the chat. Failing to offer this button must not reach the
        # registry, which would tell the person generation failed: they would regenerate, on
        # their own key, for nothing. Not retried; the stage can still be changed from the
        # list or the web app.
        logger.warning(
            "could not send the mark-as-applied prompt after delivering the resume",
            exc_info=True,
            extra={"ctx": _log_ctx(turn)},
        )


def _log_known_failure(turn: _Turn, exc: ApiError) -> None:
    """A generation that ends in an `ApiError` is answered in chat and is not a failure of the
    task, but it still leaves a log line, at the level the web handler would give it."""
    log_api_error(logger, exc, ctx={"task": "prepare", **_log_ctx(turn)})


async def _handle_list_applications(turn: _Turn) -> None:
    """Numbers the user's own tracked applications and mints a working set from that exact
    ordering ("the number belongs to a working set, not global memory"), so a later "apply
    to #N" resolves against THIS list even if the underlying applications change in
    between."""
    applications = await list_applications(turn.supabase, turn.user_id)
    if not applications:
        await turn.say(messages.NO_APPLICATIONS_TEXT)
        return

    snapshot_ids = list({a["active_job_snapshot_id"] for a in applications})
    snapshots_by_id = {s["id"]: s for s in await get_snapshots(turn.supabase, snapshot_ids)}

    await create_working_set(
        turn.supabase,
        turn.user_id,
        kind=_APPLICATIONS_WORKING_SET_KIND,
        source_channel=turn.message.channel,
        items=[a["id"] for a in applications],
    )

    lines = ["📋 Your tracked applications:", ""]
    for i, application in enumerate(applications, start=1):
        snapshot = snapshots_by_id.get(application["active_job_snapshot_id"])
        title = snapshot["title"] if snapshot else "Untitled"
        company = snapshot["company_name"] if snapshot else ""
        lines.append(f"{i}. {title} @ {company} -- {application['status']}")
    lines += ["", 'Send "apply to #N" to generate a resume for one.']
    await turn.say("\n".join(lines))


async def _handle_apply_reference(turn: _Turn, index: int) -> None:
    """Resolves "apply to #N" against the active applications working set
    `_handle_list_applications` mints, then runs the same prepare flow the "Generate resume"
    button uses."""
    working_set = await get_active_working_set(
        turn.supabase, turn.user_id, _APPLICATIONS_WORKING_SET_KIND
    )
    if working_set is None:
        await turn.say(messages.NO_WORKING_SET_TEXT)
        return

    try:
        application_id = await resolve_reference(
            turn.supabase, turn.user_id, working_set["id"], index
        )
    except WorkingSetExpired:
        await turn.say(messages.WORKING_SET_EXPIRED_TEXT)
        return
    except ReferenceOutOfRange:
        await turn.say(messages.REFERENCE_OUT_OF_RANGE_TEXT.format(index=index))
        return

    await _start_prepare(turn, application_id)


async def _handle_link_command(turn: _Turn, code: str) -> None:
    subject = turn.message.subject
    if not await link_schema_ready(turn.supabase):
        await turn.say(messages.LINK_FAILED_TEXT)
        return
    try:
        result = await consume_link_code(
            turn.supabase,
            channel=turn.message.channel,
            external_subject=subject,
            code=code,
            source_user_id=turn.user_id,
        )
    except APIError as e:
        # Answer instead of failing the webhook: a 5xx makes the channel
        # redeliver the same /link, and if the code was already consumed the
        # retry would call it invalid. A Postgres error (a 5-character
        # SQLSTATE) rolled the whole RPC back, so nothing changed; anything
        # else -- a gateway error in front of PostgREST -- says nothing about
        # whether it committed, so the user is told to send the same code
        # again: if it did commit, that resumes the link (a new code would
        # not); if it didn't, the code still works.
        logger.warning("link code consumption failed", extra={"ctx": {"code": e.code}})
        if e.code == _UNIQUE_VIOLATION:
            text = messages.LINK_CONFLICT_TEXT
        elif isinstance(e.code, str) and len(e.code) == 5:
            text = messages.LINK_FAILED_TEXT
        else:
            text = messages.LINK_UNCONFIRMED_TEXT
        await turn.say(text)
        return

    if not result["ok"]:
        await turn.say(messages.LINK_REFUSALS.get(result["reason"], messages.LINK_INVALID_TEXT))
        return

    target_user_id = result["target_user_id"]
    source_user_id = result["source_user_id"]
    if target_user_id == source_user_id:
        await turn.say(messages.LINK_ALREADY_LINKED_TEXT)
        return

    # The merge is committed. What's left -- moving the files, retiring the
    # emptied source account -- is finished before replying, so a crash in
    # it means no reply, the channel redelivers, and the same code resumes it
    # (consume_link_code recognizes it) instead of leaving the source
    # stranded behind a "Linked!" nobody will ever resend. (The update's
    # claim keeps that working: until the crashed delivery's lease runs
    # out the channel's retries are answered 503, not 200, so it keeps retrying;
    # then the retry takes the update over.)
    completion = await _finish_link(
        turn.supabase,
        source_user_id=source_user_id,
        target_user_id=target_user_id,
        subject=subject,
    )
    if result.get("resumed") and completion is not None and completion.already_complete:
        await turn.say(messages.LINK_ALREADY_LINKED_TEXT)
        return
    text = messages.format_merge_summary(result.get("summary") or {})
    if completion is None or not completion.retired:
        # The accounts are linked, but the old one still holds something --
        # say so rather than implying it's all done.
        text += messages.LINK_FINISHING_NOTE
    await turn.say(text)


async def _finish_link(
    supabase: AsyncClient, *, source_user_id: str, target_user_id: str, subject: str
) -> LinkCompletion | None:
    """`finish_link`, contained: the link itself already committed, so a
    failure finishing it is logged and the user still hears "Linked!" (with a
    note that it isn't quite done) -- the same code resumes it if they resend
    it, and nothing is lost meanwhile, since the source account isn't deleted
    until it owns nothing."""
    try:
        return await finish_link(
            supabase,
            source_user_id=source_user_id,
            target_user_id=target_user_id,
            subject=subject,
        )
    except Exception:
        logger.error(
            "finishing a link failed",
            extra={"ctx": {"source_user_id": source_user_id, "target_user_id": target_user_id}},
            exc_info=True,
        )
        return None


async def _handle_unlink_command(turn: _Turn) -> None:
    channel, subject = turn.message.channel, turn.message.subject
    # A channel-only account has no web account to detach from, and
    # dropping its identity row would strand its data under an auth user
    # nothing points at any more.
    if await is_auto_provisioned_for_subject(turn.supabase, turn.user_id, channel, subject):
        await turn.say(messages.UNLINK_NOT_LINKED_TEXT)
        return
    await unlink_subject(turn.supabase, channel, subject)
    await turn.say(messages.UNLINK_TEXT)


async def _handle_message(turn: _Turn) -> None:
    message = turn.message
    attachment = message.attachment
    if attachment is not None and _is_json_document(attachment):
        # The same size cap a pasted profile gets on the web (body_limit.py): this upload
        # never passes through that middleware, so it is checked here, against the size
        # the sender's client reports (advisory, so the download is bounded too).
        limit = max_request_body_bytes()
        if attachment.declared_size is not None and attachment.declared_size > limit:
            await turn.say(messages.document_too_large_text(limit))
            return
        try:
            raw_bytes = await turn.renderer.fetch_attachment(attachment, max_bytes=limit)
        except AttachmentTooLarge:
            await turn.say(messages.document_too_large_text(limit))
            return
        await _handle_json_import(
            turn, _decode_uploaded_bytes(raw_bytes), f"{message.channel}_json_upload"
        )
        return

    text = message.text

    link_code = parse_link_code(text)
    if link_code is not None:
        if not message.is_private:
            await turn.say(messages.LINK_PRIVATE_ONLY_TEXT)
            return
        await _handle_link_command(turn, link_code)
        return

    if is_unlink_command(text):
        await _handle_unlink_command(turn)
        return

    if looks_like_json_payload(text):
        await _handle_json_import(turn, text, f"{message.channel}_json_paste")
        return

    if looks_like_job_paste(text):
        await _handle_job_paste(turn, text)
        return

    apply_index = parse_apply_reference(text)
    if apply_index is not None:
        await _handle_apply_reference(turn, apply_index)
        return

    intent = classify(text)

    if intent == Intent.SETUP_HELP:
        await turn.say(messages.SETUP_HELP_TEXT)
        return

    if intent == Intent.CHECK_RESUME:
        version = await get_active_version(turn.supabase, turn.user_id)
        if version is None:
            await turn.say(messages.NO_RESUME_TEXT)
            return
        await turn.say(messages.format_active_profile(version))
        return

    if intent == Intent.TRACK_JOB_HELP:
        await turn.say(messages.TRACK_JOB_HELP_TEXT)
        return

    if intent == Intent.LIST_APPLICATIONS:
        await _handle_list_applications(turn)
        return

    await turn.say(messages.FALLBACK_TEXT)


async def _handle_callback(turn: _Turn, callback: Callback) -> None:
    data = callback.data

    async def acknowledge() -> None:
        await turn.renderer.ack_callback(AckCallback(callback.id))

    if data.startswith(messages.ACTIVATE_PREFIX):
        version_id = data[len(messages.ACTIVATE_PREFIX) :]
        try:
            await activate_version(turn.supabase, turn.user_id, version_id)
            await turn.say(messages.SAVED_TEXT)
        except VersionNotFound:
            await turn.say(messages.PREVIEW_GONE_TEXT)
    elif data.startswith(messages.CANCEL_PREFIX):
        version_id = data[len(messages.CANCEL_PREFIX) :]
        try:
            await delete_pending_version(turn.supabase, turn.user_id, version_id)
            await turn.say(messages.CANCELLED_TEXT)
        except (VersionNotFound, VersionAlreadyActivated):
            # Already gone (double-tap) or already confirmed (raced with
            # activate) -- either way, nothing left to cancel.
            pass
    elif data.startswith(messages.PREPARE_PREFIX):
        application_id = data[len(messages.PREPARE_PREFIX) :]
        # Answered BEFORE the generation starts, not after -- a button's spinner
        # shouldn't sit and wait for a slow action; the "this can take a minute"
        # message (sent in `_start_prepare`) is the actual "this is running"
        # signal to the user.
        await acknowledge()
        await _start_prepare(turn, application_id)
        return
    elif data.startswith(messages.STAGE_PREFIX):
        application_id, _sep, new_status = data[len(messages.STAGE_PREFIX) :].partition(":")
        try:
            await change_stage(
                turn.supabase,
                turn.user_id,
                application_id,
                new_status=new_status,
                idempotency_key=f"{turn.message.channel}-stage:{uuid.uuid4()}",
            )
            await turn.say(messages.stage_changed_text(new_status))
        except ApplicationNotFound:
            await turn.say(messages.APPLICATION_GONE_TEXT)
        except InvalidApplicationStatus:
            # This callback data is technically forgeable, and this path never goes
            # through ChangeApplicationStageRequest's own Pydantic Literal, so
            # this is the first real check `new_status` ever meets.
            await turn.say(messages.not_a_real_stage_text(new_status))

    await acknowledge()


async def handle_inbound(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    renderer: Renderer,
    message: InboundMessage,
    *,
    deferred: DeferredReplies,
) -> None:
    """Resolves the sender to a user (creating one on first contact) and handles the message
    or button tap, answering through `renderer`. Raises on an unexpected failure: the caller
    decides what that means for the delivery (the webhook answers 500 and releases its claim
    so the channel retries)."""
    user_id = await resolve_or_create_user_id_for_subject(
        supabase, message.channel, message.subject
    )
    turn = _Turn(supabase, http, renderer, message, user_id, deferred)
    if message.callback is not None:
        await _handle_callback(turn, message.callback)
    else:
        await _handle_message(turn)
