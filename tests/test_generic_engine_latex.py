"""Text becomes LaTeX safely (engines/generic/latex.py, templates.py).

Data must never become a command: whatever a profile, a model or a job posting contains, the
only control sequences in a generated document are the ones the templates write themselves and
the escapes `LatexText` puts in. And the documents may use only packages the PDF renderer's
image installs.
"""

from __future__ import annotations

import random
import re
import unicodedata
from pathlib import Path

import pytest

from between_jobs.engines.generic import latex as latex_module
from between_jobs.engines.generic.cover import CoverContent
from between_jobs.engines.generic.document import (
    EducationBlock,
    EntryBlock,
    ProjectBlock,
    ResumeDoc,
)
from between_jobs.engines.generic.latex import (
    ALLOWED_PACKAGES,
    DRAWN_LETTERS,
    ESCAPE_SEQUENCES,
    LatexText,
    clean_url,
    fold_to_ascii,
    latex_url,
)
from between_jobs.engines.generic.templates import (
    TEMPLATE_COMMANDS,
    render_cover_letter,
    render_resume,
)

_CONTROL_SEQUENCE = re.compile(r"\\([A-Za-z]+|[^A-Za-z])")


def _control_sequences(latex: str) -> set[str]:
    return set(_CONTROL_SEQUENCE.findall(latex))


# -- escaping ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("a & b", r"a \& b"),
        ("100%", r"100\%"),
        ("$5", r"\$5"),
        ("#1", r"\#1"),
        ("snake_case", r"snake\_case"),
        ("{x}", r"\{x\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
        ("a\\b", r"a\textbackslash{}b"),
        (r"\input{/etc/passwd}", r"\textbackslash{}input\{/etc/passwd\}"),
        (r"\write18{rm -rf /}", r"\textbackslash{}write18\{rm -rf /\}"),
        ("[x]", "{[}x{]}"),  # so "[" right after \item is never read as its label
        ("`quoted`", "'quoted'"),
        ('say "hi"', "say ``hi''"),
        ("a\tb\nc", "a b c"),
        ("  padded   spaces  ", "padded spaces"),
        ("C++ and C#", r"C++ and C\#"),
        ("R&D", r"R\&D"),
        # a "*" right after a forced line break would be read as its starred form and vanish
        ("*Pending* Cert", "{*}Pending{*} Cert"),
        ('a-"b" and (\'c\') "d"', "a-``b'' and ('c') ``d''"),  # an opening quote after a dash
        ("a\u20ddb", "ab"),  # an enclosing mark carries no text of its own
        ("5*3", "5{*}3"),
    ],
)
def test_every_character_tex_treats_as_syntax_is_escaped(raw: str, escaped: str) -> None:
    assert LatexText()(raw) == escaped


@pytest.mark.parametrize(
    "name",
    [
        "Jos\u00e9 Garc\u00eda",
        "Zo\u00eb M\u00fcller-\u00d8stergaard",
        "\u0141ukasz \u00c6sir \u0152uvre",
        "Stra\u00dfe \u00de\u00f3r \u00d0uro",
        "\u0160imon \u017di\u017eek \u0158ezn\u00ed\u010dek",
        "\u0130stanbul \u011ea\u011fan \u015eahin \u00c7elik",
        "Ond\u0159ej \u00dd\u0159ek \u0150ri \u0170ber",
        "\u1e9e",
    ],
)
def test_letters_the_renderer_sets_are_printed_as_they_are_spelled(name: str) -> None:
    escaper = LatexText()

    assert escaper(name) == name
    assert escaper.warnings() == []


def test_a_letter_and_a_combining_accent_are_one_drawn_letter() -> None:
    assert LatexText()("Jose\u0301") == "Jos\u00e9"
    assert LatexText()("n\u0303") == "\u00f1"


