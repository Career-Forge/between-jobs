"""The only place the built-in engine talks to a model.

Every stage asks through a `ModelSession`, which holds the three bounds the rest of the engine
relies on: the caller's own credential (the engine has none of its own), a hard cap on how many
calls one request may make, and one function that makes the call (`api/llm_client.generate`, or
a test's stand-in passed as `generate`). Nothing here logs what was asked or answered: a prompt
carries a candidate's career and a stranger's job posting, and the answer carries a rewrite of
both, and neither belongs in a log line.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.llm_client import LLMResponse
from between_jobs.api.llm_client import generate as default_generate

__all__ = [
    "MAX_CALLS_PER_APPLY",
    "CallLimitExceeded",
    "LlmGenerate",
    "ModelSession",
    "default_generate",
    "parse_object",
]

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

MAX_CALLS_PER_APPLY = 7
"""Four calls write a resume and a cover letter (reading the job, the experience and projects,
the summary, the letter); each of the last three may be asked once more with the list of what was
wrong. No stage has a way to ask a fourth time, and `ModelSession.ask` refuses the eighth call."""


class CallLimitExceeded(RuntimeError):
    """A stage tried to make more calls than a request may. A bug in the engine, never an
    answer to give a person: the cap exists so that no input can make the engine loop."""


@dataclass(slots=True)
class ModelSession:
    """One request's access to the model."""

    credential: ResolvedCredential
    generate: LlmGenerate = default_generate
    max_calls: int = MAX_CALLS_PER_APPLY
    calls: int = 0

    async def ask(self, *, system: str, user: str, max_tokens: int) -> str:
        """The model's raw answer (possibly empty). Provider failures surface as the
        `ApiError` the client raises and are not retried here: a bad key or a provider outage
        does not get better by asking again inside one request."""
        if self.calls >= self.max_calls:
            raise CallLimitExceeded(f"more than {self.max_calls} model calls in one request")
        self.calls += 1
        response = await self.generate(
            api_key=self.credential.secret,
            model=self.credential.model,
            base_url=self.credential.base_url,
            system_prompt=system,
            user_prompt=user,
            max_tokens=max_tokens,
        )
        return response.content or ""


def parse_object(raw: str) -> dict[str, Any] | None:
    """The JSON object in a model's answer, or None.

    Tolerates what models habitually do around a correct answer (a markdown code fence, a
    sentence before or after it) by reading from the first `{` to the last `}`, and nothing
    else: the result must be a JSON object, and the caller still validates every field of it."""
    text = raw.strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (ValueError, RecursionError):
            # `JSONDecodeError` is a `ValueError`, and so is the error Python raises for an
            # integer of more than 4300 digits (a degenerate run of digits is a known way for a
            # model to fail, and a posting can ask for one). Neither is a usable answer.
            continue
        if isinstance(value, dict):
            return value
    return None
