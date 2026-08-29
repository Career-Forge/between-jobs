"""between-jobs' own direct OpenRouter client (Horizon Sprint 5.0) --
the first time this backend calls an LLM directly rather than through
forge-engines' HTTP service. Company-intel synthesis has nothing to do
with resume generation, so it doesn't belong in forge-engines; this
mirrors forge-engines' own `llm_client.py` (itself adapted from
CareerForge_Telegram's proven OpenAI-compatible wrapper), narrowed to
this module's actual need: one system+user turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import APIError as OpenAIAPIError
from openai import AsyncOpenAI

from .errors import ApiError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
"""Per this project's standing rule: LLM calls always route through
OpenRouter unless explicitly told otherwise."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str


async def generate(
    *,
    api_key: str,
    model: str,
    base_url: str | None,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int | None = None,
) -> LLMResponse:
    client = AsyncOpenAI(api_key=api_key, base_url=base_url or OPENROUTER_BASE_URL)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            # This is grounded extraction from provided evidence, not a task
            # that benefits from chain-of-thought -- and OpenRouter's own
            # docs warn that reasoning tokens count against max_tokens, so a
            # reasoning model can silently burn the whole budget and return
            # empty content. `effort: "none"` is OpenRouter's documented,
            # cross-model way to turn that off; non-reasoning models ignore it.
            extra_body={"reasoning": {"effort": "none"}},
        )
    except OpenAIAPIError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Couldn't reach the LLM provider. Try again in a moment.",
            retryable=True,
        ) from e

    # OpenRouter can return HTTP 200 with an empty `choices` when every
    # upstream candidate for a routed model fails (seen in practice with
    # free-tier models under load) -- the SDK doesn't treat that as an
    # error, so it needs its own guard rather than crashing on [0].
    if not response.choices:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "The LLM provider didn't return a completion. Try again in a moment.",
            retryable=True,
        )
    return LLMResponse(content=response.choices[0].message.content or "")