@pytest.mark.parametrize(
    ("raw", "respelled", "as_written"),
    [
        ("\u0110or\u0111e", "Dor\u0111e", "\u0110 as D"),  # only the capital extracts as an eth
        ("Bra\u0219ov", "Brasov", "\u0219 as s"),  # comma below extracts as "s,"
        ("\u021aara", "Tara", "\u021a as T"),
        ("\u0136aspars", "Kaspars", "\u0136 as K"),
        ("\u0100ris", "Aris", "\u0100 as A"),  # macron: drawn, but extracts as two characters
    ],
)
def test_letters_it_cannot_set_faithfully_are_respelled_and_the_respelling_is_reported(
    raw: str, respelled: str, as_written: str
) -> None:
    escaper = LatexText()

    assert escaper(raw) == respelled
    (warning,) = escaper.warnings()
    assert "respelled in plain ASCII" in warning and as_written in warning
    assert "can't be drawn" not in warning  # this is not the "?" warning


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("\ufb01nance \uff21\uff22\uff23 \u00b2", "finance ABC 2"),
        (
            "\u201cquoted\u201d \u2018single\u2019 \u2013 \u2014 \u2026",
            "``quoted'' 'single' -- --- ...",
        ),
        ("5 \u03bcs and \u00d7", "5 us and x"),
    ],
)
def test_ligatures_width_variants_and_typographic_punctuation_become_their_plain_form(
    raw: str, folded: str
) -> None:
    escaper = LatexText()
    assert escaper(raw) == folded
    assert escaper.warnings() == []  # nothing a reader would call a different letter changed


def test_a_drawn_letter_that_is_part_of_a_name_is_the_whole_allowlist() -> None:
    # 62 Latin-1 letters + the Latin Extended-A letters that compile and extract unchanged
    assert len(DRAWN_LETTERS) == 123
    assert all(unicodedata.category(c).startswith("L") for c in DRAWN_LETTERS)
    assert "\u00d7" not in DRAWN_LETTERS and "\u00f7" not in DRAWN_LETTERS  # not letters
    # drawn, but extracted as the wrong text, so they stay folded
    for (
        left_out
    ) in "\u0110\u0218\u021a\u0136\u013b\u0145\u0122\u0156\u012e\u0172\u0100\u0112\u014c\u016a":
        assert left_out not in DRAWN_LETTERS


def test_letters_with_no_ascii_spelling_are_replaced_and_the_loss_is_reported() -> None:
    escaper = LatexText()

    out = escaper("\u041f\u0440\u0438\u0432\u0435\u0442, \u4e16\u754c \u2603 end")

    assert out == "??????, ?? ? end"
    (warning,) = escaper.warnings()
    assert "9 character(s)" in warning
    for script in ("Cyrillic", "CJK", "Symbol"):
        assert script in warning
    assert "'?'" in warning


def test_line_and_paragraph_separators_are_spaces_not_nothing() -> None:
    assert LatexText()("one\u2028two\u2029three\u2003four") == "one two three four"


def test_nothing_is_dropped_silently_except_what_carries_no_text() -> None:
    escaper = LatexText()
    # zero-width characters, a bidirectional override and a control character carry no text
    assert escaper("a\u200bb\u202ec\u0007d") == "abcd"
    assert escaper.warnings() == []
    # an emoji does: it becomes "?" and is reported
    assert escaper("ok \U0001f600") == "ok ?"
    assert escaper.warnings()


def test_a_very_long_unbroken_string_gets_places_to_break() -> None:
    long_url = "https://example.com/" + "a" * 100
    out = LatexText()(f"see {long_url} now")

    assert out.count(r"\allowbreak{}") == len(long_url) // 32
    assert out.replace(r"\allowbreak{}", "") == f"see {long_url} now"
    # ordinary words and short tokens are untouched
    assert LatexText()("a" * 32) == "a" * 32
    assert r"\allowbreak" not in LatexText()("Distinguished Principal Staff Engineer")


def test_a_fuzzed_string_can_only_produce_the_escapes_and_stays_balanced() -> None:
    rng = random.Random(20261010)
    alphabet = (
        [chr(c) for c in range(0, 128)]
        + [chr(c) for c in range(0xA0, 0x250)]
        + [chr(c) for c in range(0x370, 0x400)]
        + [chr(c) for c in range(0x400, 0x4FF)]
        + [chr(c) for c in range(0x300, 0x370)]
        + [chr(c) for c in (0x200B, 0x202E, 0x2028, 0x4E2D, 0x3042, 0xD7A3, 0x1F600, 0xFEFF)]
        + list("\\{}$&#_%^~[]`\"'<>|")
    )
    for _ in range(400):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        out = LatexText()(raw)

        assert all(c.isascii() or c in DRAWN_LETTERS for c in out), repr(raw)
        assert "\n" not in out and "\r" not in out
        # no control character reaches the document, DEL included
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in out), repr(raw)
        assert _control_sequences(out) <= ESCAPE_SEQUENCES, repr(raw)
        # no syntax character survives outside an escape
        stripped = re.sub(r"\\(?:[A-Za-z]+\{\}|[&%$#_{}])", "", out)
        for syntax in "%$&#_^~\\":
            assert syntax not in stripped, (syntax, repr(raw), out)
        # braces: every one is part of an escape, an empty group, or "{[}" / "{]}" / "{*}"
        remaining = stripped.replace("{[}", "").replace("{]}", "").replace("{*}", "")
        remaining = remaining.replace("{}", "")
        assert remaining.count("{") == 0 and remaining.count("}") == 0, (raw, out)


