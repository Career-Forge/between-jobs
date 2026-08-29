"""Telegram webhook receiver.

Verifies the update is really from Telegram, resolves (or creates) the
sender's identity, and dispatches on SEVEN things, checked in order:
  1. A document attachment shaped like a .json file -> resume import.
  2. `/link CODE` -> Sprint 2.8e's account-linking flow (consumes the
     code, merges any accumulated data onto the target web account).
  3. `/unlink` -> detaches this Telegram account from whatever it
     currently resolves to.
  4. Text shaped like a JSON payload (`looks_like_json_payload`) -> resume
     import.
  5. Text shaped like a `Title:`/`Company:` job paste
     (`looks_like_job_paste`, Sprint 3.4a) -> creates the same
     `jobs`/`job_snapshots`/`applications` rows the web's manual-paste
     form does.
  6. "apply to #N" / "apply #N" / "generate #N" (`parse_apply_reference`,
     Sprint 3.4c) -> resolves the index against the working set "list"
     minted, then runs the same prepare-and-deliver flow the "Generate
     resume" button uses.
  7. Everything else -> the deterministic command classifier
     (`classify()`): setup help, check-resume, track-job help, list
     applications, or a fallback.

Sprint 2.5 replaces the Sprint 2.4 stub (colon-delimited "my resume: <text>",
saved as raw text) with the real contract: a template message, then a
SEPARATE message carrying the actual JSON, validated deterministically
(profile.py) and staged as a PENDING profile_versions row
(profile_store.py) until the user taps a confirm/cancel inline button --
handled here via Telegram's `callback_query` update type, which the
Sprint 2.3/2.4 bridge never handled at all.

Always returns 200 for a well-authenticated request, even for update
shapes it doesn't handle -- Telegram retries on non-2xx, and there's
nothing to retry here since not every update needs an action.
"""

from __future__ import annotations

import contextlib
import hmac
import uuid
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase, get_telegram_client, get_webhook_secret
from .applications_store import (
    ApplicationNotFound,
    change_stage,
    create_application,
    list_applications,
)
from .errors import ApiError
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
from .link_codes_store import consume_link_code
from .prepare_orchestrator import latest_resume_pdf, run_prepare_application
from .profile import ProfileImportError, import_profile
from .profile_store import (
    VersionAlreadyActivated,
    VersionNotFound,
    activate_version,
    create_pending_version,
    delete_pending_version,
    get_active_version,
)
from .telegram_client import TelegramClient
from .telegram_identity import CHANNEL, resolve_or_create_user_id
from .telegram_identity import unlink as unlink_telegram_identity
from .working_sets_store import (
    ReferenceOutOfRange,
    WorkingSetExpired,
    create_working_set,
    get_active_working_set,
    resolve_reference,
)

router = APIRouter()

_ACTIVATE_PREFIX = "profile:activate:"
_CANCEL_PREFIX = "profile:cancel:"
_PREPARE_PREFIX = "app:prepare:"
_STAGE_PREFIX = "app:stage:"
_UNIQUE_VIOLATION = "23505"

_FALLBACK_TEXT = (
    'Send "set up my resume" to get started, or "check my resume" to see what\'s on file.'
)
_NO_RESUME_TEXT = 'I don\'t have a resume on file yet. Send "set up my resume" to get started.'

_LINK_INVALID_TEXT = "❌ That code isn't valid. Double-check it and try again."
_LINK_EXPIRED_TEXT = "❌ That code expired. Generate a new one from the website."
_LINK_RATE_LIMITED_TEXT = "❌ Too many wrong codes -- try again in a few minutes."
_LINK_ALREADY_LINKED_TEXT = "You're already linked to that account."
_LINK_CONFLICT_TEXT = (
    "❌ Couldn't link -- both accounts already have conflicting data "
    "(e.g. the same saved API key or tracked job). Nothing was changed."
)
_UNLINK_TEXT = "Unlinked. This Telegram account is no longer connected to any web account."

_MERGE_SUMMARY_LABELS = (
    ("profile_versions", "resume version"),
    ("applications", "tracked application"),
    ("provider_credentials", "saved API key"),
)
"""Only the tables a human would recognize -- career_facts,
application_events, event_outbox, artifact_versions, working_sets, and
link_codes all moved too (merge_user_data reparents every user-owned
table), but naming them in a chat message would just be noise."""

