"""The grounding guard for the resume importer: nothing stays in the draft that the document
does not contain.

A model turns the document's text into the profile's JSON shape. Models invent: an employer
that sounds right, a skill the person never listed, a month nobody wrote. So the model's
answer is never trusted. `ground_profile` walks it field by field, following the profile model
itself (`ResumeTemplate`, by reflection, so a field added there is covered without anyone
remembering to add it here), and keeps a value only when it is grounded in the document's text:

- a text value is kept when it appears in the text, compared case-insensitively and without
  regard to spacing or punctuation, and only at word boundaries (so `Go` is not found inside
  `Google`, nor `6` inside `16`). The numbers in the stretch of text it matches must be the
  numbers in the value, written the same way (`1.4M` is not `14M`, even when a `14` is
  elsewhere in the document). A value of one or two letters is matched case-exact and not
  when a `&` or a hyphen joins it to the word beside it: `R` is not the `r` of `R&D`, `Go` is
  not the `go` of `go-to-market`, region `OR` and country `US` are not the `or` and `us` of
  ordinary prose. (A capital letter that really is in the document -- `Section C` -- still
  grounds a `C`: that cannot be told apart from a skill without reading it.) A sentence that
  wraps over a PDF page break has the page's footer and the next page's header between its two
  halves; a value that matches only with up to two short lines at each side of a page break
  left out is kept, and nothing else is let through that way;
- a date is kept when `profile_import_dates.ground_date` finds it in the text, in whatever
  format the document wrote it, and is stored as `YYYY-MM`, `YYYY` or `present`;
- an email, a phone number or a link is kept when it is in the text and has the right shape
  (a link must be a web address: `javascript:` and friends are refused). A link or an address
  must be the whole address as written, not the start or the end of a longer one: the
  `linkedin.example.com/in/jo` of `linkedin.example.com/in/jo-4a7b2` is another profile, the
  domain of an email is not the person's portfolio;
- a field that is derived rather than written -- `is_current`, `primary`, `show_on_resume` --
  is set by rule, never taken from the model (`primary` is the email or phone the document
  shows first);
- a field that is never taken from a document -- a photo, a date of birth, a pinned entry --
  is dropped if the model proposes one;
- two judgments of placement that need no model: a location that starts with a word for a work
  arrangement (`Remote`, `Remote, US`, `Hybrid - Boston`) is not a place, and a `field` or an
  `issuer` that the `degree` or the certification's `name` already contains is not said twice.

What the guard proves is provenance, not meaning. A value the document contains is kept even
if the document contains it in a sentence a stranger planted for the model to find, or in a
different role than the model gave it (a skill under the wrong heading, a date or a "present"
that belongs to another job). The prompt asks for better, the review screen shows each value's
source span so a misplaced one is visible, and the person still activates the draft; but
containment is all this module can check.

Whatever is not kept is DROPPED, never replaced by a guess, and listed in the report with its
JSON path and the reason. A list entry that loses a field it cannot do without (a job with no
title) is dropped whole and reported once. Extra keys, values of the wrong type and oversized
values from a hostile or confused model are dropped the same way.

`compute_source_spans` is the second half: for every value that survived, where in the text it
came from, as character offsets into the same normalized text (Python string indexes, which
count code points; `utf16_offsets` converts them for a JavaScript client), so a review screen
can highlight it.

Pure functions over a string and a dict. No database, no network, no model call.
"""

from __future__ import annotations

import bisect
import re
import types
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .profile import Experience, Location, Personal, ResumeTemplate
from .profile_import_dates import DateIndex, DateRole, ground_date

DropReason = Literal[
    "not_in_document",
    "date_not_in_document",
    "invalid_value",
    "wrong_type",
    "unknown_field",
    "too_long",
    "too_many",
    "never_from_document",
    "duplicates_sibling",
    "entry_incomplete",
]

