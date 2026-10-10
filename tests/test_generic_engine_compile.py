"""The generated documents really compile, with the renderer they are written for.

Everything else in tests/test_generic_engine_*.py reads LaTeX as text. This file hands it to a
real `pdflatex`, because only a compile shows that the template and the escaping agree with TeX:
no unknown control sequence, no package the renderer lacks, no overfull page, and text that
extracts as the letters that were typed.

Two ways to compile, each skipped when it is not there (CI has neither, and this repository has
no job for it):

- a local TeX installation that has the template's packages (`pdflatex` and Lato among them);
- the PDF renderer's own image: set `GENERIC_ENGINE_LATEX_IMAGE` to its tag (for example
  `latex-service:slim-local`, built from `latex-service/Dockerfile`) and have `docker` available.
  The test starts it on a free local port, posts each document to its `/compile`, and stops it.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from generic_engine_fakes import (
    CREDENTIAL,
    NOW,
    SNAPSHOT,
    ScriptedModel,
    profile,
)
from pypdf import PdfReader

from between_jobs.engines import GenericBackend
from between_jobs.engines.generic.document import (
    EducationBlock,
    EntryBlock,
    ProjectBlock,
    ResumeDoc,
)
from between_jobs.engines.generic.latex import ALLOWED_PACKAGES, DRAWN_LETTERS, LatexText
from between_jobs.engines.generic.templates import render_cover_letter, render_resume

pytestmark = pytest.mark.latex_compile

Compile = Callable[[str], bytes]

_IMAGE = "GENERIC_ENGINE_LATEX_IMAGE"


def _local_tex_gap() -> str | None:
    """Why a local compile is not possible, or None if it is."""
    if shutil.which("pdflatex") is None or shutil.which("kpsewhich") is None:
        return "no local pdflatex"
    for package in (*ALLOWED_PACKAGES, "glyphtounicode"):
        suffix = "tex" if package == "glyphtounicode" else "sty"
        found = subprocess.run(
            ["kpsewhich", f"{package}.{suffix}"], capture_output=True, text=True, check=False
        )
        if not found.stdout.strip():
            return f"the local TeX installation has no {package}.{suffix}"
    return None


def _compile_locally(tmp_path: Path) -> Compile:
    def compile_(latex: str) -> bytes:
        (tmp_path / "doc.tex").write_text(latex, encoding="utf-8")
        for _ in range(2):  # the renderer runs two passes, for hyperref
            done = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-no-shell-escape",
                    "doc.tex",
                ],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            assert done.returncode == 0, done.stdout[-1500:]
        return (tmp_path / "doc.pdf").read_bytes()

    return compile_


@pytest.fixture(scope="module")
def renderer_container() -> Iterator[str]:
    image = os.environ.get(_IMAGE)
    if not image:
        pytest.skip(f"set {_IMAGE} to the PDF renderer's image to compile through it")
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "-p", f"127.0.0.1:{port}:5700", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start {image}: {started.stderr.strip()[:200]}")
    container = started.stdout.strip()
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                ready = httpx.post(
                    f"{url}/compile",
                    json={"latex": r"\documentclass{article}\begin{document}x\end{document}"},
                    timeout=10,
                )
                if ready.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.skip("the renderer container did not become ready")
            time.sleep(0.5)
        yield url
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False)


@pytest.fixture(params=["local pdflatex", "renderer container"])
def compile_(request: pytest.FixtureRequest, tmp_path: Path) -> Compile:
    if request.param == "local pdflatex":
        gap = _local_tex_gap()
        if gap:
            pytest.skip(gap)
        return _compile_locally(tmp_path)
    url = request.getfixturevalue("renderer_container")

    def through_container(latex: str) -> bytes:
        response = httpx.post(f"{url}/compile", json={"latex": latex}, timeout=90)
        assert response.status_code == 200, response.text[-1500:]
        return response.content

    return through_container


# -- profiles chosen to be hard on the template, not to be realistic ------------------------------


_HEAVY_JOBS = [
    {
        "title": f"Engineer {j}",
        "company": f"Company {j}",
        "location": "City, ST",
        "start_date": f"{2024 - 2 * j}-01",
        "end_date": "present" if j == 0 else f"{2026 - 2 * j}-01",
        "is_current": j == 0,
        "bullets": [
            f"Bullet {j}.{k} improved the thing by {10 + k}% across {k + 2} systems using Python "
            "and a long description that wraps over more than one line of text in the document"
            for k in range(10)
        ],
        "skills": ["Python"],
        "metrics": [],
    }
    for j in range(8)
]
_SPECIAL = r"R&D 100% $5M #1 {braces} ~tilde ^caret \backslash [brackets] under_score `tick` <b>"
_BASE = profile()

PROFILES: dict[str, dict[str, Any]] = {
    "typical": profile(),
    "bare": {
        "personal": {"name": "Sam Rowe"},
        "projects": [{"name": "solo", "bullets": ["Built a thing"]}],
    },
    "heavy": profile(experience=_HEAVY_JOBS),
    "long names": profile(
        personal={
            **_BASE["personal"],
            "name": "Maximiliana Alexandra von Hohenzollern-Schwarzenberg-Habsburg the Third",
            "headline": "Distinguished Principal Staff Senior Lead Architect of Enterprise Systems "
            * 2,
        },
        experience=[
            {
                **_BASE["experience"][0],
                "company": "The International Consortium of Advanced Distributed Systems Research "
                "and Applied Engineering Laboratories Incorporated",
                "bullets": ["x" * 400 + " https://example.com/" + "a" * 120],
            }
        ],
    ),
    "non-ASCII": profile(
        personal={
            **_BASE["personal"],
            "name": "Jos\u00e9 \u00d1u\u00f1ez-\u00d8rsted \u0141ukasiewicz",
            "headline": "Ing\u00e9nieur \u0152uvre \u00deorr \u00d0uro Stra\u00dfe",
        },
        experience=[
            {
                **_BASE["experience"][0],
                "company": "\u042f\u043d\u0434\u0435\u043a\u0441 Cloud \u5317\u4eac",
                "location": "Z\u00fcrich",
                "bullets": [
                    "R\u00e9duit la latence de 40% \u00e0 Z\u00fcrich \u2014 \U0001f600 inclus"
                ],
            }
        ],
    ),
    "links": profile(
        personal={
            **_BASE["personal"],
            "links": {
                "linkedin": "https://www.linkedin.com/in/a_b%20c?x=1&y=2#frag",
                "github": "github.com/user_name/repo~x",
                "portfolio": "https://example.com/~me/a%25b/c_d?q=a%26b#top",
                "scholar": "",
                "other": [],
            },
        },
        projects=[
            {
                "name": "p_roject #1",
                "url": "https://example.com/p%20q#r_s",
                "tech": ["C#", "C++"],
                "bullets": ["Wrote docs"],
                "metrics": [],
            }
        ],
    ),
    "special characters": profile(
        personal={**_BASE["personal"], "name": "A&B %Co $ #_ {x}", "headline": _SPECIAL},
        experience=[
            {
                **_BASE["experience"][0],
                "company": "R&D #1 {Corp} _x_",
                "title": "C++ & C# Dev ~ ^",
                "bullets": [_SPECIAL, "Saved $1,000,000 & 50% of 100% budget #1_priority"],
            }
        ],
        skills={"programming": ["C++", "C#", "R&D", "100%"], "tools": ["#hash", "a_b"]},
        certifications=[{"name": "50% Off & More", "issuer": "R&D {Inc}", "date": "2020-01"}],
        achievements=[_SPECIAL],
        summary_bullets=[_SPECIAL],
    ),
    "extras": profile(
        certifications=[
            {"name": " "},
            {"name": "Cert A"},
            {"name": "*Pending* Cert B"},
            {"name": "[2] Cert C"},
            {"name": "\u200b"},
            {"name": "Cert D"},
        ],
        patents=[{"title": " "}, {"title": "Real patent"}],
        publications=[
            {
                "title": "Why is connectivity hard?",
                "venue": "Journal of Examples",
                "date": "2022-05",
            },
            {"title": "A paper"},
        ],
    ),
    "operators": profile(
        experience=[
            {
                **_BASE["experience"][0],
                "bullets": [
                    "Used C++ <<streams>> with a << b, set x <<= 1 and y >>= 2",
                    "Wrote a,,b and c,,,d",
                ],
            }
        ],
    ),
    "two degrees": profile(
        education=[
            {
                "degree": "BS",
                "field": "Biology",
                "institution": "Lakeside College",
                "location": "Springfield, NJ",
                "start_date": "2012-09",
                "end_date": "2016-05",
                "gpa": "3.95",
                "coursework": ["Genetics", "Ecology"],
            },
            {
                "degree": "MS",
                "field": "Data Science",
                "institution": "Hillcrest University",
                "location": "Newark, NJ",
                "start_date": "2016-09",
                "end_date": "2018-05",
                "gpa": "3.9",
                "coursework": ["Databases", "Statistics"],
            },
        ],
    ),
    "a portfolio too": profile(
        personal={
            **_BASE["personal"],
            "links": {**_BASE["personal"]["links"], "portfolio": "averyquill.example.dev"},
        },
    ),
    "every contact": profile(
        personal={
            **_BASE["personal"],
            "links": {
                "linkedin": "https://www.linkedin.com/in/avery-quill-backend-data-engineer-2a4b6c",
                "github": "https://github.com/averyquill-data",
                "portfolio": "https://www.averyquill-portfolio-and-writing.example.com",
                "scholar": "",
                "other": [],
            },
        },
    ),
    "research career": {
        "personal": {"name": "Dr. Quinn Marsh", "headline": "Computational neuroscientist"},
        "publications": [
            {
                "title": "Connectivity classification & beyond",
                "venue": "Journal of Examples",
                "date": "2022-05",
            }
        ],
        "patents": [
            {"title": "Method for 100% reproducible pipelines", "patent_number": "US 1,234,567"}
        ],
        "volunteering": [
            {
                "organization": "Open Science Club",
                "role": "Organiser",
                "start_date": "2020-01",
                "end_date": "present",
                "bullets": ["Ran weekly reading groups"],
            }
        ],
        "education": [
            {"degree": "PhD", "field": "Neuroscience", "institution": "Example Institute"}
        ],
        "skills": {"programming": ["Python"]},
    },
}


async def _write(
    name: str, job: dict[str, Any] | None = None, **options: Any
) -> tuple[str, str, int, list[str]]:
    result = await GenericBackend(generate=ScriptedModel.lenient()).apply(
        None,  # type: ignore[arg-type]
        resume_template=PROFILES[name],
        job_snapshot=job or SNAPSHOT,
        credential=CREDENTIAL,
        now=NOW,
        generate_cover_letter=True,
        summary_mode="on",
        **options,
    )
    assert result.resume and result.cover_letter and result.shape_report
    return (
        result.resume["latex"],
        result.cover_letter["latex"],
        result.shape_report["target_pages"],
        result.shape_report["warnings"],
    )


def _pdf_text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _squeezed(text: str) -> str:
    """`text` without any whitespace: pypdf puts a space where the font kerns a pair ("T eam"),
    which is its extraction, not the document's."""
    return re.sub(r"\s+", "", text)


