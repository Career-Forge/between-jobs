"""Tests for application/application-event persistence (Sprint 2.6c/2.6d)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.applications_store import (
    ApplicationNotFound,
    change_stage,
    create_application,
    get_application,
    get_event_by_idempotency_key,
    list_applications,
    record_event,
)

_USER_ID = "00000000-0000-0000-0000-000000000001"
_JOB_ID = "20000000-0000-0000-0000-000000000001"
_SNAPSHOT_ID = "20000000-0000-0000-0000-000000000002"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[dict[str, Any]] = []

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: dict[str, Any]) -> _ChainBuilder:
        self.insert_calls.append(data)
        rows = [self.insert_row] if self.insert_row is not None else []
        return _ChainBuilder(rows)


class _FakeRpcBuilder:
    def __init__(self, data: Any, error: APIError | None) -> None:
        self._data = data
        self._error = error

    async def execute(self) -> SimpleNamespace:
        if self._error:
            raise self._error
        return SimpleNamespace(data=self._data)


class _FakeSupabaseClient:
    def __init__(
        self,
        applications: _FakeTable,
        application_events: _FakeTable,
        *,
        event_outbox: _FakeTable | None = None,
        rpc_data: Any = None,
        rpc_error: APIError | None = None,
    ) -> None:
        self.applications = applications
        self.application_events = application_events
        self.event_outbox = event_outbox or _FakeTable(select_rows=[])
        self.rpc_data = rpc_data
        self.rpc_error = rpc_error
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        if name == "applications":
            return self.applications
        if name == "application_events":
            return self.application_events
        if name == "event_outbox":
            return self.event_outbox
        raise AssertionError(f"unexpected table: {name}")

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        return _FakeRpcBuilder(self.rpc_data, self.rpc_error)


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "job_id": _JOB_ID,
        "active_job_snapshot_id": _SNAPSHOT_ID,
        "source_channel": "web",
    }
    kwargs.update(overrides)
    return kwargs


async def test_create_application_inserts_row_and_created_event() -> None:
    new_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    new_event = {"id": "event-1", "event_type": "application.created"}
    applications = _FakeTable(select_rows=[], insert_row=new_app)
    events = _FakeTable(select_rows=[], insert_row=new_event)
    client = _FakeSupabaseClient(applications, events)

    result = await create_application(client, _USER_ID, **_create_kwargs())  # type: ignore[arg-type]

    assert result == new_app
    assert len(applications.insert_calls) == 1
    assert applications.insert_calls[0]["status"] == "saved"
    assert len(events.insert_calls) == 1
    assert events.insert_calls[0]["event_type"] == "application.created"
    assert events.insert_calls[0]["idempotency_key"] == f"application.created:{_APPLICATION_ID}"
    assert len(client.event_outbox.insert_calls) == 1
    outbox_row = client.event_outbox.insert_calls[0]
    assert outbox_row["event_type"] == "application.created.v1"
    assert outbox_row["aggregate_id"] == _APPLICATION_ID
    assert outbox_row["idempotency_key"] == f"outbox:application.created:{_APPLICATION_ID}"


async def test_create_application_idempotent_retry_does_not_double_publish_to_outbox() -> None:
    existing_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    applications = _FakeTable(select_rows=[], insert_row=existing_app)
    events = _FakeTable(select_rows=[{"id": "event-1"}])
    client = _FakeSupabaseClient(applications, events)

    await create_application(client, _USER_ID, **_create_kwargs())  # type: ignore[arg-type]

    assert client.event_outbox.insert_calls == []


async def test_create_application_is_idempotent_on_existing_user_job_pair() -> None:
    existing_app = {"id": _APPLICATION_ID, "user_id": _USER_ID, "status": "saved"}
    applications = _FakeTable(select_rows=[existing_app])
    events = _FakeTable(select_rows=[])
    client = _FakeSupabaseClient(applications, events)

    result = await create_application(client, _USER_ID, **_create_kwargs())  # type: ignore[arg-type]

    assert result == existing_app
    assert applications.insert_calls == []
    assert events.insert_calls == []


async def test_list_applications_returns_rows() -> None:
    rows = [{"id": _APPLICATION_ID}]
    client = _FakeSupabaseClient(_FakeTable(select_rows=rows), _FakeTable(select_rows=[]))
    result = await list_applications(client, _USER_ID)  # type: ignore[arg-type]
    assert result == rows


async def test_get_application_found() -> None:
    row = {"id": _APPLICATION_ID}
    client = _FakeSupabaseClient(_FakeTable(select_rows=[row]), _FakeTable(select_rows=[]))
    result = await get_application(client, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]
    assert result == row


async def test_get_application_not_found_raises() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[]))
    with pytest.raises(ApplicationNotFound):
        await get_application(client, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]


async def test_get_event_by_idempotency_key_found() -> None:
    existing_event = {"id": "event-1", "idempotency_key": "prepare-1", "payload": {"a": 1}}
    events = _FakeTable(select_rows=[existing_event])
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), events)

    result = await get_event_by_idempotency_key(client, _USER_ID, "prepare-1")  # type: ignore[arg-type]

    assert result == existing_event


async def test_get_event_by_idempotency_key_not_found_returns_none() -> None:
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), _FakeTable(select_rows=[]))

    result = await get_event_by_idempotency_key(client, _USER_ID, "prepare-1")  # type: ignore[arg-type]

    assert result is None


async def test_record_event_inserts_when_no_existing_key() -> None:
    new_event = {"id": "event-1"}
    events = _FakeTable(select_rows=[], insert_row=new_event)
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), events)

    result = await record_event(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        event_type="application.stage_changed",
        payload={"old_stage": "saved", "new_stage": "applied"},
        actor_type="user",
        actor_id=_USER_ID,
        idempotency_key="change-1",
    )

    assert result == new_event
    assert len(events.insert_calls) == 1
    assert events.insert_calls[0]["idempotency_key"] == "change-1"


async def test_record_event_is_idempotent_on_existing_key() -> None:
    existing_event = {"id": "event-1", "idempotency_key": "change-1"}
    events = _FakeTable(select_rows=[existing_event])
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), events)

    result = await record_event(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        event_type="application.stage_changed",
        payload={"old_stage": "saved", "new_stage": "applied"},
        actor_type="user",
        actor_id=_USER_ID,
        idempotency_key="change-1",
    )

    assert result == existing_event
    assert events.insert_calls == []


async def test_record_event_generates_a_key_when_none_given() -> None:
    new_event = {"id": "event-1"}
    events = _FakeTable(select_rows=[], insert_row=new_event)
    client = _FakeSupabaseClient(_FakeTable(select_rows=[]), events)

    await record_event(
        client,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        event_type="application.note_added",
        payload={},
        actor_type="system",
        actor_id="digest_worker",
    )

    assert len(events.insert_calls) == 1
    assert events.insert_calls[0]["idempotency_key"].startswith("application.note_added:")


async def test_change_stage_calls_the_rpc_with_expected_params() -> None:
    updated_app = {"id": _APPLICATION_ID, "status": "applied"}
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), _FakeTable(select_rows=[]), rpc_data=updated_app
    )

    result = await change_stage(
        client,  # type: ignore[arg-type]
        _USER_ID,
        _APPLICATION_ID,
        new_status="applied",
        idempotency_key="change-1",
    )

    assert result == updated_app
    assert len(client.rpc_calls) == 1
    fn, params = client.rpc_calls[0]
    assert fn == "change_application_stage"
    assert params == {
        "p_user_id": _USER_ID,
        "p_application_id": _APPLICATION_ID,
        "p_new_status": "applied",
        "p_idempotency_key": "change-1",
        "p_actor_type": "user",
        "p_actor_id": _USER_ID,
    }


async def test_change_stage_defaults_actor_id_to_user_id() -> None:
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), _FakeTable(select_rows=[]), rpc_data={}
    )

    await change_stage(
        client,  # type: ignore[arg-type]
        _USER_ID,
        _APPLICATION_ID,
        new_status="applied",
        idempotency_key="change-1",
        actor_type="system",
        actor_id="digest_worker",
    )

    _fn, params = client.rpc_calls[0]
    assert params["p_actor_type"] == "system"
    assert params["p_actor_id"] == "digest_worker"


async def test_change_stage_raises_application_not_found_on_raised_exception() -> None:
    error = APIError({"message": "application x not found for user y", "code": "P0001"})
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), _FakeTable(select_rows=[]), rpc_error=error
    )

    with pytest.raises(ApplicationNotFound):
        await change_stage(
            client,  # type: ignore[arg-type]
            _USER_ID,
            _APPLICATION_ID,
            new_status="applied",
            idempotency_key="change-1",
        )


async def test_change_stage_reraises_unrelated_api_errors() -> None:
    error = APIError({"message": "connection reset", "code": "08006"})
    client = _FakeSupabaseClient(
        _FakeTable(select_rows=[]), _FakeTable(select_rows=[]), rpc_error=error
    )

    with pytest.raises(APIError):
        await change_stage(
            client,  # type: ignore[arg-type]
            _USER_ID,
            _APPLICATION_ID,
            new_status="applied",
            idempotency_key="change-1",
        )
