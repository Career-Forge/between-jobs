"""Store-level stranger cases for account deletion (launch plan P4.5)."""

from __future__ import annotations

from between_jobs.api import account_deletion

from .harness import Ctx, StoreCase


async def _attempts(ctx: Ctx, subject: str) -> int:
    rows = (
        await ctx.sb.table("link_code_attempts")
        .select("external_subject")
        .eq("channel", "telegram")
        .eq("external_subject", subject)
        .execute()
    ).data
    return len(rows)


async def _forget_link_attempts(ctx: Ctx) -> None:
    """The lockout counters are keyed by the Telegram subject, not by the user, so deleting a user
    does not reach them. Forgetting a user's must touch only the subjects of THEIR linked chats."""
    # a user may have only one Telegram identity and another case may have linked one already
    for tenant in (ctx.a, ctx.b):
        await (
            ctx.sb.table("channel_identities")
            .delete()
            .eq("user_id", tenant.user_id)
            .eq("channel", "telegram")
            .execute()
        )
    a = await ctx.need("rls_channel_identity", ctx.a)
    b = await ctx.need("rls_channel_identity", ctx.b)
    try:
        for subject in (a["subject"], b["subject"]):
            await (
                ctx.sb.table("link_code_attempts")
                .insert({"channel": "telegram", "external_subject": subject, "failed_count": 1})
                .execute()
            )

        purged_b = await account_deletion._forget_link_attempts(ctx.sb, ctx.b.user_id)

        assert purged_b >= 1
        assert await _attempts(ctx, b["subject"]) == 0, "B's own counter was not forgotten"
        assert await _attempts(ctx, a["subject"]) == 1, "B's purge reached A's linked chat"
        # the control: the same call as A forgets A's
        purged_a = await account_deletion._forget_link_attempts(ctx.sb, ctx.a.user_id)
        assert purged_a >= 1
        assert await _attempts(ctx, a["subject"]) == 0
    finally:
        # a user may have only one Telegram identity, and the two users persist across cases:
        # leave nothing behind for the next case that links one
        subjects = [a["subject"], b["subject"]]
        await (
            ctx.sb.table("link_code_attempts").delete().in_("external_subject", subjects).execute()
        )
        await (
            ctx.sb.table("channel_identities").delete().in_("external_subject", subjects).execute()
        )


STORE_CASES = [
    StoreCase("account_deletion._forget_link_attempts", _forget_link_attempts),
]
