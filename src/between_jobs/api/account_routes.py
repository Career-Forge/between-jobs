"""HTTP surface for deleting one's own account (launch plan P4.5)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from supabase import AsyncClient

from .account_deletion import (
    CONFIRMATION_PHRASE,
    AccountDeletionBlocked,
    delete_account,
    is_confirmed,
)
from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .errors import ApiError

router = APIRouter(prefix="/account")


class DeleteAccountRequest(BaseModel):
    """The person types the phrase themselves; nothing here deletes on a bare click."""

    confirm: str = Field(max_length=100)


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


@router.post("/delete", status_code=204)
async def delete_my_account(
    body: DeleteAccountRequest,
    request: Request,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> None:
    """Deletes the caller's account and everything they own, immediately and for good.

    Acts only on the caller (there is no id to pass), and only when the caller has typed the
    confirmation phrase. Answers 204 even when the account was already gone, so a request that
    was sent twice does not look like a failure the second time. 503 (retryable) means a stored
    file could not be removed and NOTHING was deleted: sending it again is safe."""
    if not is_confirmed(body.confirm):
        raise ApiError("INVALID_INPUT", f'Type "{CONFIRMATION_PHRASE}" to confirm.')
    try:
        await delete_account(supabase, http, user_id, access_token=_bearer_token(request))
    except AccountDeletionBlocked as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "We couldn't remove all of your stored files yet, so nothing was deleted. "
            "Try again in a minute.",
            retryable=True,
        ) from e
