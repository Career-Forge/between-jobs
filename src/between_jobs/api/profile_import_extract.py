"""Safe text extraction for an uploaded resume file: PDF or DOCX bytes in, plain text out.

The file is untrusted input from a stranger. Nothing here writes to disk, opens a network
connection or runs anything the file contains: bytes go in, a normalized string comes out.
The module is pure on purpose -- the route, the model call and the database all live elsewhere
-- so every defense below can be tested with a few hand-built byte strings.

What kind of file it is comes from its CONTENT (`sniff_kind`): the first bytes, and for a
zip whether it holds `word/document.xml`. A file name or a Content-Type header only ever
adds a second opinion that must agree; it never decides.

PDF (pypdf). Encrypted files are refused. At most `MAX_PDF_PAGES` pages are read and at most
`MAX_TEXT_CHARS` characters kept (the extraction says so when it cut something, it never
cuts silently). A file with no selectable text -- a scan -- is a clear error: there is no OCR.
Two-column layouts need care, because a PDF stores text in drawing order: a wrapped
sentence in the main column ends up with a sidebar line spliced into the middle of it. So each
page is read twice, plainly and as a fixed-width character grid (pypdf's layout mode, which
knows the real glyph widths), and when the grid has a persistent blank vertical gutter with a
real column of text on each side, the page is read column by column instead. A narrow column
of dates or labels beside the text (a timeline) is deliberately NOT treated as a column: it
would pull every date away from its entry. Link annotations are read too: a resume often
shows "LinkedIn" as words and keeps the address only in the link.

DOCX (standard library only: zipfile and ElementTree; no new dependency). A DOCX is a zip of
XML, so the defenses are those for a zip and for XML:

- Zip bombs: the member count, each member's uncompressed size, the archive's total
  uncompressed size and the compression ratio are checked from the zip directory, and the
  size of every member that is read is enforced AGAIN while streaming it, because a
  directory can lie.
- Path traversal: only the parts a resume needs are ever opened, and by their exact names
  (`word/document.xml`, `word/styles.xml`, the numbered header and footer parts and their
  `_rels`). Nothing is extracted to disk, so a member named `../../x` can do nothing -- but
  an archive that carries such a name, an absolute name, a backslash or a duplicate name is
  refused anyway: Word never writes one.
- XML entity expansion and external entities: any part containing a DOCTYPE or an ENTITY
  declaration is refused before it is parsed, and the text is decoded as UTF-8 first so an
  encoding trick (UTF-16) cannot hide the declaration from that check.

Text comes out in reading order: paragraphs, table rows (a row of one-line cells becomes
one line, so a table-based layout still reads), tabs as spaces, line breaks as newlines,
list items with a leading bullet, hyperlinks as their visible text followed by the target
address, and text boxes. Text Word hides (`w:vanish`) and text it marks as deleted is skipped,
so a reader sees what the file shows.

Both kinds end in `normalize_text`: Unicode NFKC, control and zero-width characters removed,
runs of blank lines collapsed, words hyphenated across a line break rejoined. Every pattern it
runs is linear in the text (a long run of letters with no space in it is a classic way to make
a backtracking regex quadratic), and the raw text is cut to a few times `MAX_TEXT_CHARS`
before it is normalized, so the cost of normalizing is bounded by the cap, not by the file.
A PDF also reports where its pages broke (`ExtractedDocument.page_breaks`), because a sentence
that wraps over a page break has the page's footer and the next page's header between its two
halves.

`extract_document_text` does not read the file in the API process. A thread cannot be killed
and a regex or a parser in C holds the interpreter's lock, so a file that costs minutes of CPU
or gigabytes of memory could not be stopped by any timeout: it would stall every other request
and outlive the one that sent it. Instead the file goes to a short-lived child process
(`profile_import_worker`) that is started with no secrets in its environment, a memory limit
and a CPU limit, and killed when the wall-clock timeout passes. At most
`MAX_CONCURRENT_EXTRACTIONS` run at once; another request gets a retryable "busy" error rather
than a queue. What the child sends back is parsed and checked like any other untrusted input.
The readers also check a deadline themselves, so that a child that is merely slow ends with a
clear error on its own; the kill is what makes the budget real.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import math
import os
import re
import signal
import sys
import time
import unicodedata
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from xml.etree import ElementTree as ET

from pypdf import PageObject, PdfReader
from pypdf.generic import DictionaryObject

logger = logging.getLogger(__name__)

DocumentKind = Literal["pdf", "docx"]

MAX_PDF_PAGES = 10
MAX_TEXT_CHARS = 60_000
EXTRACTION_TIMEOUT_SECONDS = 20.0
MAX_CONCURRENT_EXTRACTIONS = 3
CHILD_MEMORY_BYTES = 512 * 1024 * 1024
"""The address space a reading child may use. A real resume needs a small fraction of it; the
hostile files this exists for (a small PDF whose content decodes to megabytes of operators)
need several times as much. Enforced on Linux, where the API runs."""
CHILD_CPU_GRACE_SECONDS = 5
"""The child's CPU-time limit is the timeout plus this: a backstop for a child the parent
somehow failed to kill, not the normal way a slow file ends."""
_MAX_REPLY_BYTES = 4 * 1024 * 1024
_REAP_SECONDS = 5.0
_TIMEOUT_MESSAGE = "That file took too long to read. Export a simpler copy and try again."

MAX_ZIP_MEMBERS = 2_000
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_XML_PART_BYTES = 8 * 1024 * 1024
"""A part that is read and parsed (the body, the styles, a header) is held to a tighter cap than
the archive's other members: an XML tree takes many times its text's size in memory, and a
real resume's body is a few hundred kilobytes."""
MAX_TOTAL_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
_RATIO_FLOOR_BYTES = 64 * 1024
"""A member smaller than this is never judged by its compression ratio: a tiny XML part can
legitimately shrink a lot, and the absolute caps already bound what it could cost."""
_MAX_PART_COUNT = 3
"""How many header parts and how many footer parts are read at most."""
_MAX_WALK_DEPTH = 24
_MAX_FIELD_DEPTH = 8
_MAX_LINKS_PER_PAGE = 50
_MAX_URL_CHARS = 300
_MIN_ALNUM_CHARS = 20
"""Fewer letters and digits than this in the whole file and it has no readable text."""
_MIN_LETTER_SHARE = 0.35
"""Of the non-space characters, the share that must be letters or digits: below it the text
is a font-encoding mess (cid codes, symbols) that no reader could use."""

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06")
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_DOCX_MAIN_PART = "word/document.xml"
_DOCX_RELS_PART = "word/_rels/document.xml.rels"
_DOCX_STYLES_PART = "word/styles.xml"
_HEADER_FOOTER_RE = re.compile(r"^word/(header|footer)([0-9]{1,2})\.xml$")

