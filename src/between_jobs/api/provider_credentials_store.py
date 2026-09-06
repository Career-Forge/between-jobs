"""Persistence for BYOK provider credentials (Sprint 2.7c) -- encrypted
storage backing Proposal §11's CredentialResolver (Sprint 2.7e).

Every function takes a verified user_id and filters by it explicitly --
this backend always uses the service-role client, which bypasses RLS, so
these WHERE clauses are the real enforcement boundary here, same
discipline as profile_store.py. RLS on provider_credentials is
defense-in-depth for a hypothetical future direct-client path.

Encryption/decryption is delegated to the encrypt_secret/decrypt_secret
Postgres functions (Sprint 2.7b) via `.rpc(...)` -- a plaintext secret
only exists in a Python variable for the single call that needs it (a
save, or a resolve at the moment of use), never written to a log.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


class CredentialNotFound(Exception):
    """No credential exists for that (user, service, provider)."""


async def save_credential(
    supabase: AsyncClient,
    user_id: str,
    *,
    service: str,
    provider: str,
    secret: str,
    model: str | None = None,
    base_url: str | None = None,
    secret_2: str | None = None,
    scope: str | None = None,
    is_validated: bool = False,
) -> dict[str, Any]:
    """Encrypts `secret` (and `secret_2`, if given) and upserts on
    (user_id, service, provider) -- saving again for the same
    service/provider replaces the stored key (a deliberate rotate, not a
    duplicate). `secret_2` is a generic second-value slot (Job Finder
    P4c) -- what it MEANS depends on the provider: Adzuna's `app_key`,
    USAJobs' registered email. Most providers never set it. `scope` is
    OAuth-specific (Gmail reply/status parsing, outreach-v2-search-
    first.md) -- the real scope string Google actually granted, captured
    from the token endpoint's own response rather than assumed from
    whatever was requested; every non-OAuth BYOK credential leaves it
    unset."""
    encrypted = await _encrypt(supabase, secret)
    encrypted_2 = await _encrypt(supabase, secret_2) if secret_2 is not None else None
    result = (
        await supabase.table("provider_credentials")
        .upsert(
            {
                "user_id": user_id,
                "service": service,
                "provider": provider,
                "model": model,
                "secret_encrypted": encrypted,
                "secret_2_encrypted": encrypted_2,
                "base_url": base_url,
                "scope": scope,
                "is_validated": is_validated,
            },
            on_conflict="user_id,service,provider",
        )
        .execute()
    )
    return cast(dict[str, Any], result.data[0])


async def list_credentials(
    supabase: AsyncClient, user_id: str, *, service: str | None = None
) -> list[dict[str, Any]]:
    """Returns credential rows WITHOUT decrypting -- callers that only
    need "does the user have X configured" (a settings page) never touch
    the plaintext at all."""
    query = supabase.table("provider_credentials").select("*").eq("user_id", user_id)
    if service is not None:
        query = query.eq("service", service)
    result = await query.execute()
    return cast(list[dict[str, Any]], result.data)


async def get_decrypted_credential(
    supabase: AsyncClient, user_id: str, *, service: str, provider: str
) -> dict[str, Any]:
    """The only function in this module that returns plaintext -- called
    at the moment a credential is actually needed (validation, or
    CredentialResolver resolving a BYOK credential for a real API call).
    Returns the full shape a caller needs to actually use the credential
    (provider, model, base_url, decrypted secret, decrypted secret_2 --
    None for the vast majority of providers that only have one, and
    scope -- OAuth-specific, None for every non-OAuth credential), not
    just the secret -- a resolver needs `base_url` too (e.g. a custom/
    self-hosted endpoint), and re-fetching the row separately would be a
    second round trip for data already in hand. An adversarial review
    caught that `scope` was written on save but never selected back here
    -- the one function every real Gmail-credential caller actually
    uses (contact_research_routes.py's push_outreach_to_gmail today; the
    reply-checker poller tomorrow) -- silently making it impossible for
    any caller to ever learn what was actually granted."""
    result = (
        await supabase.table("provider_credentials")
        .select("provider, model, base_url, scope, secret_encrypted, secret_2_encrypted")
        .eq("user_id", user_id)
        .eq("service", service)
        .eq("provider", provider)
        .execute()
    )
    if not result.data:
        raise CredentialNotFound(f"{service}/{provider}")
    row = cast(dict[str, Any], result.data[0])
    secret = await _decrypt(supabase, cast(str, row["secret_encrypted"]))
    secret_2 = (
        await _decrypt(supabase, cast(str, row["secret_2_encrypted"]))
        if row.get("secret_2_encrypted") is not None
        else None
    )
    return {
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row["base_url"],
        "scope": row.get("scope"),
        "secret": secret,
        "secret_2": secret_2,
    }


async def delete_credential(
    supabase: AsyncClient, user_id: str, *, service: str, provider: str
) -> None:
    await (
        supabase.table("provider_credentials")
        .delete()
        .eq("user_id", user_id)
        .eq("service", service)
        .eq("provider", provider)
        .execute()
    )


async def _encrypt(supabase: AsyncClient, plaintext: str) -> str:
    result = await supabase.rpc("encrypt_secret", {"p_plaintext": plaintext}).execute()
    return cast(str, result.data)


async def _decrypt(supabase: AsyncClient, ciphertext: str) -> str:
    result = await supabase.rpc("decrypt_secret", {"p_ciphertext": ciphertext}).execute()
    return cast(str, result.data)
