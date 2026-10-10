"""The resume header, deterministically: which contact details show, in what order, how.

`flat_personal` reads the profile's personal block into the flat shape the header composer
works on (one value per field: the primary e-mail, the primary phone, each link, the place
only if the person chose to show it). `resolve_chips` applies the person's saved layout to it
and returns the chips to draw, in order, as `{field, text, href}` with plain text (never
escaped). The same two functions drive the Studio's live preview and the header of the
generated document, so what the person previews is what the resume has.

Layout (`header_layout`): `chips` is an ordered list of `{field, display_mode, display_text}`
and `separator` is `pipe`, `dot` or `bullet`. A layout with no usable chips (none, or only
entries that are not a `{field: ...}` object) means the default set; a layout that names fields
the person has nothing in is respected, and shows nothing.
`display_mode`: `full` shows the value (a link without its `https://`), `short` a brief form
of it, `label` the name of the field ("LinkedIn") as the link text, `custom` the person's own
`display_text`. `label` only applies where there is a link behind it: a label with nothing to
click would hide the value on paper, so such a field shows its value instead.
"""

from __future__ import annotations

import re
from typing import Any

from between_jobs.api.profile import ResumeTemplate

from .latex import MAX_URL_CHARS, clean_url
from .text import clean_line

DEFAULT_FIELDS = ("phone", "email", "linkedin", "github", "portfolio", "location")
LINK_FIELDS = ("linkedin", "github", "portfolio", "scholar")
LABELS = {
    "phone": "Phone",
    "email": "Email",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "portfolio": "Portfolio",
    "scholar": "Google Scholar",
    "location": "Location",
}
SEPARATORS = ("pipe", "dot", "bullet")
_MODES = ("full", "short", "label", "custom")
_MAX_CHIPS = 8
_DIGITS = re.compile(r"[^0-9+]")
_EXTENSION = re.compile(r"(?i)(?:\bext\.?|\bextension|\bx)(?=\s*\d)|[#;]")
_TRUNK_ZERO = re.compile(r"^(\+\d{1,3})\s*\(0\)")


def flat_personal(template: ResumeTemplate) -> dict[str, str]:
    """One value per header field. Date of birth, nationality, marital status, photo and the
    work-authorization text are not here: the built-in engine never prints them."""
    personal = template.personal
    email = next((e for e in personal.emails if e.primary), None) or (
        personal.emails[0] if personal.emails else None
    )
    phone = next((p for p in personal.phones if p.primary), None) or (
        personal.phones[0] if personal.phones else None
    )
    place = personal.location
    where = ", ".join(part for part in (place.city, place.region, place.country) if part.strip())
    return {
        "name": clean_line(personal.name, 120),
        "headline": clean_line(personal.headline, 160),
        "email": clean_line(email.address, 120) if email else "",
        "phone": clean_line(phone.number, 40) if phone else "",
        "linkedin": _link_value(personal.links.linkedin),
        "github": _link_value(personal.links.github),
        "portfolio": _link_value(personal.links.portfolio),
        "scholar": _link_value(personal.links.scholar),
        "location": clean_line(where, 120) if place.show_on_resume else "",
    }


def _link_value(raw: str) -> str:
    """A profile link as written, or nothing if it is longer than an address can be. It is never
    cut: half an address is a different address, and it would be printed and linked as if it
    were the person's."""
    value = clean_line(raw)
    return value if len(value) <= MAX_URL_CHARS else ""


def _tel_href(value: str) -> str | None:
    """`tel:` and the dialable digits, or None. An extension ("ext. 12", "x12", "#7") is cut
    off first, and the "(0)" some countries write after the country code is dropped, since both
    would change the number dialled."""
    number = _EXTENSION.split(value, maxsplit=1)[0].strip()
    number = _TRUNK_ZERO.sub(r"\1", number)
    digits = _DIGITS.sub("", number)
    return f"tel:{digits}" if len(digits) >= 5 else None


def _display_url(url: str) -> str:
    text = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", url)
    text = re.sub(r"^www\.", "", text)
    return text.rstrip("/")


def _short_url(url: str) -> str:
    shown = _display_url(url).split("?", 1)[0].split("#", 1)[0].rstrip("/")
    last = shown.rsplit("/", 1)[-1]
    return last if "/" in shown and last else shown


def _chip(field: str, raw: str, mode: str, custom: str) -> dict[str, Any] | None:
    value = clean_line(raw)
    if not value:
        return None
    href: str | None = None
    full = value
    short = value
    if field in LINK_FIELDS:
        href = clean_url(value)
        if href is not None:
            full, short = _display_url(href), _short_url(href)
    elif field == "email":
        href = clean_url(f"mailto:{value}") if "@" in value else None
    elif field == "phone":
        href = _tel_href(value)
    elif field == "location":
        short = value.split(",")[0].strip() or value
    text = {"full": full, "short": short}.get(mode, full)
    if mode == "label" and href is not None:
        text = LABELS[field]
    if mode == "custom" and custom:
        text = custom
    return {"field": field, "text": text, "href": href}


def resolve_chips(
    personal: dict[str, Any], header_layout: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The chips to draw for `personal` under `header_layout`, in display order. Unknown
    fields, repeated fields and fields with nothing to show are left out."""
    layout = header_layout if isinstance(header_layout, dict) else {}
    configured = layout.get("chips")
    wanted: list[tuple[str, str, str]] = []
    if isinstance(configured, list) and configured:
        for item in configured[: _MAX_CHIPS * 2]:
            if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                continue
            mode = item.get("display_mode")
            text = item.get("display_text")
            wanted.append(
                (
                    item["field"],
                    mode if isinstance(mode, str) and mode in _MODES else "full",
                    clean_line(text, 60) if isinstance(text, str) else "",
                )
            )
    if not wanted:
        wanted = [(field, "full", "") for field in DEFAULT_FIELDS]
    chips: list[dict[str, Any]] = []
    seen: set[str] = set()
    for field, mode, custom in wanted:
        if field in seen or field not in LABELS:
            continue
        seen.add(field)
        raw = personal.get(field)
        chip = _chip(field, raw, mode, custom) if isinstance(raw, str) else None
        if chip is not None:
            chips.append(chip)
        if len(chips) >= _MAX_CHIPS:
            break
    return chips


def separator_of(header_layout: dict[str, Any] | None) -> str:
    value = header_layout.get("separator") if isinstance(header_layout, dict) else None
    return value if isinstance(value, str) and value in SEPARATORS else "pipe"
