"""Putting text into LaTeX safely.

Everything a resume or cover letter shows came from data (a profile, a model's answer, a job
posting), and data must never become LaTeX commands. Two rules keep that true:

- Every piece of text goes through `LatexText` before it reaches a template. After it, the text
  is Latin letters and ASCII, and the only backslashes in it begin one of the escapes in
  `_ESCAPES` (`\\textbackslash{}`, `\\&`, `\\%` and so on), none of which can start a command that
  reads a file, writes a file or runs a program. The templates (`templates.py`) are the only
  place a command is spelled out.
- Every link goes through `clean_url` and `latex_url`: only http, https, mailto and tel
  addresses survive, and every character that could end the argument early is percent-encoded
  or escaped.

The PDF renderer (`latex-service`) compiles with `pdflatex` and the Lato typeface in the T1
encoding. That draws, and a text extractor reads back unchanged, the letters in `DRAWN_LETTERS`:
all of Latin-1 (the accented vowels, the sharp s, the o-slash, eth and thorn) and the Central
European, Turkish and Nordic letters of Latin Extended-A (the l-stroke, r-caron, s-cedilla,
g-breve and so on). A name in those letters is printed as it is spelled. That list is closed and
was checked by compiling every letter in every face the templates use
(tests/test_generic_engine_compile.py), because a letter the renderer cannot set stops the
whole PDF.

Anything else is respelled or replaced, and `LatexText.warnings` says so. Letters that draw badly
or extract wrongly (an s with a comma below, a d with a stroke) become their usual ASCII
spelling and are counted as respelled. What has no Latin spelling at all (Cyrillic, Greek, CJK,
emoji) becomes "?" and is counted by script. Either is a fact somebody must be told about, not
something to pass over in a name.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

# The only TeX Live packages the templates load, each with the TeX Live package that ships it
# in the latex-service image (`latex-service/Dockerfile`'s `tlmgr install` list). A package
# whose file is part of the LaTeX kernel itself maps to "latex-bin", which the image installs
# first. tests/test_generic_engine_latex.py checks both halves: every `\usepackage` in generated
# output is a key here, and every value is in that Dockerfile list.
ALLOWED_PACKAGES: dict[str, str] = {
    "lato": "lato",
    "fontenc": "latex-bin",
    "geometry": "geometry",
    "enumitem": "enumitem",
    "titlesec": "titlesec",
    "hyperref": "hyperref",
    "microtype": "microtype",
}

_PUNCTUATION: dict[str, str] = {
    "\u00a0": " ",  # no-break space
    "\u2007": " ",
    "\u202f": " ",
    "\u2018": "'",  # curly single quotes
    "\u2019": "'",
    "\u201a": ",",
    "\u201b": "'",
    "\u201c": '"',  # curly double quotes (turned into TeX quotes by `_quotes`)
    "\u201d": '"',
    "\u201e": '"',
    "\u2010": "-",  # hyphens and dashes
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "--",
    "\u2014": "---",
    "\u2015": "---",
    "\u2212": "-",  # minus sign
    "\u2026": "...",
    "\u2022": "-",  # bullets and middle dots
    "\u2023": "-",
    "\u25aa": "-",
    "\u25cf": "-",
    "\u25e6": "-",
    "\u00b7": "-",
    "\u2192": "->",
    "\u2190": "<-",
    "\u21d2": "=>",
    "\u00d7": "x",
    "\u00f7": "/",
    "\u00a9": "(c)",
    "\u00ae": "(R)",
    "\u2122": "(TM)",
    "\u00ab": '"',
    "\u00bb": '"',
    "\u00bf": "?",
    "\u00a1": "!",
    "\u20ac": "EUR ",  # currency signs T1 cannot set without extra packages
    "\u00a3": "GBP ",
    "\u00a5": "JPY ",
    "\u20b9": "INR ",
    "\u00b5": "u",  # micro sign
    "\u03bc": "u",  # Greek mu, as in "5 \u03bcs"
    "\u00b0": " deg",
}

_LATIN_LETTERS: dict[str, str] = {
    # Letters that do not decompose into a base letter plus a mark, with their usual spelling.
    "\u0141": "L",
    "\u0142": "l",
    "\u00d8": "O",
    "\u00f8": "o",
    "\u00c6": "AE",
    "\u00e6": "ae",
    "\u0152": "OE",
    "\u0153": "oe",
    "\u00de": "Th",
    "\u00fe": "th",
    "\u00d0": "D",
    "\u00f0": "d",
    "\u00df": "ss",
    "\u1e9e": "SS",
    "\u0110": "D",
    "\u0111": "d",
    "\u0131": "i",
    "\u0126": "H",
    "\u0127": "h",
    "\u014a": "Ng",
    "\u014b": "ng",
    "\u017f": "s",
    "\u0166": "T",
    "\u0167": "t",
    "\u0138": "k",
    "\u0149": "'n",
    "\u01c4": "DZ",
    "\u01c5": "Dz",
    "\u01c6": "dz",
    "\u01c7": "LJ",
    "\u01c8": "Lj",
    "\u01c9": "lj",
    "\u01ca": "NJ",
    "\u01cb": "Nj",
    "\u01cc": "nj",
    "\u0181": "B",
    "\u0253": "b",
    "\u018a": "D",
    "\u0257": "d",
    "\u0191": "F",
    "\u0192": "f",
    "\u0193": "G",
    "\u0260": "g",
    "\u0197": "I",
    "\u0268": "i",
    "\u0198": "K",
    "\u0199": "k",
    "\u019d": "N",
    "\u0272": "n",
    "\u01a4": "P",
    "\u01a5": "p",
    "\u01ac": "T",
    "\u01ad": "t",
    "\u01b3": "Y",
    "\u01b4": "y",
    "\u01b5": "Z",
    "\u01b6": "z",
}

DRAWN_LETTERS: frozenset[str] = frozenset(
    {chr(code) for code in range(0xC0, 0x100) if code not in (0xD7, 0xF7)}
    | {
        chr(code)
        for code in (
            # Latin Extended-A: the letters that compile in every face and extract unchanged.
            # The comma-below and cedilla letters, the ogonek i and u, the macron, circumflex,
            # breve and dot-above letters, the capital D with stroke and the digraphs are left
            # out on purpose: they draw but extract as the wrong text, or not at all.
            *(0x102, 0x103, 0x104, 0x105, 0x106, 0x107, 0x10C, 0x10D, 0x10E, 0x10F, 0x111),
            *(0x118, 0x119, 0x11A, 0x11B, 0x11E, 0x11F, 0x130, 0x131, 0x139, 0x13A, 0x13D),
            *(0x13E, 0x141, 0x142, 0x143, 0x144, 0x147, 0x148, 0x14A, 0x14B, 0x150, 0x151),
            *(0x152, 0x153, 0x154, 0x155, 0x158, 0x159, 0x15A, 0x15B, 0x15E, 0x15F, 0x160),
            *(0x161, 0x162, 0x163, 0x164, 0x165, 0x16E, 0x16F, 0x170, 0x171, 0x178, 0x179),
            *(0x17A, 0x17B, 0x17C, 0x17D, 0x17E, 0x1E9E),
        )
    }
)
"""The letters, beyond ASCII, a document sets as they are spelled."""

_ESCAPES: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    # Square brackets: right after `\item` a "[" would be read as the item's optional label.
    "[": "{[}",
    "]": "{]}",
    # A "*" that starts the text after a forced line break (`\\`) would be read as the starred
    # form of the break and vanish: "*Pending* Cert" would print as "Pending* Cert".
    "*": "{*}",
}
"""What each character TeX treats as syntax becomes. Applied one character at a time, so the
braces some of these contain are never escaped again."""

ESCAPE_SEQUENCES = frozenset(
    {
        "allowbreak",
        "textbackslash",
        "textasciitilde",
        "textasciicircum",
        "&",
        "%",
        "$",
        "#",
        "_",
        "{",
        "}",
    }
)
"""The control sequences `LatexText` can put into its output (a control symbol is listed by its
character; `allowbreak` is the break opportunity it adds inside a very long unbroken string). A
test holds a fuzzed output to exactly this set."""

_BREAK_AFTER = 32
"""A string of this many characters with no space in it (a long address, a pasted token) is given
a place to break, so it wraps instead of running off the page."""


def _script_of(char: str) -> str:
    """The script a letter belongs to ("Cyrillic", "Greek", "CJK"), or "Symbol" for anything
    that is not a letter (an emoji, an arrow, a dingbat)."""
    if not unicodedata.category(char).startswith("L"):
        return "Symbol"
    name = unicodedata.name(char, "")
    if name.startswith("CJK"):
        return "CJK"
    word = name.split(" ")[0] if name else ""
    return word.capitalize() if word else "Symbol"


def fold_to_ascii(text: str, lost: dict[str, int] | None = None) -> str:
    """`text` with everything the PDF renderer cannot draw replaced by an ASCII spelling.

    Characters with no ASCII spelling become "?"; when `lost` is given, it is updated with a
    count of them per script ("Cyrillic", "CJK", "Symbol"...), which is what
    `LatexText.warnings` reports. Control and zero-width characters are dropped, as are
    combining marks left over after folding: they carry no text of their own."""
    out: list[str] = []
    for char in text:
        code = ord(char)
        if code < 0x80:
            out.append(char)
            continue
        replacement = _PUNCTUATION.get(char) or _LATIN_LETTERS.get(char)
        if replacement is not None:
            out.append(replacement)
            continue
        category = unicodedata.category(char)
        if category in {"Zs", "Zl", "Zp"}:
            out.append(" ")
            continue
        if category in {"Mn", "Me", "Cc", "Cf"}:
            continue
        base = "".join(
            c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
        )
        if base and base.isascii():
            out.append(base)
            continue
        out.append("?")
        if lost is not None:
            script = _script_of(char)
            lost[script] = lost.get(script, 0) + 1
    return "".join(out)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t\r\n]+")


def _quotes(text: str) -> str:
    """Straight double quotes as TeX quotes: opening after a space or an opening bracket (or at
    the start), closing everywhere else. Left alone, `"` would print as a closing quote."""
    out: list[str] = []
    for index, char in enumerate(text):
        if char != '"':
            out.append(char)
            continue
        before = text[index - 1] if index else " "
        out.append("``" if before in " ([<-" else "''")
    return "".join(out)


def _is_respelling(char: str, spelling: str) -> bool:
    """Whether writing `char` as `spelling` changed a letter of somebody's text: an accented
    letter without its accent, or a letter with its own ASCII spelling. A ligature or a
    full-width letter written as plain letters says the same thing and is not counted."""
    if spelling in {"", "?"} or not unicodedata.category(char).startswith("L"):
        return False
    return char in _LATIN_LETTERS or unicodedata.normalize("NFD", char) != char


_LIGATURE_PAIRS = frozenset("<>,")
"""Characters that the typeface joins when they are doubled (`<<` is drawn as one guillemet, `,,`
as one low quotation mark). An empty group between the two keeps them as typed."""


class LatexText:
    """Turns text into what a template may print, and remembers what it could not draw.

    One instance serves one document, so `warnings` can say what was lost anywhere in it."""

    def __init__(self) -> None:
        self._lost: dict[str, int] = {}
        self._respelled: dict[str, str] = {}
        self._respelled_count = 0

    def _fold(self, text: str) -> str:
        out: list[str] = []
        for char in unicodedata.normalize("NFC", text):
            if char in DRAWN_LETTERS:
                out.append(char)
                continue
            spelling = fold_to_ascii(char, self._lost)
            if _is_respelling(char, spelling):
                self._respelled.setdefault(char, spelling)
                self._respelled_count += 1
            out.append(spelling)
        return "".join(out)

    def __call__(self, text: str) -> str:
        folded = self._fold(text)
        folded = _CONTROL.sub("", folded)
        folded = _SPACES.sub(" ", folded).strip()
        # A backtick would open a TeX quote ("`"), so it is written as a plain apostrophe; the
        # quotes in the output are only the ones `_quotes` writes on purpose.
        folded = _quotes(folded.replace("`", "'"))
        out: list[str] = []
        run = 0
        previous = ""
        for char in folded:
            run = 0 if char == " " else run + 1
            if run > _BREAK_AFTER:
                out.append(r"\allowbreak{}")
                run = 1
            if char == previous and char in _LIGATURE_PAIRS:
                out.append("{}")
            out.append(_ESCAPES.get(char, char))
            previous = char
        return "".join(out)

    def warnings(self) -> list[str]:
        """What did not survive, in sentences: the scripts the renderer cannot draw, and the
        letters that were respelled in plain ASCII. Nothing when nothing was changed."""
        warnings: list[str] = []
        if self._lost:
            scripts = ", ".join(sorted(self._lost))
            total = sum(self._lost.values())
            warnings.append(
                f"{total} character(s) ({scripts}) can't be drawn by the PDF renderer, which "
                "sets Latin text only, and were replaced with '?'. Check names and other text "
                "that uses them."
            )
        if self._respelled:
            shown = ", ".join(f"{c} as {w}" for c, w in list(self._respelled.items())[:6])
            warnings.append(
                f"{self._respelled_count} letter(s) the PDF renderer can't set as written were "
                f"respelled in plain ASCII ({shown}). Check names and places that use them."
            )
        return warnings


# -- links ---------------------------------------------------------------------------------

_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_BARE_HOST = re.compile(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?:[/?#:]|$)")
_URL_SAFE = "-._~:/?#[]@!$&'()*+,;=%"
MAX_URL_CHARS = 500
"""The longest address `clean_url` accepts; a longer one is refused, never cut."""


def clean_url(raw: str) -> str | None:
    """A link that is safe to put in a document, or None.

    Only http, https, mailto and tel addresses are kept (`javascript:`, `file:` and every other
    scheme are refused), a bare host such as `github.com/ada` gets `https://`, and whitespace
    or an over-long address makes the whole thing unusable rather than guessed at. Characters
    that are not legal in an address are percent-encoded."""
    candidate = _CONTROL.sub("", raw).strip()
    if not candidate or len(candidate) > MAX_URL_CHARS or any(c.isspace() for c in candidate):
        return None
    match = _SCHEME.match(candidate)
    if match is None:
        if not _BARE_HOST.match(candidate):
            return None
        candidate = f"https://{candidate}"
    elif match.group(1).lower() not in _SCHEMES:
        return None
    # `$`, `'`, `(`... are legal in an address and survive \href as they are; the characters
    # TeX itself reads as syntax (`\`, `{`, `}`, `^`), and anything outside ASCII, are encoded.
    return quote(candidate, safe=_URL_SAFE)


def latex_url(url: str) -> str:
    """`url` (from `clean_url`) as the first argument of `\\href`: `%`, `#`, `&` and `_` are
    escaped, which hyperref turns back into the plain character in the link it writes."""
    return "".join({"%": r"\%", "#": r"\#", "&": r"\&", "_": r"\_"}.get(char, char) for char in url)
