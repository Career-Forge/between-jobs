"""Tests for the HTTP layer. Error-mapping is tested against a faked
compiler (no pdflatex needed); the real compile path is covered by
test_compiler.py's own pdflatex-gated tests plus this file's one
end-to-end check.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from latex_service import app as app_module
from latex_service.app import app
from latex_service.compiler import CompileError, CompileResult, CompileTimeout


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_compile_success_returns_pdf_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compile(latex: str) -> CompileResult:
        assert latex == "\\documentclass{article}"
        return CompileResult(pdf_bytes=b"%PDF-fake", log="ok")

    monkeypatch.setattr(app_module, "compile_latex", fake_compile)
    with TestClient(app) as client:
        response = client.post("/compile", json={"latex": "\\documentclass{article}"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"%PDF-fake"


def test_compile_error_returns_422_with_log(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compile(latex: str) -> CompileResult:
        raise CompileError("pdflatex exited 1", "! Undefined control sequence.")

    monkeypatch.setattr(app_module, "compile_latex", fake_compile)
    with TestClient(app) as client:
        response = client.post("/compile", json={"latex": "broken"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "CompileError"
    assert "Undefined control sequence" in body["log"]


def test_compile_timeout_returns_504(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compile(latex: str) -> CompileResult:
        raise CompileTimeout("pdflatex exceeded 20.0s")

    monkeypatch.setattr(app_module, "compile_latex", fake_compile)
    with TestClient(app) as client:
        response = client.post("/compile", json={"latex": "slow"})

    assert response.status_code == 504
    assert response.json()["error"] == "CompileTimeout"


@pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not on PATH -- run inside the Docker image"
)
def test_compile_end_to_end_with_real_pdflatex() -> None:
    latex = r"""
    \documentclass{article}
    \begin{document}
    Real compile end to end.
    \end{document}
    """
    with TestClient(app) as client:
        response = client.post("/compile", json={"latex": latex})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
