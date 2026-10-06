"""HTTP surface for the tester programme: read your own enrollment, join,
withdraw. What the rows mean and how the optional gate works is in `tester_enrollment.py`.

All three act only on the caller (there is no id to pass). They answer with the same shape, so a
client can replace what it holds with whatever came back:

    {"enrolled": false}                                   no row at all
    {"enrolled", "role_cohort", "seniority", "needs_sponsorship", "consent_version",
     "consented_at", "withdrawn_at", "current_version", "needs_reconsent"}

Neither POST is behind the tester-programme gate (they are how a person gets past it); they share
the cheap-write limiter bucket `tester_enrollment`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from supabase import AsyncClient

from .app_state import get_supabase
from .auth import require_user_id
from .errors import ApiError
from .rate_limits import limit
from .tester_enrollment import (
    TESTER_AGREEMENT_VERSION,
    EnrollmentNotFound,
    RoleCohort,
    Seniority,
    enroll,
    enrollment_view,
    get_enrollment_row,
    withdraw,
)

router = APIRouter(prefix="/tester")


class EnrollRequest(BaseModel):
    """Strict: a field that is not listed is refused, and a value is never coerced (the string
    "true" is not a sponsorship answer). `needs_sponsorship` must be sent, as true, false or
    null: leaving it out is a client that forgot the question, not a person who declined it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role_cohort: RoleCohort
    seniority: Seniority
    needs_sponsorship: bool | None
    accept_version: str = Field(max_length=40)
    """The version of the agreement the person was shown. It must be the current one."""


@router.get("/enrollment")
async def get_my_enrollment(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    return enrollment_view(await get_enrollment_row(supabase, user_id))


@router.post("/enrollment", dependencies=[Depends(limit("tester_enrollment"))])
async def join_the_programme(
    body: EnrollRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Records consent to the current agreement. 409 CONFLICT when `accept_version` is not the
    current version: the agreement changed after the page loaded, so what the person agreed to
    is not what would be recorded. Nothing is written in that case."""
    if body.accept_version != TESTER_AGREEMENT_VERSION:
        raise ApiError(
            "CONFLICT",
            "The tester agreement changed since this page loaded. Reload the page, read the "
            "new version and accept it again.",
        )
    row = await enroll(
        supabase,
        user_id,
        role_cohort=body.role_cohort,
        seniority=body.seniority,
        needs_sponsorship=body.needs_sponsorship,
    )
    return enrollment_view(row)


@router.post("/enrollment/withdraw", dependencies=[Depends(limit("tester_enrollment"))])
async def withdraw_from_the_programme(
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Marks the caller's enrollment withdrawn. 404 when they have none. Withdrawing again
    changes nothing. It does not delete the account or any data."""
    try:
        row = await withdraw(supabase, user_id)
    except EnrollmentNotFound as e:
        raise ApiError("NOT_FOUND", "You are not in the tester programme.") from e
    return enrollment_view(row)
