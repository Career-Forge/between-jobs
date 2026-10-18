"""Proposal §11's CredentialResolver (Sprint 2.7e).

Joins capability_preferences (Sprint 2.7d, "what should this capability
use") to provider_credentials (Sprint 2.7c, "the secret that makes it
possible") in Python, at the moment of resolution -- never denormalized,
never cached across calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from supabase import AsyncClient

from .capability_preferences_store import DEFAULT_CAPABILITY, get_preference
from .errors import ApiError
from .provider_credentials_store import (
    CredentialNotFound,
    get_decrypted_credential,
    list_credentials,
)
from .provider_registry import PROVIDERS, BaseUrlRejected, effective_base_url

_DEFAULT_SERVICE = "llm"
"""`resolve()`'s original -- and still overwhelmingly common -- shape:
one capability, one required credential, SETUP_REQUIRED if absent. Now a
parameter (Horizon Sprint 5.0's company_intel capability is the first
consumer to pass something other than "llm") rather than a hardcoded
module constant, per this docstring's own earlier note anticipating
exactly this moment."""


@dataclass(frozen=True)
class ResolvedCredential:
    provider: str
    model: str
    secret: str
    base_url: str | None
    source: str
    """Always "byok" today -- see resolve()'s docstring for why the other
    two values from Proposal §11 ("hosted_credit", "self_host") aren't
    reachable yet."""


def llm_setup_state(row: dict[str, object]) -> str:
    """Where a saved model key stands: `ready` (checked, and a model is chosen), `needs_model`
    (checked, no model chosen yet: it cannot be used until one is) or `needs_key` (not checked,
    so not usable). The Integrations page and the setup-required errors say the same thing."""
    if row.get("is_validated") is not True:
        return "needs_key"
    return "ready" if row.get("model") else "needs_model"


def _needs_model_error(capability: str, provider_id: str) -> ApiError:
    spec = PROVIDERS.get(provider_id)
    name = spec.name if spec else provider_id
    return ApiError(
        "SETUP_REQUIRED",
        f"Your {name} key is saved, but no model is chosen for it yet. Choose one in Integrations.",
        capability=capability,
        missing=["model"],
        settings_path=_settings_path(capability),
        details={"provider": provider_id, "state": "needs_model"},
    )


async def _provider_awaiting_a_model(
    supabase: AsyncClient, user_id: str, service: str
) -> str | None:
    """The provider of a checked key that has no model, if the person has one: the usual reason
    a person with a key still has no preference (the key is saved first, the model is chosen
    second)."""
    rows = await list_credentials(supabase, user_id, service=service)
    for row in rows:
        if llm_setup_state(row) == "needs_model":
            return str(row["provider"])
    return None


def _settings_path(capability: str) -> str:
    return f"/profile/integrations?capability={capability}"


def _setup_required(capability: str, *, missing: list[str], message: str) -> ApiError:
    return ApiError(
        "SETUP_REQUIRED",
        message,
        capability=capability,
        missing=missing,
        settings_path=_settings_path(capability),
    )


def setup_required(capability: str, *, missing: list[str], message: str) -> ApiError:
    """The SETUP_REQUIRED error `resolve()` raises, for a caller that
    gathers its own credentials (`try_get_secret`) but must answer "nothing
    usable is configured" in the same shape -- same code, same
    `settings_path` convention -- so the UI's existing handling of it
    applies unchanged."""
    return _setup_required(capability, missing=missing, message=message)


async def resolve(
    supabase: AsyncClient,
    user_id: str,
    *,
    capability: str,
    provider: str | None = None,
    service: str = _DEFAULT_SERVICE,
) -> ResolvedCredential:
    """Proposal §11's resolution order, as far as this platform can
    actually honor it today:

    1. User-selected BYOK provider and model for the capability.
    2. Hosted-credit provider -- SKIPPED. This platform issues no hosted
       credits; there's no tier to select into yet.
    3. Generic local engine in self-host mode -- SKIPPED. No generic
       local engine is deployed or configured anywhere in this codebase;
       implementing this step now would mean inventing infrastructure
       nobody asked for, not honoring one that exists. A user-supplied
       "custom" BYOK provider with its own `base_url` already covers
       genuine self-hosting -- it resolves through step 1, not a separate
       code path.
    4. SETUP_REQUIRED.

    "No silent platform fallback" (§11's own words, echoed in this
    project's own dogfood note) -- every failure to resolve raises
    SETUP_REQUIRED naming exactly what's missing and where to fix it,
    never substitutes a default.
    """
    pref = await get_preference(supabase, user_id, capability)
    if pref is None:
        pref = await get_preference(supabase, user_id, DEFAULT_CAPABILITY)
    if pref is None:
        if service == _DEFAULT_SERVICE:
            awaiting = await _provider_awaiting_a_model(supabase, user_id, service)
            if awaiting is not None:
                raise _needs_model_error(capability, awaiting)
        raise _setup_required(
            capability,
            missing=["execution_mode"],
            message=f"{capability!r} has no execution mode configured yet.",
        )

    mode = pref["execution_mode"]
    if mode == "disabled":
        raise _setup_required(
            capability,
            missing=[],
            message=f"{capability!r} is turned off. Enable it in integrations to use it.",
        )
    if mode not in ("byok_first_party", "byok_generic"):
        raise _setup_required(
            capability,
            missing=["execution_mode"],
            message=f"{capability!r} is set to {mode!r}, which this platform can't run yet.",
        )

    chosen_provider: str | None = provider or pref.get("provider")
    chosen_model: str | None = pref.get("model")
    missing = [
        *([] if chosen_provider else ["provider"]),
        *([] if chosen_model else ["model"]),
    ]
    if missing or chosen_provider is None or chosen_model is None:
        if chosen_provider is not None and service == _DEFAULT_SERVICE:
            raise _needs_model_error(capability, chosen_provider)
        raise _setup_required(
            capability, missing=missing, message=f"{capability!r} needs a provider and a model."
        )

    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service=service, provider=chosen_provider
        )
    except CredentialNotFound as e:
        raise _setup_required(
            capability,
            missing=["credential"],
            message=f"No {chosen_provider} key configured for {capability!r}.",
        ) from e

    base_url = credential["base_url"]
    if service == _DEFAULT_SERVICE:
        # The address comes from the registry, or from the stored one only if it still passes
        # the provider's rule: a row saved before the rule existed cannot send a key anywhere
        # the rule would not.
        try:
            base_url = effective_base_url(chosen_provider, base_url)
        except BaseUrlRejected as e:
            raise _setup_required(
                capability,
                missing=["credential"],
                message=f"The saved {chosen_provider} key can't be used: {e} Save it again in "
                "Integrations.",
            ) from e

    return ResolvedCredential(
        provider=chosen_provider,
        model=chosen_model,
        secret=credential["secret"],
        base_url=base_url,
        source="byok",
    )


async def try_get_secret(
    supabase: AsyncClient, user_id: str, *, service: str, provider: str
) -> str | None:
    """A deliberately different shape from `resolve()`: no capability
    preference lookup, no SETUP_REQUIRED, just "does this credential
    exist" -- None if not. For a research provider (You.com, Firecrawl)
    whose absence means degrade gracefully, not fail (Proposal §26's own
    "Provider failure behavior" table: missing You.com or Firecrawl means
    a reduced lane, not an error), unlike the LLM credential `resolve()`
    still enforces as required."""
    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service=service, provider=provider
        )
    except CredentialNotFound:
        return None
    return cast(str, credential["secret"])


async def try_get_secret_pair(
    supabase: AsyncClient, user_id: str, *, service: str, provider: str
) -> tuple[str, str] | None:
    """The two-value sibling of `try_get_secret` (Job Finder P4c) for
    providers whose BYOK credential needs a second real value alongside
    the main secret (Adzuna: app_id + app_key; USAJobs: an
    Authorization-Key AND a registered email). Returns None -- degrade,
    never fail, same contract as `try_get_secret` -- when the credential
    is entirely absent OR only partially saved (a `secret` with no
    `secret_2` is unusable for either provider, not a smaller-but-valid
    configuration)."""
    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service=service, provider=provider
        )
    except CredentialNotFound:
        return None
    secret_2 = credential.get("secret_2")
    if secret_2 is None:
        return None
    return cast(str, credential["secret"]), cast(str, secret_2)
