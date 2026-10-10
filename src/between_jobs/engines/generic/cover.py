"""The cover letter stage: a few paragraphs of the candidate's voice, sentence by sentence checked.

The letter's structure is code's (the greeting, the sign-off, the candidate's name and contact
line, the date, the subject line); the model writes only the paragraphs. Each sentence is held
to the same fact check as a bullet, against the whole profile, and to more rules a letter needs:

- the job's company, title and location may be NAMED, never leaned on. They come from a
  posting a stranger wrote, so their words are let through the name rule (a letter can say
  "Globex" and "Staff Data Engineer") and a verbatim mention of any of the three is not read at
  all, but a number, tool or role in a posting's title ("Data Engineer (10 years of Kubernetes,
  led a team of 40)") never makes a claim about the candidate true;
- a stated number of years ("12 years of experience", "a decade") must be one the candidate
  wrote, or no more than the dated experience adds up to (a bare digit found anywhere in a
  profile, such as "12 services", is not a tenure);
- no placeholders: anything in square brackets, "your name", "insert ... here";
- nothing about work authorization, visas, sponsorship, citizenship, relocation, salary or when
  the candidate can start. The model is never given the profile's work-authorization text, so
  any sentence about it is by construction a claim nobody supports. The check is a net of
  keyword and phrase patterns over the prompt's own rule, and a sentence worded in a way no
  pattern anticipates can get past it. (A person answering a screening question about it does so
  in their own words, through the application form, not in a generated letter.)

What code does NOT check: anything the letter says about the company in plain words ("the
industry leader in streaming", "serves large enterprise customers"). The only company facts a
letter has are its name, the job title and the location, and the prompt forbids inventing
more, but nothing here can tell a true sentence about a company from a false one. The person
reading the letter checks those against the posting.

Failure policy, bounded like the resume's: one repair call listing the problems, then every
sentence that still fails is removed. If too little is left to be a letter, or what is left is
longer than one page holds, a short plain letter built from the candidate's own most relevant
bullets is used and the note says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .job import Job, JobRead
from .latex import fold_to_ascii
from .llm import ModelSession, parse_object
from .plan import bullet_relevance, career_years
from .prompts import COVER_SYSTEM, candidate_facts, cover_user, repair_user
from .provenance import Evidence, Finding, check, evidence_of, tenure_claims
from .sources import Corpus
from .text import model_text, word_count

MIN_WORDS = 70
SHORT_OK_WORDS = 25
MAX_WORDS = 420
_MAX_PARAGRAPHS = 6
_QUOTE_CHARS = 140
_WHERE = "the candidate's profile or the job"

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_PLACEHOLDER = re.compile(
    r"[\[\]]|\{[^}]*\}|<[^>]*>|\b(?:your name|company name|tbd)\b"
    r"|\binsert\b[^.!?]*\bhere\b|\binsert\s+(?:a|an|your|the|specific|company|name)\b",
    re.IGNORECASE,
)

# What a letter never says. Each topic is a list of alternatives, kept apart so a test can
# take them away one at a time and show that every one is the only thing stopping some sentence.
_AUTHORIZATION_ALTERNATIVES = (
    r"authori[sz](?:ed|ation)",
    r"sponsor(?:ship|ed)?\b",
    r"\bvisas?\b",
    r"citizen",
    r"green[\s-]?card",
    r"permanent[\s-]+resident",
    r"work[\s-]+permit",
    r"work[\s-]+rights?",
    r"work[\s-]+(?:status|eligibility)",
    r"right\s+to\s+(?:work|live|remain|stay)",
    r"(?:eligible|eligibility|entitled|permitted|allowed|able|cleared)\s+(?:to|for)\s+"
    r"(?:work|employ|live)",
    r"legally\s+(?:authori|eligible|able|permitted|allowed)",
    r"employment\s+(?:eligibility|status|authori|visa|rights?)",
    r"immigration",
    r"\b(?:i\s*am|i'm|as)\s+an?\s+(?:\w+\s+){0,2}(?:national|resident|permanent)\b",
    r"(?:require|need)s?\s+(?:any\s+)?(?:employer|company)[\s-]*(?:backed\s+|provided\s+)?"
    r"(?:support|help|filing|petition)",
    r"(?:support|help|filing|petition)\s+to\s+work",
)
_STATUS_CODES = r"\b(?:H-?1B|OPT|CPT|L-?1|TN|EAD)\b"
_AVAILABILITY_ALTERNATIVES = (
    r"relocat",
    r"salar(?:y|ies)",
    r"compensation\s+expectations?",
    r"notice\s+period",
    r"(?:start|joining)\s+date",
    r"immediate\s+joiner",
    r"immediately\s+available",
    r"availab\w*\s+(?:to\s+(?:start|join|begin)|immediately|from|as\s+of"
    r"|in\s+\w+\s+(?:days?|weeks?|months?)|on\s+\w+)",
    r"\b(?:start|begin|join|commenc|onboard)\w*\s+(?:\w+\s+){0,3}?"
    r"(?:immediately|right\s+away|asap|(?:in|within|after)\s+(?:\w+\s+){0,2}(?:days?|weeks?|months?)"
    r"|next\s+(?:week|month)|on\s+(?:the\s+)?\w+\s+\d|as\s+soon\s+as)",
    r"(?:two|three|four|\d+)\s+(?:weeks?|months?)'?\s+(?:notice|to\s+(?:start|join))",
)


def _any_of(alternatives: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(alternatives), re.IGNORECASE)


_POLICY: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "work authorization",
        (_any_of(_AUTHORIZATION_ALTERNATIVES), re.compile(_STATUS_CODES)),
    ),
    ("relocation or availability", (_any_of(_AVAILABILITY_ALTERNATIVES),)),
)


@dataclass(slots=True)
class CoverContent:
    opening: list[str]
    """Paragraphs before the highlights (for a model-written letter, all of them)."""
    highlights: list[str] = field(default_factory=list)
    """Bullets of the candidate's own, verbatim: only in the plain fallback letter."""
    closing: list[str] = field(default_factory=list)

    def words(self) -> int:
        return sum(word_count(text) for text in [*self.opening, *self.highlights, *self.closing])


