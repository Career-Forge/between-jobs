"""The experience and projects stage: the model words bullets, code decides everything else.

One model call is shown the entries the plan kept (their pointers, their real bullets numbered,
their tools and metrics) and answers with, per entry, the bullets it wants shown: for each, the
numbers of the source bullets it was built from and its new text. Nothing else comes back from
the model. Titles, companies, dates and places are copied from the profile later; how many
entries and bullets there are is the plan's; and what a bullet may say is decided by
`provenance.check` against the text of the source bullets it cites.

When an answer breaks a rule, the failure policy is fixed and bounded:

1. The problems are listed and the model is asked ONCE more, with its previous answer.
2. Whatever still fails after that is replaced, bullet by bullet, with the candidate's own
   bullet (the one it was built from, else the next most relevant unused one). The document is
   always produced.
3. Every bullet that failed a check is reported as a claim warning, in the wording the export
   checklist already reads ("unsupported claim (...)"), with the text that was rejected.

An answer that is not a usable JSON object at all costs the same one repair and then falls back
to the candidate's own bullets for the whole section, with a note saying so.

Two things are put right without a repair, because they are structure and not claims: a list
marker in front of a bullet ("- ", a bullet character, "1.") is removed, and the candidate's own
bullet, unchanged, is never held to the length or first-person rules it was not written under.
Emphasis marks (`*`, `_`) are not removed: they are markup the source does not have, and
`provenance.check` reports them like any other new character.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .job import Job, JobRead
from .llm import ModelSession, parse_object
from .plan import OfferedEntry, Plan, rank_bullets
from .prompts import BULLETS_SYSTEM, bullets_user, repair_user
from .provenance import Finding, check, evidence_of
from .text import FIRST_PERSON, clean_line, model_text, strip_list_marker

MAX_REWRITE_CHARS = 260
_MODEL_TEXT_CHARS = 1200
"""A model's text is clipped to this before it is checked, so a runaway answer cannot make the
check slow; anything over `MAX_REWRITE_CHARS` is a problem long before this."""
_BODY_MAX_TOKENS = 3500
_WHERE = "the source bullet(s)"
_QUOTE_CHARS = 140
MAX_CLAIM_WARNINGS = 30


@dataclass(frozen=True, slots=True)
class Bullet:
    text: str
    sources: tuple[int, ...]
    """Indices into `Source.bullets` of the bullets this one was written from."""
    original: bool
    """The candidate's own wording, unchanged."""


@dataclass(slots=True)
class _Slot:
    text: str
    sources: tuple[int, ...]
    problems: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and not self.findings


