"""The corpus and the runner behind the Telegram golden-parity test
(`test_telegram_golden_parity.py`; provenance and how to regenerate it are in
`tests/golden/telegram_bot/README.md`).

A scenario is one Telegram update plus the world around it (what the fake database holds,
what the network answers). `run_scenario` pushes it through a `Stack` -- an app wired with a
webhook router and a Telegram client class -- with the real `TelegramClient` talking to a
`MockTransport`, and returns what the outside world saw: the HTTP answer and the exact
sequence of Telegram API calls, with message text reduced to a digest.

Digests, not text, because the expected file is a fixture and the bot's copy is product
text: the digest of the rendered HTML (tags, escaping and all) changes whenever a character
of it does, and a failing comparison prints the new text so the change can be read.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from typing import Any
from unittest import mock

import httpx
import test_telegram_prepare_callback as prepare_fakes
import test_telegram_webhook as webhook_fakes
from channel_fakes import APPLICATION_ID, USER_ID, ComposedSupabase
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from between_jobs.api import rate_limits
from between_jobs.api.app_state import (
    get_http_client,
    get_supabase,
    get_telegram_client,
    get_webhook_secret,
)
from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.link_completion import LinkCompletion

SECRET = "test-secret-not-real"
TARGET = webhook_fakes._TARGET_USER_ID
TG = webhook_fakes._TELEGRAM_USER_ID


@dataclass(frozen=True)
class Stack:
    """One implementation of the bot, as far as a test can tell: an app serving the webhook,
    the client class it talks to Telegram with, and where its link helpers are patched."""

    make_app: Callable[[], FastAPI]
    client_class: Any
    patch_module: str


# -- the world -----------------------------------------------------------------------------


def _multipart(request: httpx.Request) -> dict[str, Any]:
    raw = (
        b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    )
    fields: dict[str, Any] = {}
    for part in BytesParser(policy=email_policy).parsebytes(raw).iter_parts():
        name = str(part.get_param("name", header="content-disposition"))
        decoded = part.get_payload(decode=True)
        payload = decoded if isinstance(decoded, bytes) else b""
        if part.get_filename() is not None:
            fields[name] = {"filename": part.get_filename(), "content": payload}
        else:
            fields[name] = payload.decode()
    return fields


class World:
    """What the network does in one run: it records every Telegram call and answers the
    forge-engines and LaTeX requests a resume generation makes."""

    def __init__(
        self,
        *,
        document_bytes: bytes | None = None,
        document_headers: dict[str, str] | None = None,
        apply_body: Any = None,
        apply_status: int = 200,
        compile_status: int = 200,
        entity_error_marker: str | None = None,
        telegram_down_on: str | None = None,
        edit_error: str | None = None,
    ) -> None:
        self.telegram: list[dict[str, Any]] = []
        self.document_bytes = document_bytes
        self.document_headers = document_headers or {}
        self.apply_body = apply_body
        self.apply_status = apply_status
        self.compile_status = compile_status
        self.entity_error_marker = entity_error_marker
        self.telegram_down_on = telegram_down_on
        self.edit_error = edit_error
        self._message_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            return self._telegram(request)
        if request.url.path.endswith("/apply"):
            return httpx.Response(self.apply_status, json=self.apply_body, request=request)
        if request.url.path.endswith("/compile"):
            if self.compile_status == 200:
                return httpx.Response(200, content=prepare_fakes._PDF_BYTES, request=request)
            return httpx.Response(
                self.compile_status, json={"error": "CompileError"}, request=request
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def _ok(self, request: httpx.Request) -> httpx.Response:
        self._message_id += 1
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": self._message_id}}, request=request
        )

    def _telegram(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/file/bot" in path:
            self.telegram.append({"method": "downloadFile", "path": path.split("/", 4)[-1]})
            assert self.document_bytes is not None
            return httpx.Response(
                200, content=self.document_bytes, headers=self.document_headers, request=request
            )
        method = path.rsplit("/", 1)[-1]
        if method == "sendDocument":
            fields = _multipart(request)
            self.telegram.append({"method": method, "fields": fields})
            return self._ok(request)
        if method == "getFile":
            self.telegram.append({"method": method, "params": dict(request.url.params)})
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "documents/x.json"}}, request=request
            )
        body = json.loads(request.content) if request.content else {}
        self.telegram.append({"method": method, "json": body})
        text = str(body.get("text", ""))
        if method == "editMessageText" and self.edit_error is not None:
            return httpx.Response(
                400, json={"ok": False, "description": self.edit_error}, request=request
            )
        if self.telegram_down_on and self.telegram_down_on in text:
            return httpx.Response(500, json={"ok": False}, request=request)
        if (
            self.entity_error_marker
            and self.entity_error_marker in text
            and body.get("parse_mode") == "HTML"
        ):
            return httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: can't parse entities: nope"},
                request=request,
            )
        return self._ok(request)


# -- what the outside world saw, reduced -------------------------------------------------------


def _digest(text: str) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(text.encode()).hexdigest()[:16], "chars": len(text)}


def _keyboard(markup: dict[str, Any]) -> list[list[dict[str, Any]]]:
    return [
        [{"label": _digest(b["text"]), "callback_data": b["callback_data"]} for b in row]
        for row in markup["inline_keyboard"]
    ]


def summarize(call: dict[str, Any]) -> dict[str, Any]:
    method = call["method"]
    if method == "sendMessage" or method == "editMessageText":
        body = call["json"]
        return {
            "method": method,
            "chat_id": body["chat_id"],
            "parse_mode": body.get("parse_mode"),
            "text": _digest(body["text"]),
            "reply_markup": _keyboard(body["reply_markup"]) if "reply_markup" in body else None,
        }
    if method == "sendDocument":
        fields = call["fields"]
        document = fields["document"]
        caption = fields.get("caption")
        return {
            "method": method,
            "chat_id": fields["chat_id"],
            "filename": document["filename"],
            "bytes": len(document["content"]),
            "content_sha256": hashlib.sha256(document["content"]).hexdigest()[:16],
            "caption": _digest(caption) if caption is not None else None,
            "fields": sorted(fields),
        }
    if method == "answerCallbackQuery":
        return {"method": method, "json": call["json"]}
    return call


def readable(call: dict[str, Any]) -> str:
    """A call with its text in full, for a failure message."""
    if "json" in call and "text" in call["json"]:
        return json.dumps(call["json"], ensure_ascii=False)
    return json.dumps(summarize(call), ensure_ascii=False)


# -- the corpus ----------------------------------------------------------------------------

SCENARIOS: dict[str, dict[str, Any]] = {}


def _add(name: str, update: dict[str, Any], **config: Any) -> None:
    assert name not in SCENARIOS, name
    SCENARIOS[name] = {"update": update, **config}


def _message(text: str | None = None, *, document: dict[str, Any] | None = None) -> dict[str, Any]:
    return webhook_fakes._message_update(text, document)


def _callback(data: str) -> dict[str, Any]:
    return webhook_fakes._callback_update(data)


_JOB_PASTE = (
    "Title: Staff AI Engineer\nCompany: Acme\nLocation: Remote\nURL: https://example.com/jobs/1\n\n"
    "We need a Python engineer with RAG experience."
)
_WORKING_SET_OK = [
    {"id": "ws-1", "items": ["app-1", "app-2"], "expires_at": "2999-01-01T00:00:00+00:00"}
]
_WORKING_SET_EXPIRED = [
    {"id": "ws-1", "items": ["app-1"], "expires_at": "2000-01-01T00:00:00+00:00"}
]
_PREVIEW_ROW = {
    "id": webhook_fakes._VERSION_ID,
    "canonical_json": json.loads(webhook_fakes._VALID_RESUME_JSON),
    "activated_at": None,
}
_PENDING = {"id": webhook_fakes._VERSION_ID, "user_id": USER_ID, "activated_at": None}
_LINK_OK = {
    "ok": True,
    "target_user_id": TARGET,
    "source_user_id": USER_ID,
    "summary": {"profile_versions": 1, "applications": 2, "provider_credentials": 0},
}
_RESUMED = {"ok": True, "resumed": True, "target_user_id": TARGET, "source_user_id": USER_ID}
_APPLIED_OK = {"id": APPLICATION_ID, "status": "applied"}
_PREP = {
    "applications": [{**webhook_fakes._APP_1, "id": APPLICATION_ID}],
    "working_sets": _WORKING_SET_OK,
}
_PREP_OK = prepare_fakes._FORGE_APPLY_RESPONSE_BODY
_PREP_CLEAN = {
    **_PREP_OK,
    "gate": {"outcome": "proceed", "reason": "", "cautions": []},
}
_PREP_DECLINED = {
    **_PREP_OK,
    "resume": None,
    "ats_attempts": [],
    "gate": {"outcome": "skip_low_score", "reason": "Fit score too low.", "cautions": []},
}
_PREP_CALLBACK = f"app:prepare:{APPLICATION_ID}"
_CHECK_RESUME_ROW = {
    "canonical_json": {"personal": {"name": "Jane Doe", "headline": "Engineer"}},
    "activated_at": "2026-08-06T00:00:00Z",
}

# plain commands
_add("unrecognized_text_from_a_new_user", _message("hi there"), sb={"identities": []})
_add("unrecognized_text_from_a_linked_user", _message("hi again"))
_add("setup_help", _message("set up my resume"))
_add("check_resume_none_on_file", _message("check my resume"), sb={"profile": {"select_rows": []}})
_add(
    "check_resume_active",
    _message("check my resume"),
    sb={"profile": {"select_rows": [_CHECK_RESUME_ROW]}},
)
_add("track_a_job_help", _message("track a job"))
_add("markup_characters_in_plain_text", _message("<b>hello</b> & goodbye"))
_add("empty_text", _message(""))
_add("edited_message_is_ignored", {"update_id": 6, "edited_message": {"text": "oops"}})
_add(
    "photo_with_a_caption_is_ignored",
    {
        "update_id": 5,
        "message": {
            "message_id": 1,
            "from": {"id": TG},
            "chat": {"id": TG, "type": "private"},
            "photo": [],
            "caption": "x",
        },
    },
)

# resume import
_add(
    "json_paste_shows_a_preview_with_buttons",
    _message(webhook_fakes._VALID_RESUME_JSON),
    sb={"profile": {"select_rows": [], "insert_row": _PREVIEW_ROW}},
)
_add(
    "json_paste_that_breaks_a_rule",
    _message(json.dumps({"personal": {"name": "Jane"}, "experience": [], "projects": []})),
)
_add("json_paste_that_is_not_json", _message("{not json at all <b>"))
_add(
    "json_file_upload",
    _message(
        document={
            "file_id": "file-123",
            "file_name": "resume.json",
            "mime_type": "application/json",
        }
    ),
    sb={"profile": {"select_rows": [], "insert_row": _PREVIEW_ROW}},
    world={"document_bytes": webhook_fakes._VALID_RESUME_JSON.encode()},
)
_add(
    "json_file_upload_in_utf16",
    _message(document={"file_id": "f", "file_name": "resume.json"}),
    sb={"profile": {"select_rows": [], "insert_row": _PREVIEW_ROW}},
    world={"document_bytes": webhook_fakes._VALID_RESUME_JSON.encode("utf-16")},
)
_add(
    "json_file_upload_declared_over_the_cap",
    _message(document={"file_id": "f", "file_name": "resume.json", "file_size": 4097}),
    env={"MAX_REQUEST_BODY_BYTES": "4096"},
    world={"document_bytes": webhook_fakes._VALID_RESUME_JSON.encode()},
)
_add(
    "json_file_upload_that_lies_about_its_size",
    _message(document={"file_id": "f", "file_name": "resume.json", "file_size": 10}),
    env={"MAX_REQUEST_BODY_BYTES": "2000"},
    world={"document_bytes": webhook_fakes._VALID_RESUME_JSON.encode() + b" " * 5000},
)
_add(
    "document_that_is_not_json_falls_back_to_its_caption",
    {
        **_message("list", document={"file_id": "f", "file_name": "notes.txt"}),
    },
    sb={
        "applications": [webhook_fakes._APP_1, webhook_fakes._APP_2],
        "snapshots": [webhook_fakes._SNAP_1, webhook_fakes._SNAP_2],
    },
)

# tracking and listing
_add("track_a_job", _message(_JOB_PASTE))
_add(
    "track_a_job_with_markup_in_it",
    _message("Title: <b>Boss</b> & Co\nCompany: Smith & <script>\n\nDescription <i>here</i>."),
)
_add(
    "track_a_job_without_a_company",
    _message("Title: Staff AI Engineer\n\nWe need a Python engineer."),
)
_add(
    "list_applications",
    _message("list"),
    sb={
        "applications": [webhook_fakes._APP_1, webhook_fakes._APP_2],
        "snapshots": [webhook_fakes._SNAP_1, webhook_fakes._SNAP_2],
    },
)
_add("list_applications_when_empty", _message("list"), sb={"applications": []})
_add("apply_with_no_list_yet", _message("apply to #1"), sb={"working_sets": []})
_add(
    "apply_with_an_expired_list", _message("apply to #1"), sb={"working_sets": _WORKING_SET_EXPIRED}
)
_add(
    "apply_to_a_number_off_the_list", _message("apply to #5"), sb={"working_sets": _WORKING_SET_OK}
)

# generating a resume
_add(
    "apply_to_a_number_and_get_the_resume",
    _message("apply to #1"),
    sb=_PREP,
    world={"apply_body": _PREP_OK},
)
_add(
    "generate_button_delivers_the_resume_with_warnings",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={"apply_body": _PREP_OK},
)
_add(
    "generate_button_delivers_a_clean_resume",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={"apply_body": _PREP_CLEAN},
)
_add(
    "generate_button_when_the_engine_declines",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={"apply_body": _PREP_DECLINED},
)
_add(
    "generate_button_when_setup_is_missing",
    _callback(_PREP_CALLBACK),
    sb={**_PREP, "prefs": []},
    world={"apply_body": _PREP_OK},
)
_add(
    "generate_button_when_the_pdf_will_not_compile",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={"apply_body": _PREP_OK, "compile_status": 422},
)
_add(
    "generate_button_over_the_limit",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    rate_limit="deny",
    world={"apply_body": _PREP_OK},
)
_add(
    "generate_button_when_the_limiter_is_down",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    rate_limit="broken",
    world={"apply_body": _PREP_OK},
)

# generating a resume, when the one progress message cannot be edited
_add(
    "generate_button_when_the_progress_message_was_deleted",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={
        "apply_body": _PREP_OK,
        "edit_error": "Bad Request: message to edit not found",
    },
)
_add(
    "generate_button_when_the_progress_message_cannot_be_edited_and_the_engine_declines",
    _callback(_PREP_CALLBACK),
    sb=_PREP,
    world={
        "apply_body": _PREP_DECLINED,
        "edit_error": "Bad Request: message can't be edited",
    },
)

# /privacy and /learn
_WEB_ENV = {"WEB_APP_URL": "https://app.between-jobs.example"}
_add("privacy_for_a_linked_user", _message("/privacy"), sb={"existing_user": "web"}, env=_WEB_ENV)
_add("privacy_as_a_plain_word", _message("privacy"), sb={"existing_user": "web"}, env=_WEB_ENV)
_add("privacy_on_a_server_with_no_web_address", _message("/privacy"), sb={"existing_user": "web"})
_add("privacy_for_a_telegram_only_user", _message("/privacy"), env=_WEB_ENV)
_add(
    "learn_for_a_new_account",
    _message("/learn"),
    sb={
        "existing_user": "web",
        "profile": {"select_rows": []},
        "applications": [],
        "credentials": [],
        "saved_searches": [],
    },
    env=_WEB_ENV,
)
_add(
    "learn_for_an_account_a_resume_away_from_done",
    _message("/learn"),
    sb={
        "existing_user": "web",
        "profile": {"select_rows": [_CHECK_RESUME_ROW]},
        "applications": [{**webhook_fakes._APP_1, "id": "app-1", "source_channel": "telegram"}],
        "credentials": [{"service": "llm", "provider": "openrouter", "is_validated": True}],
        "saved_searches": [{"id": "search-1"}],
    },
    env=_WEB_ENV,
)
_add(
    "learn_for_a_finished_account_on_a_server_with_no_web_address",
    _message("learn"),
    sb={
        "existing_user": "web",
        "profile": {"select_rows": [_CHECK_RESUME_ROW]},
        "applications": [{**webhook_fakes._APP_1, "id": "app-1", "source_channel": "web"}],
        "credentials": [{"service": "llm", "provider": "openrouter", "is_validated": True}],
        "saved_searches": [],
        "resume_for": ["app-1"],
    },
)
_add(
    "learn_when_the_account_cannot_be_read",
    _message("/learn"),
    sb={
        "existing_user": "web",
        "profile": {"select_rows": [_CHECK_RESUME_ROW]},
        "unreadable": ["applications", "saved_searches"],
        "credentials": [{"service": "llm", "provider": "openrouter", "is_validated": True}],
    },
    env=_WEB_ENV,
)
_add("learn_for_a_telegram_only_user", _message("/learn"), env=_WEB_ENV)

# linking
_add("link_succeeds", _message("/link ABCD2345"), sb={"rpc_data": _LINK_OK}, finish="retired")
_add(
    "link_succeeds_but_the_old_account_is_not_retired",
    _message("/link ABCD2345"),
    sb={"rpc_data": _LINK_OK},
    finish="unretired",
)
_add(
    "link_succeeds_but_finishing_it_fails",
    _message("/link ABCD2345"),
    sb={"rpc_data": _LINK_OK},
    finish="raises",
)
_add(
    "link_resumed_after_a_crash",
    _message("/link ABCD2345"),
    sb={"rpc_data": _RESUMED, "identities": [{"user_id": TARGET}], "existing_user": "web"},
    finish="retired",
)
_add(
    "link_resumed_when_it_had_already_finished",
    _message("/link ABCD2345"),
    sb={"rpc_data": _RESUMED, "identities": [{"user_id": TARGET}], "existing_user": "web"},
    finish="already",
)
_add(
    "link_in_a_group_is_refused",
    {
        **_message("/link ABCD2345"),
        "message": {
            **_message("/link ABCD2345")["message"],
            "chat": {"id": TG, "type": "supergroup"},
        },
    },
)
_add("link_while_the_database_is_behind", _message("/link ABCD2345"), link_ready=False)
_add("link_gateway_error", _message("/link ABCD2345"), sb={"rpc_error": "gateway"})
_add("link_statement_timeout", _message("/link ABCD2345"), sb={"rpc_error": "timeout"})
_add("link_collision", _message("/link ABCD2345"), sb={"rpc_error": "collision"})
for _reason in (
    "invalid_code",
    "expired_code",
    "rate_limited",
    "source_mismatch",
    "source_already_linked",
    "target_linked_elsewhere",
):
    _add(
        f"link_refused_{_reason}",
        _message("/link WRONGONE"),
        sb={"rpc_data": {"ok": False, "reason": _reason}},
    )
_add("unlink_a_linked_account", _message("/unlink"), sb={"existing_user": "web"})
_add("unlink_a_telegram_only_account", _message("/unlink"))

# buttons
_add(
    "confirm_a_preview",
    _callback(f"profile:activate:{webhook_fakes._VERSION_ID}"),
    sb={
        "profile": {
            "select_rows": [],
            "update_row": {"id": webhook_fakes._VERSION_ID, "activated_at": "2026-08-06T00:00:00Z"},
        }
    },
)
_add(
    "confirm_a_preview_that_is_gone",
    _callback(f"profile:activate:{webhook_fakes._VERSION_ID}"),
    sb={"profile": {"select_rows": [], "update_row": None}},
)
_add(
    "cancel_a_preview",
    _callback(f"profile:cancel:{webhook_fakes._VERSION_ID}"),
    sb={"profile": {"select_rows": [_PENDING]}},
)
_add(
    "cancel_a_preview_twice",
    _callback(f"profile:cancel:{webhook_fakes._VERSION_ID}"),
    sb={"profile": {"select_rows": []}},
)
_add(
    "mark_as_applied",
    _callback(f"app:stage:{APPLICATION_ID}:applied"),
    sb={"rpc_data": _APPLIED_OK},
)
_add(
    "mark_as_applied_for_an_application_that_is_gone",
    _callback(f"app:stage:{APPLICATION_ID}:applied"),
    sb={"rpc_error": "stage_not_found"},
)
_add(
    "mark_as_a_forged_status",
    _callback(f"app:stage:{APPLICATION_ID}:<b>x</b>"),
    sb={"rpc_error": "stage_invalid"},
)
_add("button_with_data_nobody_recognizes", _callback("something:else"))

# delivery
_add("a_delivery_already_done", _message("hi"), ledger="done", update_id=900)
_add("a_delivery_still_running", _message("hi"), ledger="in_progress", update_id=901)
_add("a_wrong_secret", _message("hi"), secret="nope")
_add(
    "telegram_rejects_the_markup_and_it_is_resent_as_plain_text",
    _message("set up my resume"),
    world={"entity_error_marker": "Set up your resume"},
)
_add(
    "telegram_is_down_for_the_reply",
    _message("hi"),
    world={"telegram_down_on": "Send"},
    update_id=4243,
)


# -- running a scenario ----------------------------------------------------------------------


def _error(kind: str) -> APIError:
    return {
        "gateway": APIError({"message": "bad gateway", "code": 502}),
        "timeout": APIError({"message": "statement timeout", "code": "57014"}),
        "collision": APIError(
            {"message": "duplicate key", "code": "23505", "hint": None, "details": None}
        ),
        "stage_not_found": APIError(
            {"message": "application not found", "code": "P0001", "details": None, "hint": None}
        ),
        "stage_invalid": APIError(
            {"message": "invalid status", "code": "22023", "details": None, "hint": None}
        ),
    }[kind]


def _supabase(config: dict[str, Any]) -> ComposedSupabase:
    config = copy.deepcopy(config)
    kwargs: dict[str, Any] = {}
    if "identities" in config:
        kwargs["channel_identities_rows"] = config["identities"]
    kwargs["rpc_data"] = config.get("rpc_data")
    if "rpc_error" in config:
        kwargs["rpc_error"] = _error(config["rpc_error"])
    if "existing_user" in config:
        kwargs["existing_user"] = webhook_fakes._WEB_USER
    if "profile" in config:
        kwargs["profile_versions"] = webhook_fakes._FakeProfileVersionsTable(**config["profile"])
    for key, table in (
        ("applications", "applications"),
        ("snapshots", "job_snapshots"),
        ("working_sets", "working_sets"),
    ):
        if key in config:
            kwargs[table] = webhook_fakes._FakeSimpleTable(select_rows=config[key])
    supabase = ComposedSupabase(**kwargs)
    if "prefs" in config:
        supabase._extra["capability_preferences"] = prepare_fakes._FakeTable(
            select_rows=config["prefs"]
        )
    if "credentials" in config:
        supabase._extra["provider_credentials"] = prepare_fakes._FakeTable(
            select_rows=config["credentials"]
        )
    if "saved_searches" in config:
        supabase._extra["saved_searches"] = prepare_fakes._FakeTable(
            select_rows=config["saved_searches"]
        )
    if "resume_for" in config:
        supabase._extra["artifact_versions"] = prepare_fakes._FakeTable(
            select_rows=[
                {"artifact_id": artifact_id_for(a, "resume")} for a in config["resume_for"]
            ]
        )
    for name in config.get("unreadable", []):
        supabase._extra[name] = _Unreadable()
    return supabase


class _Unreadable:
    """A table whose every read fails, as a database that is down would."""

    def select(self, *_: Any, **__: Any) -> Any:
        raise ConnectionError("the database is unreachable")


def _finish_link(mode: str | None) -> Callable[..., Any]:
    async def finish(_supabase: Any, **_: Any) -> LinkCompletion:
        if mode == "raises":
            raise RuntimeError("storage exploded")
        if mode == "unretired":
            return LinkCompletion(
                already_complete=False, retired=False, leftover={"storage.objects": 1}
            )
        if mode == "already":
            return LinkCompletion(already_complete=True, retired=True)
        return LinkCompletion(already_complete=False, retired=True)

    return finish


def run_scenario(name: str, stack: Stack) -> dict[str, Any]:
    scenario = SCENARIOS[name]
    update = copy.deepcopy(scenario["update"])
    if "update_id" in scenario:
        update["update_id"] = scenario["update_id"]
    supabase = _supabase(scenario.get("sb", {}))
    if scenario.get("ledger"):
        asyncio.run(supabase.update_ledger.claim(update["update_id"]))
        if scenario["ledger"] == "done":
            asyncio.run(supabase.update_ledger.complete(update["update_id"]))

    world = World(**scenario.get("world", {}))
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handler))
    telegram = stack.client_class(http, "test-token-not-real")

    mode = scenario.get("rate_limit", "allow")

    async def claim(_supabase: Any, _user_id: str, _bucket: str) -> rate_limits.RateLimitDecision:
        if mode == "broken":
            raise ConnectionError("the limiter is unreachable")
        return rate_limits.RateLimitDecision(mode != "deny", 725 if mode == "deny" else 0)

    async def link_ready(_supabase: Any) -> bool:
        return bool(scenario.get("link_ready", True))

    app = stack.make_app()
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_http_client] = lambda: http
    app.dependency_overrides[get_telegram_client] = lambda: telegram
    app.dependency_overrides[get_webhook_secret] = lambda: SECRET
    headers = {}
    if scenario.get("secret", SECRET) is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = scenario.get("secret", SECRET)
    env = {"MAX_REQUEST_BODY_BYTES": "1048576", **scenario.get("env", {})}
    try:
        with ExitStack() as stack_:
            stack_.enter_context(mock.patch.dict(os.environ, env))
            # The web address is part of what /privacy and /learn say: a scenario that does not
            # give one has none, whatever the machine running the test has in its environment.
            if "WEB_APP_URL" not in env:
                os.environ.pop("WEB_APP_URL", None)
            stack_.enter_context(mock.patch.object(rate_limits, "claim_rate_limit_slot", claim))
            stack_.enter_context(mock.patch(f"{stack.patch_module}.link_schema_ready", link_ready))
            stack_.enter_context(
                mock.patch(
                    f"{stack.patch_module}.finish_link", _finish_link(scenario.get("finish"))
                )
            )
            # Leaving the client block runs the app's shutdown, which finishes any deferred work.
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.post("/telegram/webhook", json=update, headers=headers)
    finally:
        app.dependency_overrides.clear()
    return {
        "http": [
            response.status_code,
            response.json() if response.status_code < 500 else None,
            response.headers.get("retry-after"),
        ],
        "calls": world.telegram,
    }


def reduce(result: dict[str, Any]) -> dict[str, Any]:
    """What `run_scenario` saw, reduced to what the expected file holds."""
    return {"http": result["http"], "telegram": [summarize(c) for c in result["calls"]]}


def observed(name: str, stack: Stack) -> dict[str, Any]:
    return reduce(run_scenario(name, stack))
