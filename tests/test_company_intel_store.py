"""Tests for company-intel persistence (Horizon Sprint 5.0)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api.company_intel_pipeline import Claim
from between_jobs.api.company_intel_store import create_run, get_claims_for_run, get_latest_run

_USER_ID = "00000000-0000-0000-0000-000000000001"
_APPLICATION_ID = "30000000-0000-0000-0000-000000000001"
_RUN_ID = "70000000-0000-0000-0000-000000000001"


class _ChainBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def eq(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def order(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    def limit(self, *_: Any, **__: Any) -> _ChainBuilder:
        return self

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeTable:
    def __init__(
        self, *, select_rows: list[dict[str, Any]], insert_row: dict[str, Any] | None = None
    ) -> None:
        self.select_rows = select_rows
        self.insert_row = insert_row
        self.insert_calls: list[Any] = []
        self._next_id = 1

    def select(self, *_: Any, **__: Any) -> _ChainBuilder:
        return _ChainBuilder(self.select_rows)

    def insert(self, data: Any) -> _ChainBuilder:
        # Mirrors real Postgrest insert-with-representation: every row
        # gets a DB-assigned id and becomes visible to a later select(),
        # same as `id uuid primary key default gen_random_uuid()` would.
        self.insert_calls.append(data)
        if isinstance(data, list):
            rows = []
            for row in data:
                rows.append({"id": f"row-{self._next_id}", **row})
                self._next_id += 1
        else:
            rows = [self.insert_row] if self.insert_row is not None else [{**data, "id": "row-1"}]
        self.select_rows.extend(rows)
        return _ChainBuilder(rows)


class _FakeSupabaseClient:
    def __init__(
        self,
        *,
        runs: _FakeTable | None = None,
        claims: _FakeTable | None = None,
    ) -> None:
        self.company_intel_runs = runs or _FakeTable(
            select_rows=[], insert_row={"id": _RUN_ID, "application_id": _APPLICATION_ID}
        )
        self.company_intel_claims = claims or _FakeTable(select_rows=[])
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> Any:
        return {
            "company_intel_runs": self.company_intel_runs,
            "company_intel_claims": self.company_intel_claims,
        }[name]

    def rpc(self, fn: str, params: dict[str, Any]) -> _FakeRpcBuilder:
        self.rpc_calls.append((fn, params))
        if fn == "create_company_intel_run":
            return self._create_company_intel_run(params)
        raise AssertionError(f"unexpected rpc: {fn}")

    def _create_company_intel_run(self, params: dict[str, Any]) -> _FakeRpcBuilder:
        """What `create_company_intel_run` does in one transaction: the run, then its claims."""
        run = self.company_intel_runs.insert(
            {
                "user_id": params["p_user_id"],
                "application_id": params["p_application_id"],
                "company_name": params["p_company_name"],
                "providers_used": params["p_providers_used"],
                "warnings": params["p_warnings"],
            }
        )._rows[0]
        if params["p_claims"]:
            self.company_intel_claims.insert(
                [{"run_id": run["id"], **claim} for claim in params["p_claims"]]
            )
        return _FakeRpcBuilder(run)


class _FakeRpcBuilder:
    def __init__(self, data: Any) -> None:
        self._data = data

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


def _claim() -> Claim:
    return Claim(
        category="product_and_mission",
        claim_text="Acme builds banking software.",
        source_url="https://acme.example",
        source_title="About Acme",
        confidence="high",
    )


async def test_create_run_inserts_the_run_and_its_claims() -> None:
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[_claim()],
        providers_used=["you_com"],
        warnings=[],
    )

    assert run["id"] == _RUN_ID
    assert len(supabase.company_intel_runs.insert_calls) == 1
    assert supabase.company_intel_runs.insert_calls[0]["company_name"] == "Acme"
    assert len(supabase.company_intel_claims.insert_calls) == 1
    inserted_claims = supabase.company_intel_claims.insert_calls[0]
    assert inserted_claims[0]["run_id"] == _RUN_ID
    assert inserted_claims[0]["category"] == "product_and_mission"


async def test_create_run_persists_claims_with_distinct_ids_even_when_identical() -> None:
    """Regression test: `Claim` (the pipeline's TypedDict) carries no
    `id` -- two claims that are otherwise identical (same category and
    text, which a real LLM synthesis pass can plausibly produce) must
    still come back as distinct, individually addressable rows once
    persisted, not collapse onto a shared id."""
    supabase = _FakeSupabaseClient()

    run = await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[_claim(), _claim()],
        providers_used=["you_com"],
        warnings=[],
    )

    persisted = await get_claims_for_run(supabase, run["id"])  # type: ignore[arg-type]
    ids = [row["id"] for row in persisted]
    assert len(ids) == 2
    assert all(ids)
    assert len(set(ids)) == len(ids)


async def test_create_run_is_one_database_call_and_never_writes_a_table_itself() -> None:
    """P0.10: the run and its claims are written by ONE function, in one transaction. A second
    request for the claims is how an empty dossier used to be left behind."""
    supabase = _FakeSupabaseClient()

    await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[_claim()],
        providers_used=["you_com", "firecrawl"],
        warnings=["note"],
    )

    assert [name for name, _ in supabase.rpc_calls] == ["create_company_intel_run"]
    params = supabase.rpc_calls[0][1]
    assert params == {
        "p_user_id": _USER_ID,
        "p_application_id": _APPLICATION_ID,
        "p_company_name": "Acme",
        "p_providers_used": ["you_com", "firecrawl"],
        "p_warnings": ["note"],
        "p_claims": [
            {
                "category": "product_and_mission",
                "claim_text": "Acme builds banking software.",
                "source_url": "https://acme.example",
                "source_title": "About Acme",
                "confidence": "high",
            }
        ],
    }


async def test_create_run_with_no_claims_sends_an_empty_list_not_null() -> None:
    supabase = _FakeSupabaseClient()

    await create_run(
        supabase,  # type: ignore[arg-type]
        _USER_ID,
        application_id=_APPLICATION_ID,
        company_name="Acme",
        claims=[],
        providers_used=[],
        warnings=["You.com key missing"],
    )

    assert supabase.rpc_calls[0][1]["p_claims"] == []
    assert supabase.company_intel_claims.insert_calls == []


async def test_create_run_lets_a_failure_from_the_database_propagate() -> None:
    class _Failing(_FakeSupabaseClient):
        def rpc(self, fn: str, params: dict[str, Any]) -> Any:
            raise RuntimeError("claims insert failed")

    with pytest.raises(RuntimeError, match="claims insert failed"):
        await create_run(
            _Failing(),  # type: ignore[arg-type]
            _USER_ID,
            application_id=_APPLICATION_ID,
            company_name="Acme",
            claims=[_claim()],
            providers_used=[],
            warnings=[],
        )


async def test_get_latest_run_returns_none_when_nothing_exists() -> None:
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[]))

    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]

    assert result is None


async def test_get_latest_run_returns_the_top_row() -> None:
    row = {"id": _RUN_ID, "company_name": "Acme"}
    supabase = _FakeSupabaseClient(runs=_FakeTable(select_rows=[row]))

    result = await get_latest_run(supabase, _USER_ID, _APPLICATION_ID)  # type: ignore[arg-type]

    assert result == row


async def test_get_claims_for_run_returns_the_rows() -> None:
    rows = [{"id": "claim-1", "category": "hiring_activity"}]
    supabase = _FakeSupabaseClient(claims=_FakeTable(select_rows=rows))

    result = await get_claims_for_run(supabase, _RUN_ID)  # type: ignore[arg-type]

    assert result == rows


# -- no fallback: a database without the function is a deploy error, not something to paper over --


class _RpcFails(_FakeSupabaseClient):
    def __init__(self, error: APIError) -> None:
        super().__init__()
        self._error = error

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        self.rpc_calls.append((fn, params))
        outer = self

        class _Call:
            async def execute(self) -> Any:
                raise outer._error

        return _Call()


@pytest.mark.parametrize(
    "code",
    [
        "PGRST202",  # the function is not in this database
        "23502",  # any other database error
    ],
)
async def test_a_database_error_propagates_and_nothing_is_written_outside_the_function(
    code: str,
) -> None:
    error = APIError({"message": "boom", "code": code, "hint": None, "details": None})
    supabase = _RpcFails(error)

    with pytest.raises(APIError) as raised:
        await create_run(
            supabase,  # type: ignore[arg-type]
            _USER_ID,
            application_id=_APPLICATION_ID,
            company_name="Acme",
            claims=[_claim()],
            providers_used=[],
            warnings=[],
        )

    assert raised.value.code == code
    assert supabase.company_intel_runs.insert_calls == []
    assert supabase.company_intel_claims.insert_calls == []