_PDF_CONTENT_TYPES = frozenset({"application/pdf"})
_DOCX_CONTENT_TYPES = frozenset(
    {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
)
_NEUTRAL_CONTENT_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

ExtractionReason = Literal[
    "unsupported_type",
    "empty",
    "encrypted",
    "no_text",
    "unreadable",
    "unsafe",
    "timeout",
    "busy",
]
_REASONS: frozenset[str] = frozenset(
    {"unsupported_type", "empty", "encrypted", "no_text", "unreadable", "unsafe", "timeout"}
)
"""The reasons a reading child may report. "busy" is never one of them: it is the parent's own."""


class ExtractionError(Exception):
    """A file that cannot be turned into text. `reason` says why in a form the route maps to
    a status; `message` is the honest, specific sentence for the person who uploaded it."""

    def __init__(self, reason: ExtractionReason, message: str) -> None:
        super().__init__(message)
        self.reason: ExtractionReason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    """The normalized text. Everything downstream (the model, the grounding guard, the
    source spans) works on exactly this string."""
    kind: DocumentKind
    pages_total: int | None = None
    pages_read: int | None = None
    truncated: bool = False
    """Something was cut: pages beyond the cap, or characters beyond the cap."""
    column_pages: tuple[int, ...] = ()
    """PDF pages (1-based) that were read column by column rather than in drawing order."""
    notices: tuple[str, ...] = field(default_factory=tuple)
    """Plain sentences about what was cut or changed, for the person to read."""
    page_breaks: tuple[int, ...] = ()
    """For a PDF, the offsets into `text` (characters) where a page after the first begins, in
    increasing order. Empty for a DOCX, which has no fixed pages."""


# -- what kind of file is this ---------------------------------------------------------------


def refuse_multipart(content_type: str | None) -> None:
    """The file goes in as the raw request body. A client that sent a multipart form instead
    gets told so, rather than the less helpful "that is not a PDF"."""
    if (content_type or "").strip().lower().startswith("multipart/"):
        raise ExtractionError(
            "unsupported_type",
            "Send the file's raw bytes as the request body (Content-Type application/pdf or "
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document), "
            "not as a multipart form.",
        )


def sniff_kind(data: bytes) -> DocumentKind:
    """The kind of document `data` is, decided by its bytes alone. Raises ExtractionError
    ("unsupported_type") for anything that is not a PDF or a DOCX, with a message that names
    the case (a legacy .doc gets its own)."""
    if not data:
        raise ExtractionError("empty", "That file is empty.")
    if data.startswith(_PDF_MAGIC):
        return "pdf"
    if data.startswith(_OLE_MAGIC):
        raise ExtractionError(
            "unsupported_type",
            "That looks like a legacy Word .doc file. Only PDF or DOCX is supported: "
            "open it in Word and save it as .docx or export it as a PDF.",
        )
    if data.startswith(_ZIP_MAGICS):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except (zipfile.BadZipFile, ValueError, NotImplementedError, RuntimeError) as e:
            raise ExtractionError(
                "unreadable", "That file looks like a DOCX but is damaged and can't be opened."
            ) from e
        if _DOCX_MAIN_PART in names:
            return "docx"
    raise ExtractionError(
        "unsupported_type", "That file is not a PDF or a DOCX. Only PDF or DOCX is supported."
    )


def check_declared_type(kind: DocumentKind, content_type: str | None, filename: str | None) -> None:
    """The second opinion: a Content-Type or a file extension that names a supported kind must
    agree with what the bytes are, and a Content-Type that names something else entirely is
    refused. A missing or generic one (`application/octet-stream`, which browsers send for a
    file they do not recognize) is fine: the bytes decide. Raises ExtractionError
    ("unsupported_type") on a mismatch."""
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if declared in _PDF_CONTENT_TYPES:
        declared_kind: DocumentKind | None = "pdf"
    elif declared in _DOCX_CONTENT_TYPES:
        declared_kind = "docx"
    elif declared in _NEUTRAL_CONTENT_TYPES:
        declared_kind = None
    else:
        raise ExtractionError(
            "unsupported_type",
            f"The upload says it is {declared[:60]!r}. Only PDF or DOCX is supported.",
        )
    if declared_kind is not None and declared_kind != kind:
        raise ExtractionError(
            "unsupported_type",
            f"The upload says it is a {declared_kind.upper()} but its content is a "
            f"{kind.upper()}. Re-export it and try again.",
        )

    extension = _extension(filename)
    if extension == ".doc":
        raise ExtractionError(
            "unsupported_type",
            "Legacy .doc files are not supported. Only PDF or DOCX is: save it as .docx or "
            "export it as a PDF.",
        )
    if extension in (".pdf", ".docx") and extension[1:] != kind:
        raise ExtractionError(
            "unsupported_type",
            f"The file is named {extension} but its content is a {kind.upper()}. "
            "Rename it or re-export it and try again.",
        )


def _extension(filename: str | None) -> str:
    if not filename:
        return ""
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def clean_filename(filename: str | None) -> str | None:
    """A file name safe to echo back or log: no directories, no control characters, at most 120
    characters. None when nothing usable is left."""
    if not filename:
        return None
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    return name[:120] or None


# -- the deadline ----------------------------------------------------------------------------


class _Deadline:
    """A cooperative deadline. The readers check it inside their loops and stop themselves, so a
    slow file ends with a clear error even where nothing kills the process; the parent of a
    reading child also kills it at the same moment, which is what stops a stretch of work that
    cannot check (a single call into the PDF library)."""

    def __init__(self, seconds: float) -> None:
        self._end = time.monotonic() + seconds

    def check(self) -> None:
        if time.monotonic() > self._end:
            raise ExtractionError("timeout", _TIMEOUT_MESSAGE)


def _check(deadline: _Deadline | None) -> None:
    if deadline is not None:
        deadline.check()


# -- normalization ---------------------------------------------------------------------------

_HYPHENS = str.maketrans({"\N{HYPHEN}": "-", "\N{NON-BREAKING HYPHEN}": "-"})
"""The hyphens a renderer puts at the end of a line (browsers use U+2010) are the plain hyphen
for everything downstream."""
_PAGE_MARK = "\ufdd0"
"""A Unicode noncharacter, so no document's own text holds one (and any that does is removed
before pages are joined). Between two PDF pages it stands alone on a line while the text is
normalized, and comes out as the blank line it replaces, with its offset recorded."""
_LETTER = r"[^\W\d_]"
_AT_WORD_START = rf"(?<!{_LETTER})"
r"""Every pattern below that reads a run of letters may only begin where the run begins. Without
this, `([^\W\d_]+)-$` starts at every letter of a long run and fails at the far end of each
attempt: quadratic in the length of the run, inside C code that holds the interpreter's lock."""
_WORD_RE = re.compile(rf"{_LETTER}+")
_HYPHENATED_RE = re.compile(rf"{_AT_WORD_START}{_LETTER}+(?:-{_LETTER}+)+")
_LINE_END_HYPHEN_RE = re.compile(rf"{_AT_WORD_START}({_LETTER}+)-$")
_HYPHEN_WINDOW = 80
"""How far back from the end of a line its last word is looked for: no real word is longer,
and the cost of rejoining a line stays independent of how long the line has grown."""


def normalize_text(raw: str) -> str:
    """NFKC; control, zero-width and bidi-control characters removed; every kind of space a
    plain space; blank-line runs collapsed; words hyphenated across a line break rejoined."""
    return _normalize(raw, pages=False)[0]


def _normalize(raw: str, *, pages: bool) -> tuple[str, tuple[int, ...]]:
    """`normalize_text`, plus the offsets in the result where a page begins. A page begins
    after a line made of `_PAGE_MARK`, which only counts when `pages` is true."""
    text = unicodedata.normalize("NFKC", raw).replace("\r\n", "\n")
    text = text.translate(_HYPHENS)
    out: list[str] = []
    for ch in text:
        if ch == "\n" or (pages and ch == _PAGE_MARK):
            out.append(ch)
        elif ch in "\r\t\f\v":
            out.append("\n" if ch == "\r" else " ")
        else:
            category = unicodedata.category(ch)
            if category in ("Zl", "Zp"):
                out.append("\n")
            elif category == "Zs":
                out.append(" ")
            elif category[0] != "C":
                out.append(ch)
    lines = [re.sub(r" {2,}", " ", line).strip() for line in "".join(out).split("\n")]
    return _assemble(_rejoin_hyphenated(lines))


