"""Stand-ins for the model-provider tests: an in-memory credentials store, an HTTP client that
answers from a function, and the OpenAI SDK pointed at a mock transport.

Nothing here opens a socket. The in-memory store honours the `.eq(...)` filters the stores
really apply, so a test with two users shows the user-id filter doing its job (a fake that
returned every row would hide a missing one).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Coroutine
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from between_jobs.api import llm_client

Handler = (
    Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
)


def http_client(handler: Handler) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` (what the app hands the routes) that answers from `handler`."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def json_response(status: int, body: Any = None, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, json=body, **kwargs)


# -- an in-memory supabase ------------------------------------------------------------------------


class _Query:
    def __init__(self, db: FakeDb, name: str, op: str, payload: Any = None) -> None:
        self._db, self._name, self._op, self._payload = db, name, op, payload
        self._filters: list[tuple[str, Any]] = []

    def eq(self, column: str, value: Any) -> _Query:
        self._filters.append((column, value))
        return self

    def in_(self, column: str, values: list[Any]) -> _Query:  # not needed by the routes
        raise AssertionError("unexpected .in_()")

    def _matching(self) -> list[dict[str, Any]]:
        return [
            row
            for row in self._db.rows[self._name]
            if all(row.get(column) == value for column, value in self._filters)
        ]

    async def execute(self) -> SimpleNamespace:
        rows = self._db.rows[self._name]
        if self._op == "select":
            return SimpleNamespace(data=[dict(r) for r in self._matching()])
        if self._op == "delete":
            doomed = self._matching()
            self._db.rows[self._name] = [r for r in rows if r not in doomed]
            return SimpleNamespace(data=[])
        if self._op == "update":
            changed = self._matching()
            for row in changed:
                row.update(self._payload)
            return SimpleNamespace(data=[dict(r) for r in changed])
        if self._op == "upsert":
            keys = (
                ("user_id", "service", "provider")
                if self._name == "provider_credentials"
                else (
                    "user_id",
                    "capability",
                )
            )
            for row in rows:
                if all(row.get(k) == self._payload.get(k) for k in keys):
                    row.update(self._payload)
                    return SimpleNamespace(data=[dict(row)])
            new = {"id": f"{self._name}-{len(rows) + 1}", **self._payload}
            rows.append(new)
            return SimpleNamespace(data=[dict(new)])
        raise AssertionError(self._op)


class _Table:
    def __init__(self, db: FakeDb, name: str) -> None:
        self._db, self._name = db, name

    def select(self, *_: Any, **__: Any) -> _Query:
        return _Query(self._db, self._name, "select")

    def upsert(self, data: dict[str, Any], on_conflict: str = "") -> _Query:
        return _Query(self._db, self._name, "upsert", data)

    def update(self, data: dict[str, Any]) -> _Query:
        return _Query(self._db, self._name, "update", data)

    def delete(self) -> _Query:
        return _Query(self._db, self._name, "delete")


class _Rpc:
    def __init__(self, data: str) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


class FakeDb:
    """`provider_credentials` and `capability_preferences`, keyed the way the real tables are."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {
            "provider_credentials": [],
            "capability_preferences": [],
        }

    def table(self, name: str) -> _Table:
        if name not in self.rows:
            raise AssertionError(f"unexpected table {name}")
        return _Table(self, name)

    def rpc(self, fn: str, params: dict[str, Any]) -> _Rpc:
        if fn == "encrypt_secret":
            return _Rpc(f"enc[{params['p_plaintext']}]")
        if fn == "decrypt_secret":
            return _Rpc(str(params["p_ciphertext"])[4:-1])
        raise AssertionError(f"unexpected rpc {fn}")

    # -- seeding and reading, for tests ----------------------------------------------------------

    def add_credential(
        self,
        user_id: str,
        provider: str,
        secret: str,
        *,
        model: str | None = None,
        base_url: str | None = None,
        validated: bool = True,
    ) -> None:
        self.rows["provider_credentials"].append(
            {
                "id": f"cred-{len(self.rows['provider_credentials']) + 1}",
                "user_id": user_id,
                "service": "llm",
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "secret_encrypted": f"enc[{secret}]",
                "secret_2_encrypted": None,
                "scope": None,
                "is_validated": validated,
            }
        )

    def credentials_of(self, user_id: str) -> list[dict[str, Any]]:
        return [r for r in self.rows["provider_credentials"] if r["user_id"] == user_id]

    def preferences_of(self, user_id: str) -> list[dict[str, Any]]:
        return [r for r in self.rows["capability_preferences"] if r["user_id"] == user_id]


# -- the OpenAI SDK over a mock transport ----------------------------------------------------------


def _sdk_http() -> Any:
    import openai._exceptions as sdk_exceptions

    names = vars(sdk_exceptions)
    return names.get("httpx2") or names["httpx"]


SDK_HTTP = _sdk_http()


class SdkWire:
    """`llm_client.AsyncOpenAI` over a mock transport: every request the SDK sends is kept, and
    the options `generate` built the client with are kept too."""

    def __init__(self, handler: Callable[[Any], Any]) -> None:
        self.handler = handler
        self.requests: list[Any] = []
        self.built: list[dict[str, Any]] = []
        self.http_clients: list[Any] = []

    def _handle(self, request: Any) -> Any:
        self.requests.append(request)
        return self.handler(request)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> SdkWire:
        def build(**options: Any) -> openai.AsyncOpenAI:
            self.http_clients.append(options.pop("http_client", None))
            self.built.append(options)
            return openai.AsyncOpenAI(
                http_client=SDK_HTTP.AsyncClient(transport=SDK_HTTP.MockTransport(self._handle)),
                **options,
            )

        monkeypatch.setattr(llm_client, "AsyncOpenAI", build)
        llm_client._working_attempt.clear()
        return self

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def completion(text: str = "ok", finish_reason: str = "stop") -> Any:
    return SDK_HTTP.Response(
        200,
        json={
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {"role": "assistant", "content": text},
                }
            ],
        },
    )


def sdk_error(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Any:
    return SDK_HTTP.Response(status, json=body, headers=headers or {})


AsyncProbe = Callable[..., Awaitable[None]]