def _links(pdf: bytes) -> list[str]:
    found: list[str] = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        for annotation in page.get("/Annots") or []:
            action = annotation.get_object().get("/A")
            if action and "/URI" in action:
                found.append(str(action["/URI"]))
    return found


@pytest.mark.parametrize("name", sorted(PROFILES))
async def test_every_profile_compiles_to_a_resume_and_a_letter_within_their_page_targets(
    compile_: Compile, name: str
) -> None:
    resume_tex, cover_tex, target, _warnings = await _write(name)

    resume_pdf = compile_(resume_tex)
    cover_pdf = compile_(cover_tex)

    assert len(PdfReader(io.BytesIO(resume_pdf)).pages) <= target
    assert len(PdfReader(io.BytesIO(cover_pdf)).pages) == 1
    # one-page profiles really are one page, even the heavy ones (the budget keeps to its target)
    if name != "heavy":
        assert target == 1


async def test_text_extracts_as_the_letters_that_were_typed(compile_: Compile) -> None:
    resume_tex, cover_tex, _target, _warnings = await _write("typical")

    text = _pdf_text(compile_(resume_tex))

    for word in (
        "Avery Quill",
        "Northwind Labs",
        "Airflow",
        "Snowflake",
        "Springfield",
        "40%",
        "$120K",
    ):
        assert word in text, word
    assert (
        "\ufb01" not in text and "\ufb02" not in text
    )  # no ligature glyphs for a parser to trip on
    letter = _squeezed(_pdf_text(compile_(cover_tex)))
    assert "DearHiringTeam," in letter and "GlobexCorporation" in letter