def _assemble(lines: list[str]) -> tuple[str, tuple[int, ...]]:
    """The lines joined by newlines, with every run of blank lines (a page mark is one) cut to a
    single blank line and none at either end; and where each page mark fell, as the offset of
    the first line after it."""
    parts: list[str] = []
    breaks: list[int] = []
    length = 0
    gap = False  # a blank line or a page mark has come since the last line of text
    page = False  # ... and one of them was a page mark
    for line in lines:
        if line == _PAGE_MARK:
            gap = page = True
        elif not line:
            gap = True
        else:
            if parts:
                separator = "\n\n" if gap else "\n"
                parts.append(separator)
                length += len(separator)
                if page:
                    breaks.append(length)
            parts.append(line)
            length += len(line)
            gap = page = False
    return "".join(parts), tuple(breaks)


def _hyphen_tail(line: str) -> re.Match[str] | None:
    """The last word of `line` and the hyphen after it, when the line ends in one."""
    if not line.endswith("-"):
        return None
    return _LINE_END_HYPHEN_RE.search(line, max(0, len(line) - _HYPHEN_WINDOW))


def _rejoin_hyphenated(lines: list[str]) -> list[str]:
    """`infra-` at the end of a line and `structure` at the start of the next become
    `infrastructure`. When the rest of the text shows the hyphen is part of the word (the
    document spells it `cross-functional` elsewhere), the hyphen stays. A line that continues
    with a capital or a digit is left alone."""
    if not any(_hyphen_tail(line) for line in lines):
        return lines
    text = "\n".join(lines)
    words = {w.lower() for w in _WORD_RE.findall(text)}
    compounds = {w.lower() for w in _HYPHENATED_RE.findall(text)}
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        settled: list[str] = []  # the stretches of this line that no later join can change
        while i < len(lines):  # a word split over three lines keeps joining
            match = _hyphen_tail(line)
            if match is None:
                break
            # One blank line between the halves is a gap in the other column of the page
            # (the two columns share rows), not a paragraph break; a page break is one too.
            gap = 1 if lines[i] in ("", _PAGE_MARK) and i + 1 < len(lines) else 0
            following = lines[i + gap]
            head = _WORD_RE.match(following)
            if head is None or not following[0].islower():
                break
            before, after = match.group(1), head.group(0)
            keep_hyphen = (
                f"{before}-{after}".lower() in compounds and (before + after).lower() not in words
            )
            settled.append(line[: match.start(1)])
            line = f"{before}{'-' if keep_hyphen else ''}{following}"
            i += 1 + gap
        out.append("".join(settled) + line)
    return out


def _letter_digit_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


def _check_readable(text: str) -> None:
    if _letter_digit_count(text) < _MIN_ALNUM_CHARS:
        raise ExtractionError(
            "no_text",
            "That file has no selectable text (it looks like a scan or an image). "
            "Export it as a text-based PDF or run OCR on it first, then upload it again.",
        )
    non_space = sum(1 for ch in text if not ch.isspace())
    if non_space and _letter_digit_count(text) / non_space < _MIN_LETTER_SHARE:
        raise ExtractionError(
            "no_text",
            "The text in that file can't be read (its fonts use an encoding that does not "
            "map to letters). Export it as a PDF again, or save it as DOCX.",
        )


