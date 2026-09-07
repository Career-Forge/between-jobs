"""HTTP surface for the browser extension (browser-extension.md E1).

Selector-map serving is deliberately NOT here yet -- its real design
(signing, key management, D4's fail-closed verification behavior) depends
on what E2's unsigned dev-served map actually looks like once built, so it
lands in E3 alongside that work. This module only carries the parts that
don't need that decided first: resolving a tab's URL to an already-tracked
application (D3's hybrid page-detection), and known-question memory
(matching + saving `approved_answers`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from supabase import AsyncClient

from .app_state import get_supabase
from .applications_store import find_application_by_url
from .auth import require_user_id
from .extension_answers_store import match_approved_answer, save_approved_answer
from .models import MatchApprovedAnswerRequest, SaveApprovedAnswerRequest

router = APIRouter(prefix="/extension")


@router.get("/lookup")
async def lookup_application_by_url(
    url: str = Query(min_length=1),
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    application = await find_application_by_url(supabase, user_id, url)
    return {"application_id": application["id"] if application else None}


@router.post("/match-answer")
async def match_answer(
    body: MatchApprovedAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    answer = await match_approved_answer(
        supabase,
        user_id,
        normalized_question=body.normalized_question,
        canonical_intent=body.canonical_intent,
        jurisdiction=body.jurisdiction,
    )
    return {"answer": answer}


@router.post("/approved-answers", status_code=201)
async def save_answer(
    body: SaveApprovedAnswerRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    return await save_approved_answer(
        supabase,
        user_id,
        normalized_question=body.normalized_question,
        answer_text=body.answer_text,
        canonical_intent=body.canonical_intent,
        evidence_fact_ids=body.evidence_fact_ids,
        jurisdiction=body.jurisdiction,
        sensitive_category=body.sensitive_category,
        expires_at=body.expires_at,
    )