def test_a_stray_delete_character_and_other_controls_never_reach_the_document() -> None:
    assert LatexText()("a\x7fb\x1bc\x00d") == "abcd"
    assert LatexText()("a\x7f") == "a"


@pytest.mark.parametrize("pair", ["<<", ">>", ",,"])
def test_a_doubled_character_the_typeface_would_join_is_kept_as_typed(pair: str) -> None:
    out = LatexText()(f"a {pair} b{pair}c")

    # an empty group between the two keeps TeX's ligature program from joining them
    assert out == f"a {pair[0]}{{}}{pair[1]} b{pair[0]}{{}}{pair[1]}c"
    assert LatexText()(pair[0]) == pair[0]  # a single one is untouched
    assert LatexText()("<>") == "<>"
    assert "{}" in latex_module.LatexText()("x<<y")  # and `{}` adds no control sequence
    assert _control_sequences(out) <= ESCAPE_SEQUENCES


# Every fold, written out here and not read back from the tables: a typo in a table must fail.
_FOLDS: list[tuple[int, str]] = [
    (0x00A0, " "), (0x2007, " "), (0x202F, " "),
    (0x2018, "'"), (0x2019, "'"), (0x201A, ","), (0x201B, "'"),
    (0x201C, '"'), (0x201D, '"'), (0x201E, '"'), (0x00AB, '"'), (0x00BB, '"'),
    (0x2010, "-"), (0x2011, "-"), (0x2012, "-"), (0x2013, "--"), (0x2014, "---"),
    (0x2015, "---"), (0x2212, "-"),
    (0x2026, "..."), (0x2022, "-"), (0x2023, "-"), (0x25AA, "-"), (0x25CF, "-"),
    (0x25E6, "-"), (0x00B7, "-"),
    (0x2192, "->"), (0x2190, "<-"), (0x21D2, "=>"),
    (0x00D7, "x"), (0x00F7, "/"), (0x00A9, "(c)"), (0x00AE, "(R)"), (0x2122, "(TM)"),
    (0x00BF, "?"), (0x00A1, "!"),
    (0x20AC, "EUR "), (0x00A3, "GBP "), (0x00A5, "JPY "), (0x20B9, "INR "),
    (0x00B5, "u"), (0x03BC, "u"), (0x00B0, " deg"),
    (0x0141, "L"), (0x0142, "l"), (0x00D8, "O"), (0x00F8, "o"), (0x00C6, "AE"),
    (0x00E6, "ae"), (0x0152, "OE"), (0x0153, "oe"), (0x00DE, "Th"), (0x00FE, "th"),
    (0x00D0, "D"), (0x00F0, "d"), (0x00DF, "ss"), (0x1E9E, "SS"), (0x0110, "D"),
    (0x0111, "d"), (0x0131, "i"), (0x0126, "H"), (0x0127, "h"), (0x014A, "Ng"),
    (0x014B, "ng"), (0x017F, "s"), (0x0166, "T"), (0x0167, "t"), (0x0138, "k"),
    (0x0149, "'n"), (0x01C4, "DZ"), (0x01C5, "Dz"), (0x01C6, "dz"), (0x01C7, "LJ"),
    (0x01C8, "Lj"), (0x01C9, "lj"), (0x01CA, "NJ"), (0x01CB, "Nj"), (0x01CC, "nj"),
    (0x0181, "B"), (0x0253, "b"), (0x018A, "D"), (0x0257, "d"), (0x0191, "F"),
    (0x0192, "f"), (0x0193, "G"), (0x0260, "g"), (0x0197, "I"), (0x0268, "i"),
    (0x0198, "K"), (0x0199, "k"), (0x019D, "N"), (0x0272, "n"), (0x01A4, "P"),
    (0x01A5, "p"), (0x01AC, "T"), (0x01AD, "t"), (0x01B3, "Y"), (0x01B4, "y"),
    (0x01B5, "Z"), (0x01B6, "z"),
]  # fmt: skip