_SETUP_HELP_TEXT = """🗂️ *Set up your resume (one-time)*

I read resumes from a fixed JSON template -- deterministic, private, no AI guessing in the loop.

*How:*
1️⃣ Copy the JSON below.
2️⃣ Paste it into ChatGPT/Claude with your real resume, and ask it to fill \
the template in with your actual info.
3️⃣ Send the filled JSON back to me -- as pasted text, or as a *.json* file.

```
{
  "personal": {
    "name": "<Your Name>",
    "headline": "<e.g. Software Engineer>",
    "emails": [{"address": "<you@example.com>", "primary": true}],
    "phones": [{"number": "<+1 555 0100>", "primary": true, "region": "US"}],
    "links": {"linkedin": "", "github": "", "portfolio": "", "scholar": ""},
    "location": {"city": "", "region": "", "country": "", "show_on_resume": false},
    "work_authorization": ""
  },
  "summary_bullets": ["<one line summarizing who you are>"],
  "experience": [
    {
      "title": "<Job Title>",
      "company": "<Company>",
      "location": "<City, ST>",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM or present",
      "is_current": false,
      "bullets": ["<what you did, with a number if you can>"],
      "skills": ["<Python>"],
      "metrics": [],
      "pin": null
    }
  ],
  "projects": [],
  "education": [
    {
      "degree": "<B.S. Computer Science>",
      "institution": "<University>",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM"
    }
  ],
  "publications": [],
  "patents": [],
  "skills": {
    "programming": [], "ai_ml": [], "data_mlops": [],
    "cloud_devops": [], "tools": [], "other": []
  },
  "certifications": [],
  "achievements": [],
  "languages": [],
  "volunteering": []
}
```
Sections shown empty above (publications, patents, certifications, \
languages, volunteering, and links.scholar) are optional -- fill in \
whichever apply to you and delete the rest. At least one of experience, \
projects, publications, patents, or volunteering needs a real entry.

`"pin"` on an experience/project/education entry (shown above as `null`) \
is a manual choice, not something to fill in from your resume: set it to \
`{"mandatory": true, "min_bullets": 3}` (min_bullets 1-6, optional) to \
force that entry into every generated resume regardless of relevance -- \
up to 6 pinned entries total."""

_TRACK_JOB_HELP_TEXT = """📋 *Track a job*

Send me the job in this format -- Title and Company are required, \
Location and URL are optional:

```
Title: Staff AI Engineer
Company: Acme
Location: Remote
URL: https://example.com/jobs/123

<paste the full job description here>
```

I don't fetch job postings from a link yet -- paste the description text \
along with the URL, and I'll track both."""

_JOB_TRACKED_TEXT = "✅ Tracking *{title}* at *{company}*."
_GENERATING_TEXT = "⏳ Generating your resume for this job -- this can take a minute..."
_PREPARE_DECLINED_TEXT = "The resume engine didn't produce a resume for this job.\n\n{warnings}"
_PREPARE_SUCCESS_CAPTION = "📄 Resume -- ATS score {score}/100{warnings}"

_APPLICATIONS_WORKING_SET_KIND = "applications"
_NO_APPLICATIONS_TEXT = 'Nothing tracked yet. Send "track a job" to get started.'
_NO_WORKING_SET_TEXT = 'Send "list" first to number your applications, then "apply to #N".'
_WORKING_SET_EXPIRED_TEXT = 'That list expired -- send "list" again to get fresh numbers.'
_REFERENCE_OUT_OF_RANGE_TEXT = '#{index} isn\'t on your list -- send "list" to see the numbers.'

_STAGE_APPLIED_STATUS = "applied"
_MARK_APPLIED_BUTTON_TEXT = "✅ Mark as applied"
_MARK_APPLIED_PROMPT_TEXT = "Applying with this one?"
_STAGE_CHANGED_TEXT = "✅ Marked as *{status}*."


def _verify_webhook_secret(request: Request, expected: str = Depends(get_webhook_secret)) -> None:
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid webhook secret")


def _is_json_document(document: dict[str, Any]) -> bool:
    file_name = str(document.get("file_name", ""))
    mime_type = str(document.get("mime_type", ""))
    return file_name.lower().endswith(".json") or mime_type == "application/json"


def _decode_uploaded_bytes(raw: bytes) -> str:
    # utf-8-sig transparently strips a UTF-8 BOM if present; UTF-16 (the
    # common case for a Windows-Notepad-saved file) is the fallback n8n's
    # own file-upload path had to handle for the same reason.
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("utf-16")