async def test_special_characters_come_out_as_typed(compile_: Compile) -> None:
    resume_tex, _cover, _target, _warnings = await _write("special characters")

    text = _pdf_text(compile_(resume_tex))

    for typed in (
        "A&B %Co $ #_ {x}",
        "R&D 100% $5M #1 {braces}",
        "~tilde",
        "^caret",
        "[brackets]",
        "under_score",
        "C++",
    ):
        assert typed in text, typed
    assert "\\backslash" in text


async def test_links_keep_their_percent_hash_underscore_and_tilde(compile_: Compile) -> None:
    resume_tex, cover_tex, _target, _warnings = await _write("links")

    links = _links(compile_(resume_tex))

    assert "https://www.linkedin.com/in/a_b%20c?x=1&y=2#frag" in links
    assert "https://github.com/user_name/repo~x" in links
    assert "https://example.com/~me/a%25b/c_d?q=a%26b#top" in links
    assert "https://example.com/p%20q#r_s" in links  # the project's own address
    assert "mailto:avery.quill@example.com" in links and "tel:+15550100199" in links
    assert "https://www.linkedin.com/in/a_b%20c?x=1&y=2#frag" in _links(compile_(cover_tex))


async def test_names_in_letters_the_renderer_sets_are_printed_as_spelled_and_the_rest_is_reported(
    compile_: Compile,
) -> None:
    resume_tex, _cover, _target, warnings = await _write("non-ASCII")

    text = _pdf_text(compile_(resume_tex))

    assert "Jos\u00e9 \u00d1u\u00f1ez-\u00d8rsted \u0141ukasiewicz" in text
    assert "Ing\u00e9nieur \u0152uvre \u00deorr \u00d0uro Stra\u00dfe" in text
    assert "Z\u00fcrich" in text and "?????? Cloud ??" in text
    assert any("Cyrillic" in w and "CJK" in w for w in warnings)  # nothing was dropped silently
    assert not any("respelled" in w for w in warnings)  # nothing a name needed was respelled