@pytest.mark.parametrize(("code", "spelling"), _FOLDS)
def test_every_fold_in_the_tables_is_the_spelling_it_should_be(code: int, spelling: str) -> None:
    assert fold_to_ascii(chr(code)) == spelling


def test_the_fold_list_above_is_complete() -> None:
    in_tables = {ord(c) for c in latex_module._PUNCTUATION} | {
        ord(c) for c in latex_module._LATIN_LETTERS
    }
    assert {code for code, _ in _FOLDS} == in_tables


# -- links ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("https://example.com/in/ada", "https://example.com/in/ada"),
        ("github.com/ada", "https://github.com/ada"),
        ("www.example.org", "https://www.example.org"),
        ("mailto:ada@example.com", "mailto:ada@example.com"),
        ("tel:+15550100", "tel:+15550100"),
        ("HTTPS://example.com/A", "HTTPS://example.com/A"),
        ("https://example.com/a b", None),
        ("javascript:alert(1)", None),
        ("data:text/html;base64,AAAA", None),
        ("file:///etc/passwd", None),
        ("ada@example.com", None),
        ("not a url", None),
        ("", None),
        ("https://example.com/" + "a" * 600, None),
    ],
)
def test_only_http_https_mailto_and_tel_links_survive(raw: str, cleaned: str | None) -> None:
    assert clean_url(raw) == cleaned


def test_characters_that_could_end_a_link_early_are_encoded_or_escaped() -> None:
    cleaned = clean_url('https://example.com/p?a=1&b=%2F#frag_x~y{z}\\q^|`"<>\u00e9')

    assert cleaned is not None
    for dangerous in '{}\\^|`"<> ':
        assert dangerous not in cleaned
    assert cleaned.isascii()
    escaped = latex_url(cleaned)
    assert r"\%" in escaped and r"\#" in escaped and r"\&" in escaped and r"\_" in escaped
    stripped = re.sub(r"\\[%#&_]", "", escaped)
    for syntax in "%#&_":
        assert syntax not in stripped


# -- the documents -----------------------------------------------------------------------------

_HOSTILE = r"\input{/etc/passwd} \write18{x} \openin1=a \include{b} %&$#_{}^~ \\ \end{document}"


def _hostile_doc() -> ResumeDoc:
    return ResumeDoc(
        name=f"Ada {_HOSTILE}",
        headline=_HOSTILE,
        chips=[
            {"field": "email", "text": _HOSTILE, "href": "mailto:ada@example.com"},
            {"field": "linkedin", "text": "ln", "href": "https://example.com/%#_~&{}\\"},
        ],
        separator="dot",
        density="balanced",
        summary=_HOSTILE,
        experience=[
            EntryBlock("/experience/0", _HOSTILE, _HOSTILE, _HOSTILE, _HOSTILE, [_HOSTILE] * 2)
        ],
        projects=[
            ProjectBlock("/projects/0", _HOSTILE, "https://example.com/%#_", _HOSTILE, [_HOSTILE]),
            ProjectBlock("/projects/1", "second", "https://example.com/%#_~&{}\\^|`", "", ["x"]),
        ],
        education=[
            EducationBlock("/education/0", _HOSTILE, _HOSTILE, _HOSTILE, _HOSTILE, [_HOSTILE])
        ],
        skills=[(_HOSTILE, _HOSTILE)],
        certifications=[_HOSTILE],
        publications=[_HOSTILE],
        patents=[_HOSTILE],
        volunteering=[EntryBlock("/volunteering/0", _HOSTILE, "", _HOSTILE, _HOSTILE, [_HOSTILE])],
        achievements=[_HOSTILE],
        languages=_HOSTILE,
    )


def _hostile_letter() -> str:
    return render_cover_letter(
        name=_HOSTILE,
        chips=[{"field": "email", "text": _HOSTILE, "href": "mailto:a@example.com"}],
        separator="bullet",
        company=_HOSTILE,
        title=_HOSTILE,
        date_text=_HOSTILE,
        content=CoverContent(opening=[_HOSTILE], highlights=[_HOSTILE], closing=[_HOSTILE]),
        esc=LatexText(),
    )


def _documents() -> dict[str, str]:
    return {
        "resume": render_resume(_hostile_doc(), LatexText()),
        "cover letter": _hostile_letter(),
    }


