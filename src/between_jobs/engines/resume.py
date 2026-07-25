"""ResumeEngine contract -- BYO and hosted implementations both satisfy this shape.

A ResumeEngine turns a job description and a candidate's master resume into a
tailored resume document. This repo ships (or will ship) a generic, honest-prompt
implementation of this protocol so the platform works end-to-end standalone with
user-supplied keys; the hosted ForgeEngines MCP is a separate, higher-quality
implementation of the same contract. Callers depend on the protocol, never on
which concrete engine backs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ResumeRequest:
    """Inputs to a resume-generation call."""

    job_description: str
    master_resume: str
    profile: dict[str, str]
    target_role: str = ""
    target_company: str = ""
    preferences: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResumeResult:
    """Output of a resume-generation call.

    `pdf_path`/`tex_path` are None when an engine doesn't render to LaTeX/PDF --
    a minimal BYO implementation may return structured content only.
    """

    content: str
    pdf_path: str | None = None
    tex_path: str | None = None
    warnings: tuple[str, ...] = ()


@runtime_checkable
class ResumeEngine(Protocol):
    """Contract every resume-generation engine (BYO or hosted) must satisfy."""

    async def generate(self, request: ResumeRequest) -> ResumeResult: ...
