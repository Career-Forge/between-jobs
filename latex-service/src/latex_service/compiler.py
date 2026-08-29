"""Shells out to `pdflatex` in an isolated temp directory (Sprint 3.2e).

Deliberately minimal: this service has exactly one job -- turn a LaTeX
source string into PDF bytes, or fail with the compiler's own log
attached. It knows nothing about forge-engines' templates, calibration,
or content -- that stays entirely on the caller's side (the renderer
spike's whole point is that the SAME LaTeX forge-engines already produces
for export becomes the preview too, not a second implementation).

Security: never pass `-shell-escape` -- LaTeX's `\\write18` primitive can
execute arbitrary shell commands if that flag is enabled, and this
service compiles content that ultimately traces back to LLM output
(escaped by forge-engines' `escape_latex_text_v2`, but defense-in-depth
costs nothing here). Each compile runs in its own temp directory, deleted
whether it succeeds or fails, so no compile run can see another's files.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path

_TEX_FILENAME = "resume.tex"
_PDF_FILENAME = "resume.pdf"
_COMPILE_TIMEOUT_SECONDS = 20.0
"""A hard cap on a single pdflatex invocation -- a malformed or
adversarial input should fail fast, not hang the service."""
_PASSES = 2
"""hyperref (used by forge-engines' template for every contact link)
needs a second pass to resolve link anchors correctly on some inputs;
running a fixed two passes is simpler and safer than trying to detect
"rerun needed" from the log, and costs one extra sub-second compile."""


class CompileError(Exception):
    """Raised when pdflatex exits non-zero or produces no PDF. `log`
    carries pdflatex's own output -- never swallowed, since the specific
    line number/reason is exactly what a caller needs to fix bad input."""

    def __init__(self, message: str, log: str) -> None:
        super().__init__(message)
        self.log = log


class CompileTimeout(Exception):
    """Raised when a single pdflatex pass exceeds `_COMPILE_TIMEOUT_SECONDS`."""


@dataclass(frozen=True, slots=True)
class CompileResult:
    pdf_bytes: bytes
    log: str


async def _run_pdflatex(work_dir: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        "pdflatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        _TEX_FILENAME,
        cwd=work_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_COMPILE_TIMEOUT_SECONDS)
    except TimeoutError as e:
        process.kill()
        await process.wait()
        raise CompileTimeout(f"pdflatex exceeded {_COMPILE_TIMEOUT_SECONDS}s") from e

    log = stdout.decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise CompileError(f"pdflatex exited {process.returncode}", log)
    return log


async def compile_latex(latex: str) -> CompileResult:
    """Runs `pdflatex` twice against `latex` in a fresh temp directory.
    Raises `CompileError`/`CompileTimeout` on failure; the temp directory
    is always removed, success or failure."""
    with tempfile.TemporaryDirectory(prefix="latex-service-") as raw_dir:
        work_dir = Path(raw_dir)
        (work_dir / _TEX_FILENAME).write_text(latex, encoding="utf-8")

        log = ""
        for _ in range(_PASSES):
            log = await _run_pdflatex(work_dir)

        pdf_path = work_dir / _PDF_FILENAME
        if not pdf_path.exists():
            raise CompileError("pdflatex reported success but produced no PDF", log)
        return CompileResult(pdf_bytes=pdf_path.read_bytes(), log=log)
