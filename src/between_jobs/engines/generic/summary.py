"""The summary stage: two sentences at the top of the resume, or nothing.

A summary is the part of a resume most tempting to inflate, so this stage is the strictest.
Code verifies, and nothing else:

- it is checked against the whole profile (not one entry) like any other text: numbers and
  their units, names, tools, links, role words and seniority words (`provenance.check`);
- a number or a claim of leadership must be one the candidate wrote about themselves (their
  headline or summary notes), not one found anywhere in the profile;
- a number of years, also written in words ("ten years", "a decade"), must be one the candidate
  wrote, and "dozens", "hundreds" and "thousands" must be words the profile uses;
- it is two sentences' worth of plain prose: at most `MAX_SUMMARY_CHARS`, no first person.

Qualitative claims in plain words ("a global leader", "a unicorn startup") are not checked by
any code; the person reading the resume is the only check on those.

When it fails twice the candidate's own summary notes (`summary_bullets`) are used verbatim
instead. If the candidate wrote none, there is no summary rather than one nobody can vouch for.

`summary_mode`: `off` writes none; `on` writes one; `auto` writes one only when the candidate
gave summary notes to write it from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .job import Job, JobRead
from .llm import ModelSession, parse_object
from .prompts import SUMMARY_SYSTEM, candidate_facts, repair_user, summary_user
from .provenance import MAGNITUDE_WORDS, Evidence, Finding, check, evidence_of, tenure_claims
from .sources import Corpus
from .text import FIRST_PERSON, clean_line, model_text

MAX_SUMMARY_CHARS = 420
_WHERE = "the candidate's profile"
_QUOTE_CHARS = 160


@dataclass(slots=True)
class SummaryResult:
    text: str | None = None
    claim_warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    from_model: bool = False


def wanted(mode: str, corpus: Corpus) -> bool:
    if mode == "on":
        return True
    return mode == "auto" and bool(corpus.template.summary_bullets)


def _fallback(corpus: Corpus) -> str | None:
    notes = [clean_line(note) for note in corpus.template.summary_bullets[:2] if clean_line(note)]
    return " ".join(notes) or None


def _problems(
    raw: str, evidence: Evidence, numbers: Evidence, guarded: tuple[str, ...]
) -> tuple[str, list[str], list[str]]:
    """`(text, format problems, fabrication problems)` for a model's answer."""
    answer = parse_object(raw)
    value = answer.get("summary") if answer else None
    text = model_text(value, 1200)
    if not text:
        return "", ["the answer was not a JSON object with a non-empty summary"], []
    problems: list[str] = []
    if len(text) > MAX_SUMMARY_CHARS:
        problems.append(f"the summary is longer than {MAX_SUMMARY_CHARS} characters")
    if FIRST_PERSON.search(text):
        problems.append("the summary uses the first person")
    whole = [*check(text, evidence, guarded), *_magnitude_findings(text, evidence)]
    flagged = {(f.kind, f.token) for f in whole}
    # A number or a claim of leadership in a summary must be one the candidate wrote about
    # themselves, not one found anywhere in the profile: "12 years" is not made true by "12
    # services" in a bullet, nor "managed teams" by one job's "led the migration".
    narrow = [
        f
        for f in [*check(text, numbers), *_spelled_years(text, numbers)]
        if f.kind in {"number", "scope"} and (f.kind, f.token) not in flagged
    ]
    findings = [f.describe(_WHERE) for f in whole]
    findings += [f.describe("the candidate's own summary notes") for f in narrow]
    return text, problems, findings


def _magnitude_findings(text: str, evidence: Evidence) -> list[Finding]:
    """ "A decade", "dozens": a size or a span in words, which the digit check cannot read."""
    words = {word.lower() for word in re.findall(r"[A-Za-z]+", text)}
    return [Finding("number", word) for word in sorted((words & MAGNITUDE_WORDS) - evidence.words)]


def _spelled_years(text: str, numbers: Evidence) -> list[Finding]:
    """Years of experience written in words ("ten years", "twenty-five years", "a decade"):
    the digit check passes them, so each is held to the numbers in the candidate's own notes."""
    return [
        Finding("number", claim.text)
        for claim in tenure_claims(text)
        if claim.spelled and (f"{claim.value:g}", "") not in numbers.numbers
    ]


def _claim(text: str, disposition: str, reasons: list[str]) -> str:
    quoted = text if len(text) <= _QUOTE_CHARS else text[: _QUOTE_CHARS - 3].rstrip() + "..."
    return f'unsupported claim ({disposition}): "{quoted}" -- {"; ".join(reasons)} [summary]'


async def write_summary(
    session: ModelSession, job: Job, read: JobRead, corpus: Corpus, mode: str
) -> SummaryResult:
    if not wanted(mode, corpus):
        return SummaryResult()
    user = summary_user(job, read, candidate_facts(corpus, read, entries=5, bullets=3))
    evidence = evidence_of(corpus.profile_text)
    template = corpus.template
    numbers = evidence_of(template.personal.headline, *template.summary_bullets)
    guarded = read.guarded
    result = SummaryResult()
    raw = await session.ask(system=SUMMARY_SYSTEM, user=user, max_tokens=500)
    text, formatting, findings = _problems(raw, evidence, numbers, guarded)
    if formatting or findings:
        if findings:
            result.claim_warnings.append(_claim(text, "removed before output", findings))
        raw = await session.ask(
            system=SUMMARY_SYSTEM,
            user=repair_user(user, raw, [*formatting, *findings]),
            max_tokens=500,
        )
        text, formatting, findings = _problems(raw, evidence, numbers, guarded)
        if findings:
            result.claim_warnings.append(
                _claim(text, "replaced with your own summary notes", findings)
            )
    if text and not formatting and not findings:
        result.text, result.from_model = text, True
        return result
    result.text = _fallback(corpus)
    result.notes.append(
        "The summary the model wrote could not be verified against your profile, so "
        + ("your own summary notes are used." if result.text else "the resume has no summary.")
    )
    return result