def _build_preview_message(
    version: dict[str, Any], stats: dict[str, int], warnings: tuple[str, ...]
) -> str:
    personal = version["canonical_json"]["personal"]
    name = personal.get("name", "")
    headline = personal.get("headline", "")
    primary_email = next((e["address"] for e in personal.get("emails", []) if e.get("primary")), "")

    lines = ["📄 Resume preview", "", f"Name: {name}"]
    if headline:
        lines.append(f"Headline: {headline}")
    if primary_email:
        lines.append(f"Email: {primary_email}")
    lines += [
        "",
        "Detected:",
        f"• Experience: {stats['experience']}",
        f"• Projects: {stats['projects']}",
        f"• Education: {stats['education']}",
        f"• Skills: {stats['skills']}",
    ]
    if warnings:
        lines.append("")
        lines.append("⚠️ Notes:")
        lines += [f"• {w}" for w in warnings]
    lines += ["", "Tap a button below to confirm."]
    return "\n".join(lines)


def _format_merge_summary(summary: dict[str, int]) -> str:
    parts = [
        f"{summary[key]} {label}(s)" for key, label in _MERGE_SUMMARY_LABELS if summary.get(key)
    ]
    if not parts:
        return "✅ Linked! Your Telegram account is now connected to your web account."
    return "✅ Linked! Brought over " + ", ".join(parts) + " from this Telegram account."


def _build_preview_keyboard(version_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Looks good -- save it",
                    "callback_data": f"{_ACTIVATE_PREFIX}{version_id}",
                },
                {"text": "❌ Cancel", "callback_data": f"{_CANCEL_PREFIX}{version_id}"},
            ]
        ]
    }


def _format_active_profile(version: dict[str, Any]) -> str:
    personal = version["canonical_json"]["personal"]
    name = personal.get("name", "")
    headline = personal.get("headline", "")

    lines = ["✅ I have your resume on file.", "", f"Name: {name}"]
    if headline:
        lines.append(f"Headline: {headline}")
    lines += ["", f"Last updated: {version.get('activated_at', '')}", "", _FALLBACK_TEXT]
    return "\n".join(lines)


async def _handle_json_import(
    supabase: AsyncClient,
    user_id: str,
    raw_text: str,
    source_kind: str,
    telegram: TelegramClient,
    chat_id: int,
) -> None:
    try:
        imported = import_profile(raw_text)
    except ProfileImportError as e:
        await telegram.send_message(chat_id, f"❌ {e}")
        return

    version = await create_pending_version(supabase, user_id, imported, source_kind)
    preview = _build_preview_message(version, imported.stats, imported.warnings)
    keyboard = _build_preview_keyboard(version["id"])
    await telegram.send_message(chat_id, preview, reply_markup=keyboard)


def _build_prepare_keyboard(application_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📄 Generate resume", "callback_data": f"{_PREPARE_PREFIX}{application_id}"}]
        ]
    }


def _build_mark_applied_keyboard(application_id: str) -> dict[str, Any]:
    callback_data = f"{_STAGE_PREFIX}{application_id}:{_STAGE_APPLIED_STATUS}"
    button = {"text": _MARK_APPLIED_BUTTON_TEXT, "callback_data": callback_data}
    return {"inline_keyboard": [[button]]}


async def _handle_job_paste(
    supabase: AsyncClient,
    user_id: str,
    telegram: TelegramClient,
    chat_id: int,
    text: str,
) -> None:
    """Sprint 3.4a -- the Telegram-side equivalent of the web's manual-
    paste `PasteJobForm` (2.6f), reusing the exact same store functions
    (`create_job_from_paste`, `create_application`) rather than a second
    job-creation path. This is the first Telegram handler that reaches
    into the applications pipeline at all -- everything before this
    sprint only ever touched profile_store/link_codes_store.

    The confirmation carries a "Generate resume" button (3.4b) --
    `_handle_callback`'s `_PREPARE_PREFIX` branch."""
    parsed = parse_job_paste(text)
    if isinstance(parsed, list):
        await telegram.send_message(chat_id, "❌ " + " / ".join(parsed))
        return

    job, snapshot = await create_job_from_paste(
        supabase,
        title=parsed["title"],
        company_name=parsed["company_name"],
        description_text=parsed["description_text"],
        canonical_url=parsed["canonical_url"],
        location_text=parsed["location_text"],
    )
    application = await create_application(
        supabase,
        user_id,
        job_id=job["id"],
        active_job_snapshot_id=snapshot["id"],
        source_channel=CHANNEL,
    )
    await telegram.send_message(
        chat_id,
        _JOB_TRACKED_TEXT.format(title=parsed["title"], company=parsed["company_name"]),
        reply_markup=_build_prepare_keyboard(application["id"]),
    )