@pytest.mark.parametrize("which", ["resume", "cover letter"])
def test_no_command_can_come_from_data(which: str) -> None:
    latex = _documents()[which]

    assert _control_sequences(latex) <= TEMPLATE_COMMANDS | ESCAPE_SEQUENCES
    # the only \input is the template's own, in the preamble, and it names one fixed file
    assert re.findall(r"\\input\{[^}]*\}", latex) == [r"\input{glyphtounicode}"]
    for forbidden in ("write18", "openin", "openout", "include", "immediate", "catcode", "def"):
        assert not re.search(rf"\\{forbidden}(?![A-Za-z])", latex), forbidden
    # and the document is still one document: data did not close it early
    assert latex.count(r"\end{document}") == 1
    assert latex.rstrip().endswith(r"\end{document}")


@pytest.mark.parametrize("which", ["resume", "cover letter"])
def test_a_document_is_plain_ascii_with_balanced_environments_and_braces(which: str) -> None:
    latex = _documents()[which]

    assert latex.isascii()
    assert latex.count(r"\begin{itemize}") == latex.count(r"\end{itemize}")
    unescaped = re.sub(r"\\[{}]", "", latex)
    unescaped = unescaped.replace("{[}", "").replace("{]}", "")
    assert unescaped.count("{") == unescaped.count("}")


def _declared_packages(latex: str) -> set[str]:
    names: set[str] = set()
    for match in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", latex):
        names.update(part.strip() for part in match.group(1).split(","))
    return names


def _installed_tex_live_packages() -> set[str]:
    """The packages the PDF renderer's image installs, read from its Dockerfile's `tlmgr
    install` list (the one place that says what is on the image)."""
    dockerfile = Path(__file__).resolve().parents[1] / "latex-service" / "Dockerfile"
    lines = dockerfile.read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.startswith("RUN") and "tlmgr install" in line
    )
    block: list[str] = []
    for line in lines[start:]:
        block.append(line.rstrip("\\").replace("tlmgr install", ""))
        if not line.rstrip().endswith("\\"):
            break
    return {token for token in " ".join(block).split() if re.fullmatch(r"[a-z0-9-]+", token)}


@pytest.mark.parametrize("which", ["resume", "cover letter"])
def test_every_package_a_document_loads_is_allowed_and_installed_on_the_renderer(
    which: str,
) -> None:
    declared = _declared_packages(_documents()[which])

    assert declared  # the templates do load packages: this is not an empty pass
    assert declared <= set(ALLOWED_PACKAGES)
    installed = _installed_tex_live_packages()
    assert "latex-bin" in installed  # the parse found the real list
    for package in declared:
        assert ALLOWED_PACKAGES[package] in installed, package


def test_the_allowlist_has_no_package_the_templates_do_not_use() -> None:
    used = _declared_packages(_documents()["resume"]) | _declared_packages(
        _documents()["cover letter"]
    )
    assert used == set(ALLOWED_PACKAGES)


def test_the_installed_list_parse_reads_the_whole_block() -> None:
    installed = _installed_tex_live_packages()
    assert {"lato", "geometry", "enumitem", "titlesec", "hyperref", "microtype"} <= installed


def test_links_are_rendered_with_escaped_addresses_and_text() -> None:
    latex = render_resume(_hostile_doc(), LatexText())

    assert r"\href{https://example.com/\%\#\_}" in latex  # the project's address, escaped
    # a hostile address was refused, so the chip is plain text, not a link
    assert "example.com/%#_~&" not in latex
    assert r"\href{mailto:ada@example.com}" in latex


def _project_latex(url: str) -> str:
    doc = ResumeDoc(
        name="Ada",
        headline="",
        chips=[],
        separator="pipe",
        density="compact",
        projects=[ProjectBlock("/projects/0", "p", url, "", [])],
    )
    return render_resume(doc, LatexText())


def test_a_project_address_that_tries_to_close_the_link_early_is_encoded() -> None:
    latex = _project_latex("https://x.example/a}\\input{/etc/passwd}{")

    assert r"\href{https://x.example/a\%7D\%5Cinput\%7B/etc/passwd\%7D\%7B}" in latex
    assert "\\input{/etc/passwd}" not in latex
    assert _control_sequences(latex) <= TEMPLATE_COMMANDS | ESCAPE_SEQUENCES


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://x.example/a",
        "ssh://x.example",
        "chrome-extension://abc",
        "data:text/html;base64,AAAA",
        "file:///etc/passwd",
    ],
)
def test_a_project_address_with_a_scheme_other_than_web_mail_or_phone_is_not_a_link(
    url: str,
) -> None:
    latex = _project_latex(url)

    assert r"\href" not in latex
    assert url.split(":")[0] not in latex.split(r"\begin{document}")[1]
    assert r"\projectheading{\textbf{p}}" in latex  # the name is still printed, as plain text