@dataclass(slots=True)
class _Parsed:
    entries: dict[str, list[_Slot]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    structural: list[str] = field(default_factory=list)
    usable: bool = False

    @property
    def needs_repair(self) -> bool:
        return (
            not self.usable
            or bool(self.structural)
            or any(not slot.ok for slots in self.entries.values() for slot in slots)
        )

    def problem_lines(self) -> list[str]:
        lines = list(self.structural)
        for pointer, slots in self.entries.items():
            for position, slot in enumerate(slots, start=1):
                reasons = [*slot.problems, *(f.describe(_WHERE) for f in slot.findings)]
                if reasons:
                    lines.append(f"{pointer} bullet {position}: " + "; ".join(reasons))
        return lines


@dataclass(slots=True)
class BodyResult:
    bullets: dict[str, list[Bullet]]
    """For every kept experience entry and every shown project, the bullets to print."""
    project_order: list[str]
    claim_warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _source_numbers(value: Any, allowed: tuple[int, ...]) -> tuple[int, ...] | None:
    """The source bullet numbers a model cited, if they are one or two of the numbers it was
    shown for this entry, without repeats."""
    raw = value if isinstance(value, list) else [value]
    numbers: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item not in allowed:
            return None
        if item not in numbers:
            numbers.append(item)
    return tuple(numbers) if 1 <= len(numbers) <= 2 else None


def _parse(raw: str, offered: dict[str, OfferedEntry], read: JobRead) -> _Parsed:
    answer = parse_object(raw)
    entries = answer.get("entries") if answer else None
    if answer is None or not isinstance(entries, list):
        return _Parsed(structural=["the answer was not a JSON object with an entries list"])
    parsed = _Parsed(usable=True)
    guarded = read.guarded
    for item in entries:
        pointer = item.get("pointer") if isinstance(item, dict) else None
        if isinstance(pointer, str):
            pointer = pointer.strip()
            if pointer not in offered and f"/{pointer}" in offered:
                pointer = f"/{pointer}"  # "experience/0" for "/experience/0"
        if not isinstance(pointer, str) or pointer not in offered:
            shown = clean_line(str(pointer), 40) if pointer is not None else "(none)"
            parsed.structural.append(f'the pointer "{shown}" is not one of the entries given')
            continue
        if pointer in parsed.entries:
            continue  # a pointer used twice: the first use stands
        source = offered[pointer].source
        slots: list[_Slot] = []
        bullets = item.get("bullets")
        for bullet in bullets if isinstance(bullets, list) else []:
            if not isinstance(bullet, dict):
                continue
            # a list marker the model put in front ("- ", a bullet character) is structure, and
            # structure is code's: it is dropped, not argued about
            text = strip_list_marker(model_text(bullet.get("text"), _MODEL_TEXT_CHARS))
            numbers = _source_numbers(bullet.get("from"), offered[pointer].shown)
            slot = _Slot(text=text, sources=numbers or ())
            # the candidate's own bullet, unchanged, is theirs to have written
            own = numbers is not None and text in [source.bullets[i] for i in numbers]
            if numbers is None:
                slot.problems.append('"from" must list one or two of the entry\'s bullet numbers')
            if not text:
                slot.problems.append("the text is empty")
            elif len(text) > MAX_REWRITE_CHARS and not own:
                slot.problems.append(f"the text is longer than {MAX_REWRITE_CHARS} characters")
            if text and not own and FIRST_PERSON.search(text):
                slot.problems.append("the text uses the first person")
            if text and numbers is not None:
                evidence = evidence_of(*source.support_for(list(numbers)))
                slot.findings = check(text, evidence, guarded)
            slots.append(slot)
        parsed.entries[pointer] = slots
        parsed.order.append(pointer)
    return parsed


def _quote(text: str) -> str:
    return text if len(text) <= _QUOTE_CHARS else text[: _QUOTE_CHARS - 3].rstrip() + "..."


def _claims(parsed: _Parsed, disposition: str) -> list[str]:
    warnings: list[str] = []
    for pointer, slots in parsed.entries.items():
        for slot in slots:
            if slot.findings:
                reasons = "; ".join(f.describe(_WHERE) for f in slot.findings)
                warnings.append(
                    f'unsupported claim ({disposition}): "{_quote(slot.text)}" -- {reasons} '
                    f"[{pointer}]"
                )
    return warnings


def _settle_entry(offer: OfferedEntry, slots: list[_Slot], read: JobRead) -> list[Bullet]:
    """The bullets an entry shows: the model's good ones, the candidate's own where the model's
    failed a check, no source bullet twice, within the entry's maximum, topped up to its minimum
    (the pin, or the floor) from the most relevant bullets not yet used."""
    source = offer.source
    ranking = rank_bullets(source, read)
    used: set[int] = set()
    kept: list[Bullet] = []

    def original(prefer: tuple[int, ...]) -> Bullet | None:
        for index in (*prefer, *ranking):
            if index not in used:
                used.add(index)
                return Bullet(source.bullets[index], (index,), True)
        return None

    for slot in slots:
        if slot.ok:
            if slot.sources and all(index in used for index in slot.sources):
                continue  # says what an earlier bullet already says
            used.update(slot.sources)
            kept.append(Bullet(slot.text, slot.sources, original=slot.text in source.bullets))
        else:
            replacement = original(slot.sources)
            if replacement is not None:
                kept.append(replacement)
    kept = kept[: offer.maximum]
    used = {index for bullet in kept for index in bullet.sources}
    while len(kept) < offer.minimum:
        replacement = original(())
        if replacement is None:
            break
        kept.append(replacement)
    return kept


async def write_body(session: ModelSession, job: Job, read: JobRead, plan: Plan) -> BodyResult:
    offered = {offer.source.pointer: offer for offer in (*plan.experience, *plan.projects)}
    if not any(offer.source.bullets for offer in offered.values()):
        # Nothing to reword: no call is made, and the entries (if any) are shown as they are.
        return BodyResult(
            bullets={offer.source.pointer: [] for offer in plan.experience},
            project_order=[offer.source.pointer for offer in plan.projects[: plan.project_limit]],
        )
    user = bullets_user(job, read, [*plan.experience, *plan.projects], plan.project_limit)
    raw = await session.ask(system=BULLETS_SYSTEM, user=user, max_tokens=_BODY_MAX_TOKENS)
    parsed = _parse(raw, offered, read)
    claim_warnings: list[str] = []
    notes: list[str] = []
    final = parsed
    if parsed.needs_repair:
        claim_warnings += _claims(parsed, "removed before output")
        raw = await session.ask(
            system=BULLETS_SYSTEM,
            user=repair_user(user, raw, parsed.problem_lines()),
            max_tokens=_BODY_MAX_TOKENS,
        )
        final = _parse(raw, offered, read)
        if not final.usable:
            notes.append(
                "The model's answer for your experience and projects could not be used, so "
                "your own bullets are shown, chosen by relevance to the job."
            )
        claim_warnings += _claims(final, "replaced with your original wording")

    bullets: dict[str, list[Bullet]] = {}
    for offer in plan.experience:
        slots = final.entries.get(offer.source.pointer, [])
        bullets[offer.source.pointer] = _settle_entry(offer, slots, read)

    if final.usable:
        chosen = [p for p in final.order if offered[p].source.kind == "project"]
    else:
        chosen = [offer.source.pointer for offer in plan.projects]
    project_order = chosen[: plan.project_limit]
    for offer in plan.projects:
        pointer = offer.source.pointer
        if offer.source.pinned and pointer not in project_order:
            project_order.append(pointer)  # a pinned project is never left out
    for pointer in project_order:
        bullets[pointer] = _settle_entry(offered[pointer], final.entries.get(pointer, []), read)
    return BodyResult(
        bullets=bullets,
        project_order=project_order,
        claim_warnings=claim_warnings[:MAX_CLAIM_WARNINGS],
        notes=notes,
    )