_MAX_LEAF_CHARS = 1_500
_MAX_LIST_ITEMS = 100
_MAX_REPORTED_VALUE_CHARS = 160
_MAX_OCCURRENCES = 2_000
_SIGNIFICANT = frozenset("+#$%")
"""Besides letters and digits, the characters that change what a token is: `C++` is not `C`,
`50%` is not `50`."""
_TINY_MAX_LETTERS = 2
"""A value of this many letters or fewer must match case-exact and stand alone (see the module
docstring). Three-letter skills (`SQL`, `AWS`, `Git`) are often written in lowercase lists and
stay case-insensitive."""
_GLUE = frozenset("&-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{EN DASH}")
"""What joins a short value to the word beside it so that it is part of that word, not a word.
`/` is not here: `R/Python` and `C/C++` are lists."""
_ADDRESS_CONTINUES_LEFT = frozenset("._%+-@/~")
_ADDRESS_CONTINUES_RIGHT = frozenset("_%+-@~")
_EDGE_LINES = 2
_EDGE_LINE_MAX_CHARS = 80
"""How much of a page's edge may be left out when a value is looked for across a page break:
at most this many lines at the end of a page and at the start of the next, none longer than
this (a long line is text, not a footer or a running header)."""
_EDGE_VARIANTS = tuple(
    sorted(
        [(foot, head) for foot in range(_EDGE_LINES + 1) for head in range(_EDGE_LINES + 1)][1:],
        key=lambda c: (sum(c), abs(c[0] - c[1])),
    )
)
"""(lines left out at the foot of a page, at the head of the next), fewest in all first."""

# (model, field) -> (which end of a range, must be YYYY-MM)
DATE_FIELDS: dict[tuple[str, str], tuple[DateRole, bool]] = {
    ("Experience", "start_date"): ("start", True),
    ("Experience", "end_date"): ("end", True),
    ("Education", "start_date"): ("start", False),
    ("Education", "end_date"): ("end", False),
    ("VolunteerEntry", "start_date"): ("start", False),
    ("VolunteerEntry", "end_date"): ("end", False),
    ("Publication", "date"): ("single", False),
    ("Patent", "date"): ("single", False),
    ("Certification", "date"): ("single", False),
}
URL_FIELDS = frozenset(
    {
        ("Links", "linkedin"),
        ("Links", "github"),
        ("Links", "portfolio"),
        ("Links", "scholar"),
        ("OtherLink", "url"),
        ("Project", "url"),
        ("Publication", "url"),
        ("Patent", "url"),
        ("Certification", "url"),
    }
)
PLACE_FIELDS = frozenset({("Location", "city"), ("Location", "region"), ("Location", "country")})
"""Fields that name a place. A word for a work arrangement is in the document often enough
(`Remote, US`) and is exactly what a model files under `city` when it must put something there."""
_PLACE_SPLIT_RE = re.compile(r"\s*[,;/|()\N{EN DASH}\N{EM DASH}]\s*|\s+-\s+")
_NOT_A_PLACE = frozenset(
    {
        "remote",
        "hybrid",
        "onsite",
        "on-site",
        "on site",
        "anywhere",
        "worldwide",
        "global",
        "wfh",
        "work from home",
        "telecommute",
    }
)
ALREADY_IN_SIBLING: dict[str, tuple[str, str]] = {
    "Education": ("field", "degree"),
    "Certification": ("issuer", "name"),
}
"""model -> (field, sibling): the field is dropped when the sibling already contains it. A model
that cannot tell a degree from its subject copies `Applied Widget Engineering Certificate` into
both `degree` and `field`; the same words are in the document, so grounding alone keeps both,
and the resume would say it twice. The profile splits them (`Bachelor of Science` and
`Statistics`), never repeats."""
EMAIL_FIELDS = frozenset({("Email", "address")})
PHONE_FIELDS = frozenset({("Phone", "number")})
NEVER_FROM_DOCUMENT = frozenset(
    {
        ("Personal", "dob"),
        ("Personal", "nationality"),
        ("Personal", "marital_status"),
        ("Personal", "photo"),
        ("Personal", "signature"),
        ("Personal", "work_authorization_status"),
        ("Phone", "region"),
    }
)
"""Render-only or sensitive fields a document import never fills: the person sets them on
purpose in the profile editor. `pin` is never taken from a document either (see `is_pin`)."""