async def _run_prepare_and_deliver(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    telegram: TelegramClient,
    chat_id: int,
    application_id: str,
) -> None:
    """Sprint 3.4b (button) / 3.4c ("apply to #N") -- runs the exact same
    `run_prepare_application` / `latest_resume_pdf` orchestration the
    web's `/prepare` and `/resume.pdf` routes call, then turns the result
    into a chat message (+ a real file, via `TelegramClient.send_document`)
    instead of a JSON body. `ApiError` is caught here rather than left to
    propagate -- this handler IS the client-facing boundary for a
    Telegram-triggered prepare, the same role `errors.py`'s FastAPI
    handler plays for the web route. Shared by both triggers so "tap the
    button on a just-created job" and "apply to #N from your list" behave
    identically -- one prepare flow, two ways to reach it."""
    await telegram.send_message(chat_id, _GENERATING_TEXT)
    try:
        result = await run_prepare_application(
            supabase, http, user_id, application_id, idempotency_key=f"telegram:{uuid.uuid4()}"
        )
    except ApiError as e:
        await telegram.send_message(chat_id, f"❌ {e.message}")
        return

    warnings = cast(list[str], result.get("warnings") or [])
    if result.get("resume") is None:
        warning_text = "\n".join(f"• {w}" for w in warnings) if warnings else "No details given."
        await telegram.send_message(chat_id, _PREPARE_DECLINED_TEXT.format(warnings=warning_text))
        return

    try:
        _version_row, pdf_bytes = await latest_resume_pdf(supabase, http, user_id, application_id)
    except ApiError as e:
        await telegram.send_message(chat_id, f"❌ {e.message}")
        return

    score = result.get("final_score")
    score_text = str(int(score)) if score is not None else "--"
    warning_suffix = ("\n" + "\n".join(f"• {w}" for w in warnings)) if warnings else ""
    await telegram.send_document(
        chat_id,
        "resume.pdf",
        pdf_bytes,
        caption=_PREPARE_SUCCESS_CAPTION.format(score=score_text, warnings=warning_suffix),
    )
    # Sprint 3.4d -- "track/approve via buttons": one tap moves the
    # application from "saved" (its default status, per
    # applications_store._DEFAULT_STATUS) to "applied", the natural next
    # step right after reviewing a freshly generated resume. A separate
    # message rather than `reply_markup` on the document itself -- proven,
    # already-tested shape (`send_message` + a keyboard), not a new one.
    await telegram.send_message(
        chat_id,
        _MARK_APPLIED_PROMPT_TEXT,
        reply_markup=_build_mark_applied_keyboard(application_id),
    )


async def _handle_list_applications(
    supabase: AsyncClient, user_id: str, telegram: TelegramClient, chat_id: int
) -> None:
    """Sprint 3.4c -- numbers the user's own tracked applications and
    mints a working set from that exact ordering (Proposal §21: "the
    number belongs to a working set, not global memory"), so a later
    "apply to #N" resolves against THIS list even if the underlying
    applications change in between. The first real consumer of
    `working_sets_store` -- Sprint 2.6e shipped it fully tested but
    entirely unwired."""
    applications = await list_applications(supabase, user_id)
    if not applications:
        await telegram.send_message(chat_id, _NO_APPLICATIONS_TEXT)
        return

    snapshot_ids = list({a["active_job_snapshot_id"] for a in applications})
    snapshots_by_id = {s["id"]: s for s in await get_snapshots(supabase, snapshot_ids)}

    await create_working_set(
        supabase,
        user_id,
        kind=_APPLICATIONS_WORKING_SET_KIND,
        source_channel=CHANNEL,
        items=[a["id"] for a in applications],
    )

    lines = ["📋 Your tracked applications:", ""]
    for i, application in enumerate(applications, start=1):
        snapshot = snapshots_by_id.get(application["active_job_snapshot_id"])
        title = snapshot["title"] if snapshot else "Untitled"
        company = snapshot["company_name"] if snapshot else ""
        lines.append(f"{i}. {title} @ {company} -- {application['status']}")
    lines += ["", 'Send "apply to #N" to generate a resume for one.']
    await telegram.send_message(chat_id, "\n".join(lines))


