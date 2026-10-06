"""Synthetic resume files for the document-import tests, built in code from fictional text.

A tiny PDF writer (text in the standard Helvetica font at chosen positions, optional link
annotations) and a DOCX builder (a zip of hand-written WordprocessingML). Nothing here is
read from a file: every byte is made by the test that uses it, so there is no sample resume,
real or invented, checked into the repository.
"""

from __future__ import annotations

import io
import math
import struct
import zipfile
import zlib
from dataclasses import dataclass
from typing import Any

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
_WPS = 'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
_WP = 'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
_A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

CONTENT_TYPES = (
    f'{_XML_DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)


# -- PDF -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    text: str
    size: float = 10.0
    rotation: float = 0.0
    """Degrees, counter-clockwise. A reader's layout mode cannot place rotated text."""


@dataclass(frozen=True)
class Link:
    rect: tuple[float, float, float, float]
    uri: str


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _draw(t: Text) -> str:
    if not t.rotation:
        return f"BT /F1 {t.size} Tf {t.x} {t.y} Td ({_pdf_escape(t.text)}) Tj ET"
    cos, sin = math.cos(math.radians(t.rotation)), math.sin(math.radians(t.rotation))
    matrix = f"{cos:.4f} {sin:.4f} {-sin:.4f} {cos:.4f} {t.x} {t.y}"
    return f"BT /F1 {t.size} Tf {matrix} Tm ({_pdf_escape(t.text)}) Tj ET"


def make_pdf(pages: list[list[Text]], *, links: list[list[Link]] | None = None) -> bytes:
    """A PDF with one page per list of `Text`, each drawn with its own `Tj` at its position
    (the way many generators draw a line), in the order given."""
    links = links or [[] for _ in pages]
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)

    catalog = add("")  # filled in below, once the page tree's number is known
    tree = add("")
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_numbers: list[int] = []
    for items, page_links in zip(pages, links, strict=True):
        stream = "\n".join(_draw(t) for t in items)
        content = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        annot_numbers = [
            add(
                f"<< /Type /Annot /Subtype /Link /Rect [{' '.join(str(v) for v in link.rect)}] "
                f"/Border [0 0 0] /A << /S /URI /URI ({_pdf_escape(link.uri)}) >> >>"
            )
            for link in page_links
        ]
        annots = (
            f" /Annots [{' '.join(f'{n} 0 R' for n in annot_numbers)}]" if annot_numbers else ""
        )
        page = add(
            f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R{annots} >>"
        )
        page_numbers.append(page)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode("latin-1")
    kids = " ".join(f"{n} 0 R" for n in page_numbers)
    objects[tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_numbers)} >>".encode(
        "latin-1"
    )
    return assemble_pdf(objects, catalog)


