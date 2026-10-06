"""Store-level cross-tenant cases for the per-user stores: capability preferences, provider
credentials, the extension's approved answers and sign-out watermark, link codes and the
Telegram identity lookup. Each function is called directly, as B, with A's ids."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

import pytest
from postgrest.exceptions import APIError

from between_jobs.api import (
    capability_preferences_store as prefs,
)
from between_jobs.api import (
    extension_answers_store as answers,
)
from between_jobs.api import (
    extension_auth,
    link_codes_store,
    telegram_identity,
)
from between_jobs.api import (
    provider_credentials_store as creds,
)

from . import cases_applications, cases_user  # noqa: F401  (importing registers their seeders)
from .harness import Ctx, StoreCase

_SERVICE = "search"
_PROVIDER = "serper"


async def _a_pref(ctx: Ctx) -> dict[str, Any]:
    await ctx.need("rls_capability_preference", ctx.a)
    rows = (
        await ctx.sb.table("capability_preferences")
        .select("*")
        .eq("user_id", ctx.a.user_id)
        .execute()
    ).data
    return dict(rows[-1])


async def _pref_rows(ctx: Ctx, user_id: str, capability: str) -> list[dict[str, Any]]:
    return list(
        (
            await ctx.sb.table("capability_preferences")
            .select("*")
            .eq("user_id", user_id)
            .eq("capability", capability)
            .execute()
        ).data
    )


async def _b_same_capability(ctx: Ctx, capability: str) -> None:
    await prefs.set_preference(
        ctx.sb,
        ctx.b.user_id,
        capability=capability,
        execution_mode="byok",
        provider="openrouter",
        model=ctx.mark(ctx.b, ctx.tag("b-model")),
    )


async def get_preference(ctx: Ctx) -> None:
    pref = await _a_pref(ctx)
    cap = pref["capability"]
    assert await prefs.get_preference(ctx.sb, ctx.b.user_id, cap) is None
    got = await prefs.get_preference(ctx.sb, ctx.a.user_id, cap)
    assert got is not None and got["id"] == pref["id"]
    # B holding the same capability name gets B's own row, never A's.
    await _b_same_capability(ctx, cap)
    own = await prefs.get_preference(ctx.sb, ctx.b.user_id, cap)
    assert own is not None and own["user_id"] == ctx.b.user_id and own["id"] != pref["id"]
    await (
        ctx.sb.table("capability_preferences")
        .delete()
        .eq("user_id", ctx.b.user_id)
        .eq("capability", cap)
        .execute()
    )


async def list_preferences(ctx: Ctx) -> None:
    pref = await _a_pref(ctx)
    await _b_same_capability(ctx, ctx.tag("b-cap"))
    b_rows = await prefs.list_preferences(ctx.sb, ctx.b.user_id)
    assert b_rows, "B's own preference is missing, so an empty-of-A's list proves nothing"
    assert all(r["user_id"] == ctx.b.user_id for r in b_rows)
    assert pref["id"] not in {r["id"] for r in b_rows}
    a_rows = await prefs.list_preferences(ctx.sb, ctx.a.user_id)
    assert pref["id"] in {r["id"] for r in a_rows}
    assert all(r["user_id"] == ctx.a.user_id for r in a_rows)


async def delete_preference(ctx: Ctx) -> None:
    pref = await _a_pref(ctx)
    cap = pref["capability"]
    await _b_same_capability(ctx, cap)
    await prefs.delete_preference(ctx.sb, ctx.b.user_id, cap)
    assert await _pref_rows(ctx, ctx.b.user_id, cap) == []  # B's own went
    assert await _pref_rows(ctx, ctx.a.user_id, cap) == [pref]  # A's untouched
    # B with no row of the capability at all: nothing of A's moves.
    await prefs.delete_preference(ctx.sb, ctx.b.user_id, cap)
    assert await _pref_rows(ctx, ctx.a.user_id, cap) == [pref]
    await prefs.delete_preference(ctx.sb, ctx.a.user_id, cap)
    assert await _pref_rows(ctx, ctx.a.user_id, cap) == []


# -- provider credentials ----------------------------------------------------------------------


async def _creds_rows(ctx: Ctx, user_id: str) -> list[dict[str, Any]]:
    return list(
        (
            await ctx.sb.table("provider_credentials")
            .select("*")
            .eq("user_id", user_id)
            .eq("service", _SERVICE)
            .eq("provider", _PROVIDER)
            .execute()
        ).data
    )


async def _clear_b_credential(ctx: Ctx) -> None:
    await (
        ctx.sb.table("provider_credentials")
        .delete()
        .eq("user_id", ctx.b.user_id)
        .eq("service", _SERVICE)
        .eq("provider", _PROVIDER)
        .execute()
    )


async def get_decrypted_credential(ctx: Ctx) -> None:
    seeded = await ctx.need("u_credential", ctx.a)
    await _clear_b_credential(ctx)
    with pytest.raises(creds.CredentialNotFound):
        await creds.get_decrypted_credential(
            ctx.sb, ctx.b.user_id, service=_SERVICE, provider=_PROVIDER
        )
    got = await creds.get_decrypted_credential(
        ctx.sb, ctx.a.user_id, service=_SERVICE, provider=_PROVIDER
    )
    assert got["secret"] == seeded["secret"] and got["model"] == seeded["model"]
    # B with their own key for the same (service, provider) gets B's, never A's.
    await creds.save_credential(
        ctx.sb,
        ctx.b.user_id,
        service=_SERVICE,
        provider=_PROVIDER,
        secret=ctx.mark(ctx.b, ctx.tag("b-secret")),
    )
    own = await creds.get_decrypted_credential(
        ctx.sb, ctx.b.user_id, service=_SERVICE, provider=_PROVIDER
    )
    assert own["secret"] != seeded["secret"] and own["model"] != seeded["model"]
    await _clear_b_credential(ctx)


async def list_credentials(ctx: Ctx) -> None:
    seeded = await ctx.need("u_credential", ctx.a)
    await _clear_b_credential(ctx)
    await creds.save_credential(
        ctx.sb,
        ctx.b.user_id,
        service=_SERVICE,
        provider=_PROVIDER,
        secret=ctx.mark(ctx.b, ctx.tag("b-secret")),
    )
    for kwargs in ({}, {"service": _SERVICE}):
        b_rows = await creds.list_credentials(ctx.sb, ctx.b.user_id, **kwargs)
        assert b_rows and all(r["user_id"] == ctx.b.user_id for r in b_rows)
        assert seeded["id"] not in {r["id"] for r in b_rows}
        a_rows = await creds.list_credentials(ctx.sb, ctx.a.user_id, **kwargs)
        assert seeded["id"] in {r["id"] for r in a_rows}
        assert all(r["user_id"] == ctx.a.user_id for r in a_rows)
    await _clear_b_credential(ctx)


async def delete_credential(ctx: Ctx) -> None:
    seeded = await ctx.need("u_credential", ctx.a)
    before = await _creds_rows(ctx, ctx.a.user_id)
    assert [r["id"] for r in before] == [seeded["id"]]
    await _clear_b_credential(ctx)
    await creds.save_credential(
        ctx.sb,
        ctx.b.user_id,
        service=_SERVICE,
        provider=_PROVIDER,
        secret=ctx.mark(ctx.b, ctx.tag("b-secret")),
    )
    await creds.delete_credential(ctx.sb, ctx.b.user_id, service=_SERVICE, provider=_PROVIDER)
    assert await _creds_rows(ctx, ctx.b.user_id) == []  # B's own went
    assert await _creds_rows(ctx, ctx.a.user_id) == before  # A's untouched
    await creds.delete_credential(ctx.sb, ctx.b.user_id, service=_SERVICE, provider=_PROVIDER)
    assert await _creds_rows(ctx, ctx.a.user_id) == before
    await creds.delete_credential(ctx.sb, ctx.a.user_id, service=_SERVICE, provider=_PROVIDER)
    assert await _creds_rows(ctx, ctx.a.user_id) == []


# -- the extension's approved answers ----------------------------------------------------------


async def _answer_row(ctx: Ctx, answer_id: str) -> dict[str, Any]:
    rows = (await ctx.sb.table("approved_answers").select("*").eq("id", answer_id).execute()).data
    assert len(rows) == 1
    return dict(rows[0])


async def match_approved_answer(ctx: Ctx) -> None:
    a = await ctx.need("app_approved_answer", ctx.a)
    # Tier 1 (exact question) and tier 2 (same intent), each with A's jurisdiction.
    assert (
        await answers.match_approved_answer(
            ctx.sb,
            ctx.b.user_id,
            normalized_question=a["question"],
            jurisdiction=a["jurisdiction"],
        )
        is None
    )
    assert (
        await answers.match_approved_answer(
            ctx.sb,
            ctx.b.user_id,
            normalized_question=ctx.tag("some other wording"),
            canonical_intent=a["intent"],
            jurisdiction=a["jurisdiction"],
        )
        is None
    )
    exact = await answers.match_approved_answer(
        ctx.sb, ctx.a.user_id, normalized_question=a["question"], jurisdiction=a["jurisdiction"]
    )
    assert exact is not None and exact["id"] == a["id"] and exact["answer_text"] == a["answer"]
    by_intent = await answers.match_approved_answer(
        ctx.sb,
        ctx.a.user_id,
        normalized_question=ctx.tag("some other wording"),
        canonical_intent=a["intent"],
        jurisdiction=a["jurisdiction"],
    )
    assert by_intent is not None and by_intent["id"] == a["id"]


async def save_approved_answer(ctx: Ctx) -> None:
    a = await ctx.need("app_approved_answer", ctx.a)
    before = await _answer_row(ctx, a["id"])
    b_text = ctx.mark(ctx.b, ctx.tag("B-answer"))
    saved = await answers.save_approved_answer(
        ctx.sb, ctx.b.user_id, normalized_question=a["question"], answer_text=b_text
    )
    # B gets a row of B's own: not A's row, and none of A's metadata merged into it.
    assert saved["user_id"] == ctx.b.user_id and saved["id"] != a["id"]
    assert saved["answer_text"] == b_text
    assert saved["canonical_intent"] is None and saved["jurisdiction"] is None
    assert await _answer_row(ctx, a["id"]) == before
    mine = (
        await ctx.sb.table("approved_answers")
        .select("id")
        .eq("user_id", ctx.a.user_id)
        .eq("normalized_question", a["question"])
        .execute()
    ).data
    assert [r["id"] for r in mine] == [a["id"]]
    # Control: as A the same call updates A's own row and keeps her metadata.
    own = await answers.save_approved_answer(
        ctx.sb, ctx.a.user_id, normalized_question=a["question"], answer_text="A edited"
    )
    assert own["id"] == a["id"] and own["canonical_intent"] == a["intent"]
    await ctx.sb.table("approved_answers").delete().eq("id", saved["id"]).execute()


async def record_answer_used(ctx: Ctx) -> None:
    a = await ctx.need("app_approved_answer", ctx.a)
    before = await _answer_row(ctx, a["id"])
    # A stranger is told "not found" and nothing moves.
    assert await answers.record_answer_used(ctx.sb, ctx.b.user_id, a["id"]) is False
    assert await _answer_row(ctx, a["id"]) == before
    # Control: the owner's report counts, and stamps when.
    assert await answers.record_answer_used(ctx.sb, ctx.a.user_id, a["id"]) is True
    after = await _answer_row(ctx, a["id"])
    assert after["times_used"] == before["times_used"] + 1
    assert before["last_used_at"] is None and after["last_used_at"] is not None
    # Reports that arrive together are all counted: the increment is one statement that takes the
    # row lock, not a read followed by a write (which would have both start from the same count).
    reports = 10
    results = await asyncio.gather(
        *(answers.record_answer_used(ctx.sb, ctx.a.user_id, a["id"]) for _ in range(reports))
    )
    assert results == [True] * reports
    assert (await _answer_row(ctx, a["id"]))["times_used"] == after["times_used"] + reports
    # ...and a stranger's reports, in the same burst, still move nothing.
    strangers = await asyncio.gather(
        *(answers.record_answer_used(ctx.sb, ctx.b.user_id, a["id"]) for _ in range(reports))
    )
    assert strangers == [False] * reports
    assert (await _answer_row(ctx, a["id"]))["times_used"] == after["times_used"] + reports


# -- extension sign-out watermark, link codes, telegram identity -------------------------------


async def get_extension_signed_out_at(ctx: Ctx) -> None:
    signed_out = datetime(2031, 5, 6, 7, 8, 9, tzinfo=UTC)
    await ctx.sb.table("extension_sign_outs").delete().eq("user_id", ctx.b.user_id).execute()
    await (
        ctx.sb.table("extension_sign_outs")
        .upsert({"user_id": ctx.a.user_id, "signed_out_at": signed_out.isoformat()})
        .execute()
    )
    assert await extension_auth.get_extension_signed_out_at(ctx.sb, ctx.b.user_id) is None
    assert await extension_auth.get_extension_signed_out_at(ctx.sb, ctx.a.user_id) == int(
        signed_out.timestamp()
    )
    await ctx.sb.table("extension_sign_outs").delete().eq("user_id", ctx.a.user_id).execute()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def _pending(ctx: Ctx, user_id: str) -> list[dict[str, Any]]:
    return list(
        (
            await ctx.sb.table("link_codes")
            .select("*")
            .eq("user_id", user_id)
            .eq("channel", "telegram")
            .is_("consumed_at", "null")
            .execute()
        ).data
    )


async def mint_code(ctx: Ctx) -> None:
    a_code = (await ctx.need("rls_link_code", ctx.a))["code"]
    a_rows = await _pending(ctx, ctx.a.user_id)
    assert [r["code_hash"] for r in a_rows] == [_hash(a_code)]
    b_code, _ = await link_codes_store.mint_code(ctx.sb, ctx.b.user_id, "telegram")
    # B's mint must neither invalidate A's pending code nor create a code under A.
    assert await _pending(ctx, ctx.a.user_id) == a_rows
    assert [r["code_hash"] for r in await _pending(ctx, ctx.b.user_id)] == [_hash(b_code)]
    # Control: A's own mint replaces A's pending code and leaves B's alone.
    new_code, _ = await link_codes_store.mint_code(ctx.sb, ctx.a.user_id, "telegram")
    assert [r["code_hash"] for r in await _pending(ctx, ctx.a.user_id)] == [_hash(new_code)]
    assert [r["code_hash"] for r in await _pending(ctx, ctx.b.user_id)] == [_hash(b_code)]
    await ctx.sb.table("link_codes").delete().eq("user_id", ctx.b.user_id).execute()


async def get_chat_id(ctx: Ctx) -> None:
    subject = (await ctx.need("rls_channel_identity", ctx.a))["subject"]
    b_rows = (
        await ctx.sb.table("channel_identities")
        .select("external_subject")
        .eq("user_id", ctx.b.user_id)
        .eq("channel", "telegram")
        .execute()
    ).data
    assert all(r["external_subject"] != subject for r in b_rows)
    got = await telegram_identity.get_chat_id(ctx.sb, ctx.b.user_id)
    assert got != int(subject)
    assert got is None if not b_rows else got == int(b_rows[0]["external_subject"])
    assert await telegram_identity.get_chat_id(ctx.sb, ctx.a.user_id) == int(subject)


STORE_CASES = [
    StoreCase("capability_preferences_store.delete_preference", delete_preference),
    StoreCase("capability_preferences_store.get_preference", get_preference),
    StoreCase("capability_preferences_store.list_preferences", list_preferences),
    StoreCase("provider_credentials_store.delete_credential", delete_credential),
    StoreCase("provider_credentials_store.get_decrypted_credential", get_decrypted_credential),
    StoreCase("provider_credentials_store.list_credentials", list_credentials),
    StoreCase("extension_answers_store.match_approved_answer", match_approved_answer),
    StoreCase("extension_answers_store.record_answer_used", record_answer_used),
    StoreCase("extension_answers_store.save_approved_answer", save_approved_answer),
    StoreCase("extension_auth.get_extension_signed_out_at", get_extension_signed_out_at),
    StoreCase("link_codes_store.mint_code", mint_code),
    StoreCase("telegram_identity.get_chat_id", get_chat_id),
]
_ = APIError
