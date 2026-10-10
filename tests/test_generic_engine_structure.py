"""The structure of the built-in engine's documents, pinned against synthetic fixtures.

See tests/golden/generic_engine/README.md for why these fixtures are synthetic and structure-only
and why that is the documented exception to the repository's golden convention: there is no
reference engine to match, so nothing here claims parity with one.

The expected files hold a description of each document (its packages, its sections in order,
its item counts, the escapes it used, whether its braces balance), not its text.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from generic_engine_fakes import CREDENTIAL, NOW, ScriptedModel

from between_jobs.engines import GenericBackend
from between_jobs.engines.generic.latex import ALLOWED_PACKAGES, ESCAPE_SEQUENCES

_GOLDEN = Path(__file__).parent / "golden" / "generic_engine"
_UPDATE = os.environ.get("GENERIC_ENGINE_UPDATE_GOLDENS") == "1"

_CONTROL_SEQUENCE = re.compile(r"\\([A-Za-z]+|[^A-Za-z])")


def _load(kind: str, name: str) -> Any:
    return json.loads((_GOLDEN / kind / name).read_text())


def structure_of_resume(latex: str) -> dict[str, Any]:
    body = latex.split(r"\begin{document}")[1]
    sections = re.split(r"\\section\{([^}]*)\}", body)
    titles = sections[1::2]
    chunks = sections[2::2]
    unescaped = re.sub(r"\\[{}]", "", latex).replace("{[}", "").replace("{]}", "")
    return {
        "packages": sorted(
            {
                name.strip()
                for match in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", latex)
                for name in match.group(1).split(",")
            }
        ),
        "document_class": re.search(r"\\documentclass\[([^\]]*)\]", latex).group(1),  # type: ignore[union-attr]
        "sections": [
            {
                "title": title,
                "items": chunk.count(r"\item"),
                "entries": chunk.count(r"\entry{") + chunk.count(r"\projectheading{"),
            }
            for title, chunk in zip(titles, chunks, strict=True)
        ],
        "links": latex.count(r"\href{"),
        "escapes_used": sorted(
            seq for seq in set(_CONTROL_SEQUENCE.findall(body)) if seq in ESCAPE_SEQUENCES
        ),
        "braces_balanced": unescaped.count("{") == unescaped.count("}"),
        "environments_closed": latex.count(r"\begin{itemize}") == latex.count(r"\end{itemize}")
        and latex.count(r"\begin{document}") == latex.count(r"\end{document}") == 1,
        "ascii_only": latex.isascii(),
    }


def structure_of_letter(latex: str) -> dict[str, Any]:
    body = latex.split(r"\begin{document}")[1].split(r"\end{document}")[0]
    lines = [line for line in body.splitlines() if line.strip()]
    paragraphs = [
        line
        for line in lines
        if not line.startswith(("\\", "{", "Sincerely", "Dear ", "Hiring Team"))
    ]
    unescaped = re.sub(r"\\[{}]", "", latex).replace("{[}", "").replace("{]}", "")
    return {
        "packages": sorted(
            {
                name.strip()
                for match in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", latex)
                for name in match.group(1).split(",")
            }
        ),
        "document_class": re.search(r"\\documentclass\[([^\]]*)\]", latex).group(1),  # type: ignore[union-attr]
        "has_date": any(line.startswith(r"\bigskip ") for line in lines),
        "has_addressee": any(line.startswith(r"Hiring Team") for line in lines),
        "has_subject": any(line.startswith(r"\textbf{Re: ") for line in lines),
        "greeting": any(line.startswith("Dear Hiring Team,") for line in lines),
        "paragraphs": len(paragraphs),
        "signed": any(line.startswith("Sincerely,") for line in lines),
        "braces_balanced": unescaped.count("{") == unescaped.count("}"),
        "ascii_only": latex.isascii(),
    }


async def _documents(profile_file: str) -> dict[str, str]:
    result = await GenericBackend(generate=ScriptedModel.lenient()).apply(
        None,  # type: ignore[arg-type]
        resume_template=_load("inputs", profile_file),
        job_snapshot=_load("inputs", "job.json"),
        credential=CREDENTIAL,
        now=NOW,
        generate_cover_letter=True,
        summary_mode="on",
    )
    assert result.resume and result.cover_letter
    return {"resume": result.resume["latex"], "cover_letter": result.cover_letter["latex"]}


@pytest.mark.parametrize(
    ("profile_file", "expected_file"),
    [
        ("typical_profile.json", "typical.structure.json"),
        ("special_profile.json", "special.structure.json"),
    ],
)
async def test_the_documents_have_the_structure_the_fixtures_describe(
    profile_file: str, expected_file: str
) -> None:
    documents = await _documents(profile_file)
    actual = {
        "resume": structure_of_resume(documents["resume"]),
        "cover_letter": structure_of_letter(documents["cover_letter"]),
    }
    path = _GOLDEN / "expected" / expected_file

    if _UPDATE:
        path.write_text(json.dumps(actual, indent=2) + "\n")
    expected = json.loads(path.read_text())

    assert actual == expected


def test_the_fixtures_themselves_are_what_the_readme_says() -> None:
    readme = (_GOLDEN / "README.md").read_text()
    assert "no parity with any other engine" in readme
    assert "synthetic" in readme and "structure-only" in readme
    assert sorted(p.name for p in (_GOLDEN / "inputs").iterdir()) == [
        "job.json",
        "special_profile.json",
        "typical_profile.json",
    ]


async def test_every_package_in_a_golden_is_on_the_renderers_allowlist() -> None:
    for expected_file in ("typical.structure.json", "special.structure.json"):
        expected = _load("expected", expected_file)
        for document in ("resume", "cover_letter"):
            assert set(expected[document]["packages"]) <= set(ALLOWED_PACKAGES)