_WEB_URL_RE = re.compile(
    r"^(?:https?://)?[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
    r"(?::\d{1,5})?(?:[/?#]\S*)?$"
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MARKER_CHARS = (
    "\N{BULLET}\N{BLACK CIRCLE}\N{BLACK SMALL SQUARE}\N{MIDDLE DOT}"
    "\N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK}"
    "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}*-\N{EN DASH}\N{EM DASH}"
)
_LEADING_MARKER_RE = re.compile(rf"^[\s{re.escape(_MARKER_CHARS)}]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS_RE = re.compile(r",(?=\d{3}(?!\d))")


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class DroppedLeaf:
    path: str
    """JSON pointer into the model's draft (`/experience/1/bullets/2`)."""
    reason: DropReason
    detail: str
    value: str | None = None
    """What the model proposed, shortened; None when it was not a string."""


@dataclass(frozen=True, slots=True)
class Assumption:
    path: str
    value: str
    note: str


@dataclass(slots=True)
class GuardResult:
    profile: dict[str, Any] | None
    """The cleaned draft, ready for the profile model; None when something essential is
    missing (`problems` says what)."""
    dropped: list[DroppedLeaf] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    kept: int = 0
    """How many values were checked against the text and kept."""


# -- the document ----------------------------------------------------------------------------


def squash(text: str) -> str:
    """The comparison form of a string: NFKC, case-folded, only letters, digits and the
    significant symbols."""
    out: list[str] = []
    for ch in unicodedata.normalize("NFKC", text):
        for folded in ch.casefold():
            if folded.isalnum() or folded in _SIGNIFICANT:
                out.append(folded)
    return "".join(out)


def _as_written(text: str) -> str:
    """`squash` without the case folding: the letters, digits and symbols as they are written."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", text) if ch.isalnum() or ch in _SIGNIFICANT
    )


def _number_tokens(text: str) -> list[str]:
    """The numbers a string holds in order, thousands separators removed (`3,000` is `3000`)."""
    return [_THOUSANDS_RE.sub("", token) for token in _NUMBER_RE.findall(text)]


def numbers_in(text: str) -> set[str]:
    """The numbers a string holds, thousands separators removed (`3,000` is `3000`)."""
    return set(_number_tokens(text))


@dataclass(slots=True)
class _Index:
    """The text reduced to its letters, digits and significant symbols, with a map back to
    where each one was and flags for where a word begins and ends. `skipped` lists the ranges of
    the text that were left out."""

    squashed: str
    offsets: list[int]
    starts: bytearray
    ends: bytearray
    skipped: tuple[tuple[int, int], ...] = ()


def _build_index(text: str, skipped: Sequence[tuple[int, int]] = ()) -> _Index:
    squashed: list[str] = []
    offsets: list[int] = []
    starts = bytearray()
    ends = bytearray()
    in_token = False
    pending = iter(skipped)
    current = next(pending, None)
    for position, ch in enumerate(text):
        while current is not None and position >= current[1]:
            current = next(pending, None)
        left_out = current is not None and position >= current[0]
        for folded in ch.casefold():
            if not left_out and (folded.isalnum() or folded in _SIGNIFICANT):
                if not in_token:
                    starts.append(1)
                    in_token = True
                else:
                    starts.append(0)
                ends.append(0)
                squashed.append(folded)
                offsets.append(position)
            elif in_token:
                ends[-1] = 1
                in_token = False
    if in_token:
        ends[-1] = 1
    return _Index("".join(squashed), offsets, starts, ends, tuple(skipped))


class GroundingDocument:
    """The document's text prepared for matching: a squashed copy with a map back to the
    original offsets, and the places where a word begins and ends. `page_breaks` are the
    offsets where a PDF's pages after the first begin (`ExtractedDocument.page_breaks`)."""

    def __init__(self, text: str, page_breaks: Sequence[int] = ()) -> None:
        self.text = text
        self._strict = _build_index(text)
        self._page_breaks = tuple(sorted({b for b in page_breaks if 0 < b < len(text)}))
        self._edge_indexes: list[_Index] | None = None
        self.dates = DateIndex.from_text(text)
        self._found: dict[tuple[str, tuple[str, ...], str], list[tuple[int, int]]] = {}

    def find(self, value: str) -> list[tuple[int, int]]:
        """Every place `value` is in the text, as (start, end) offsets: whole words only, spacing
        and punctuation ignored, the numbers in the matched text the same as the value's.
        Remembered per distinct value (the same letters with another number, or another case for
        a short one, is another value), so a long list of the same word costs one search.

        A value of one or two letters must also match case-exact, and not as part of a word
        joined by `&` or a hyphen. When nothing is found and the text has page breaks, the search
        is repeated with the lines at the edge of each page left out (a sentence that wraps over
        a page break has a footer and a header between its halves)."""
        needle = squash(value)
        if not needle:
            return []
        tiny = len(needle) <= _TINY_MAX_LETTERS and needle.isalpha()
        written = _as_written(value) if tiny else ""
        numbers = _number_tokens(value)
        cache_key = (needle, tuple(numbers), written)
        cached = self._found.get(cache_key)
        if cached is not None:
            return cached
        found = self._search(self._strict, needle, numbers, written if tiny else None)
        if not found:
            for index in self._page_edge_indexes():
                found = self._search(index, needle, numbers, written if tiny else None)
                if found:
                    break
        self._found[cache_key] = found
        return found

    def _search(
        self, index: _Index, needle: str, numbers: list[str], written: str | None
    ) -> list[tuple[int, int]]:
        found: list[tuple[int, int]] = []
        position = 0
        while len(found) < _MAX_OCCURRENCES:
            at = index.squashed.find(needle, position)
            if at < 0:
                break
            last = at + len(needle) - 1
            position = at + 1
            if not (index.starts[at] and index.ends[last]):
                continue
            start, end = index.offsets[at], index.offsets[last] + 1
            if _number_tokens(self._visible(start, end, index.skipped)) != numbers:
                continue
            if written is not None and not self._stands_alone(start, end, written):
                continue
            found.append((start, end))
        return found

    def _visible(self, start: int, end: int, skipped: tuple[tuple[int, int], ...]) -> str:
        """The text from `start` to `end` without the ranges an index left out."""
        if not skipped:
            return self.text[start:end]
        pieces: list[str] = []
        at = start
        for left, right in skipped:
            if right <= at or left >= end:
                continue
            if left > at:
                pieces.append(self.text[at:left])
            at = max(at, right)
        if at < end:
            pieces.append(self.text[at:end])
        return " ".join(pieces)

    def _stands_alone(self, start: int, end: int, written: str) -> bool:
        """For a value of one or two letters: the text there is written exactly as the value is
        (the case matches) and is not glued to a neighbouring word (`R&D`, `go-to-market`)."""
        text = self.text
        if _as_written(text[start:end]) != written:
            return False
        glued_before = start >= 2 and text[start - 1] in _GLUE and text[start - 2].isalnum()
        glued_after = end + 1 < len(text) and text[end] in _GLUE and text[end + 1].isalnum()
        return not (glued_before or glued_after)

    def _page_edge_indexes(self) -> list[_Index]:
        """The text indexed again with the edge lines of every page left out, for each number of
        lines (up to `_EDGE_LINES`) at the foot of a page and at the head of the next, fewest
        lines first. Built the first time a value is not found, and only when the text has
        page breaks."""
        if self._edge_indexes is None:
            self._edge_indexes = []
            if self._page_breaks:
                for foot, head in _EDGE_VARIANTS:
                    skipped = sorted(
                        r for b in self._page_breaks for r in self._edge_lines(b, foot, head)
                    )
                    if skipped:
                        self._edge_indexes.append(_build_index(self.text, skipped))
        return self._edge_indexes

    def _edge_lines(self, page_break: int, foot: int, head: int) -> list[tuple[int, int]]:
        """The ranges of the last `foot` lines of text before a page break and the first `head`
        after it. None at all when one of them is too long to be page furniture."""
        text = self.text
        ranges: list[tuple[int, int]] = []
        end = len(text[:page_break].rstrip())
        for _ in range(foot):
            if end <= 0:
                break
            start = text.rfind("\n", 0, end) + 1
            if end - start > _EDGE_LINE_MAX_CHARS:
                return []
            ranges.append((start, end))
            end = len(text[:start].rstrip())
        start = page_break
        for _ in range(head):
            while start < len(text) and text[start].isspace():
                start += 1
            if start >= len(text):
                break
            end = text.find("\n", start)
            end = len(text) if end < 0 else end
            if end - start > _EDGE_LINE_MAX_CHARS:
                return []
            ranges.append((start, end))
            start = end
        return ranges

    def contains(self, value: str) -> bool:
        return bool(self.find(value))

    def contains_address(self, value: str) -> bool:
        """A link or an email address is in the text AS an address: the whole of it, not the
        start or the end of a longer one. `find` ignores punctuation, and a `.`, `/`, `-` or `@`
        is exactly where a longer address could continue, so the characters on either side of
        the match are checked too: the scheme and `www.` that were stripped before matching
        may be there, and so may whatever comes after the address in a sentence."""
        return any(self._ends_the_address(start, end) for start, end in self.find(value))

    def _ends_the_address(self, start: int, end: int) -> bool:
        text = self.text
        if start > 0 and text[start - 1] in _ADDRESS_CONTINUES_LEFT:
            if not text[max(0, start - 8) : start].lower().endswith(("://", "www.")):
                return False
        elif start > 0 and text[start - 1].isalnum():
            return False
        if end >= len(text):
            return True
        after = text[end]
        if after.isalnum() or after in _ADDRESS_CONTINUES_RIGHT:
            return False
        return not (after in "./" and end + 1 < len(text) and text[end + 1].isalnum())


def utf16_offsets(text: str) -> Callable[[int], int]:
    """A function from an offset into `text` in code points (what Python indexes by, and what
    `GroundingDocument` and the source spans use) to the same place in UTF-16 code units (what
    JavaScript's `String.prototype.slice` indexes by). The two differ by one for every character
    outside the Basic Multilingual Plane before the offset -- every emoji. Valid for offsets that
    fall between characters, which every span boundary does."""
    astral = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]
    return lambda offset: offset + bisect.bisect_left(astral, offset)


# -- reading the profile model ---------------------------------------------------------------

Kind = Literal["str", "str_list", "model", "model_list", "bool", "other"]


def classify_annotation(annotation: Any) -> tuple[Kind, type[BaseModel] | None]:
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = [a for a in get_args(annotation) if a is not type(None)]
        return classify_annotation(members[0]) if len(members) == 1 else ("other", None)
    if annotation is str:
        return "str", None
    if annotation is bool:
        return "bool", None
    if origin is list:
        (item,) = get_args(annotation)
        if item is str:
            return "str_list", None
        if isinstance(item, type) and issubclass(item, BaseModel):
            return "model_list", item
        return "other", None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return "model", annotation
    return "other", None


def is_pin(model: type[BaseModel], name: str) -> bool:
    return name == "pin" and "pin" in model.model_fields


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _is_work_arrangement(text: str) -> bool:
    """The text is a word for a work arrangement, or starts with one before a list or bracket
    separator (`Remote`, `Remote, US`, `Remote (US)`, `Hybrid - Boston`). Such a value is
    dropped, not trimmed to the place after it: code does not rewrite what the document wrote,
    and what is left unknown stays labeled as unknown."""
    folded = text.casefold()
    if folded.strip(" .,") in _NOT_A_PLACE:
        return True
    parts = [part.strip(" .,") for part in _PLACE_SPLIT_RE.split(folded) if part.strip(" .,")]
    return bool(parts) and parts[0] in _NOT_A_PLACE


def _strip_marker(value: str) -> str:
    return _LEADING_MARKER_RE.sub("", value)


def _url_for_matching(value: str) -> str:
    value = re.sub(r"^https?://", "", value.strip(), flags=re.IGNORECASE)
    value = re.sub(r"^www\.", "", value, flags=re.IGNORECASE)
    return value.rstrip("/")


def _shorten(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return (
        value
        if len(value) <= _MAX_REPORTED_VALUE_CHARS
        else value[:_MAX_REPORTED_VALUE_CHARS] + "..."
    )


def _describe_type(value: object) -> str:
    return type(value).__name__


# -- the walk --------------------------------------------------------------------------------


class _Walker:
    def __init__(self, doc: GroundingDocument) -> None:
        self.doc = doc
        self.dropped: list[DroppedLeaf] = []
        self.assumptions: list[Assumption] = []
        self.problems: list[str] = []
        self.kept = 0

    def drop(self, path: str, reason: DropReason, detail: str, value: object = None) -> None:
        self.dropped.append(DroppedLeaf(path, reason, detail, _shorten(value)))

    def model(
        self, cls: type[BaseModel], data: dict[str, Any], path: str
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """The cleaned object and the names of required fields it lacks (None when it lacks
        any)."""
        cleaned: dict[str, Any] = {}
        missing: list[str] = []
        for name in data:
            if name not in cls.model_fields:
                self.drop(
                    f"{path}/{name}", "unknown_field", "not a field of the profile", data[name]
                )

        for name, info in cls.model_fields.items():
            child = f"{path}/{name}"
            value = data.get(name)
            present = name in data and value not in (None, "", [], {})
            kind, sub = classify_annotation(info.annotation)

            if (cls.__name__, name) in NEVER_FROM_DOCUMENT or is_pin(cls, name):
                if present:
                    self.drop(
                        child,
                        "never_from_document",
                        "this field is not filled from a document; set it in the profile editor",
                        value,
                    )
                continue
            if kind in ("bool", "other"):
                if present and kind == "other":
                    self.drop(child, "never_from_document", "not read from a document", value)
                continue

            if kind == "str":
                kept = self.leaf(cls, name, child, value) if present else None
                if kept is not None:
                    cleaned[name] = kept
                elif info.is_required():
                    missing.append(name)
            elif kind == "str_list":
                if present:
                    items = self.str_list(cls, name, child, value)
                    if items:
                        cleaned[name] = items
            elif kind == "model" and sub is not None:
                if present and isinstance(value, dict):
                    inner, inner_missing = self.model(sub, value, child)
                    if inner is not None:
                        cleaned[name] = inner
                    else:
                        missing.append(name)
                        self.problems.append(f"{child}: no {', '.join(inner_missing)} found")
                elif present:
                    self.drop(
                        child, "wrong_type", f"expected an object, got {_describe_type(value)}"
                    )
                    if info.is_required():
                        missing.append(name)
                elif info.is_required():
                    missing.append(name)
            elif kind == "model_list" and sub is not None and present:
                entries = self.entries(sub, child, value)
                if entries:
                    cleaned[name] = entries

        redundant = ALREADY_IN_SIBLING.get(cls.__name__)
        if redundant is not None:
            field_name, sibling_name = redundant
            repeated, sibling = cleaned.get(field_name), cleaned.get(sibling_name)
            if repeated and sibling and GroundingDocument(sibling).find(repeated):
                self.drop(
                    f"{path}/{field_name}",
                    "duplicates_sibling",
                    f"already part of the {sibling_name}",
                    repeated,
                )
                del cleaned[field_name]
                self.kept -= 1

        if cls is Experience:
            cleaned["is_current"] = cleaned.get("end_date") == "present"
        elif cls is Location:
            cleaned["show_on_resume"] = any(cleaned.get(k) for k in ("city", "region", "country"))
        elif cls is Personal:
            for key, field_name in (("emails", "address"), ("phones", "number")):
                self.mark_primary(cleaned.get(key, []), field_name)
        return (None if missing else cleaned), missing

    def mark_primary(self, entries: list[dict[str, Any]], field_name: str) -> None:
        """`primary` goes to the entry the document shows first (an earlier one in the list
        wins a tie, and so does one the document does not show at all). The model's own order
        and its own `primary` are not used: an instruction planted in a document could
        otherwise name the address that receives recruiters' replies."""

        def first_seen(entry: dict[str, Any]) -> int:
            found = self.doc.find(entry[field_name])
            return found[0][0] if found else len(self.doc.text)

        positions = [first_seen(entry) for entry in entries]
        primary = min(range(len(entries)), key=lambda i: (positions[i], i), default=None)
        for index, entry in enumerate(entries):
            entry["primary"] = index == primary

    def entries(self, cls: type[BaseModel], path: str, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            self.drop(path, "wrong_type", f"expected a list, got {_describe_type(value)}")
            return []
        kept: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            child = f"{path}/{index}"
            if index >= _MAX_LIST_ITEMS:
                self.drop(
                    child, "too_many", f"more than {_MAX_LIST_ITEMS} entries; the rest were dropped"
                )
                break
            if not isinstance(item, dict):
                self.drop(child, "wrong_type", f"expected an object, got {_describe_type(item)}")
                continue
            cleaned, missing = self.model(cls, item, child)
            if cleaned is None:
                # The fields that were dropped inside it are already in the report; one more
                # line says the entry as a whole went.
                self.drop(
                    child,
                    "entry_incomplete",
                    "removed: no value for "
                    + ", ".join(missing)
                    + " could be found in the document",
                )
                continue
            kept.append(cleaned)
        return kept

    def str_list(self, cls: type[BaseModel], name: str, path: str, value: object) -> list[str]:
        if not isinstance(value, list):
            self.drop(path, "wrong_type", f"expected a list, got {_describe_type(value)}")
            return []
        kept: list[str] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            child = f"{path}/{index}"
            if index >= _MAX_LIST_ITEMS:
                self.drop(
                    child, "too_many", f"more than {_MAX_LIST_ITEMS} items; the rest were dropped"
                )
                break
            if item in (None, ""):
                continue
            text = self.leaf(cls, name, child, item, list_item=True)
            if text is None:
                continue
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                kept.append(text)
        return kept

    def leaf(
        self,
        cls: type[BaseModel],
        name: str,
        path: str,
        value: object,
        *,
        list_item: bool = False,
    ) -> str | None:
        if not isinstance(value, str):
            self.drop(path, "wrong_type", f"expected text, got {_describe_type(value)}")
            return None
        text = _clean_text(value)
        if list_item:
            text = _strip_marker(text)
        if not text:
            return None
        if len(text) > _MAX_LEAF_CHARS:
            self.drop(path, "too_long", f"longer than {_MAX_LEAF_CHARS} characters", text)
            return None

        key = (cls.__name__, name)
        if key in DATE_FIELDS:
            role, require_month = DATE_FIELDS[key]
            grounding = ground_date(text, self.doc.dates, role=role, require_month=require_month)
            if grounding is None:
                self.drop(path, "date_not_in_document", "this date is not in the document", text)
                return None
            if grounding.year_only:
                self.assumptions.append(
                    Assumption(
                        path,
                        grounding.value,
                        "The document shows only the year, so the month is a convention "
                        "(January for a start, December for an end). Check it.",
                    )
                )
            self.kept += 1
            return grounding.value

        if key in PLACE_FIELDS and _is_work_arrangement(text):
            self.drop(path, "invalid_value", "a work arrangement, not a place", text)
            return None
        if key in EMAIL_FIELDS and not _EMAIL_RE.match(text):
            self.drop(path, "invalid_value", "not an email address", text)
            return None
        if key in PHONE_FIELDS and not 7 <= sum(ch.isdigit() for ch in text) <= 15:
            self.drop(path, "invalid_value", "not a phone number", text)
            return None
        needle = text
        if key in URL_FIELDS:
            if not _WEB_URL_RE.match(text):
                self.drop(path, "invalid_value", "not a web address", text)
                return None
            needle = _url_for_matching(text)

        is_address = key in URL_FIELDS or key in EMAIL_FIELDS
        if not (self.doc.contains_address(needle) if is_address else self.doc.contains(needle)):
            self.drop(path, "not_in_document", "this text is not in the document", text)
            return None
        self.kept += 1
        return text


def ground_profile(raw: object, doc: GroundingDocument) -> GuardResult:
    """Cleans the model's answer against the document. `profile` is None, with `problems`
    saying why, when the answer is not an object or lacks something no profile can do without
    (the person's name)."""
    if not isinstance(raw, dict):
        return GuardResult(
            None, problems=[f"the model's answer was not a JSON object (got {_describe_type(raw)})"]
        )
    walker = _Walker(doc)
    cleaned, missing = walker.model(ResumeTemplate, raw, "")
    problems: list[str] = []
    if cleaned is None:
        if "personal" in missing:
            problems.append("no name for the person could be found in the document")
        problems.extend(p for p in walker.problems if not p.startswith("/personal"))
    return GuardResult(cleaned, walker.dropped, walker.assumptions, problems, walker.kept)


# -- source spans ----------------------------------------------------------------------------


def compute_source_spans(profile: dict[str, Any], doc: GroundingDocument) -> dict[str, Span]:
    """For every non-empty text, date, link, email and phone value in `profile` (the final
    canonical profile), where in the document it came from. Keyed by JSON pointer into
    `profile`.

    A value that appears several times is placed at the occurrence that belongs to its entry:
    the first value of an entry (its title) is placed after the previous entry's, since a
    resume lists entries in order, and the rest of the entry's values at the nearest
    occurrence at or after it. An occurrence another value has already taken is passed over
    when there is a free one."""
    spans: dict[str, Span] = {}
    claimed: set[tuple[int, int]] = set()

    def pick(
        found: list[tuple[int, int]], anchor: int | None, floor: int | None
    ) -> tuple[int, int] | None:
        if not found:
            return None
        free = [f for f in found if f not in claimed] or found
        if anchor is None:
            if floor is not None:
                free = [f for f in free if f[0] >= floor] or free
            return free[0]
        ahead = [f for f in free if f[0] >= anchor]
        return min(ahead, key=lambda f: f[0] - anchor) if ahead else max(free, key=lambda f: f[0])

    def leaf_span(
        cls: type[BaseModel], name: str, value: str, anchor: int | None, floor: int | None
    ) -> tuple[int, int] | None:
        key = (cls.__name__, name)
        if key in DATE_FIELDS:
            role, require_month = DATE_FIELDS[key]
            grounding = ground_date(
                value, doc.dates, role=role, require_month=require_month, near=anchor
            )
            return grounding.span if grounding else None
        needle = _url_for_matching(value) if key in URL_FIELDS else value
        return pick(doc.find(needle), anchor, floor)

    def visit(
        cls: type[BaseModel],
        data: dict[str, Any],
        path: str,
        anchor: int | None,
        floor: int | None,
    ) -> tuple[int, int] | None:
        """Records the spans of one object; returns where its first value was found."""
        local = anchor
        first: tuple[int, int] | None = None
        for name, info in cls.model_fields.items():
            if name not in data:
                continue
            value = data[name]
            child = f"{path}/{name}"
            kind, sub = classify_annotation(info.annotation)
            if kind == "str" and isinstance(value, str) and value:
                found = leaf_span(cls, name, value, local, floor)
                if found is not None:
                    spans[child] = Span(*found)
                    claimed.add(found)
                    if local is None:
                        local = found[0]
                        first = found
            elif kind == "str_list" and isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, str) and item:
                        found = leaf_span(cls, name, item, local, floor)
                        if found is not None:
                            spans[f"{child}/{index}"] = Span(*found)
                            claimed.add(found)
            elif kind == "model" and sub is not None and isinstance(value, dict):
                inner = visit(sub, value, child, local, floor)
                if local is None and inner is not None:
                    local, first = inner[0], inner
            elif kind == "model_list" and sub is not None and isinstance(value, list):
                previous: tuple[int, int] | None = None
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        started = visit(
                            sub,
                            item,
                            f"{child}/{index}",
                            None,
                            None if previous is None else previous[1],
                        )
                        previous = started if started is not None else previous
        return first

    visit(ResumeTemplate, profile, "", None, None)
    return spans
