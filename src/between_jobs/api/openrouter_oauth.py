"""The server's half of connecting OpenRouter in one click (OAuth with PKCE).

The browser does the first half: it makes a random `code_verifier`, sends the person to
OpenRouter's `/auth` page with the SHA-256 `code_challenge` of it, and gets a one-time `code`
back on its own address. It then hands this server the code and the verifier, and this server
exchanges them at OpenRouter for an API key.

THE SERVER HOLDS NO STATE. There is no table of pending sign-ins and nothing to expire here: the
challenge was given to OpenRouter by the browser, so OpenRouter is what checks that the verifier
fits the code, that the code is unused and that it is under ten minutes old. A replayed code, a
wrong verifier and an expired code are all refused by it (403), which is why the exchange is
useless to anyone who saw the code but not the verifier. The only thing this module adds is that
it will not make the call for a verifier that cannot be one (RFC 7636: 43 to 128 characters of
`A-Z a-z 0-9 - . _ ~`) and that it always asks for the `S256` method; `plain`, where the
challenge IS the verifier, is never offered.

Neither the code nor the verifier nor the key it returns is logged, stored or sent anywhere but
OpenRouter. The key goes back to the route, which checks it and stores it encrypted.
"""

from __future__ import annotations

import re

import httpx

from .errors import ApiError
from .provider_checks import provider_failure
from .provider_http import UpstreamFailure, fetch_json
from .provider_registry import OPENROUTER, PROVIDERS

EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"

CODE_PATTERN = re.compile(r"[A-Za-z0-9._~\-]{8,512}")
VERIFIER_PATTERN = re.compile(r"[A-Za-z0-9._~\-]{43,128}")
"""The shapes the route accepts. A code's alphabet is not documented, so it is held to the same
URL-safe set as a verifier, with a wide length range."""

_KEY_PATTERN = re.compile(r"[\x21-\x7e]{8,512}")


def _declined() -> ApiError:
    return ApiError(
        "PROVIDER_REJECTED",
        "OpenRouter did not accept that sign-in. Start the connection again.",
        settings_path="/profile/integrations",
        details={"reason": "oauth_code_rejected", "provider": OPENROUTER},
    )


async def exchange_code(http: httpx.AsyncClient, *, code: str, code_verifier: str) -> str:
    """The API key OpenRouter issues for this code and verifier. ApiError when it will not:
    a code it does not accept (used, expired or not issued for this verifier) is
    PROVIDER_REJECTED with reason `oauth_code_rejected`; a throttle, an outage or an answer
    without a key are classified like any provider failure."""
    spec = PROVIDERS[OPENROUTER]
    if not CODE_PATTERN.fullmatch(code) or not VERIFIER_PATTERN.fullmatch(code_verifier):
        raise ApiError(
            "INVALID_INPUT",
            "That sign-in code or verifier is not in the form OpenRouter issues.",
            details={"reason": "oauth_malformed"},
        )
    try:
        answer = await fetch_json(
            http,
            "POST",
            EXCHANGE_URL,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json_body={
                "code": code,
                "code_verifier": code_verifier,
                "code_challenge_method": "S256",
            },
        )
    except UpstreamFailure as e:
        raise provider_failure(spec, e.reason) from e

    if answer.status in (400, 403):
        # 400: the method is not recognised; 403: the code or verifier is wrong, or the code
        # expired. Both end the same way for the person: begin again.
        raise _declined()
    if answer.status == 429:
        raise provider_failure(spec, "rate_limited")
    if answer.status != 200:
        raise provider_failure(spec, "provider_error")
    key = answer.body.get("key") if isinstance(answer.body, dict) else None
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise provider_failure(spec, "empty_response")
    return key
