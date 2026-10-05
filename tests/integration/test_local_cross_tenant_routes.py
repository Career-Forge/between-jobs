"""Every user-scoped route, tried as a stranger (launch plan P4.7).

The real FastAPI app (its real lifespan, auth and stores) runs against a real local Supabase
stack with two real users. For each case in `cross_tenant/cases_*.py`, A's rows are seeded and
the request that targets them is sent with B's own access token: B must get a stranger's
answer, see nothing of A's, and leave A's data as it was. The same request as A is the
control. `test_cross_tenant_coverage.py` fails when an operation has neither a case nor a
reason to have none.

Nothing leaves the machine: `loopback_only` refuses any socket that is not the local stack.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from .conftest import World
from .cross_tenant.harness import Ctx, Pair, load_cases, run_case

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

_CASES = load_cases()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.operation)
async def test_a_stranger_gets_nothing_of_anothers(
    case: Any, tenant_client: httpx.AsyncClient, tenant_world: World, tenant_pair: Pair
) -> None:
    await run_case(case, Ctx(tenant_world, tenant_pair), tenant_client)