def _slot_text(pdf: bytes) -> str:
    return _squeezed(_pdf_text(pdf))


_LETTERS = "".join(sorted(DRAWN_LETTERS))


def test_every_letter_the_templates_say_they_can_set_compiles_and_extracts_unchanged(
    compile_: Compile,
) -> None:
    """The allowlist in latex.py is only as good as this test: a letter the renderer cannot set
    stops the whole PDF, and one it sets but extracts as something else is a misspelled name to
    an applicant-tracking system. Every letter goes through every face the templates use:
    regular, bold (the name, the company, the skill labels) and italic (the headline, the title)."""
    esc = LatexText()
    resume = ResumeDoc(
        name=_LETTERS,  # large and bold
        headline=_LETTERS,  # italic
        chips=[{"field": "location", "text": _LETTERS, "href": None}],
        separator="pipe",
        density="compact",
        summary=_LETTERS,
        experience=[
            EntryBlock("/experience/0", _LETTERS, _LETTERS, _LETTERS, _LETTERS, [_LETTERS])
        ],
        projects=[ProjectBlock("/projects/0", _LETTERS, "", _LETTERS, [_LETTERS])],
        education=[
            EducationBlock("/education/0", _LETTERS, _LETTERS, _LETTERS, _LETTERS, [_LETTERS])
        ],
        skills=[(_LETTERS, _LETTERS)],
        certifications=[_LETTERS],
    )
    letter = render_cover_letter(
        name=_LETTERS,
        chips=[{"field": "location", "text": _LETTERS, "href": None}],
        separator="pipe",
        company=_LETTERS,
        title=_LETTERS,
        date_text=_LETTERS,
        content=latex_content(_LETTERS),
        esc=esc,
    )

    for tex in (render_resume(resume, esc), letter):
        text = _slot_text(compile_(tex))
        # once per slot, in order, as typed
        assert text.count(_LETTERS) >= 8, len(text)
        assert esc.warnings() == []