def _finish(
    raw: str,
    *,
    extra_notices: list[str],
    deadline: _Deadline | None = None,
    pages: bool = False,
) -> tuple[str, bool, list[str], tuple[int, ...]]:
    """Normalizes, applies the character caps, checks there is readable text. Returns the text,
    whether it was cut, the notices, and (when `pages`) where each page after the first begins.

    The raw text is cut BEFORE it is normalized, to a few times the cap on the normalized text
    (normalizing drops characters, so it needs some room): the work of normalizing is bounded
    by the cap, not by what the file holds."""
    notices = list(extra_notices)
    raw_limit = MAX_TEXT_CHARS * 3
    raw_cut = len(raw) > raw_limit
    if raw_cut:
        raw = raw[:raw_limit]
    _check(deadline)
    text, breaks = _normalize(raw, pages=pages)
    _check(deadline)
    _check_readable(text)
    truncated = raw_cut
    if len(text) > MAX_TEXT_CHARS:
        cut = text.rfind("\n", 0, MAX_TEXT_CHARS)
        text = text[: cut if cut > MAX_TEXT_CHARS // 2 else MAX_TEXT_CHARS].rstrip()
        truncated = True
        notices.append(
            f"The document is long: only the first {MAX_TEXT_CHARS:,} characters were read."
        )
    elif raw_cut:
        notices.append("The document is long: only the start of it was read.")
    return text, truncated, notices, tuple(b for b in breaks if b < len(text))


# -- PDF -------------------------------------------------------------------------------------

_MIN_GUTTER_COLUMNS = 4
_MIN_GRID_TEXT_ROWS = 8
_MIN_COLUMN_RUN = 3
_MIN_ROWS_WITH_BOTH_SIDES = 4
_MAX_COLUMN_DEPTH = 2
_GRID_LOSS_TOLERANCE = 0.97
_MAX_GRID_ROWS = 500
_MAX_GRID_COLUMNS = 500
"""A page's character grid larger than this (a real page is about 80 rows by 150 columns) is
not searched for columns: the search is quadratic and a hostile page can make the grid huge."""


@dataclass(frozen=True, slots=True)
class _GridRow:
    text: str
    spacer: bool
    """The whole original row was blank: a real gap between paragraphs, kept as a blank line.
    A cell that is blank only because the other column has text on that row is not one."""


def _read_pdf(data: bytes, deadline: _Deadline) -> ExtractedDocument:
    try:
        return _read_pdf_unguarded(data, deadline)
    except ExtractionError:
        raise
    except Exception as e:  # pypdf raises many unrelated types on a damaged or hostile file
        raise ExtractionError(
            "unreadable", "That PDF is damaged or unusual and could not be read."
        ) from e


def _read_pdf_unguarded(data: bytes, deadline: _Deadline) -> ExtractedDocument:
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ExtractionError(
            "encrypted",
            "That PDF is password-protected or encrypted. Export an unprotected copy "
            "(print it to a new PDF) and upload that.",
        )
    total = len(reader.pages)
    pages_read = min(total, MAX_PDF_PAGES)
    page_texts: list[str] = []
    column_pages: list[int] = []
    raw_chars = 0
    for index in range(pages_read):
        deadline.check()
        page = reader.pages[index]
        plain = page.extract_text(visitor_operand_before=_stop_at(deadline)) or ""
        text = plain
        deadline.check()
        if plain.strip():
            reordered = _read_in_columns(page, deadline)
            deadline.check()
            if reordered is not None and _keeps_the_content(plain, reordered):
                text = reordered
                column_pages.append(index + 1)
        links = _page_links(page, text)
        if links:
            text = text + "\n\n" + "\n".join(links)
        page_texts.append(text.replace(_PAGE_MARK, ""))
        raw_chars += len(text)
        if raw_chars > MAX_TEXT_CHARS * 3:
            pages_read = index + 1
            break

    notices: list[str] = []
    if total > pages_read:
        notices.append(
            f"The PDF has {total} pages: only the first {pages_read} were read. "
            "Anything after that is not in the draft."
        )
    text, cut, notices, breaks = _finish(
        f"\n{_PAGE_MARK}\n".join(page_texts),
        extra_notices=notices,
        deadline=deadline,
        pages=True,
    )
    return ExtractedDocument(
        text=text,
        kind="pdf",
        pages_total=total,
        pages_read=pages_read,
        truncated=cut or total > pages_read,
        column_pages=tuple(column_pages),
        notices=tuple(notices),
        page_breaks=breaks,
    )


def _stop_at(deadline: _Deadline) -> Callable[[Any, Any, Any, Any], None]:
    """A visitor for the PDF library's plain text reading, which calls it before every operator
    in the page's content: a page that holds an enormous number of them stops at the deadline
    instead of at the end of the page. (Its layout reading has no such hook.)"""

    def visit(_operator: Any, _args: Any, _cm_matrix: Any, _tm_matrix: Any) -> None:
        deadline.check()

    return visit


def _read_in_columns(page: PageObject, deadline: _Deadline | None = None) -> str | None:
    """The page read column by column, or None: no gutter, or the layout reading failed (the
    plain reading, which already worked, is then used as it is)."""
    try:
        return _column_reading_order(page.extract_text(extraction_mode="layout") or "", deadline)
    except ExtractionError:
        raise  # the deadline passed: that is not a reason to fall back to the plain reading
    except Exception as e:  # layout mode is the fragile one; its failure must not fail the page
        logger.info("pdf layout reading failed", extra={"ctx": {"error": type(e).__name__}})
        return None


def _keeps_the_content(plain: str, reordered: str) -> bool:
    """The column reading must still hold what the plain reading held: layout mode drops text
    it cannot place (rotated text, fonts it cannot decode), and a reading that lost some is
    worse than interleaved lines."""
    return _letter_digit_count(reordered) >= _GRID_LOSS_TOLERANCE * _letter_digit_count(plain)


def _page_links(page: PageObject, visible_text: str) -> list[str]:
    """http(s), mailto and tel targets of the page's link annotations that are not already
    spelled out in the page text, in order, deduplicated."""
    try:
        annotations = page.annotations
    except Exception as e:  # a broken annotation array must not fail the page
        logger.debug("pdf annotations unreadable", extra={"ctx": {"error": type(e).__name__}})
        return []
    if not annotations:
        return []
    visible = _squash_for_link_check(unicodedata.normalize("NFKC", visible_text))
    found: list[str] = []
    for annotation in list(annotations)[: _MAX_LINKS_PER_PAGE * 2]:
        try:
            obj = annotation.get_object()
            if not isinstance(obj, DictionaryObject) or obj.get("/Subtype") != "/Link":
                continue
            action = obj.get("/A")
            action_obj = action.get_object() if action is not None else None
            target = action_obj.get("/URI") if isinstance(action_obj, DictionaryObject) else None
        except Exception as e:  # one bad annotation must not lose the others
            logger.debug("pdf annotation unreadable", extra={"ctx": {"error": type(e).__name__}})
            continue
        if not isinstance(target, str):
            continue
        url = safe_link_target(target)
        if url is None or url in found or _squash_for_link_check(url) in visible:
            continue
        found.append(url)
        if len(found) >= _MAX_LINKS_PER_PAGE:
            break
    return found


def _squash_for_link_check(text: str) -> str:
    """A link or a stretch of text in a form where "is this link already written out here"
    is a plain substring test: lowercase, no scheme or `www.`, no whitespace."""
    text = re.sub(r"\s+", "", text.lower())
    return re.sub(r"^(?:https?://|mailto:|tel:)(?:www\.)?", "", text)


def safe_link_target(target: str) -> str | None:
    """A link target worth showing a reader: http(s), mailto or tel only (never javascript:,
    data:, file: ...), no whitespace or control characters, bounded length. A mailto target is
    returned as the bare address and a tel target as the bare number."""
    target = target.strip()
    if not target or len(target) > _MAX_URL_CHARS:
        return None
    if any(ch.isspace() or unicodedata.category(ch)[0] == "C" for ch in target):
        return None
    lowered = target.lower()
    if lowered.startswith(("http://", "https://")):
        return target
    if lowered.startswith("mailto:"):
        address = target[7:].split("?", 1)[0]
        return address or None
    if lowered.startswith("tel:"):
        return target[4:] or None
    return None


def _column_reading_order(grid_text: str, deadline: _Deadline | None = None) -> str | None:
    """The page read column by column, or None when the grid has no real column gutter."""
    rows = [_GridRow(line.rstrip(), spacer=not line.strip()) for line in grid_text.split("\n")]
    if len(rows) > _MAX_GRID_ROWS or any(len(row.text) > _MAX_GRID_COLUMNS for row in rows):
        return None
    lines, used = _read_rows(rows, 0, deadline)
    if not used:
        return None
    return "\n".join(lines)


def _read_rows(
    rows: list[_GridRow], depth: int, deadline: _Deadline | None
) -> tuple[list[str], bool]:
    gutter = _find_gutter(rows, deadline) if depth < _MAX_COLUMN_DEPTH else None
    if gutter is None:
        return _emit_rows(rows), False

    first, last = gutter
    out: list[str] = []
    left: list[_GridRow] = []
    right: list[_GridRow] = []

    def flush() -> None:
        for column in (left, right):
            if not column:
                continue
            lines, _ = _read_rows(column, depth + 1, deadline)
            out.extend(lines)
            out.append("")
        left.clear()
        right.clear()

    for row in rows:
        if row.text[first : last + 1].strip():
            # A line that crosses the gutter (a banner, a footer): it ends the band above it
            # and is read in place, between the two.
            flush()
            out.append(row.text.strip())
            continue
        left.append(_GridRow(row.text[:first].rstrip(), row.spacer))
        right.append(_GridRow(row.text[last + 1 :].rstrip(), row.spacer))
    flush()
    return out, True


def _emit_rows(rows: list[_GridRow]) -> list[str]:
    out: list[str] = []
    for row in rows:
        stripped = row.text.strip()
        if stripped:
            out.append(stripped)
        elif row.spacer:
            out.append("")
    return out


_SEGMENT_RE = re.compile(r"\S+(?: \S+)*")
"""A stretch of text on a grid row: words one space apart. Two or more spaces end it."""


def _find_gutter(rows: list[_GridRow], deadline: _Deadline | None = None) -> tuple[int, int] | None:
    """The first and last column index of a blank vertical gutter that splits `rows` into two
    real columns of text, or None. A gutter is a run of at least `_MIN_GUTTER_COLUMNS` columns
    that almost no row writes into (a banner or a footer may cross it), with text on both sides
    that forms a stack of consecutive lines: a column of right-aligned dates, or a date column
    beside a timeline, has only isolated single lines and is not a column."""
    text_rows = [r for r in rows if r.text.strip()]
    if len(text_rows) < _MIN_GRID_TEXT_ROWS:
        return None
    width = max(len(r.text) for r in rows)
    occupancy = [0] * width
    for row in text_rows:
        for i, ch in enumerate(row.text):
            if ch != " ":
                occupancy[i] += 1
    filled = [i for i, count in enumerate(occupancy) if count]
    if not filled:
        return None
    first_col, last_col = filled[0], filled[-1]
    tolerance = max(2, int(len(text_rows) * 0.05))

    zones: list[tuple[int, int]] = []
    i = first_col + 1
    while i < last_col:
        if occupancy[i] <= tolerance:
            j = i
            while j < last_col and occupancy[j] <= tolerance:
                j += 1
            if j - i >= _MIN_GUTTER_COLUMNS:
                zones.append((i, j - 1))
            i = j
        else:
            i += 1

    segments = [
        [(m.start(), m.end() - 1) for m in _SEGMENT_RE.finditer(row.text)] for row in text_rows
    ]
    for zone in sorted(zones, key=lambda z: z[0] - z[1]):
        _check(deadline)
        gutter = _trim_zone(segments, zone, tolerance)
        if gutter is not None and _is_two_columns(rows, *gutter):
            return gutter
    return None


def _trim_zone(
    segments: list[list[tuple[int, int]]], zone: tuple[int, int], allowance: int
) -> tuple[int, int] | None:
    """The part of a nearly blank strip that is really blank. The longest lines of the left
    column poke into the strip (its ragged right edge), and so may the right column's (its
    ragged left edge): the strip is trimmed back to where they stop, so a cut never lands inside
    a word. A line that crosses the whole strip is a banner and is read in place, not trimmed
    around -- and so is a line that only reaches into the strip (a banner wider than the left
    column but narrower than the page), up to `allowance` of them: the fewest lines are set aside
    that leave a gutter wide enough, and of those the widest gutter wins.

    `segments` is, for each row of text, the (first, last) column of every stretch of text on it.

    Which lines to set aside is settled without trying every combination of them (that grows
    exponentially with `allowance`, and a hostile page can arrange for none to work): whatever
    is set aside, the strip's edge is decided by the most intrusive line left on each side, so
    the best choices are always "the k most intrusive on the left and the m most intrusive on
    the right", and there are only (allowance + 1) squared of those."""
    first, last = zone
    left: dict[int, int] = {}
    right: dict[int, int] = {}
    for index, row in enumerate(segments):
        for start, end in row:
            if end < first or start > last or (start < first and end > last):
                continue
            if start < first:
                left[index] = max(left.get(index, -1), end)
            elif end > last:
                right[index] = min(right.get(index, end), start)
    chosen = _best_trim(left, right, zone, allowance)
    return None if chosen is None else (chosen[2], chosen[3])


def _best_trim(
    left: dict[int, int], right: dict[int, int], zone: tuple[int, int], allowance: int
) -> tuple[int, int, int, int] | None:
    """(lines set aside, gutter width, first column, last column) of the best gutter, or None.
    `left` maps each row that pokes into the strip from the left to the last column it reaches,
    `right` each row that pokes in from the right to the first column it reaches."""
    first, last = zone
    by_left = sorted(left, key=lambda i: -left[i])
    by_right = sorted(right, key=lambda i: right[i])
    best: tuple[int, int, int, int] | None = None  # (lines set aside, -width, first, last)
    for set_left in range(min(allowance, len(by_left)) + 1):
        for set_right in range(min(allowance, len(by_right)) + 1):
            ignored = {*by_left[:set_left], *by_right[:set_right]}
            if len(ignored) > allowance:
                continue
            lo = max([first, *(left[i] + 1 for i in by_left[: allowance + 1] if i not in ignored)])
            hi = min([last, *(right[i] - 1 for i in by_right[: allowance + 1] if i not in ignored)])
            width = hi - lo + 1
            if width >= _MIN_GUTTER_COLUMNS:
                candidate = (len(ignored), -width, lo, hi)
                if best is None or candidate < best:
                    best = candidate
    return None if best is None else (best[0], -best[1], best[2], best[3])


def _is_two_columns(rows: list[_GridRow], first: int, last: int) -> bool:
    """Both sides of the gutter are real columns: each has a stack of at least
    `_MIN_COLUMN_RUN` consecutive lines (a blank line between paragraphs does not end a stack,
    a line with nothing on that side does), and at least a few lines have text on both sides.
    A line that crosses the gutter ends every stack."""
    best = {"left": 0, "right": 0}
    run = {"left": 0, "right": 0}
    both = 0
    for row in rows:
        if row.text[first : last + 1].strip():
            run = {"left": 0, "right": 0}
            continue
        has = {
            "left": bool(row.text[:first].strip()),
            "right": bool(row.text[last + 1 :].strip()),
        }
        for side in ("left", "right"):
            if has[side]:
                run[side] += 1
                best[side] = max(best[side], run[side])
            elif not row.spacer:
                run[side] = 0
        if has["left"] and has["right"]:
            both += 1
    return (
        both >= _MIN_ROWS_WITH_BOTH_SIDES
        and best["left"] >= _MIN_COLUMN_RUN
        and best["right"] >= _MIN_COLUMN_RUN
    )


# -- DOCX ------------------------------------------------------------------------------------

_W_NS = frozenset(
    {
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "http://purl.oclc.org/ooxml/wordprocessingml/main",
    }
)
_R_NS = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "http://purl.oclc.org/ooxml/officeDocument/relationships",
    }
)
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_HYPERLINK_FIELD_RE = re.compile(
    r'HYPERLINK\s+(?:\\[A-Za-z]\s+)*(?:"([^"]+)"|(\S+))', re.IGNORECASE
)
_BULLET = "\N{BULLET} "

