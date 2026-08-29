"""FastAPI wrapper around compiler.py (Sprint 3.2e).

Stateless and content-agnostic on purpose: this service never imports
anything from forge-engines or between-jobs, and never will -- the whole
point of "preview is the actual compiled artifact" (Proposal §24.5.8) is
that this compiles exactly the LaTeX another service already generated,
with no template/content knowledge of its own to drift out of sync.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Response
from pydantic import BaseModel

from .compiler import CompileError, CompileTimeout, compile_latex

app = FastAPI(title="latex-service", version="0.0.1")


class CompileRequest(BaseModel):
    latex: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/compile")
async def compile_endpoint(body: CompileRequest) -> Response:
    try:
        result = await compile_latex(body.latex)
    except CompileTimeout as e:
        return _error(504, "CompileTimeout", str(e))
    except CompileError as e:
        return _error(422, "CompileError", str(e), log=e.log)
    return Response(content=result.pdf_bytes, media_type="application/pdf")


def _error(status_code: int, error: str, message: str, *, log: str | None = None) -> Response:
    body: dict[str, Any] = {"error": error, "message": message}
    if log is not None:
        # Tail only -- a runaway log (e.g. a genuinely pathological
        # input) shouldn't balloon the error response.
        body["log"] = log[-4000:]
    return Response(
        content=json.dumps(body), media_type="application/json", status_code=status_code
    )