def latex_content(text: str) -> Any:
    from between_jobs.engines.generic.cover import CoverContent

    return CoverContent(opening=[text], highlights=[text], closing=[text])


async def test_an_education_detail_is_a_line_of_its_own_not_the_end_of_the_next_heading(
    compile_: Compile,
) -> None:
    resume_tex, _cover, _target, _warnings = await _write("two degrees", page_count_override=2)

    lines = [line.strip() for line in _pdf_text(compile_(resume_tex)).splitlines()]

    for detail in ("GPA: 3.95", "Coursework: Genetics, Ecology", "GPA: 3.9"):
        assert detail in lines, (detail, lines)
    # the next institution is on a line after the detail, never run on after it
    assert not [line for line in lines if "GPA" in line and "University" in line]
    assert not [line for line in lines if "Ecology" in line and "University" in line]


async def test_a_doubled_angle_bracket_or_comma_is_printed_as_typed(compile_: Compile) -> None:
    resume_tex, _cover, _target, _warnings = await _write("operators")

    text = _pdf_text(compile_(resume_tex))

    assert "<<streams>>" in text and "a << b" in text and "<<= 1" in text and ">>= 2" in text
    assert "a,,b" in text and "c,,,d" in text
    assert "\u00ab" not in text and "\u00bb" not in text and "\u201e" not in text


async def test_a_leading_star_after_a_forced_line_break_is_printed(compile_: Compile) -> None:
    job = {**SNAPSHOT, "company_name": "*Acme* Inc"}
    personal = {**_BASE["personal"], "name": "*Star Name"}
    PROFILES["stars"] = profile(
        personal=personal,
        certifications=[{"name": "Cert A"}, {"name": "*Pending* Cert B"}, {"name": "[2] Cert C"}],
    )
    try:
        resume_tex, cover_tex, _target, _warnings = await _write("stars", job)
    finally:
        del PROFILES["stars"]

    resume = _pdf_text(compile_(resume_tex))
    letter = _pdf_text(compile_(cover_tex))

    assert "*Pending* Cert B" in resume and "[2] Cert C" in resume
    assert "*Acme* Inc" in letter and "*Star Name" in letter


async def test_blank_looking_extras_neither_stop_the_build_nor_print_an_empty_line(
    compile_: Compile,
) -> None:
    resume_tex, _cover, _target, _warnings = await _write("extras", page_count_override=2)

    text = _pdf_text(compile_(resume_tex))  # a bare line break would stop pdflatex: this compiles

    for kept in (
        "Cert A",
        "Cert D",
        "Real patent",
        "Why is connectivity hard? Journal of Examples",
    ):
        assert kept in text, kept
    assert "May 2022" in text  # a publication's date is written like a certification's


_WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
)


def _words(pdf: bytes, tmp_path: Path) -> list[list[tuple[float, float, float, float, str]]]:
    """Every word of every page as (left, top, right, bottom, text) in points, from poppler: the
    only way to see that text stays inside the margins and where a page ends."""
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext (poppler) is not installed")
    source = tmp_path / "geometry.pdf"
    source.write_bytes(pdf)
    done = subprocess.run(
        ["pdftotext", "-bbox", str(source), "-"], capture_output=True, text=True, check=True
    )
    pages = []
    for page in done.stdout.split("<page ")[1:]:
        pages.append(
            [
                (float(a), float(b), float(c), float(d), text)
                for a, b, c, d, text in _WORD.findall(page)
            ]
        )
    return pages