_PASSTHROUGH_BLOCKS = frozenset({"ins", "moveTo", "customXml", "smartTag", "sdtContent", "sdt"})
_SKIPPED_REVISIONS = frozenset({"del", "moveFrom"})


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return namespace, local
    return "", tag


def _wname(element: ET.Element) -> str | None:
    namespace, local = _split_tag(element.tag)
    return local if namespace in _W_NS else None


def _w_attr(element: ET.Element, local: str) -> str | None:
    for key, value in element.attrib.items():
        namespace, name = _split_tag(key)
        if name == local and namespace in _W_NS:
            return value
    return None


def _r_attr(element: ET.Element, local: str) -> str | None:
    for key, value in element.attrib.items():
        namespace, name = _split_tag(key)
        if name == local and namespace in _R_NS:
            return value
    return None


def _unsafe(message: str) -> ExtractionError:
    return ExtractionError("unsafe", message)


def _read_docx(data: bytes, deadline: _Deadline) -> ExtractedDocument:
    try:
        return _read_docx_unguarded(data, deadline)
    except ExtractionError:
        raise
    except (
        zipfile.BadZipFile,
        zlib.error,  # a part whose compressed data is damaged
        EOFError,  # a part that ends before its declared size
        ValueError,
        NotImplementedError,
        RuntimeError,
        OSError,
    ) as e:
        raise ExtractionError(
            "unreadable", "That DOCX is damaged or unusual and could not be read."
        ) from e


def _read_docx_unguarded(data: bytes, deadline: _Deadline) -> ExtractedDocument:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = _vet_archive(archive, len(data))
        main_info = members.get(_DOCX_MAIN_PART)
        if main_info is None:
            raise ExtractionError(
                "unsupported_type",
                "That file is not a PDF or a DOCX. Only PDF or DOCX is supported.",
            )

        styles = _numbered_styles(_read_xml(archive, members, _DOCX_STYLES_PART))
        reader = _DocxReader(styles=styles, deadline=deadline)

        header_parts = _numbered_parts(members, "header")
        footer_parts = _numbered_parts(members, "footer")
        header_text = [
            reader.read_part(archive, members, part, f"word/_rels/{part[5:]}.rels")
            for part in header_parts
        ]
        body = reader.read_part(archive, members, _DOCX_MAIN_PART, _DOCX_RELS_PART)
        footer_text = [
            reader.read_part(archive, members, part, f"word/_rels/{part[5:]}.rels")
            for part in footer_parts
        ]

    pieces: list[str] = []
    seen: set[str] = set()
    for chunk in [*header_text, body, *footer_text]:
        if chunk.strip() and chunk not in seen:
            seen.add(chunk)
            pieces.append(chunk)
    notices: list[str] = []
    if reader.truncated:
        notices.append("The document is long: only the start of it was read.")
    text, cut, notices, _ = _finish("\n\n".join(pieces), extra_notices=notices, deadline=deadline)
    return ExtractedDocument(
        text=text, kind="docx", truncated=cut or reader.truncated, notices=tuple(notices)
    )


