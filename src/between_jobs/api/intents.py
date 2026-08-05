"""Deterministic intent classification for the Telegram bridge (Sprint 2.4).

Regex, not an LLM call -- matches the project's own L1/L2/L3 philosophy
("no agent where a regex does"; master plan §3.3) and there are only a
couple of intents so far. Revisit once the intent vocabulary grows past
what regex can maintain -- that's a real future migration, not something
to build ahead of need.

Both real intents are colon-delimited in a single message (e.g. "my
resume: <text>") rather than a multi-turn "ok, now send the text"
conversation -- there's no conversation-state machinery yet, and forcing
one into existence for this is exactly the kind of premature complexity
the L1/L2/L3 law warns against.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import NamedTuple


class Intent(StrEnum):
    SET_UP_RESUME = "set_up_resume"
    CHECK_RESUME = "check_resume"
    UNKNOWN = "unknown"


class Classification(NamedTuple):
    intent: Intent
    resume_text: str | None = None


_SET_UP_RESUME_RE = re.compile(
    r"^(?:set up my resume|my resume|resume)\s*:\s*(.+)$", re.IGNORECASE | re.DOTALL
)
_CHECK_RESUME_RE = re.compile(
    r"^(?:check my resume|do you have my resume|show my resume)\??$", re.IGNORECASE
)


def classify(text: str) -> Classification:
    text = text.strip()

    if m := _SET_UP_RESUME_RE.match(text):
        resume_text = m.group(1).strip()
        if resume_text:
            return Classification(Intent.SET_UP_RESUME, resume_text)
        return Classification(Intent.UNKNOWN)

    if _CHECK_RESUME_RE.match(text):
        return Classification(Intent.CHECK_RESUME)

    return Classification(Intent.UNKNOWN)
