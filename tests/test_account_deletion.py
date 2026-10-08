"""Tests for deleting an account (launch plan P4.5): the order of the steps, which of them may
fail, and the one that must not.

Everything the service touches is a recording fake sharing one `events` list, so a test can say
"the files went before the user". The real thing (a real local stack, a real user, every table
and the bucket) is `tests/integration/test_local_account_deletion.py`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from supabase_auth.errors import AuthApiError

from between_jobs.api import account_deletion
from between_jobs.api.account_deletion import (
    AccountDeletionBlocked,
    delete_account,
    is_confirmed,
)
from between_jobs.api.provider_credentials_store import CredentialNotFound

_USER = "00000000-0000-0000-0000-0000000000a1"
_Paths = list[str]  # the fake bucket below has a method called `list`, which shadows the builtin
_Entries = list[dict[str, Any]]


class _Bucket:
    """A bucket holding `files` (full paths); lists them a level at a time like Storage does."""

    def __init__(
        self, events: list[Any], files: list[str], *, fail_on_remove: bool = False
    ) -> None:
        self.events = events
        self.files: _Paths = [*files]
        self.fail_on_remove = fail_on_remove
        self.removed_batches: list[_Paths] = []
        self.keep_after_remove = False  # simulate files that reappear

    async def list(self, prefix: str, options: dict[str, int]) -> _Entries:
        depth = len(prefix.split("/"))
        names: dict[str, bool] = {}  # name -> is a file
        for path in self.files:
            parts = path.split("/")
            if path.startswith(prefix + "/") and len(parts) > depth:
                is_file = len(parts) == depth + 1
                names[parts[depth]] = names.get(parts[depth], True) and is_file
        entries = [
            {"name": name, "id": f"id-{name}" if is_file else None}
            for name, is_file in sorted(names.items())
        ]
        offset, limit = options["offset"], options["limit"]
        return entries[offset : offset + limit]

    async def remove(self, paths: _Paths) -> None:
        if self.fail_on_remove:
            raise RuntimeError("storage is down")
        self.events.append(("remove", len(paths)))
        self.removed_batches.append([*paths])
        if not self.keep_after_remove:
            self.files = [f for f in self.files if f not in paths]


class _Admin:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.delete_error: Exception | None = None
        self.sign_out_error: Exception | None = None
        self.email: str | None = "leaving@example.com"
        self.lookup_error: Exception | None = None
        self.on_delete: Any = None  # called when the user is deleted: a generation still running

    async def sign_out(self, jwt: str, scope: str) -> None:
        self.events.append(("sign_out", jwt, scope))
        if self.sign_out_error:
            raise self.sign_out_error

    async def get_user_by_id(self, user_id: str) -> Any:
        self.events.append(("lookup", user_id))
        if self.lookup_error:
            raise self.lookup_error
        return SimpleNamespace(user=SimpleNamespace(email=self.email))

    async def delete_user(self, user_id: str) -> None:
        self.events.append(("delete_user", user_id))
        if self.delete_error:
            raise self.delete_error
        if self.on_delete:
            self.on_delete()


class _Query:
    def __init__(self, table: _Table) -> None:
        self.table = table
        self.filters: dict[str, Any] = {}
        self.is_delete = False

    def select(self, *_: Any) -> _Query:
        return self

    def delete(self) -> _Query:
        self.is_delete = True
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self.filters[column] = value
        return self

    async def execute(self) -> Any:
        rows = [r for r in self.table.rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.is_delete:
            self.table.events.append(("purge", self.table.name, dict(self.filters)))
            for row in rows:
                self.table.rows.remove(row)
        return SimpleNamespace(data=rows)


class _Table:
    def __init__(self, events: list[Any], name: str, rows: list[dict[str, Any]]) -> None:
        self.events = events
        self.name = name
        self.rows = rows
        self.select_error: Exception | None = None

    def select(self, *args: Any) -> _Query:
        if self.select_error:
            raise self.select_error
        return _Query(self).select(*args)

    def delete(self) -> _Query:
        return _Query(self).delete()


class _Rpc:
    def __init__(self, data: Any, error: Exception | None = None) -> None:
        self.data = data
        self.error = error

    async def execute(self) -> Any:
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class _Supabase:
    def __init__(
        self,
        events: list[Any],
        *,
        files: list[str] | None = None,
        identities: list[dict[str, Any]] | None = None,
        attempts: list[dict[str, Any]] | None = None,
        leftovers: dict[str, int] | None = None,
    ) -> None:
        self.events = events
        self.bucket = _Bucket(events, files or [])
        self.admin = _Admin(events)
        self.auth = SimpleNamespace(admin=self.admin)
        self.storage = SimpleNamespace(from_=lambda name: self.bucket)
        self.tables = {
            "channel_identities": _Table(events, "channel_identities", identities or []),
            "link_code_attempts": _Table(events, "link_code_attempts", attempts or []),
        }
        self.leftovers = leftovers or {}
        self.purge_error: Exception | None = None
        self.audit_entries = 4

    def table(self, name: str) -> _Table:
        return self.tables[name]

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        if name == "purge_user_auth_traces":
            self.events.append(("purge_audit", params["p_user_id"], params["p_email"]))
            return _Rpc(self.audit_entries, self.purge_error)
        assert name == "user_owned_row_counts" and params == {"p_user_id": _USER}
        self.events.append(("check",))
        return _Rpc(self.leftovers)


def _google(events: list[Any], status: int = 200, body: str = "") -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        events.append(("revoke", str(request.url), request.content.decode()))
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    log: list[Any] = []

    async def credential(*_: Any, **__: Any) -> dict[str, Any]:
        return {"secret": "refresh-token-1", "scope": "gmail.readonly"}

    async def extension_sign_out(_: Any, user_id: str) -> None:
        log.append(("extension_sign_out", user_id))

    monkeypatch.setattr(account_deletion, "get_decrypted_credential", credential)
    monkeypatch.setattr(account_deletion, "record_extension_sign_out", extension_sign_out)
    return log


def _kinds(events: list[Any]) -> list[str]:
    seen: list[str] = []
    for event in events:
        if event[0] not in seen:
            seen.append(event[0])
    return seen


# -- the order ---------------------------------------------------------------------------------


async def test_the_steps_run_in_the_documented_order(events: list[Any]) -> None:
    supabase = _Supabase(
        events,
        files=[f"{_USER}/a/1.pdf", f"{_USER}/a/2.pdf"],
        identities=[{"channel": "telegram", "external_subject": "42", "user_id": _USER}],
        attempts=[{"channel": "telegram", "external_subject": "42"}],
    )

    report = await delete_account(
        supabase,  # type: ignore[arg-type]
        _google(events),
        _USER,
        access_token="jwt-1",
    )

    assert _kinds(events) == [
        "remove",
        "revoke",
        "extension_sign_out",
        "sign_out",
        "purge",
        "lookup",
        "delete_user",
        "purge_audit",
        "check",
    ]
    assert report.gmail_revoked is True
    assert report.files_removed == 2
    assert report.link_attempts_purged == 1
    assert report.user_deleted is True
    assert report.audit_entries_purged == 4
    assert report.leftover_tables == {}
    # the audit log is purged AFTER the deletion (which writes an entry of its own), with the
    # email read BEFORE it (afterwards it can no longer be looked up)
    assert events.index(("lookup", _USER)) < events.index(("delete_user", _USER))
    assert ("purge_audit", _USER, "leaving@example.com") in events
    revoke = next(e for e in events if e[0] == "revoke")
    assert revoke[1] == account_deletion.GOOGLE_REVOKE_URL
    assert "refresh-token-1" in revoke[2]  # the stored refresh token is what is revoked
    assert ("sign_out", "jwt-1", "global") in events


async def test_a_person_without_gmail_skips_the_revoke(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def none(*_: Any, **__: Any) -> dict[str, Any]:
        raise CredentialNotFound("oauth/gmail")

    monkeypatch.setattr(account_deletion, "get_decrypted_credential", none)

    report = await delete_account(_Supabase(events), _google(events), _USER)  # type: ignore[arg-type]

    assert report.gmail_revoked is None
    assert "revoke" not in _kinds(events)
    assert report.user_deleted is True


# -- best effort: these may fail and the account is still deleted ------------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, "", True),
        (400, '{"error": "invalid_token"}', True),  # already revoked or expired: nothing to do
        (400, '{"error": "invalid_request"}', False),
        (500, "", False),
    ],
)
async def test_what_google_says_is_reported_and_never_blocks(
    events: list[Any], status: int, body: str, expected: bool
) -> None:
    report = await delete_account(
        _Supabase(events),  # type: ignore[arg-type]
        _google(events, status, body),
        _USER,
    )

    assert report.gmail_revoked is expected
    assert report.user_deleted is True


async def test_google_being_unreachable_does_not_block(events: list[Any]) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    http = httpx.AsyncClient(transport=httpx.MockTransport(down))

    report = await delete_account(_Supabase(events), http, _USER)  # type: ignore[arg-type]

    assert report.gmail_revoked is False
    assert report.user_deleted is True


async def test_a_session_that_cannot_be_ended_does_not_block(
    events: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any) -> None:
        raise RuntimeError("table missing")

    monkeypatch.setattr(account_deletion, "record_extension_sign_out", broken)
    supabase = _Supabase(events)
    supabase.admin.sign_out_error = RuntimeError("gotrue is down")

    report = await delete_account(
        supabase,  # type: ignore[arg-type]
        _google(events),
        _USER,
        access_token="jwt-1",
    )

    assert report.user_deleted is True


async def test_no_token_means_no_global_sign_out_but_the_extension_is_still_signed_out(
    events: list[Any],
) -> None:
    await delete_account(_Supabase(events), _google(events), _USER)  # type: ignore[arg-type]

    assert "sign_out" not in _kinds(events)
    assert ("extension_sign_out", _USER) in events


# -- the one that must not fail ----------------------------------------------------------------


async def test_files_are_removed_across_folders_and_in_batches_of_a_hundred(
    events: list[Any],
) -> None:
    files = [f"{_USER}/app-{i % 7}/v{i}.pdf" for i in range(250)] + [f"{_USER}/top.pdf"]
    supabase = _Supabase(events, files=files)

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.files_removed == 251
    assert supabase.bucket.files == []
    assert max(len(batch) for batch in supabase.bucket.removed_batches) <= 100
    assert len(supabase.bucket.removed_batches) >= 3


async def test_only_this_users_folder_is_touched(events: list[Any]) -> None:
    other = "00000000-0000-0000-0000-0000000000b2"
    supabase = _Supabase(events, files=[f"{_USER}/mine.pdf", f"{other}/theirs.pdf"])

    await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert supabase.bucket.files == [f"{other}/theirs.pdf"]


async def test_a_file_that_cannot_be_removed_stops_everything_and_nothing_is_deleted(
    events: list[Any],
) -> None:
    supabase = _Supabase(events, files=[f"{_USER}/a.pdf"])
    supabase.bucket.fail_on_remove = True

    with pytest.raises(AccountDeletionBlocked):
        await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    # nothing at all ran: no connection was cut and no session ended
    assert events == []
    assert supabase.bucket.files == [f"{_USER}/a.pdf"]


async def test_files_that_keep_reappearing_stop_everything_after_a_hard_cap(
    events: list[Any],
) -> None:
    supabase = _Supabase(events, files=[f"{_USER}/a.pdf"])
    supabase.bucket.keep_after_remove = True

    with pytest.raises(AccountDeletionBlocked):
        await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert "delete_user" not in _kinds(events)
    assert len(supabase.bucket.removed_batches) == account_deletion._MAX_REMOVE_PASSES


# -- the user, and what is left ----------------------------------------------------------------


async def test_a_user_who_is_already_gone_is_a_success_not_an_error(events: list[Any]) -> None:
    supabase = _Supabase(events)
    supabase.admin.delete_error = AuthApiError("User not found", 404, "user_not_found")

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.user_deleted is False


async def test_any_other_failure_to_delete_the_user_is_raised(events: list[Any]) -> None:
    supabase = _Supabase(events)
    supabase.admin.delete_error = AuthApiError("boom", 500, "unexpected_failure")

    with pytest.raises(AuthApiError):
        await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]


async def test_only_the_users_own_lockout_counters_are_forgotten(events: list[Any]) -> None:
    supabase = _Supabase(
        events,
        identities=[{"channel": "telegram", "external_subject": "42", "user_id": _USER}],
        attempts=[
            {"channel": "telegram", "external_subject": "42"},
            {"channel": "telegram", "external_subject": "99"},  # someone else's chat
        ],
    )

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.link_attempts_purged == 1
    assert supabase.tables["link_code_attempts"].rows == [
        {"channel": "telegram", "external_subject": "99"}
    ]


async def test_a_discord_identitys_lockout_counter_is_forgotten_and_only_that_channels(
    events: list[Any],
) -> None:
    """The counters are keyed by (channel, subject); the same id on another channel, and another
    person's Discord counter, stay."""
    supabase = _Supabase(
        events,
        identities=[
            {"channel": "telegram", "external_subject": "42", "user_id": _USER},
            {"channel": "discord", "external_subject": "777000111222333444", "user_id": _USER},
        ],
        attempts=[
            {"channel": "telegram", "external_subject": "42"},
            {"channel": "discord", "external_subject": "777000111222333444"},
            {"channel": "discord", "external_subject": "42"},  # the same text, another channel
            {"channel": "discord", "external_subject": "999000111222333444"},  # someone else
        ],
    )

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.link_attempts_purged == 2
    assert supabase.tables["link_code_attempts"].rows == [
        {"channel": "discord", "external_subject": "42"},
        {"channel": "discord", "external_subject": "999000111222333444"},
    ]


