"""The two documents, as LaTeX: a plain single-column resume and a plain cover letter.

Written for this repository and for the PDF renderer it ships (`latex-service`): only packages
that image installs (`latex.ALLOWED_PACKAGES`), Lato for the typeface, US-letter paper, black
text, no tables and no columns, so what a person sees is what an applicant-tracking system
reads. The text is selectable and copies out as plain letters: `glyphtounicode` maps glyphs back
to text, and `\\DisableLigatures` stops "fi" and "fl" becoming single ligature glyphs, which a
text extractor would otherwise hand on as "\ufb01" and "\ufb02".

This module is the one place a LaTeX command is spelled out. Every piece of data is passed
through a `LatexText` first, and every link through `clean_url`/`latex_url`, so no command can
come from data; `TEMPLATE_COMMANDS` lists every control sequence the templates themselves use,
and a test holds generated documents to exactly that set plus the escapes in `latex.py`.
"""

from __future__ import annotations

from typing import Any

from .cover import CoverContent
from .document import EducationBlock, EntryBlock, ProjectBlock, ResumeDoc
from .latex import LatexText, clean_url, latex_url
from .plan import Density

TEMPLATE_COMMANDS = frozenset(
    {
        "documentclass",
        "usepackage",
        "input",
        "pdfgentounicode",
        "pagestyle",
        "setlength",
        "emergencystretch",
        "parindent",
        "parskip",
        "raggedright",
        "centering",
        "linespread",
        "titleformat",
        "titlespacing",
        "titlerule",
        "setlist",
        "newcommand",
        "section",
        "begin",
        "end",
        "item",
        "par",
        "noindent",
        "nopagebreak",
        "hfill",
        "bigskip",
        "medskip",
        "vspace",
        "href",
        "textbf",
        "textit",
        "itshape",
        "bfseries",
        "normalsize",
        "small",
        "large",
        "Large",
        "LARGE",
        "MakeUppercase",
        "cdot",
        "bullet",
        "DisableLigatures",
        "begingroup",
        "endgroup",
        "entry",
        "projectheading",
        "\\",
    }
)
"""Every control sequence the two templates write themselves (a control symbol is listed by its
character). `latex.ESCAPE_SEQUENCES` is the other half: what escaped data can contain."""

_SPACING: dict[Density, tuple[str, str, str, str]] = {
    # (space before a section title, after it, between bullets, around a list)
    "compact": ("5pt", "2pt", "0pt", "1pt"),
    "balanced": ("8pt", "3pt", "1pt", "2pt"),
    "spacious": ("11pt", "4pt", "2.5pt", "3pt"),
}

_RESUME_HEAD = r"""\documentclass[letterpaper,10pt]{article}
\usepackage[default]{lato}
\usepackage[T1]{fontenc}
\usepackage[protrusion=false,expansion=false]{microtype}
\DisableLigatures[f]{encoding = *, family = *}
\usepackage[top=0.5in,bottom=0.5in,left=0.65in,right=0.65in]{geometry}
\usepackage{enumitem}
\usepackage{titlesec}
\usepackage[hidelinks]{hyperref}
\input{glyphtounicode}
\pdfgentounicode=1
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\emergencystretch}{2em}
\raggedright
\titleformat{\section}{\normalsize\bfseries}{}{0pt}{\MakeUppercase}[\titlerule]
\titlespacing*{\section}{0pt}{%(before)s}{%(after)s}
\setlist[itemize]{leftmargin=1.2em,itemsep=%(between)s,topsep=%(around)s,parsep=0pt,partopsep=0pt,beginpenalty=10000}
\newcommand{\entry}[4]{\noindent\textbf{#1}\hfill #2\par\nopagebreak\noindent\textit{#3}\hfill #4\par\nopagebreak}
\newcommand{\projectheading}[2]{\noindent #1\hfill \textit{#2}\par\nopagebreak}
\begin{document}
"""  # noqa: E501

_SEPARATORS = {"pipe": r" $|$ ", "dot": r" $\cdot$ ", "bullet": r" $\bullet$ "}


def _link(esc: LatexText, text: str, href: str | None) -> str:
    target = clean_url(href) if href else None
    shown = esc(text)
    return rf"\href{{{latex_url(target)}}}{{{shown}}}" if target else shown


def _chips(esc: LatexText, chips: list[dict[str, Any]], separator: str) -> str:
    glue = _SEPARATORS.get(separator, _SEPARATORS["pipe"])
    return glue.join(_link(esc, str(c["text"]), c.get("href")) for c in chips)


def _bullets(esc: LatexText, bullets: list[str]) -> str:
    if not bullets:
        return ""
    items = "\n".join(rf"\item {esc(text)}" for text in bullets)
    return f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}\n"


def _entry(esc: LatexText, block: EntryBlock) -> str:
    head = rf"\entry{{{esc(block.primary)}}}{{{esc(block.place)}}}"
    head += rf"{{{esc(block.secondary)}}}{{{esc(block.dates)}}}"
    return head + "\n" + _bullets(esc, block.bullets)


