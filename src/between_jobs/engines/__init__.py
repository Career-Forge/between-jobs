"""Engine contracts -- BYO and hosted implementations satisfy the same protocols.

Deterministic code owns structure and shape; LLMs choose words only (see CLAUDE.md's
engineering rules). These protocols are intentionally thin -- callers depend on the
contract, never on which concrete engine (a generic BYOK implementation shipped here,
or the private ForgeEngines MCP) backs it. No concrete implementation ships in this
module yet; it's the seam the rest of the platform is built against.
"""

from between_jobs.engines.resume import ResumeEngine, ResumeRequest, ResumeResult
from between_jobs.engines.score import ScoreEngine, ScoreRequest, ScoreResult

__all__ = [
    "ResumeEngine",
    "ResumeRequest",
    "ResumeResult",
    "ScoreEngine",
    "ScoreRequest",
    "ScoreResult",
]
