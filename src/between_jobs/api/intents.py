"""Deterministic message classification for the Telegram bridge.

Regex, not an LLM call -- matches the project's own L1/L2/L3 philosophy
("no agent where a regex does"; master plan §3.3).

Three layers, matching n8n's own separation of concerns:
  1. `looks_like_json_payload()` -- a JSON-shape gate, checked BEFORE
     intent classification (see telegram_webhook.py). A long or
     brace-leading message is almost certainly a resume-JSON paste, not a
     command -- classifying it first means real JSON never has to also
     match a command regex to be recognized.
  2. `parse_link_code()` / `is_unlink_command()` (Sprint 2.8e),
     `looks_like_job_paste()` / `parse_job_paste()` (Sprint 3.4a) -- also
     checked before `classify()`, for the same reason `classify()`'s own
     `Intent` enum has no payload: none of these can carry the data they
     extract (a link code; a job's title/company/description), so they
     stay outside its contract entirely instead of forcing `Intent` to
     grow variants with data attached.
  3. `classify()` -- everything else: short command-like messages only.

Sprint 2.5 drops the old colon-delimited "my resume: <text>" one-shot
format (a deliberate Sprint 2.4 stub, never the real contract) for the
actual flow: a template message, then a SEPARATE message carrying the
JSON, validated deterministically and confirmed via inline buttons before
anything becomes canonical (profile.py / profile_store.py /
telegram_webhook.py).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypedDict

_JSON_PAYLOAD_LENGTH_THRESHOLD = 800


class Intent(StrEnum):
    SETUP_HELP = "setup_help"
    CHECK_RESUME = "check_resume"
    TRACK_JOB_HELP = "track_job_help"
    LIST_APPLICATIONS = "list_applications"
    UNKNOWN = "unknown"


_SETUP_HELP_RE = re.compile(
    r"^(?:/setup|set\s*up(?:\s+my)?\s+resume|my\s+resume|resume)\s*\??$", re.IGNORECASE
)
_CHECK_RESUME_RE = re.compile(
    r"^(?:check my resume|do you have my resume|show my resume)\??$", re.IGNORECASE
)
_TRACK_JOB_HELP_RE = re.compile(
    r"^(?:/track|track (?:a |this )?job|add (?:a )?job|new application|apply(?: to)?"
    r"(?: a job)?)\??$",
    re.IGNORECASE,
)
_LIST_APPLICATIONS_RE = re.compile(
    r"^(?:/list|list(?:\s+my)?(?:\s+applications|\s+jobs)?|my\s+applications)\??$", re.IGNORECASE
)
_LINK_RE = re.compile(r"^/link\s+(\S+)$", re.IGNORECASE)
_UNLINK_RE = re.compile(r"^/unlink$", re.IGNORECASE)

_JOB_PASTE_TRIGGER_RE = re.compile(r"^title\s*:", re.IGNORECASE)
_JOB_FIELD_RE = re.compile(r"^(title|company|location|url)\s*:\s*(.*)$", re.IGNORECASE)

_APPLY_REFERENCE_RE = re.compile(r"^(?:apply(?:\s+to)?|generate)\s*#?(\d+)\??$", re.IGNORECASE)


def looks_like_json_payload(text: str) -> bool:
    """A message this long, or starting with `{`, is almost certainly a
    resume-JSON paste rather than a short command -- checked before
    `classify()` so real JSON is never misrouted."""
    stripped = text.strip()
    return len(stripped) > _JSON_PAYLOAD_LENGTH_THRESHOLD or stripped.startswith("{")


def parse_link_code(text: str) -> str | None:
    """Returns the code from `/link CODE`, or None if the message isn't
    that shape. Case-preserved as typed -- `link_codes_store` hashes
    exactly what's submitted, and this project's code alphabet is
    uppercase-only by convention, not by normalization here."""
    match = _LINK_RE.match(text.strip())
    return match.group(1) if match else None


def is_unlink_command(text: str) -> bool:
    return bool(_UNLINK_RE.match(text.strip()))


class JobPasteFields(TypedDict):
    title: str
    company_name: str
    location_text: str | None
    canonical_url: str | None
    description_text: str


def looks_like_job_paste(text: str) -> bool:
    """A message starting with a `Title:` line is a structured job paste
    -- checked before `classify()`, same reasoning as
    `looks_like_json_payload`: a long, shaped message shouldn't also have
    to match a short command regex to be recognized."""
    return bool(_JOB_PASTE_TRIGGER_RE.match(text.strip()))


def parse_job_paste(text: str) -> JobPasteFields | list[str]:
    """Parses a `Title:`/`Company:`/`Location:`/`URL:` header block
    followed by the job description -- the Telegram equivalent of the
    web's manual-paste form (Sprint 2.6f's `PasteJobForm`), not a URL
    scraper: no Firecrawl (or any other) scraping exists anywhere in this
    platform yet, so a pasted URL is stored for dedup/display only, the
    same as the web form's own `canonical_url` field.

    Returns a list of named validation errors instead of guessing at
    missing required fields, matching `profile.py.import_profile`'s own
    contract."""
    lines = text.strip().splitlines()
    fields: dict[str, str] = {}
    body_start = len(lines)
    for i, line in enumerate(lines):
        match = _JOB_FIELD_RE.match(line)
        if match is None:
            body_start = i
            break
        fields[match.group(1).lower()] = match.group(2).strip()

    description_text = "\n".join(lines[body_start:]).strip()

    errors = []
    if not fields.get("title"):
        errors.append("Missing required field: Title")
    if not fields.get("company"):
        errors.append("Missing required field: Company")
    if not description_text:
        errors.append("Missing the job description text after the header fields")
    if errors:
        return errors

    return JobPasteFields(
        title=fields["title"],
        company_name=fields["company"],
        location_text=fields.get("location") or None,
        canonical_url=fields.get("url") or None,
        description_text=description_text,
    )


def parse_apply_reference(text: str) -> int | None:
    """Returns the 1-based index from "apply to #3" / "apply #3" /
    "generate #3", or None if the message isn't that shape. Checked
    before `classify()` for the usual reason: the index is data
    `Intent`'s payload-free enum can't carry. Resolving the index against
    the user's actual working set (`working_sets_store.resolve_reference`)
    happens entirely on the caller's side -- this function only parses
    the reference's shape, matching `parse_link_code`'s own split between
    "does this look like the command" and "is the extracted value valid"."""
    match = _APPLY_REFERENCE_RE.match(text.strip())
    return int(match.group(1)) if match else None


def classify(text: str) -> Intent:
    text = text.strip()

    if _SETUP_HELP_RE.match(text):
        return Intent.SETUP_HELP

    if _CHECK_RESUME_RE.match(text):
        return Intent.CHECK_RESUME

    if _TRACK_JOB_HELP_RE.match(text):
        return Intent.TRACK_JOB_HELP

    if _LIST_APPLICATIONS_RE.match(text):
        return Intent.LIST_APPLICATIONS

    return Intent.UNKNOWN
