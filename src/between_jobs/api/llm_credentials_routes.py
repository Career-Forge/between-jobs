"""The model-key routes beyond saving a key: what providers there are, what models a key can
use, choosing one, and connecting OpenRouter in one click.

    GET  /credentials/llm/providers            the registry, for the provider picker
    POST /credentials/llm/models               list a provider's models (with a key in the body,
                                               or the caller's saved one)
    PUT  /credentials/llm/{provider}/model     prove a model with one real completion, then use it
    POST /credentials/openrouter/exchange      finish OpenRouter's OAuth PKCE sign-in

Saving a key (`POST /credentials`, credentials_routes.py) and choosing its model are separate
steps on purpose: the list of models comes from the key, so the key has to exist (or at least be
checkable) first. A key with no model is saved and not usable (`setup_state: needs_model`).

Authorization is the same everywhere: the user comes from the verified token (`require_user_id`),
every credential read is by that user id, and a model list is fetched with either a key the
caller just typed or the caller's own saved one. The key in a request body is held as a
`SecretStr`, goes to the provider as a header and nowhere else: not stored (by the list route),
not logged, not in an error and not in an answer.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends

from supabase import AsyncClient

from .app_state import get_http_client, get_supabase
from .auth import require_user_id
from .capability_preferences_store import DEFAULT_CAPABILITY, set_preference
from .credential_resolver import setup_required
from .credentials_routes import _validate_openrouter_key, public_credential
from .errors import ApiError
from .models import ChooseModelRequest, ListModelsRequest, OpenRouterExchangeRequest
from .openrouter_oauth import exchange_code
from .provider_checks import list_models, probe_model
from .provider_credentials_store import (
    CredentialNotFound,
    get_decrypted_credential,
    list_credentials,
    save_credential,
    set_credential_model,
)
from .provider_registry import (
    OPENROUTER,
    PROVIDERS,
    BaseUrlRejected,
    ProviderSpec,
    clean_secret,
    effective_base_url,
    invalid_input,
    public_catalog,
    storable_base_url,
)
from .rate_limits import limit

router = APIRouter(prefix="/credentials")


def _provider(provider_id: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise ApiError("INVALID_INPUT", f"llm/{provider_id} isn't a supported model provider.")
    return spec


async def _saved_key(
    supabase: AsyncClient, user_id: str, spec: ProviderSpec
) -> tuple[str, str | None]:
    """(the caller's saved key for this provider, the address to call it at). Both come from
    the caller's own row, read by the verified user id."""
    try:
        credential = await get_decrypted_credential(
            supabase, user_id, service="llm", provider=spec.id
        )
    except CredentialNotFound as e:
        raise setup_required(
            DEFAULT_CAPABILITY,
            missing=["credential"],
            message=f"No {spec.name} key is saved yet. Save one in Settings first.",
        ) from e
    try:
        base_url = effective_base_url(spec.id, credential["base_url"])
    except BaseUrlRejected as e:
        raise setup_required(
            DEFAULT_CAPABILITY,
            missing=["credential"],
            message=f"The saved {spec.name} key can't be used: {e} Save it again in Settings.",
        ) from e
    return str(credential["secret"]), base_url


@router.get("/llm/providers")
async def list_llm_providers(_: str = Depends(require_user_id)) -> dict[str, Any]:
    """The providers a model key can be saved for, with the pages to get and manage a key, the
    regions Qwen has and the addresses the custom provider allows. Static; no outbound call."""
    return {"providers": public_catalog()}


@router.post("/llm/models", dependencies=[Depends(limit("credential_models"))])
async def list_provider_models(
    body: ListModelsRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """The models the key can use, normalised to `id`, `name`, `context_length` and `created`
    (a unix time), newest first, at most 500. `listing` is `live`, or `unavailable` for a
    provider that has no model list route (Qwen and the custom hosts may not): the person then
    types a model id. `truncated` says the list was cut."""
    spec = _provider(body.provider)
    if body.secret is not None:
        try:
            stored = storable_base_url(
                spec,
                region=body.region,
                workspace_id=body.workspace_id,
                base_url=body.base_url,
            )
            base_url = effective_base_url(spec.id, stored)
        except BaseUrlRejected as e:
            raise invalid_input(e) from e
        try:
            secret = clean_secret(body.secret.get_secret_value())
        except ValueError as e:
            raise ApiError(
                "INVALID_INPUT", str(e), details={"reason": "key_malformed", "provider": spec.id}
            ) from None
    else:
        secret, base_url = await _saved_key(supabase, user_id, spec)

    result = await list_models(http, spec, base_url, secret)
    return {
        "provider": spec.id,
        "listing": result.listing,
        "truncated": result.truncated,
        "models": [model.as_json() for model in result.models],
    }


@router.put("/llm/{provider}/model", dependencies=[Depends(limit("credential_model"))])
async def choose_model(
    provider: str,
    body: ChooseModelRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
) -> dict[str, Any]:
    """Uses `model` with the caller's saved key for `provider`: one real completion (a handful of
    tokens, a short time limit) proves the provider accepts it, and only then is it stored and
    made the default. A model the provider turns down (unknown, retired, not on this account)
    changes nothing."""
    spec = _provider(provider)
    secret, base_url = await _saved_key(supabase, user_id, spec)

    await probe_model(api_key=secret, model=body.model, base_url=base_url)

    saved = await set_credential_model(
        supabase, user_id, service="llm", provider=spec.id, model=body.model
    )
    if saved is None:  # deleted while the model was being proved
        raise setup_required(
            DEFAULT_CAPABILITY,
            missing=["credential"],
            message=f"No {spec.name} key is saved any more. Save one in Settings first.",
        )
    await set_preference(
        supabase,
        user_id,
        capability=DEFAULT_CAPABILITY,
        execution_mode="byok_first_party",
        provider=spec.id,
        model=body.model,
    )
    return public_credential(saved)


@router.post(
    "/openrouter/exchange",
    status_code=201,
    dependencies=[Depends(limit("credential_exchange"))],
)
async def exchange_openrouter_code(
    body: OpenRouterExchangeRequest,
    user_id: str = Depends(require_user_id),
    supabase: AsyncClient = Depends(get_supabase),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """Finishes the one-click OpenRouter sign-in (api/openrouter_oauth.py): the browser sends
    the one-time `code` and its own `code_verifier`; the key OpenRouter issues is checked
    and stored encrypted exactly like a pasted one, and the redacted credential comes back.
    Reconnecting keeps the model already chosen. No model is chosen here, so a first
    connection is `needs_model` until `PUT /credentials/llm/openrouter/model`."""
    key = await exchange_code(
        http,
        code=body.code.get_secret_value(),
        code_verifier=body.code_verifier.get_secret_value(),
    )
    await _validate_openrouter_key(http, key)
    kept: str | None = None
    for row in await list_credentials(supabase, user_id, service="llm"):
        if row.get("provider") == OPENROUTER and isinstance(row.get("model"), str):
            kept = row["model"] or None
    saved = await save_credential(
        supabase,
        user_id,
        service="llm",
        provider=OPENROUTER,
        secret=key,
        model=kept,
        base_url=None,
        is_validated=True,
    )
    return public_credential(saved)
