"""The meta-tests for the cross-tenant suite (launch plan P4.7). They need no database, so
they run with the ordinary unit tests: a new route that has neither an isolation case nor a
recorded reason to have none fails here, before anyone can ship it."""

from __future__ import annotations

import re

from between_jobs.api.app import app

from .cross_tenant.harness import (
    SEEDERS,
    load_cases,
    load_not_user_scoped,
    load_store_cases,
    operations,
)
from .cross_tenant.ownership_scan import all_functions, ownership_filtered_functions

_ID_PARAM = re.compile(r"\{[^}]*\}")


def test_every_operation_has_a_case_or_a_recorded_reason() -> None:
    served = operations(app)
    cased = {case.operation for case in load_cases()}
    excused = set(load_not_user_scoped())

    assert served - cased - excused == set(), (
        "these operations have no cross-tenant case and no reason in NOT_USER_SCOPED: "
        f"{sorted(served - cased - excused)}"
    )
    assert (cased | excused) - served == set(), (
        "cases or excuses for operations the app no longer serves: "
        f"{sorted((cased | excused) - served)}"
    )


def test_no_operation_is_both_tested_and_excused_or_listed_twice() -> None:
    cases = load_cases()
    operations_listed = [case.operation for case in cases]
    assert len(operations_listed) == len(set(operations_listed)), "an operation has two cases"
    assert set(operations_listed).isdisjoint(load_not_user_scoped())


def test_only_shared_or_secret_operations_may_skip_a_case_while_taking_an_id() -> None:
    """An excuse is how a user-scoped route would hide, so an operation with a path parameter
    can only be excused as shared reference data or a secret-authenticated callback."""
    for operation, why in load_not_user_scoped().items():
        path = operation.split(" ", 1)[1]
        if _ID_PARAM.search(path):
            assert why.category in {"shared", "secret"}, (
                f"{operation} takes a path parameter but is excused as {why.category!r}"
            )
        assert why.reason.strip(), f"{operation} is excused without a reason"


def test_every_seeder_names_the_tables_it_fills() -> None:
    load_cases()
    for kind, seeder in SEEDERS.items():
        assert seeder.tables, f"seeder {kind!r} does not say which tables it fills"


def test_every_function_that_filters_by_the_caller_has_a_store_case() -> None:
    """The API reaches Postgres with the service role, so a query that forgets its `user_id`
    filter shows one user another's data. A new such query without a case fails here."""
    needed = set(ownership_filtered_functions())
    cased = [case.target for case in load_store_cases()]

    assert len(cased) == len(set(cased)), "a store function has two cases"
    assert needed - set(cased) == set(), (
        f"these functions filter by user_id and have no store case: {sorted(needed - set(cased))}"
    )
    # A case may also pin a guard that is not a direct `.eq("user_id", ...)` (a function that
    # calls an ownership lookup instead), but it must name a function that exists.
    unknown = set(cased) - all_functions()
    assert unknown == set(), f"store cases for functions that do not exist: {sorted(unknown)}"