def _vet_archive(archive: zipfile.ZipFile, archive_bytes: int) -> dict[str, zipfile.ZipInfo]:
    """Checks the zip directory against every cap and returns name -> entry. Refuses an
    archive Word could not have written: too many or too large members, a suspicious
    compression ratio, an unsafe or repeated name, an encrypted member."""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_MEMBERS:
        raise _unsafe(
            f"That DOCX holds {len(infos):,} parts, far more than a document needs, "
            "so it was not opened. Export it as a PDF instead."
        )
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        name = info.filename
        if _unsafe_member_name(name):
            raise _unsafe("That DOCX contains a part with an unsafe name, so it was not opened.")
        if name in members:
            raise _unsafe("That DOCX contains the same part twice, so it was not opened.")
        members[name] = info
        total += info.file_size
        if info.file_size > MAX_MEMBER_BYTES:
            raise _unsafe("That DOCX contains a part that is far too large, so it was not opened.")
        if info.file_size > _RATIO_FLOOR_BYTES and info.file_size > MAX_COMPRESSION_RATIO * max(
            info.compress_size, 1
        ):
            raise _unsafe("That DOCX is compressed in a suspicious way, so it was not opened.")
    if total > MAX_TOTAL_UNCOMPRESSED_BYTES or total > MAX_COMPRESSION_RATIO * max(
        archive_bytes, 1
    ):
        raise _unsafe(
            "That DOCX expands to far more data than a document needs, so it was not opened."
        )
    return members


def _unsafe_member_name(name: str) -> bool:
    if not name or len(name) > 255 or "\x00" in name or "\\" in name:
        return True
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return True
    return ".." in name.split("/")


def _numbered_parts(members: dict[str, zipfile.ZipInfo], kind: str) -> list[str]:
    parts = []
    for name in members:
        match = _HEADER_FOOTER_RE.match(name)
        if match and match.group(1) == kind:
            parts.append((int(match.group(2)), name))
    return [name for _, name in sorted(parts)[:_MAX_PART_COUNT]]


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """One member's bytes, with the size enforced while streaming: the directory's declared
    size is a claim, not a fact."""
    if info.flag_bits & 0x1:
        raise _unsafe("That DOCX has an encrypted part, so it was not opened.")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise _unsafe("That DOCX uses an unusual compression method, so it was not opened.")
    if info.file_size > MAX_XML_PART_BYTES:
        raise _unsafe(
            "That DOCX has a text part far larger than a document needs, so it was not opened."
        )
    limit = min(info.file_size, MAX_XML_PART_BYTES)
    chunks: list[bytes] = []
    total = 0
    with archive.open(info) as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _unsafe("That DOCX has a part larger than it declares, so it was not opened.")
            chunks.append(chunk)
    return b"".join(chunks)


def _read_xml(
    archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str
) -> ET.Element | None:
    info = members.get(name)
    if info is None:
        return None
    return _parse_xml(_read_member(archive, info))


def _parse_xml(raw: bytes) -> ET.Element:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ExtractionError(
            "unreadable", "That DOCX has a part in an encoding that is not supported."
        ) from e
    text = text.removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}")
    if "\x00" in text:
        raise ExtractionError("unreadable", "That DOCX has a damaged part.")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise _unsafe(
            "That DOCX contains an XML declaration that can be used to attack a reader, so it "
            "was not opened. Export it as a PDF instead."
        )
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise ExtractionError("unreadable", "That DOCX has a damaged part.") from e


def _numbered_styles(root: ET.Element | None) -> frozenset[str]:
    """The paragraph style ids that carry a list numbering, directly or through the style they
    are based on: a paragraph in such a style is a list item even with no numbering of its
    own (python-docx's and Word's own bullet styles work this way)."""
    if root is None:
        return frozenset()
    direct: dict[str, bool] = {}
    based_on: dict[str, str] = {}
    for style in root:
        if _wname(style) != "style":
            continue
        style_id = _w_attr(style, "styleId")
        if not style_id:
            continue
        direct[style_id] = False
        for child in style:
            name = _wname(child)
            if name == "basedOn":
                parent = _w_attr(child, "val")
                if parent:
                    based_on[style_id] = parent
            elif name == "pPr":
                direct[style_id] = _has_numbering(child)
    numbered = set()
    for style_id in direct:
        current: str | None = style_id
        for _ in range(8):
            if current is None:
                break
            if direct.get(current):
                numbered.add(style_id)
                break
            current = based_on.get(current)
    return frozenset(numbered)


def _has_numbering(ppr: ET.Element) -> bool:
    for child in ppr:
        if _wname(child) == "numPr":
            for grand in child:
                if _wname(grand) == "numId":
                    return _w_attr(grand, "val") not in (None, "0")
            return False
    return False


@dataclass(slots=True)
class _Field:
    instruction: list[str] = field(default_factory=list)
    result_from: int | None = None
    url: str | None = None
    ignored: bool = False
    """A field nested deeper than `_MAX_FIELD_DEPTH`: its text is read, its target is not. Every
    hyperlink field that ends has to look through all the text since it began, so a paragraph
    of thousands of fields wrapped around each other costs fields times runs."""


