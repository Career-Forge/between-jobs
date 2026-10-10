"""Plain-text hygiene shared by every stage of the built-in engine.

Two jobs. Text that comes from outside (a job posting above all, which a stranger wrote) is
cleaned and clipped before a prompt carries it. And the text a prompt carries is laid out in
tagged sections whose tags cannot be forged from inside: every `<` and `>` in a section's text
is written as an entity, so a posting that contains `</job_posting>` or a whole fake section
stays inside the section it arrived in, as plain characters.
"""

from __future__ import annotations

import re
import unicodedata

_STRIPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})
"""Control, format (zero-width, bidirectional overrides, the invisible "tag" characters),
surrogate, private-use, unassigned and line/paragraph separators."""

_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_line(text: str, max_chars: int | None = None) -> str:
    """`text` on one line: invisible and control characters removed, whitespace collapsed,
    clipped to `max_chars`."""
    kept = "".join(
        " " if char in "\n\r\t" else char
        for char in text
        if char in "\n\r\t" or unicodedata.category(char) not in _STRIPPED_CATEGORIES
    )
    collapsed = _SPACES.sub(" ", kept).strip()
    return collapsed[:max_chars].rstrip() if max_chars is not None else collapsed


def clean_block(text: str, max_chars: int) -> str:
    """Like `clean_line` but keeps line breaks (at most one blank line in a row), for a posting
    whose structure helps the reader. Clipped to `max_chars`."""
    kept = "".join(
        char
        for char in text.replace("\r\n", "\n").replace("\r", "\n")
        if char in "\n\t" or unicodedata.category(char) not in _STRIPPED_CATEGORIES
    )
    lines = [_SPACES.sub(" ", line).strip() for line in kept.split("\n")]
    joined = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return joined[:max_chars].rstrip()


def escape_markup(text: str) -> str:
    """`<` and `>` written as entities, so text inside a section can never form a tag. (`&` is
    left alone: it forges nothing, and escaping it would hand the model "R&amp;D" to copy.)"""
    return text.replace("<", "&lt;").replace(">", "&gt;")


def unescape_markup(text: str) -> str:
    """What a model that copied an entity out of a section meant: `&lt;`, `&gt;` and `&amp;`
    back to the characters. Nothing else is decoded."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def model_text(value: object, max_chars: int | None = None) -> str:
    """A string from a model's answer on one line, entities decoded and invisible characters
    removed; empty if the value is not a string at all."""
    return clean_line(unescape_markup(value), max_chars) if isinstance(value, str) else ""


_LIST_MARKER = re.compile(r"^(?:[-*+\u2022\u2023\u25cf\u25aa\u25e6\u2013\u2014]|\d{1,2}[.)])\s+")
_MARKER_PASSES = 2

FIRST_PERSON = re.compile(r"\b(?:I(?!/)|[Mm]y|[Mm]e|[Mm]ine)\b")
"""First-person words. `I/O` is not one, nor is the state abbreviation `ME`."""


def strip_list_marker(text: str) -> str:
    """`text` without a list marker the model put in front of it ("- ", "* ", a bullet
    character, "1. "): structure is code's, and a bullet is already a list item. At most two
    markers are removed, so a runaway answer cannot make this loop."""
    for _ in range(_MARKER_PASSES):
        stripped = _LIST_MARKER.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text


def section(tag: str, text: str) -> str:
    """One tagged section of a prompt. The tag name is the engine's own (a literal in the
    prompt module); the text is data, with `<` and `>` written as entities."""
    return f"<{tag}>\n{escape_markup(text)}\n</{tag}>"


def word_count(text: str) -> int:
    return len(text.split())