async def _handle_apply_reference(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    telegram: TelegramClient,
    chat_id: int,
    index: int,
) -> None:
    """Sprint 3.4c -- resolves "apply to #N" against the active
    applications working set `_handle_list_applications` mints, then runs
    the same prepare-and-deliver flow the "Generate resume" button uses."""
    working_set = await get_active_working_set(supabase, user_id, _APPLICATIONS_WORKING_SET_KIND)
    if working_set is None:
        await telegram.send_message(chat_id, _NO_WORKING_SET_TEXT)
        return

    try:
        application_id = await resolve_reference(supabase, user_id, working_set["id"], index)
    except WorkingSetExpired:
        await telegram.send_message(chat_id, _WORKING_SET_EXPIRED_TEXT)
        return
    except ReferenceOutOfRange:
        await telegram.send_message(chat_id, _REFERENCE_OUT_OF_RANGE_TEXT.format(index=index))
        return

    await _run_prepare_and_deliver(supabase, http, user_id, telegram, chat_id, application_id)


async def _handle_link_command(
    supabase: AsyncClient,
    user_id: str,
    telegram_user_id: int,
    telegram: TelegramClient,
    chat_id: int,
    code: str,
) -> None:
    try:
        result = await consume_link_code(
            supabase,
            channel=CHANNEL,
            external_subject=str(telegram_user_id),
            code=code,
            source_user_id=user_id,
        )
    except APIError as e:
        if e.code == _UNIQUE_VIOLATION:
            await telegram.send_message(chat_id, _LINK_CONFLICT_TEXT)
            return
        raise

    if not result["ok"]:
        reason = result["reason"]
        text = {
            "rate_limited": _LINK_RATE_LIMITED_TEXT,
            "expired_code": _LINK_EXPIRED_TEXT,
        }.get(reason, _LINK_INVALID_TEXT)
        await telegram.send_message(chat_id, text)
        return

    target_user_id = result["target_user_id"]
    if target_user_id == user_id:
        await telegram.send_message(chat_id, _LINK_ALREADY_LINKED_TEXT)
        return

    # Everything the source identity had has been reparented -- clean up
    # the now-empty orphaned auto-provisioned auth user rather than
    # leaving it behind permanently. The link itself already committed;
    # a leftover empty auth user on failure here is a harmless gap (same
    # acceptance as telegram_identity.unlink's own note), not worth
    # failing this confirmation over.
    with contextlib.suppress(Exception):
        await supabase.auth.admin.delete_user(user_id)

    await telegram.send_message(chat_id, _format_merge_summary(result["summary"]))


async def _handle_unlink_command(
    supabase: AsyncClient, telegram_user_id: int, telegram: TelegramClient, chat_id: int
) -> None:
    await unlink_telegram_identity(supabase, telegram_user_id)
    await telegram.send_message(chat_id, _UNLINK_TEXT)


async def _handle_message(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    telegram_user_id: int,
    telegram: TelegramClient,
    chat_id: int,
    message: dict[str, Any],
) -> None:
    document = message.get("document")
    if isinstance(document, dict) and _is_json_document(document):
        raw_bytes = await telegram.download_document(document["file_id"])
        raw_text = _decode_uploaded_bytes(raw_bytes)
        await _handle_json_import(
            supabase, user_id, raw_text, "telegram_json_upload", telegram, chat_id
        )
        return

    text = message.get("text") or message.get("caption") or ""

    link_code = parse_link_code(text)
    if link_code is not None:
        await _handle_link_command(
            supabase, user_id, telegram_user_id, telegram, chat_id, link_code
        )
        return

    if is_unlink_command(text):
        await _handle_unlink_command(supabase, telegram_user_id, telegram, chat_id)
        return

    if looks_like_json_payload(text):
        await _handle_json_import(supabase, user_id, text, "telegram_json_paste", telegram, chat_id)
        return

    if looks_like_job_paste(text):
        await _handle_job_paste(supabase, user_id, telegram, chat_id, text)
        return

    apply_index = parse_apply_reference(text)
    if apply_index is not None:
        await _handle_apply_reference(supabase, http, user_id, telegram, chat_id, apply_index)
        return

    intent = classify(text)

    if intent == Intent.SETUP_HELP:
        await telegram.send_message(chat_id, _SETUP_HELP_TEXT)
        return

    if intent == Intent.CHECK_RESUME:
        version = await get_active_version(supabase, user_id)
        if version is None:
            await telegram.send_message(chat_id, _NO_RESUME_TEXT)
            return
        await telegram.send_message(chat_id, _format_active_profile(version))
        return

    if intent == Intent.TRACK_JOB_HELP:
        await telegram.send_message(chat_id, _TRACK_JOB_HELP_TEXT)
        return

    if intent == Intent.LIST_APPLICATIONS:
        await _handle_list_applications(supabase, user_id, telegram, chat_id)
        return

    await telegram.send_message(chat_id, _FALLBACK_TEXT)