class _Paragraph:
    """Text collected from one paragraph's runs, plus the lines of any text boxes inside it."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.fields: list[_Field] = []
        self.boxed: list[str] = []

    def add(self, text: str) -> None:
        if not self.fields or self.fields[-1].result_from is not None:
            self.parts.append(text)


class _DocxReader:
    def __init__(self, *, styles: frozenset[str], deadline: _Deadline) -> None:
        self._styles = styles
        self._deadline = deadline
        self._links: dict[str, str] = {}
        self._chars = 0
        self.truncated = False

    # -- one part -----------------------------------------------------------------------------

    def read_part(
        self,
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        part: str,
        rels_part: str,
    ) -> str:
        root = _read_xml(archive, members, part)
        if root is None:
            return ""
        self._links = self._read_links(_read_xml(archive, members, rels_part))
        container = root
        for child in root:
            if _wname(child) == "body":
                container = child
                break
        return "\n".join(self._blocks(container, 0))

    @staticmethod
    def _read_links(root: ET.Element | None) -> dict[str, str]:
        links: dict[str, str] = {}
        if root is None:
            return links
        for rel in root:
            if _split_tag(rel.tag) != (_PKG_REL_NS, "Relationship"):
                continue
            if not rel.attrib.get("Type", "").endswith("/hyperlink"):
                continue
            if rel.attrib.get("TargetMode") != "External":
                continue
            url = safe_link_target(rel.attrib.get("Target", ""))
            rel_id = rel.attrib.get("Id")
            if url and rel_id:
                links[rel_id] = url
        return links

    # -- blocks: paragraphs and tables --------------------------------------------------------

    def _blocks(self, container: ET.Element, depth: int) -> list[str]:
        lines: list[str] = []
        if depth > _MAX_WALK_DEPTH or self.truncated:
            return lines
        for child in container:
            self._deadline.check()
            if self.truncated:
                break
            name = _wname(child)
            if name == "p":
                lines.extend(self._paragraph_lines(child, depth))
            elif name == "tbl":
                lines.extend(self._table_lines(child, depth))
            elif name in _PASSTHROUGH_BLOCKS:
                lines.extend(self._blocks(child, depth + 1))
            elif name is None and _split_tag(child.tag) == (_MC_NS, "AlternateContent"):
                for branch in self._choice(child):
                    lines.extend(self._blocks(branch, depth + 1))
        return lines

    @staticmethod
    def _choice(alternate: ET.Element) -> list[ET.Element]:
        """The branch of an `mc:AlternateContent` a reader uses: the first `Choice`, else the
        `Fallback`. Both branches usually hold the same content, so reading both would say
        everything twice."""
        for child in alternate:
            if _split_tag(child.tag) == (_MC_NS, "Choice"):
                return [child]
        return [c for c in alternate if _split_tag(c.tag) == (_MC_NS, "Fallback")]

    def _table_lines(self, table: ET.Element, depth: int) -> list[str]:
        lines: list[str] = []
        for row in table:
            if _wname(row) != "tr":
                continue
            cells = [self._blocks(cell, depth + 1) for cell in row if _wname(cell) == "tc"]
            filled = [[line for line in cell if line.strip()] for cell in cells]
            filled = [cell for cell in filled if cell]
            if not filled:
                continue
            if len(filled) > 1 and all(len(cell) == 1 for cell in filled):
                lines.append(" | ".join(cell[0] for cell in filled))
            else:
                for cell in filled:
                    lines.extend(cell)
                    lines.append("")
        return lines

    # -- one paragraph ------------------------------------------------------------------------

    def _paragraph_lines(self, element: ET.Element, depth: int) -> list[str]:
        paragraph = _Paragraph()
        listed = False
        for child in element:
            name = _wname(child)
            if name == "pPr":
                listed = self._is_list_item(child)
            else:
                self._inline(child, paragraph, depth)
        text = "".join(paragraph.parts)
        lines = [line.strip() for line in text.split("\n")]
        if listed and lines and lines[0]:
            lines[0] = _BULLET + lines[0]
        lines.extend(paragraph.boxed)
        for line in lines:
            self._chars += len(line) + 1
        if self._chars > MAX_TEXT_CHARS * 3:
            self.truncated = True
        return lines

    def _is_list_item(self, ppr: ET.Element) -> bool:
        if _has_numbering(ppr):
            return True
        for child in ppr:
            if _wname(child) == "pStyle":
                return _w_attr(child, "val") in self._styles
        return False

    def _inline(self, element: ET.Element, paragraph: _Paragraph, depth: int) -> None:
        if depth > _MAX_WALK_DEPTH:
            return
        # The block loop checks the clock once per paragraph, and a paragraph can hold any number
        # of runs, links and fields: every one of them passes through here.
        self._deadline.check()
        name = _wname(element)
        if name == "r":
            self._run(element, paragraph, depth)
        elif name == "hyperlink":
            self._hyperlink(element, paragraph, depth)
        elif name == "fldSimple":
            self._simple_field(element, paragraph, depth)
        elif name in _PASSTHROUGH_BLOCKS:
            for child in element:
                self._inline(child, paragraph, depth + 1)
        elif name is None and _split_tag(element.tag) == (_MC_NS, "AlternateContent"):
            for branch in self._choice(element):
                for child in branch:
                    self._inline(child, paragraph, depth + 1)
        # `del` and `moveFrom` (deleted text), bookmarks, proofing marks: nothing to read.

    def _hyperlink(self, element: ET.Element, paragraph: _Paragraph, depth: int) -> None:
        rel_id = _r_attr(element, "id")
        url = self._links.get(rel_id) if rel_id else None
        self._linked(element, paragraph, depth, url)

    def _simple_field(self, element: ET.Element, paragraph: _Paragraph, depth: int) -> None:
        match = _HYPERLINK_FIELD_RE.search(_w_attr(element, "instr") or "")
        url = safe_link_target(match.group(1) or match.group(2)) if match else None
        self._linked(element, paragraph, depth, url)

    def _linked(
        self, element: ET.Element, paragraph: _Paragraph, depth: int, url: str | None
    ) -> None:
        start = len(paragraph.parts)
        for child in element:
            self._inline(child, paragraph, depth + 1)
        if url:
            visible = "".join(paragraph.parts[start:])
            if _squash_for_link_check(url) not in _squash_for_link_check(visible):
                paragraph.parts.append(f" ({url})")

    def _run(self, run: ET.Element, paragraph: _Paragraph, depth: int) -> None:
        if self._is_hidden(run):
            return
        for child in run:
            name = _wname(child)
            if name == "t":
                paragraph.add(child.text or "")
            elif name in ("tab", "ptab"):
                paragraph.add(" ")
            elif name in ("br", "cr"):
                paragraph.add("\n")
            elif name == "noBreakHyphen":
                paragraph.add("-")
            elif name == "instrText":
                if paragraph.fields and not paragraph.fields[-1].ignored:
                    paragraph.fields[-1].instruction.append(child.text or "")
            elif name == "fldChar":
                self._field_char(child, paragraph)
            elif name in ("drawing", "pict", "object") or (
                name is None and _split_tag(child.tag) == (_MC_NS, "AlternateContent")
            ):
                paragraph.boxed.extend(self._text_boxes(child, depth + 1))

    def _field_char(self, element: ET.Element, paragraph: _Paragraph) -> None:
        kind = _w_attr(element, "fldCharType")
        if kind == "begin":
            # Kept balanced with its `end` either way: only what it would collect is skipped.
            paragraph.fields.append(_Field(ignored=len(paragraph.fields) >= _MAX_FIELD_DEPTH))
        elif kind == "separate" and paragraph.fields:
            current = paragraph.fields[-1]
            current.result_from = len(paragraph.parts)
            match = (
                None
                if current.ignored
                else _HYPERLINK_FIELD_RE.search("".join(current.instruction))
            )
            if match:
                current.url = safe_link_target(match.group(1) or match.group(2))
        elif kind == "end" and paragraph.fields:
            current = paragraph.fields.pop()
            if current.url and current.result_from is not None:
                visible = "".join(paragraph.parts[current.result_from :])
                if _squash_for_link_check(current.url) not in _squash_for_link_check(visible):
                    paragraph.parts.append(f" ({current.url})")

    @staticmethod
    def _is_hidden(run: ET.Element) -> bool:
        for child in run:
            if _wname(child) == "rPr":
                for prop in child:
                    if _wname(prop) == "vanish":
                        return _w_attr(prop, "val") not in ("0", "false", "off")
        return False

    def _text_boxes(self, element: ET.Element, depth: int) -> list[str]:
        """The lines of every text box under a drawing or a legacy picture element."""
        if depth > _MAX_WALK_DEPTH:
            return []
        if _split_tag(element.tag) == (_MC_NS, "AlternateContent"):
            lines: list[str] = []
            for branch in self._choice(element):
                lines.extend(self._text_boxes(branch, depth + 1))
            return lines
        if _wname(element) == "txbxContent":
            return self._blocks(element, depth + 1)
        lines = []
        for child in element:
            lines.extend(self._text_boxes(child, depth + 1))
        return lines


# -- entry points ----------------------------------------------------------------------------


def extract_text_sync(
    data: bytes, kind: DocumentKind, *, timeout: float = EXTRACTION_TIMEOUT_SECONDS
) -> ExtractedDocument:
    """The blocking extraction, in this process. The API does not call it: a file that costs
    too much cannot be stopped from inside the process that reads it. It goes through
    `extract_document_text`, which runs this in a child process. This is what that child runs,
    and what tests and one-off scripts call."""
    deadline = _Deadline(timeout)
    if kind == "pdf":
        return _read_pdf(data, deadline)
    return _read_docx(data, deadline)


async def sniff_document_kind(data: bytes) -> DocumentKind:
    """`sniff_kind` off the event loop: telling a DOCX from another zip reads the zip's
    directory, which for a hostile archive is not instant."""
    return await asyncio.to_thread(sniff_kind, data)


# -- reading in a child process --------------------------------------------------------------

_WORKER_MODULE = f"{__name__.rpartition('.')[0]}.profile_import_worker"
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
"""The directory the package was imported from. The child is pointed at it, so it runs exactly
the code its parent runs, whatever way the parent found it."""
_ENV_PASSED = ("SYSTEMROOT", "LANG", "LC_ALL", "LC_CTYPE")
"""The only variables the child inherits: what an interpreter needs to start. Everything else
(the service's keys and tokens above all) stays behind."""
_UNREADABLE = (
    "That file could not be read. Export a fresh copy (print it to a new PDF) and try again."
)
_OUT_OF_MEMORY = (
    "That file needs far more memory than a document should, so it was not opened. "
    "Export a simpler copy and try again."
)
_BUSY = "Several resume files are being read right now. Try again in a few seconds."
_running = 0
"""How many reading children are alive. Only touched from the event loop, between awaits."""


def _worker_command(kind: DocumentKind, timeout: float) -> list[str]:
    """The child's command line. `-P` keeps the working directory off its import path."""
    cpu_seconds = math.ceil(timeout) + CHILD_CPU_GRACE_SECONDS
    return [
        sys.executable,
        "-P",
        "-m",
        _WORKER_MODULE,
        kind,
        repr(float(timeout)),
        str(CHILD_MEMORY_BYTES),
        str(cpu_seconds),
    ]


def _child_environment() -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_PASSED if name in os.environ}
    env["PYTHONPATH"] = str(_PACKAGE_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def extraction_reply(data: bytes, kind: DocumentKind, timeout: float) -> bytes:
    """What the reading child sends its parent: the extraction, or the reason it failed, as one
    line of ASCII JSON. Runs inside the child (`profile_import_worker`)."""
    payload: dict[str, Any]
    try:
        document = extract_text_sync(data, kind, timeout=timeout)
    except ExtractionError as e:
        payload = {"ok": False, "reason": e.reason, "message": e.message}
    except MemoryError:
        payload = {"ok": False, "reason": "unsafe", "message": _OUT_OF_MEMORY}
    except Exception as e:  # a defect in the reader, not something to blame the file for
        logger.warning("resume reader failed", extra={"ctx": {"error_type": type(e).__name__}})
        payload = {
            "ok": False,
            "reason": "unreadable",
            "message": _UNREADABLE,
            "error_type": type(e).__name__,
        }
    else:
        payload = {
            "ok": True,
            "text": document.text,
            "kind": document.kind,
            "pages_total": document.pages_total,
            "pages_read": document.pages_read,
            "truncated": document.truncated,
            "column_pages": list(document.column_pages),
            "notices": list(document.notices),
            "page_breaks": list(document.page_breaks),
        }
    return json.dumps(payload).encode("ascii")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unreadable() -> ExtractionError:
    return ExtractionError("unreadable", _UNREADABLE)


def _int_list(value: object, limit: int) -> list[int]:
    if not isinstance(value, list) or len(value) > limit or not all(_is_int(v) for v in value):
        raise _unreadable()
    return list(value)


def _decode_reply(raw: bytes, kind: DocumentKind) -> ExtractedDocument:
    """The child's reply as an `ExtractedDocument`, or the `ExtractionError` it reported. A
    reply that is not exactly what `extraction_reply` writes is an error too: the child had a
    stranger's file in its hands, so nothing it says is believed until it has been checked."""
    try:
        payload = json.loads(raw)
    except ValueError:
        raise _unreadable() from None
    if not isinstance(payload, dict):
        raise _unreadable()

    if payload.get("ok") is False:
        reason, message = payload.get("reason"), payload.get("message")
        if reason not in _REASONS or not isinstance(message, str):
            raise _unreadable()
        if payload.get("error_type"):
            logger.warning(
                "resume reader failed",
                extra={"ctx": {"error_type": str(payload["error_type"])[:80]}},
            )
        raise ExtractionError(cast(ExtractionReason, reason), message[:500])

    text, notices = payload.get("text"), payload.get("notices")
    if (
        payload.get("ok") is not True
        or payload.get("kind") != kind
        or not isinstance(text, str)
        or len(text) > MAX_TEXT_CHARS
        or not isinstance(payload.get("truncated"), bool)
        or not isinstance(notices, list)
        or len(notices) > 10
        or not all(isinstance(n, str) and len(n) <= 500 for n in notices)
    ):
        raise _unreadable()
    counts = (payload.get("pages_total"), payload.get("pages_read"))
    if not all(count is None or _is_int(count) for count in counts):
        raise _unreadable()
    page_breaks = _int_list(payload.get("page_breaks"), MAX_PDF_PAGES)
    return ExtractedDocument(
        text=text,
        kind=kind,
        pages_total=counts[0],
        pages_read=counts[1],
        truncated=payload["truncated"],
        column_pages=tuple(_int_list(payload.get("column_pages"), MAX_PDF_PAGES)),
        notices=tuple(notices),
        page_breaks=tuple(b for b in page_breaks if 0 < b < len(text)),
    )


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await stream.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise _unreadable()
        chunks.append(chunk)
    return b"".join(chunks)


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stdin.write(data)
        await stdin.drain()
        stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass  # the child stopped listening (it has ended); its exit status says why


async def _exchange(process: asyncio.subprocess.Process, data: bytes) -> bytes:
    """Sends the file to the child and collects its reply. The two happen at the same time, so a
    child that starts talking before it has listened cannot leave both sides waiting."""
    stdin, stdout = process.stdin, process.stdout
    if stdin is None or stdout is None:
        raise RuntimeError("the reading child was started without its pipes")
    feeder = asyncio.create_task(_feed(stdin, data))
    try:
        reply = await _read_capped(stdout, _MAX_REPLY_BYTES)
        await process.wait()
    finally:
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
    return reply


async def _reap(process: asyncio.subprocess.Process) -> None:
    """The child is gone when this returns: killed if it was still running."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), _REAP_SECONDS)


async def _read_in_child(data: bytes, kind: DocumentKind, timeout: float) -> ExtractedDocument:
    process = await asyncio.create_subprocess_exec(
        *_worker_command(kind, timeout),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_child_environment(),
        start_new_session=True,
    )
    try:
        reply = await asyncio.wait_for(_exchange(process, data), timeout)
    except TimeoutError as e:
        raise ExtractionError("timeout", _TIMEOUT_MESSAGE) from e
    finally:
        # Also when the caller went away (the request was cancelled): no child outlives it.
        await _reap(process)
    returncode = process.returncode
    if returncode != 0:
        logger.warning("resume reader ended abnormally", extra={"ctx": {"returncode": returncode}})
        if returncode == -getattr(signal, "SIGXCPU", 0):  # the CPU-time limit ended it
            raise ExtractionError("timeout", _TIMEOUT_MESSAGE)
        raise _unreadable()
    return _decode_reply(reply, kind)


async def extract_document_text(
    data: bytes, kind: DocumentKind, *, timeout: float = EXTRACTION_TIMEOUT_SECONDS
) -> ExtractedDocument:
    """`extract_text_sync` in a child process, under a wall-clock timeout that kills it.

    Raises ExtractionError: "timeout" when the child had to be stopped (or stopped itself),
    "busy" when `MAX_CONCURRENT_EXTRACTIONS` are already running (retry shortly), and
    otherwise whatever the reader reported about the file."""
    global _running
    if _running >= MAX_CONCURRENT_EXTRACTIONS:
        raise ExtractionError("busy", _BUSY)
    _running += 1
    try:
        return await _read_in_child(data, kind, timeout)
    finally:
        _running -= 1
