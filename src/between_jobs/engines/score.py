"""ScoreEngine contract -- BYO and hosted implementations both satisfy this shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ScoreRequest:
    """Inputs to a fit-scoring call."""

    resume_text: str
    job_description: str
    target_role: str = ""
    target_company: str = ""


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Output of a fit-scoring call.

    `breakdown` keys are engine-defined categories (e.g. skills/experience/
    education). The platform treats `overall_score` as load-bearing and the
    breakdown as explanatory detail, never the reverse -- an engine's category
    weights are its own implementation detail, not part of this contract.
    """

    overall_score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    rationale: str = ""


@runtime_checkable
class ScoreEngine(Protocol):
    """Contract every fit-scoring engine (BYO or hosted) must satisfy."""

    async def score(self, request: ScoreRequest) -> ScoreResult: ...
