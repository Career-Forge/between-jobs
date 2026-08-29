"""Tests for compiler.py.

These need a real `pdflatex` binary -- there's no meaningful way to unit
test "does pdflatex compile this" without it, since that's the entire job
of this module. Skipped when pdflatex isn't on PATH (e.g. running the
suite outside the Docker image); real coverage happens inside the
container / CI where texlive is actually installed.
"""

from __future__ import annotations

import shutil

import pytest

from latex_service.compiler import CompileError, compile_latex

pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not on PATH -- run inside the Docker image"
)

_MINIMAL_DOC = r"""
\documentclass{article}
\begin{document}
Hello, world.
\end{document}
"""

_BROKEN_DOC = r"""
\documentclass{article}
\begin{document}
\undefinedcommandthatdoesnotexist
\end{document}
"""


async def test_compile_latex_produces_a_real_pdf() -> None:
    result = await compile_latex(_MINIMAL_DOC)
    assert result.pdf_bytes.startswith(b"%PDF")
    assert len(result.pdf_bytes) > 0


async def test_compile_latex_raises_on_broken_input() -> None:
    with pytest.raises(CompileError) as exc_info:
        await compile_latex(_BROKEN_DOC)
    assert "undefinedcommandthatdoesnotexist" in exc_info.value.log.lower()


async def test_compile_latex_raises_on_empty_input() -> None:
    with pytest.raises(CompileError):
        await compile_latex("")