async def _handle_callback(
    supabase: AsyncClient,
    http: httpx.AsyncClient,
    user_id: str,
    telegram: TelegramClient,
    chat_id: int,
    callback_query: dict[str, Any],
) -> None:
    callback_data = callback_query.get("data", "")

    if callback_data.startswith(_ACTIVATE_PREFIX):
        version_id = callback_data[len(_ACTIVATE_PREFIX) :]
        try:
            await activate_version(supabase, user_id, version_id)
            await telegram.send_message(chat_id, "✅ Saved! Your resume is now on file.")
        except VersionNotFound:
            await telegram.send_message(
                chat_id, "❌ That preview is gone -- please resend your resume JSON."
            )
    elif callback_data.startswith(_CANCEL_PREFIX):
        version_id = callback_data[len(_CANCEL_PREFIX) :]
        try:
            await delete_pending_version(supabase, user_id, version_id)
            await telegram.send_message(chat_id, "Cancelled. Nothing was saved.")
        except (VersionNotFound, VersionAlreadyActivated):
            # Already gone (double-tap) or already confirmed (raced with
            # activate) -- either way, nothing left to cancel.
            pass
    elif callback_data.startswith(_PREPARE_PREFIX):
        application_id = callback_data[len(_PREPARE_PREFIX) :]
        # Answered BEFORE the (potentially minute-long) prepare run, not
        # after -- Telegram's own guidance is that a button's spinner
        # shouldn't sit and wait for a slow action; `_GENERATING_TEXT`
        # (sent inside the handler) is the actual "this is running"
        # signal to the user.
        await telegram.answer_callback_query(callback_query["id"])
        await _run_prepare_and_deliver(supabase, http, user_id, telegram, chat_id, application_id)
        return
    elif callback_data.startswith(_STAGE_PREFIX):
        application_id, _sep, new_status = callback_data[len(_STAGE_PREFIX) :].partition(":")
        try:
            await change_stage(
                supabase,
                user_id,
                application_id,
                new_status=new_status,
                idempotency_key=f"telegram-stage:{uuid.uuid4()}",
            )
            await telegram.send_message(chat_id, _STAGE_CHANGED_TEXT.format(status=new_status))
        except ApplicationNotFound:
            await telegram.send_message(chat_id, "❌ Couldn't find that application anymore.")

    await telegram.answer_callback_query(callback_query["id"])


@router.post("/telegram/webhook", dependencies=[Depends(_verify_webhook_secret)])
async def telegram_webhook(
    request: Request,
    supabase: AsyncClient = Depends(get_supabase),
    telegram: TelegramClient = Depends(get_telegram_client),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, str]:
    update = cast(dict[str, Any], await request.json())

    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        telegram_user_id = callback_query["from"]["id"]
        chat_id = callback_query["message"]["chat"]["id"]
        user_id = await resolve_or_create_user_id(supabase, telegram_user_id)
        await _handle_callback(supabase, http, user_id, telegram, chat_id, callback_query)
        return {"status": "ok"}

    message = update.get("message")
    if not isinstance(message, dict) or ("text" not in message and "document" not in message):
        return {"status": "ignored"}

    telegram_user_id = message["from"]["id"]
    chat_id = message["chat"]["id"]

    user_id = await resolve_or_create_user_id(supabase, telegram_user_id)
    await _handle_message(supabase, http, user_id, telegram_user_id, telegram, chat_id, message)
    return {"status": "ok"}
