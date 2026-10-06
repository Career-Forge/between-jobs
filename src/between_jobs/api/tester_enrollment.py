"""Tester programme enrollment: who has accepted the tester agreement, and the optional gate
that keeps the costly features closed until they have.

What is stored. One row per person in `tester_enrollments` (the table and its grants are in
`supabase/migrations/20261005162139_create_product_events_and_tester_enrollments.sql`): the role
cohort and seniority band they joined under, an optional answer to "would you need an employer to
sponsor your right to work" (null = not asked or declined, never read as "no", and read by nothing
but the operator's own reports: no product logic touches it, which
`tests/test_tester_enrollment.py` pins by scanning this package for the column's name), the
version of the agreement they accepted, when, and when they withdrew. Every write goes through
this module with the service-role client; a user token can only read its own row (RLS).

The agreement itself is `web/src/content/testerAgreement.ts`. TESTER_AGREEMENT_VERSION below is
the same string as the one in that file, and a test fails when the two differ: changing the
text means changing both, and everyone who accepted the old version is then no longer enrolled
(`counts_as_enrolled` compares versions) and is asked to accept the new one.

Withdrawing sets `withdrawn_at` and nothing else. It does not delete the row, the account or any
usage record, and joining again clears it.

THE GATE (`TESTER_PROGRAM_REQUIRED`). Off by default, so a self-hosted server and a developer's
machine behave exactly as before. A server that sets it to `on` refuses the costly features to a
signed-in person who is not enrolled: `403 ENROLLMENT_REQUIRED`, with a message that says to
join. The check is `enrollment_error_or_none`, and it lives in ONE place that every costly route
already passes through: `rate_limits.rate_limit_error_or_none`, which the per-user limiter
dependency (`limit(...)`) and the chat bot's resume generation both call before they count a
request. So the gate covers exactly the routes that carry a limiter, which is exactly the routes
that spend money or heavy compute (a test pins that every costly route has one). Two things sit
outside it on purpose, and are listed here so nobody has to wonder:

* The three routes of this module are not gated: they are how a person becomes enrolled, so
  gating them would lock everyone out. Their limiter bucket is in `UNGATED_BUCKETS`.
* `POST /extension/draft-answer` has its own older limiter and spends the person's AI key on
  every call, so it carries the gate as a dependency of its own (`require_enrollment`).

Everything cheap stays open: reading and editing one's own data, listing, `GET /capabilities`,
and deleting one's own account. Nobody is locked out of leaving.

Fail closed. If the enrollment lookup itself fails (the database is unreachable, the answer is
malformed), the request is REFUSED with a retryable 503, not let through. The rate limiter fails
open, because its job is protection and a limiter bug must not take the product down. This check is
a consent gate: letting a request through because the lookup broke would let someone use the
costly features without the consent being recorded, which is the one thing the gate exists to
stop. A request that reaches it needs the database for its real work anyway, so an outage that
fails the lookup was going to fail the request regardless.

Background work is NOT gated. The saved-search matcher and the Gmail reply checker run on a
schedule, for accounts that saved a search or connected Gmail, using that person's own keys;
there is no request to refuse, and this check does not reach them. Saving a search is a cheap
write that stays open, so on a server that requires the programme an unenrolled person's saved
search is still matched. A deployment that needs those closed too would give the workers the same
check; it is not done here.

The Tester Agreement says exactly this to the person (`web/src/content/testerAgreement.ts`,
"Leaving the programme": withdrawing stops them starting the costly features, and does not stop
saved searches or Gmail reply checking, which they pause, delete or disconnect themselves), and
so does the withdraw confirmation on the enrollment page. `tests/test_tester_enrollment_gate.py`
fails when either worker starts asking about enrollment, so that the day someone gives them the
check, this paragraph and those two texts are changed together.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, cast, get_args

from fastapi import Depends
from postgrest.exceptions import APIError

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .env import strict_on_off
from .errors import ApiError

logger = logging.getLogger(__name__)

TESTER_AGREEMENT_VERSION = "2026-10-06"
"""The version of web/src/content/testerAgreement.ts a person must have accepted. The same string
as the web constant of that name; tests/test_tester_enrollment.py fails when they differ."""

RoleCohort = Literal[
    "data_analyst",
    "data_engineer",
    "data_scientist",
    "ai_ml_engineer",
    "software_engineer",
    "frontend_engineer",
    "devops_sre",
    "qa_sdet",
    "product_manager",
    "business_analyst",
]
Seniority = Literal["new_grad", "early_career", "mid", "senior", "lead_plus"]
"""Both lists are mirrored, value for value, by the CHECK constraints on `tester_enrollments`, and
tests/test_tester_enrollment.py reads the migration and fails when either side changes alone."""

ROLE_COHORTS: frozenset[str] = frozenset(get_args(RoleCohort))
SENIORITIES: frozenset[str] = frozenset(get_args(Seniority))

_PROGRAM_REQUIRED_ENV = "TESTER_PROGRAM_REQUIRED"
_TABLE = "tester_enrollments"
_COLUMNS = "role_cohort, seniority, needs_sponsorship, consent_version, consented_at, withdrawn_at"
_FOREIGN_KEY_VIOLATION = "23503"
"""What an insert raises when the user id is not in `auth.users` (an account deleted while its
access token still verifies)."""

ENROLLMENT_REQUIRED_MESSAGE = (
    "Joining the tester programme comes first. Open the Between Jobs website, accept the "
    "tester agreement there, then try again."
)
LOOKUP_FAILED_MESSAGE = (
    "We could not check your tester enrollment just now, so nothing was started. "
    "Try again in a moment."
)


def tester_program_required() -> bool:
    """TESTER_PROGRAM_REQUIRED=on|off, default off. Parsed strictly (anything else stops the
    boot, see `env.strict_on_off`): this is a legal gate, and a spelling such as `=false` that a
    looser reading took as "set, so on" would close the product to everyone, while one read the
    other way would open it. Read on every use, so a test can change it; `app.lifespan` reads it
    once at startup so a bad value stops the boot instead of failing requests one by one."""
    return strict_on_off(_PROGRAM_REQUIRED_ENV, default=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EnrollmentNotFound(Exception):
    """The caller has no enrollment row (there is nothing to withdraw)."""


def counts_as_enrolled(row: dict[str, Any]) -> bool:
    """Whether this row lets its owner past the gate: consent to the CURRENT agreement, not
    withdrawn. The one definition, used by `is_enrolled` and by the answer the routes give."""
    return (
        row.get("withdrawn_at") is None and row.get("consent_version") == TESTER_AGREEMENT_VERSION
    )


def enrollment_view(row: dict[str, Any] | None) -> dict[str, Any]:
    """What the routes answer with. No row at all is `{"enrolled": false}`. With one, `enrolled`
    is `counts_as_enrolled`, and `needs_reconsent` says the person is a current tester (not
    withdrawn) whose accepted version is not the current one, so the web app can say "the
    agreement changed" instead of treating them like a stranger. A withdrawn person is not
    asked to reconsent: they are asked to join."""
    if row is None:
        return {"enrolled": False}
    withdrawn = row.get("withdrawn_at") is not None
    return {
        "enrolled": counts_as_enrolled(row),
        "role_cohort": row.get("role_cohort"),
        "seniority": row.get("seniority"),
        "needs_sponsorship": row.get("needs_sponsorship"),
        "consent_version": row.get("consent_version"),
        "consented_at": row.get("consented_at"),
        "withdrawn_at": row.get("withdrawn_at"),
        "current_version": TESTER_AGREEMENT_VERSION,
        "needs_reconsent": (not withdrawn)
        and row.get("consent_version") != TESTER_AGREEMENT_VERSION,
    }


async def get_enrollment_row(supabase: AsyncClient, user_id: str) -> dict[str, Any] | None:
    result = await supabase.table(_TABLE).select(_COLUMNS).eq("user_id", user_id).execute()
    rows = result.data
    if not rows:
        return None
    if not isinstance(rows, list) or not isinstance(rows[0], dict):
        raise RuntimeError("tester_enrollments returned an unexpected shape")
    return cast(dict[str, Any], rows[0])


async def is_enrolled(supabase: AsyncClient, user_id: str) -> bool:
    """A row exists, it is not withdrawn, and it records consent to the current version."""
    row = await get_enrollment_row(supabase, user_id)
    return row is not None and counts_as_enrolled(row)


async def enroll(
    supabase: AsyncClient,
    user_id: str,
    *,
    role_cohort: str,
    seniority: str,
    needs_sponsorship: bool | None,
) -> dict[str, Any]:
    """Records consent to the current agreement: creates the row, or rewrites the caller's own
    one (a new answer, a re-acceptance after a change, a return after withdrawing). Idempotent:
    sending the same thing twice leaves the same row, with the later time. `created_at` is not
    in the payload, so it keeps the day the person first joined."""
    payload = {
        "user_id": user_id,
        "role_cohort": role_cohort,
        "seniority": seniority,
        "needs_sponsorship": needs_sponsorship,
        "consent_version": TESTER_AGREEMENT_VERSION,
        "consented_at": _now(),
        "withdrawn_at": None,
    }
    try:
        await supabase.table(_TABLE).upsert(payload, on_conflict="user_id").execute()
    except APIError as e:
        if e.code == _FOREIGN_KEY_VIOLATION:
            raise ApiError("AUTH_REQUIRED", "This account no longer exists.") from e
        raise
    # Read back rather than trust the upsert's echo: what is stored is what is answered.
    row = await get_enrollment_row(supabase, user_id)
    if row is None:
        raise RuntimeError("tester_enrollments has no row right after an upsert")
    return row


async def withdraw(supabase: AsyncClient, user_id: str) -> dict[str, Any]:
    """Marks the caller's enrollment withdrawn. Raises `EnrollmentNotFound` when there is none.
    Idempotent: a second withdrawal keeps the first one's time."""
    row = await get_enrollment_row(supabase, user_id)
    if row is None:
        raise EnrollmentNotFound
    if row.get("withdrawn_at") is None:
        await (
            supabase.table(_TABLE)
            .update({"withdrawn_at": _now()})
            .eq("user_id", user_id)
            .is_("withdrawn_at", "null")
            .execute()
        )
        row = await get_enrollment_row(supabase, user_id)
        if row is None:
            # The account was deleted between the two reads.
            raise EnrollmentNotFound
    return row