async def test_rows_left_behind_are_reported_and_logged_loudly(
    events: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    supabase = _Supabase(events, leftovers={"public.some_new_table": 3})

    with caplog.at_level("ERROR"):
        report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.leftover_tables == {"public.some_new_table": 3}
    assert "rows remain" in caplog.text


# -- the confirmation phrase -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "ok"),
    [
        ("delete my account", True),
        ("  Delete   My ACCOUNT  ", True),
        ("delete my account.", False),
        ("delete account", False),
        ("", False),
        ("yes", False),
    ],
)
def test_the_phrase_must_be_typed_out(typed: str, ok: bool) -> None:
    assert is_confirmed(typed) is ok


# -- the audit log -----------------------------------------------------------------------------


async def test_a_failing_audit_log_purge_is_reported_not_raised(
    events: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    supabase = _Supabase(events)
    supabase.purge_error = RuntimeError("function missing")

    with caplog.at_level("ERROR"):
        report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.user_deleted is True
    assert report.audit_entries_purged is None
    assert "audit log" in caplog.text


async def test_an_email_that_cannot_be_read_still_purges_by_id(events: list[Any]) -> None:
    supabase = _Supabase(events)
    supabase.admin.lookup_error = RuntimeError("gotrue is down")

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert ("purge_audit", _USER, None) in events
    assert report.audit_entries_purged == 4


# -- uploads that race the deletion ------------------------------------------------------------


async def test_a_file_uploaded_while_the_account_is_being_deleted_is_swept_afterwards(
    events: list[Any],
) -> None:
    """A generation still running when deletion began uploads after the first listing; its row
    cascades away with the user, so the file is all that is left."""
    supabase = _Supabase(events, files=[f"{_USER}/a.pdf"])
    supabase.admin.on_delete = lambda: supabase.bucket.files.append(f"{_USER}/late/b.pdf")

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.files_removed == 1
    assert report.late_files_removed == 1
    assert supabase.bucket.files == []
    # the sweep is after the deletion, and before the audit log and the final check
    kinds = _kinds(events)
    assert kinds.index("delete_user") < kinds.index("purge_audit")
    assert events.index(("delete_user", _USER)) < events.index(
        ("remove", 1), events.index(("delete_user", _USER))
    )


async def test_a_sweep_that_fails_is_logged_loudly_and_never_undoes_the_deletion(
    events: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    supabase = _Supabase(events)

    def late_upload_and_storage_down() -> None:
        supabase.bucket.files.append(f"{_USER}/late.pdf")
        supabase.bucket.fail_on_remove = True

    supabase.admin.on_delete = late_upload_and_storage_down

    with caplog.at_level("ERROR"):
        report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.user_deleted is True
    assert report.late_files_removed == 0
    assert "files may remain" in caplog.text


async def test_a_lockout_counter_that_cannot_be_forgotten_does_not_stop_the_deletion(
    events: list[Any],
) -> None:
    supabase = _Supabase(events)
    supabase.tables["channel_identities"].select_error = RuntimeError("postgrest is down")

    report = await delete_account(supabase, _google(events), _USER)  # type: ignore[arg-type]

    assert report.link_attempts_purged == 0
    assert report.user_deleted is True