@dataclass(slots=True)
class CoverResult:
    content: CoverContent
    claim_warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fallback: bool = False


@dataclass(slots=True)
class _Sentence:
    text: str
    findings: list[Finding]


@dataclass(slots=True)
class _Parsed:
    paragraphs: list[list[_Sentence]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.paragraphs)

    def flagged(self) -> list[tuple[str, list[Finding]]]:
        return [(s.text, s.findings) for p in self.paragraphs for s in p if s.findings]

    def words(self, *, clean_only: bool) -> int:
        return sum(
            word_count(s.text)
            for p in self.paragraphs
            for s in p
            if not (clean_only and s.findings)
        )


@dataclass(frozen=True, slots=True)
class _Rules:
    """What a sentence is held to: the evidence, the posting's guarded terms, the mentions of
    the job's own company, title and location to read past, and what the dated experience adds
    up to."""

    evidence: Evidence
    guarded: tuple[str, ...]
    job_mentions: re.Pattern[str] | None
    max_years: int
    own_years: frozenset[float]
    """The lengths of time the candidate's own text states."""


def _policy_findings(sentence: str) -> list[Finding]:
    return [
        Finding("policy", label)
        for label, patterns in _POLICY
        if any(pattern.search(sentence) for pattern in patterns)
    ]


def _placeholder_findings(sentence: str) -> list[Finding]:
    return [Finding("placeholder", "placeholder")] if _PLACEHOLDER.search(sentence) else []


def _tenure_findings(sentence: str, rules: _Rules) -> list[Finding]:
    return [
        Finding("number", claim.text)
        for claim in tenure_claims(sentence)
        if claim.value > rules.max_years and claim.value not in rules.own_years
    ]


def _sentence_findings(sentence: str, rules: _Rules) -> list[Finding]:
    # A verbatim mention of the posting's own company, title or place is a name, not a claim
    # about the candidate, so it is not read; everything else in the sentence is.
    checked = sentence
    if rules.job_mentions is not None:
        checked = rules.job_mentions.sub(" ", fold_to_ascii(sentence))
    return [
        *check(checked, rules.evidence, rules.guarded),
        *_tenure_findings(checked, rules),
        *_policy_findings(sentence),
        *_placeholder_findings(sentence),
    ]


def _parse(raw: str, rules: _Rules) -> _Parsed:
    answer = parse_object(raw)
    paragraphs = answer.get("paragraphs") if answer else None
    parsed = _Parsed()
    if not isinstance(paragraphs, list):
        parsed.problems.append("the answer was not a JSON object with a paragraphs list")
        return parsed
    for paragraph in paragraphs[:_MAX_PARAGRAPHS]:
        text = model_text(paragraph, 3000)
        if not text:
            continue
        sentences = [part for part in _SENTENCE_BREAK.split(text) if part]
        parsed.paragraphs.append(
            [_Sentence(sentence, _sentence_findings(sentence, rules)) for sentence in sentences]
        )
    if not parsed.paragraphs:
        parsed.problems.append("the letter has no paragraphs")
    total = parsed.words(clean_only=False)
    if parsed.paragraphs and total > MAX_WORDS:
        parsed.problems.append(f"the letter is {total} words; it must be under {MAX_WORDS}")
    return parsed


def _problem_lines(parsed: _Parsed) -> list[str]:
    lines = list(dict.fromkeys(parsed.problems))
    for number, paragraph in enumerate(parsed.paragraphs, start=1):
        for position, sentence in enumerate(paragraph, start=1):
            if sentence.findings:
                reasons = "; ".join(f.describe(_WHERE) for f in sentence.findings)
                lines.append(f"paragraph {number}, sentence {position}: {reasons}")
    return lines