# -- the gate ---------------------------------------------------------------------------------


async def enrollment_error_or_none(supabase: AsyncClient, user_id: str) -> ApiError | None:
    """None when the person may go ahead: the programme is not required on this server, or they
    are enrolled. 403 ENROLLMENT_REQUIRED when it is required and they are not; a retryable 503
    when the lookup failed (fail closed, see the module docstring). With the programme off it
    does nothing and asks the database nothing."""
    if not tester_program_required():
        return None
    try:
        enrolled = await is_enrolled(supabase, user_id)
    except Exception:
        logger.error(
            "tester enrollment lookup failed; refusing the request",
            exc_info=True,
            extra={"ctx": {"check": "tester_enrollment"}},
        )
        return ApiError("PROVIDER_UNAVAILABLE", LOOKUP_FAILED_MESSAGE, retryable=True)
    if enrolled:
        return None
    return ApiError("ENROLLMENT_REQUIRED", ENROLLMENT_REQUIRED_MESSAGE)


def require_enrollment(
    *, auth: Callable[..., Awaitable[str]] = require_user_id
) -> Callable[..., Awaitable[None]]:
    """The gate as a FastAPI dependency, for a costly route that does not carry the rate
    limiter (`limit`), which has the gate built in. `auth` is the dependency that yields the
    user id, as for `limit`."""

    async def enforce_enrollment(
        user_id: str = Depends(auth),
        supabase: AsyncClient = Depends(get_supabase),
    ) -> None:
        error = await enrollment_error_or_none(supabase, user_id)
        if error is not None:
            raise error

    return enforce_enrollment
