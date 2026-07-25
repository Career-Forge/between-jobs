"""Structural tests for the BYOEngine contracts.

A dummy implementation of each protocol proves the contract shape is usable by both
a future BYO implementation and the hosted ForgeEngines MCP client without needing
inheritance -- structural typing (Protocol), not a base class, is the seam.
"""

from __future__ import annotations

import asyncio

from between_jobs.engines import (
    ResumeEngine,
    ResumeRequest,
    ResumeResult,
    ScoreEngine,
    ScoreRequest,
    ScoreResult,
)


class _DummyResumeEngine:
    async def generate(self, request: ResumeRequest) -> ResumeResult:
        return ResumeResult(content=f"resume for {request.target_role}")


class _DummyScoreEngine:
    async def score(self, request: ScoreRequest) -> ScoreResult:
        return ScoreResult(overall_score=0.0, breakdown={})


def test_dummy_resume_engine_satisfies_protocol() -> None:
    engine: ResumeEngine = _DummyResumeEngine()
    assert isinstance(engine, ResumeEngine)


def test_dummy_score_engine_satisfies_protocol() -> None:
    engine: ScoreEngine = _DummyScoreEngine()
    assert isinstance(engine, ScoreEngine)


def test_resume_request_round_trip() -> None:
    request = ResumeRequest(
        job_description="jd", master_resume="resume", profile={}, target_role="SWE"
    )
    result = asyncio.run(_DummyResumeEngine().generate(request))
    assert "resume for SWE" in result.content
