"""Every store function that filters by the caller, called as a stranger (launch plan P4.7).

The route suite proves what a request does; this proves what each query does. A second wall
behind a route's own check (a facts list behind a version lookup, a helper only a worker calls)
can be removed and no route case will notice, so each function is exercised directly:
user B asks it for user A's rows and must get a stranger's answer, A must still get hers, and
A's data must be unchanged. `test_cross_tenant_coverage.py` fails when a function that filters
by `user_id` has no case here.

Run with `pytest -m local_supabase` after `supabase start` and `supabase db reset --local`."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import World
from .cross_tenant.harness import Ctx, Pair, load_store_cases

pytestmark = [pytest.mark.local_supabase, pytest.mark.asyncio(loop_scope="module")]

_CASES = load_store_cases()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.target)
async def test_a_stranger_gets_nothing_from_a_store_function(
    case: Any, tenant_world: World, tenant_pair: Pair
) -> None:
    await case.run(Ctx(tenant_world, tenant_pair))