def _project(esc: LatexText, block: ProjectBlock) -> str:
    name = rf"\textbf{{{esc(block.name)}}}"
    target = clean_url(block.url) if block.url else None
    if target:
        name = rf"\href{{{latex_url(target)}}}{{{name}}}"
    return rf"\projectheading{{{name}}}{{{esc(block.tech)}}}" + "\n" + _bullets(esc, block.bullets)


def _education(esc: LatexText, block: EducationBlock) -> str:
    head = rf"\entry{{{esc(block.institution)}}}{{{esc(block.place)}}}"
    head += rf"{{{esc(block.degree)}}}{{{esc(block.dates)}}}"
    # each detail is a paragraph of its own: left open, the next \entry would continue the line
    details = "".join(rf"\noindent {esc(line)}\par" + "\n" for line in block.details)
    return head + "\n" + details


def _section(title: str, body: str) -> str:
    return f"\\section{{{title}}}\n{body}"


def _lines(esc: LatexText, lines: list[str]) -> str:
    """Lines separated by forced breaks. A line with nothing left after escaping is skipped: a
    break with no text before it is a LaTeX error ("no line here to end")."""
    kept = [text for text in (esc(line) for line in lines) if text.strip()]
    return "\\\\\n".join(kept) + "\n" if kept else ""


def render_resume(doc: ResumeDoc, esc: LatexText) -> str:
    before, after, between, around = _SPACING[doc.density]
    out = [_RESUME_HEAD % {"before": before, "after": after, "between": between, "around": around}]
    out.append(rf"\begingroup\centering{{\LARGE\bfseries {esc(doc.name)}}}\par" + "\n")
    if doc.headline:
        out.append(rf"\vspace{{2pt}}{{\small\itshape {esc(doc.headline)}}}\par" + "\n")
    if doc.chips:
        out.append(rf"\vspace{{2pt}}{{\small {_chips(esc, doc.chips, doc.separator)}}}\par" + "\n")
    out.append("\\endgroup\n")

    if doc.summary:
        out.append(_section("Summary", esc(doc.summary) + "\n"))
    if doc.experience:
        out.append(_section("Experience", "".join(_entry(esc, b) for b in doc.experience)))
    if doc.projects:
        out.append(_section("Projects", "".join(_project(esc, b) for b in doc.projects)))
    if doc.education:
        out.append(_section("Education", "".join(_education(esc, b) for b in doc.education)))
    if doc.skills:
        rows = [rf"\textbf{{{esc(label)}:}} {esc(items)}" for label, items in doc.skills]
        out.append(_section("Skills", "\\\\\n".join(rows) + "\n"))
    if doc.certifications:
        out.append(_section("Certifications", _lines(esc, doc.certifications)))
    if doc.publications:
        out.append(_section("Publications", _lines(esc, doc.publications)))
    if doc.patents:
        out.append(_section("Patents", _lines(esc, doc.patents)))
    if doc.volunteering:
        out.append(_section("Volunteering", "".join(_entry(esc, b) for b in doc.volunteering)))
    if doc.achievements:
        out.append(_section("Achievements", _bullets(esc, doc.achievements)))
    if doc.languages:
        out.append(_section("Languages", esc(doc.languages) + "\n"))
    out.append("\\end{document}\n")
    return "".join(out)


_LETTER_HEAD = r"""\documentclass[letterpaper,11pt]{article}
\usepackage[default]{lato}
\usepackage[T1]{fontenc}
\usepackage[protrusion=false,expansion=false]{microtype}
\DisableLigatures[f]{encoding = *, family = *}
\usepackage[margin=1in]{geometry}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\input{glyphtounicode}
\pdfgentounicode=1
\pagestyle{empty}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.8em}
\setlist[itemize]{leftmargin=1.4em,itemsep=2pt,topsep=0pt,parsep=0pt,partopsep=0pt}
\linespread{1.08}
\begin{document}
"""


def render_cover_letter(
    *,
    name: str,
    chips: list[dict[str, Any]],
    separator: str,
    company: str,
    title: str,
    date_text: str,
    content: CoverContent,
    esc: LatexText,
) -> str:
    out = [_LETTER_HEAD, rf"{{\Large\bfseries {esc(name)}}}\par" + "\n"]
    if chips:
        # Ragged-right in a group of its own: a justified line of unbreakable links overruns the
        # margin, and the body of the letter stays justified.
        out.append("\\begingroup\\raggedright\\setlength{\\emergencystretch}{2em}\n")
        out.append(rf"{{\small {_chips(esc, chips, separator)}}}\par" + "\n")
        out.append("\\endgroup\n")
    if date_text:
        out.append(rf"\bigskip {esc(date_text)}\par" + "\n")
    if company:
        out.append(rf"Hiring Team\\ {esc(company)}\par" + "\n")
    if title:
        out.append(rf"\textbf{{Re: {esc(title)}}}\par" + "\n")
    out.append("Dear Hiring Team,\\par\n")
    for paragraph in content.opening:
        out.append(esc(paragraph) + "\n\n")
    if content.highlights:
        out.append(_bullets(esc, content.highlights))
    for paragraph in content.closing:
        out.append(esc(paragraph) + "\n\n")
    out.append(rf"Sincerely,\\ {esc(name)}" + "\n")
    out.append("\\end{document}\n")
    return "".join(out)