async def test_the_contact_line_of_a_letter_stays_inside_the_margin(
    compile_: Compile, tmp_path: Path
) -> None:
    """Six contact links on one justified line have no place to break and used to run out of
    the right margin (one inch: 540 points) by tens of points: "a portfolio too" ended at 588."""
    for name in ("a portfolio too", "every contact", "typical"):
        _resume_tex, cover_tex, _target, _warnings = await _write(name)

        (page,) = _words(compile_(cover_tex), tmp_path)

        assert max(right for _l, _t, right, _b, _w in page) <= 540.5, name


async def test_the_contact_line_of_a_resume_stays_inside_the_margin(
    compile_: Compile, tmp_path: Path
) -> None:
    resume_tex, _cover, _target, _warnings = await _write("every contact")

    pages = _words(compile_(resume_tex), tmp_path)

    assert max(right for page in pages for _l, _t, right, _b, _w in page) <= 612 - 0.65 * 72 + 0.5


def _first_page_ends_with(pages: list[list[tuple[float, float, float, float, str]]]) -> str:
    """The last line of the first page, as text."""
    words = pages[0]
    last_top = round(max(top for _l, top, _r, _b, _w in words))
    return " ".join(w for _l, top, _r, _b, w in sorted(words) if round(top) == last_top)


def _long_resume(padding: int) -> ResumeDoc:
    """Eight jobs and three projects, with `padding` extra words in the very first bullet. Each
    extra word moves everything after it down the page, so a sweep of `padding` walks the end of
    page one across every kind of line."""
    jobs = [
        EntryBlock(
            f"/experience/{n}",
            f"Company {n}",
            "City, ST",
            f"Engineer {n}",
            "Jan 2020 -- Jan 2022",
            [
                f"Bullet {n}.{k} improved the thing by {10 + k}% across {k + 2} systems "
                + "and more " * (9 if k % 2 else 3)
                + ("pad " * padding if (n, k) == (0, 0) else "")
                for k in range(4)
            ],
        )
        for n in range(8)
    ]
    projects = [
        ProjectBlock(
            f"/projects/{n}", f"proj{n}", "", "Python", [f"Built {n}.{k}" for k in range(2)]
        )
        for n in range(3)
    ]
    return ResumeDoc(
        name="Sam Rowe",
        headline="",
        chips=[],
        separator="pipe",
        density="balanced",
        experience=jobs,
        projects=projects,
    )


@pytest.mark.parametrize("padding", [0, 20, 40, 60, 62, 64, 66, 68])
def test_a_page_does_not_end_on_an_entry_heading_or_between_its_two_rows(
    compile_: Compile, tmp_path: Path, padding: int
) -> None:
    """A heading is two rows (company and place, title and dates) that belong to the bullets
    under them. TeX breaks a page at the last place that fits, so with no rule the page ends on a
    heading about once in twenty tries: at paddings 62 to 69 it ended on the second row (the
    list could break after it) and, with the two rows one paragraph, on the first."""
    pdf = compile_(render_resume(_long_resume(padding), LatexText()))

    pages = _words(pdf, tmp_path)

    assert len(pages) >= 2
    last_line = _first_page_ends_with(pages)
    assert not re.search(r"Company \d|Engineer \d|proj\d|PROJECTS|EXPERIENCE", last_line), last_line


async def test_a_very_long_unbroken_string_wraps_inside_the_margins(compile_: Compile) -> None:
    resume_tex, _cover, _target, _warnings = await _write("long names")

    reader = PdfReader(io.BytesIO(compile_(resume_tex)))

    widths: list[float] = []

    def collect(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        if text.strip():
            widths.append(tm[4])

    reader.pages[0].extract_text(visitor_text=collect)
    assert widths and max(widths) < 612 - 0.5 * 72, "text runs past the right margin"


async def test_the_page_budget_holds_at_every_density_and_page_count(compile_: Compile) -> None:
    for density in ("compact", "balanced", "spacious"):
        for pages in (1, 2):
            resume_tex, _cover, target, _warnings = await _write(
                "heavy", density=density, page_count_override=pages
            )
            assert target == pages
            assert len(PdfReader(io.BytesIO(compile_(resume_tex))).pages) <= pages, (density, pages)