def _extras_latex(*, certifications: list[str], publications: list[str], patents: list[str]) -> str:
    doc = ResumeDoc(
        name="Ada",
        headline="",
        chips=[],
        separator="pipe",
        density="compact",
        certifications=certifications,
        publications=publications,
        patents=patents,
    )
    return render_resume(doc, LatexText())


@pytest.mark.parametrize(
    "lines",
    [
        ["", "Cert A"],
        ["Cert A", " ", "Cert B"],
        ["\u200b", "\u200b", "Cert A"],
        ["Cert A", ""],
        ["", ""],
    ],
)
def test_a_line_with_no_text_never_becomes_a_bare_forced_break(lines: list[str]) -> None:
    """A `\\\\` with nothing before it is a LaTeX error ("no line here to end") that stops the
    PDF. The template skips such a line whatever the caller let through."""
    latex = _extras_latex(certifications=lines, publications=lines, patents=lines)

    body = latex.split(r"\begin{document}")[1]
    assert not [line for line in body.splitlines() if line.strip() == "\\\\"]
    assert "\\\\\n\\\\" not in body  # nor two in a row


def test_an_education_detail_is_closed_so_the_next_entry_starts_on_a_new_line() -> None:
    block = EducationBlock(
        "/education/0", "Lakeside College", "NJ", "BS, Biology", "2016", ["GPA: 3.9"]
    )
    doc = ResumeDoc(
        name="Ada",
        headline="",
        chips=[],
        separator="pipe",
        density="compact",
        education=[block, block],
    )

    latex = render_resume(doc, LatexText())

    assert latex.count(r"\noindent GPA: 3.9\par") == 2
    assert r"\par GPA" not in latex  # an open paragraph would carry on into the next \entry


def test_a_cover_letters_contact_line_is_ragged_in_a_group_of_its_own() -> None:
    letter = _hostile_letter()

    head, _, rest = letter.partition(r"\begingroup")
    assert r"\raggedright" in rest.split(r"\endgroup")[0]
    assert r"\setlength{\emergencystretch}{2em}" in rest.split(r"\endgroup")[0]
    assert r"\small" in rest.split(r"\endgroup")[0] and r"\par" in rest.split(r"\endgroup")[0]
    # only the contact line is ragged: the body of the letter is not
    assert r"\raggedright" not in head and r"\raggedright" not in rest.split(r"\endgroup")[1]


def test_an_entry_and_the_start_of_a_list_cannot_be_split_across_a_page() -> None:
    latex = render_resume(_hostile_doc(), LatexText())

    # the two rows of a heading are paragraphs of their own, kept together; a list is not
    # allowed to break before its first item
    assert r"\hfill #2\par\nopagebreak\noindent\textit{#3}\hfill #4\par\nopagebreak" in latex
    assert "beginpenalty=10000" in latex


def test_a_link_with_a_scheme_that_could_run_code_is_never_written() -> None:
    doc = ResumeDoc(
        name="Ada",
        headline="",
        chips=[{"field": "portfolio", "text": "site", "href": "javascript:alert(1)"}],
        separator="pipe",
        density="compact",
    )
    latex = render_resume(doc, LatexText())

    assert "javascript" not in latex
    assert r"\href" not in latex


def test_the_escaper_reports_what_a_document_lost() -> None:
    escaper = LatexText()
    doc = ResumeDoc(
        name="\u0414\u0436\u043e\u043d",
        headline="",
        chips=[],
        separator="pipe",
        density="balanced",
    )

    latex = render_resume(doc, escaper)

    assert "????" not in latex.split(r"\begin{document}")[0]
    assert escaper.warnings() and "Cyrillic" in escaper.warnings()[0]


def test_the_documents_ask_for_text_that_extracts_as_letters() -> None:
    latex = render_resume(_hostile_doc(), LatexText())

    assert r"\pdfgentounicode=1" in latex
    assert r"\DisableLigatures[f]" in latex  # "fi" and "fl" would extract as one glyph each