def _claims(parsed: _Parsed, disposition: str) -> list[str]:
    warnings: list[str] = []
    for text, findings in parsed.flagged():
        quoted = text if len(text) <= _QUOTE_CHARS else text[: _QUOTE_CHARS - 3].rstrip() + "..."
        reasons = "; ".join(f.describe(_WHERE) for f in findings)
        warnings.append(
            f'unsupported claim ({disposition}): "{quoted}" -- {reasons} [cover letter]'
        )
    return warnings


def _plain_letter(job: Job, read: JobRead, corpus: Corpus) -> CoverContent:
    role = f"the {job.title} position" if job.title else "this position"
    company = f" at {job.company}" if job.company else ""
    candidates: list[tuple[float, int, str]] = []
    for order, source in enumerate((*corpus.experience, *corpus.projects)):
        for bullet in source.bullets:
            candidates.append((-bullet_relevance(bullet, read), order, bullet))
    best = [text for _score, _order, text in sorted(candidates)[:3]]
    opening = [f"I am writing to apply for {role}{company}."]
    if best:
        opening.append("Here is some of my work that relates most closely to it:")
    return CoverContent(
        opening=opening,
        highlights=best,
        closing=[
            "I would welcome the chance to talk about how this experience could help the team."
        ],
    )


def _folded(value: str) -> str:
    return fold_to_ascii(value).strip()


def _rules_for(job: Job, read: JobRead, corpus: Corpus, now: datetime) -> _Rules:
    # The job's words may be NAMED (they are let through the name rule, so "Globex" and "Staff
    # Data Engineer" can be written) but never lean on: its numbers, tools, roles and links are
    # left out of the evidence, since a stranger wrote them.
    evidence = evidence_of(corpus.profile_text)
    named = evidence_of(job.company, job.title, job.location)
    evidence = replace(
        evidence, words=evidence.words | named.words, stems=evidence.stems | named.stems
    )
    spelled = (_folded(value) for value in (job.title, job.company, job.location))
    forms = sorted({form for form in spelled if len(form) >= 2}, key=len, reverse=True)
    mentions = re.compile("|".join(re.escape(form) for form in forms), re.IGNORECASE)
    return _Rules(
        evidence=evidence,
        guarded=read.guarded,
        job_mentions=mentions if forms else None,
        max_years=int(career_years(corpus, now)),
        own_years=frozenset(claim.value for claim in tenure_claims(corpus.profile_text)),
    )


async def write_cover_letter(
    session: ModelSession,
    job: Job,
    read: JobRead,
    corpus: Corpus,
    now: datetime | None = None,
) -> CoverResult:
    user = cover_user(job, read, candidate_facts(corpus, read, entries=4, bullets=3))
    rules = _rules_for(job, read, corpus, now or datetime.now(UTC))
    result = CoverResult(content=CoverContent(opening=[]))

    raw = await session.ask(system=COVER_SYSTEM, user=user, max_tokens=1400)
    parsed = _parse(raw, rules)
    if _problem_lines(parsed):
        result.claim_warnings += _claims(parsed, "removed before output")
        raw = await session.ask(
            system=COVER_SYSTEM,
            user=repair_user(user, raw, _problem_lines(parsed)),
            max_tokens=1400,
        )
        parsed = _parse(raw, rules)
        result.claim_warnings += _claims(parsed, "removed")

    kept_words = parsed.words(clean_only=True)
    removed = parsed.words(clean_only=False) - kept_words
    too_long = kept_words > MAX_WORDS
    # a letter is kept if enough of it survives; one that lost nothing is kept even if short;
    # one still longer than a page holds after the repair is not kept at all
    if (
        parsed.usable
        and not too_long
        and (kept_words >= MIN_WORDS or (removed == 0 and kept_words >= SHORT_OK_WORDS))
    ):
        result.content = CoverContent(
            opening=[
                " ".join(s.text for s in paragraph if not s.findings)
                for paragraph in parsed.paragraphs
                if any(not s.findings for s in paragraph)
            ]
        )
        removed_sentences = sum(1 for p in parsed.paragraphs for s in p if s.findings)
        if removed_sentences:
            result.notes.append(
                f"{removed_sentences} sentence(s) of the cover letter were removed because they "
                "said something your profile does not support."
            )
        return result

    result.fallback = True
    result.content = _plain_letter(job, read, corpus)
    if too_long:
        result.notes.append(
            f"The cover letter the model wrote was longer than {MAX_WORDS} words, which does not "
            "fit one page, even after a second try, so a short plain letter built from your own "
            "bullets is used. Rewrite it in your own words before you send it."
        )
    else:
        result.notes.append(
            "The cover letter the model wrote could not be verified against your profile, so a "
            "short plain letter built from your own bullets is used. Rewrite it in your own "
            "words before you send it."
        )
    return result