def assemble_pdf(objects: list[bytes], catalog: int) -> bytes:
    """A PDF file from its objects (object N is `objects[N - 1]`): the header, each object, the
    cross-reference table and the trailer that names `catalog` as the root. The tests that need
    a shape `make_pdf` cannot draw (a form XObject, a long compressed stream) build their own
    objects and put them together here."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("latin-1")
    )
    return out.getvalue()


def flate_stream(payload: bytes, dictionary: str = "") -> bytes:
    """A PDF stream object holding `payload` deflated: a few kilobytes on disk that decode to
    megabytes, which is what a hostile file does."""
    packed = zlib.compress(payload, 9)
    return (
        f"<< /Length {len(packed)} /Filter /FlateDecode {dictionary} >>\nstream\n".encode("latin-1")
        + packed
        + b"\nendstream"
    )


def lines_at(
    x: float, top: float, lines: list[str], *, size: float = 10.0, pitch: float = 14.0
) -> list[Text]:
    """`lines`, one `Text` each, going down the page from `top` at `pitch` points a line."""
    return [Text(x, top - i * pitch, line, size) for i, line in enumerate(lines)]


# -- DOCX ------------------------------------------------------------------------------------


def run(text: str, *, hidden: bool = False) -> str:
    props = "<w:rPr><w:vanish/></w:rPr>" if hidden else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{_xml_escape(text)}</w:t></w:r>'


def para(*runs: str, style: str | None = None, numbered: bool = False) -> str:
    props = ""
    if style:
        props += f'<w:pStyle w:val="{style}"/>'
    if numbered:
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    ppr = f"<w:pPr>{props}</w:pPr>" if props else ""
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def text_para(text: str, **kwargs: object) -> str:
    return para(run(text), **kwargs)  # type: ignore[arg-type]


def table(rows: list[list[list[str]]]) -> str:
    """A table: rows of cells, each cell a list of paragraph XML strings."""
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc><w:tcPr/>{''.join(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in rows
    )
    return f"<w:tbl>{body}</w:tbl>"


def hyperlink(rel_id: str, text: str) -> str:
    return f'<w:hyperlink r:id="{rel_id}">{run(text)}</w:hyperlink>'


def document_xml(*blocks: str, sect: str = "") -> str:
    return (
        f"{_XML_DECL}<w:document {_W} {_R} {_MC} {_WPS} {_WP} {_A}>"
        f"<w:body>{''.join(blocks)}{sect}</w:body></w:document>"
    )


def part_xml(root: str, *blocks: str) -> str:
    return f"{_XML_DECL}<w:{root} {_W} {_R}>{''.join(blocks)}</w:{root}>"


def rels_xml(links: dict[str, str]) -> str:
    items = "".join(
        f'<Relationship Id="{rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        f'Target="{_xml_escape(target)}" TargetMode="External"/>'
        for rid, target in links.items()
    )
    return (
        f'{_XML_DECL}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'relationships">{items}</Relationships>'
    )


def styles_xml(numbered_style_ids: list[str], *, based_on: dict[str, str] | None = None) -> str:
    styles = ""
    for style_id in numbered_style_ids:
        styles += (
            f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{style_id}"/>'
            '<w:pPr><w:numPr><w:numId w:val="3"/></w:numPr></w:pPr></w:style>'
        )
    for style_id, parent in (based_on or {}).items():
        styles += (
            f'<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{style_id}"/>'
            f'<w:basedOn w:val="{parent}"/></w:style>'
        )
    return f"{_XML_DECL}<w:styles {_W}>{styles}</w:styles>"


def make_docx(
    document: str,
    *,
    extra: dict[str, str | bytes] | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    """A DOCX zip around `document` (the text of `word/document.xml`); `extra` adds or
    replaces parts by name."""
    parts: dict[str, str | bytes] = {
        "[Content_Types].xml": CONTENT_TYPES,
        "word/document.xml": document,
    }
    parts.update(extra or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def damage_compressed_part(data: bytes, member: str, *, flips: int = 30) -> bytes:
    """`data` with about `flips` bytes of `member`'s compressed data inverted: the zip directory
    still reads, so the file passes for a DOCX, and the part itself cannot be decompressed."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(member)
    name_length, extra_length = struct.unpack(
        "<HH", data[info.header_offset + 26 : info.header_offset + 30]
    )
    start = info.header_offset + 30 + name_length + extra_length
    damaged = bytearray(data)
    for offset in range(start, start + info.compress_size, max(1, info.compress_size // flips)):
        damaged[offset] ^= 0xFF
    return bytes(damaged)


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


# -- a fictional resume as the importer sees it, and the model's faithful answer ----------------

SAMPLE_RESUME_TEXT = """Pat Example
Senior Widget Engineer
pat.example@example.test | (555) 010-0100 | linkedin.example.test/in/pat-example
Springfield, ZZ, Fictionland
Cleared for all work in Fictionland with no sponsor needed.

SUMMARY
Engineer with many years of work on widgets and gadgets.

EXPERIENCE
Senior Widget Engineer Jun 2022 - Present
Acme Fictional Corp | Springfield, ZZ
\N{BULLET} Rebuilt the sprocket inventory report for 12 depots, saving $1.4M a year.
\N{BULLET} Cut the nightly widget build from 40 to 15 minutes with Python and SQL.
Tools: Python, SQL, Tableau
Widget Engineer 2019 - 2021
Other Fictional Co
\N{BULLET} Wrote the Redis-to-Postgres sync job for 3,000 gadget records.
Tools: Kafka, Python

EDUCATION
Bachelor of Science in Widgetry
Marlowe Fictional University | 2015

SKILLS
Python, SQL, Go, C++, Tableau, Kafka, Google Cloud
CERTIFICATIONS
Certified Widget Assembler (03/2024)
Site: https://widgets.example.test/pat
"""


def good_model_answer() -> dict[str, Any]:
    return {
        "personal": {
            "name": "Pat Example",
            "headline": "Senior Widget Engineer",
            "emails": [{"address": "pat.example@example.test"}],
            "phones": [{"number": "(555) 010-0100"}],
            "links": {"linkedin": "linkedin.example.test/in/pat-example"},
            "location": {"city": "Springfield", "region": "ZZ", "country": "Fictionland"},
            "work_authorization": "Cleared for all work in Fictionland with no sponsor needed.",
        },
        "summary_bullets": ["Engineer with many years of work on widgets and gadgets."],
        "experience": [
            {
                "title": "Senior Widget Engineer",
                "company": "Acme Fictional Corp",
                "location": "Springfield, ZZ",
                "start_date": "2022-06",
                "end_date": "present",
                "bullets": [
                    "Rebuilt the sprocket inventory report for 12 depots, saving $1.4M a year.",
                    "Cut the nightly widget build from 40 to 15 minutes with Python and SQL.",
                ],
                "skills": ["Python", "SQL", "Tableau"],
            },
            {
                "title": "Widget Engineer",
                "company": "Other Fictional Co",
                "start_date": "2019-01",
                "end_date": "2021-12",
                "bullets": ["Wrote the Redis-to-Postgres sync job for 3,000 gadget records."],
                "skills": ["Kafka", "Python"],
            },
        ],
        "education": [
            {
                "degree": "Bachelor of Science in Widgetry",
                "institution": "Marlowe Fictional University",
                "end_date": "2015",
            }
        ],
        "skills": {"programming": ["Python", "SQL", "Go", "C++"], "tools": ["Tableau", "Kafka"]},
        "certifications": [
            {
                "name": "Certified Widget Assembler",
                "date": "2024-03",
                "url": "https://widgets.example.test/pat",
            }
        ],
    }
