"""The order things happen in when a resume (and a cover letter) is written, and its bounds.

1. The profile is validated and indexed (`sources`); the posting is cleaned (`job`).
2. One model call reads what the posting asks for. If that fails to produce a usable answer the
   rest goes ahead without it, matching on the candidate's own skills the posting mentions.
3. Code decides the shape (`plan`): how many pages, which jobs are kept, how many bullets each
   may show.
4. The three writing stages -- experience and projects (`body`), the summary (`summary`) and,
   when asked for, the cover letter (`cover`) -- run side by side, each with its own checks and
   at most one repair call.
5. Code assembles the resume to the page budget (`assemble`) and prints both documents
   (`templates`).

Bounds. At most `llm.MAX_CALLS_PER_APPLY` (7) model calls: four to write, three to repair.
The whole run stops after `APPLY_TIMEOUT_SECONDS`, the same limit the separate engine's client
gives it. Prompt sizes are capped where the prompts are built. The only thing logged is a count
line at the end (calls, flagged claims, pages): never a prompt, an answer, or a word of the
profile or the posting.

What this engine does not do is said in the result, not hidden: it does not score the resume
(`ats_attempts` is empty), does not estimate fit (`fit` is None), has no gate that can decline
(`gate` is None), and says in the shape report's warnings when a setting it ignores was asked
for.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from between_jobs.api.engine_contract import Step0Result
from between_jobs.api.errors import ApiError
from between_jobs.api.forge_engines_client import ForgeApplyResult

from .assemble import assemble
from .body import BodyResult, write_body
from .cover import CoverResult, write_cover_letter
from .header import flat_personal, resolve_chips, separator_of
from .job import Job, JobRead, combine_terms, prepare_job
from .latex import LatexText
from .llm import ModelSession
from .plan import Density, build_plan, choose_shape
from .sources import Corpus, build_corpus
from .step0 import read_job
from .summary import SummaryResult, write_summary
from .templates import render_cover_letter, render_resume

logger = logging.getLogger(__name__)

APPLY_TIMEOUT_SECONDS = 180.0
"""The separate engine's client waits this long for a whole run; the built-in engine keeps to
the same bound."""

_US_STYLE_LOCALES = frozenset({"", "US", "CA"})


def ignored_settings(
    *,
    locale: str | None,
    show_nationality: bool,
    dealbreaker_assertions: list[str] | None,
    force_generate: bool,
    bullet_lead_in: str | None,
) -> list[str]:
    """One plain sentence for each setting this engine received and did not apply."""
    notes: list[str] = []
    if (locale or "").upper() not in _US_STYLE_LOCALES:
        notes.append(
            f"The built-in engine writes one US-style resume; the regional format for "
            f"{locale} was not applied."
        )
    if show_nationality:
        notes.append(
            "The built-in engine never prints nationality, so that setting was not applied."
        )
    if dealbreaker_assertions:
        notes.append(
            "The built-in engine does not check a job's dealbreakers, so your statements about "
            "them were not used."
        )
    if force_generate:
        notes.append(
            "The built-in engine has no fit check that could decline to write, so 'generate "
            "anyway' changed nothing."
        )
    if bullet_lead_in not in (None, "", "none"):
        notes.append(
            "The built-in engine does not bold bullet lead-ins, so that style was not applied."
        )
    return notes


async def _first_failure[T](awaitables: list[Awaitable[T]]) -> list[T]:
    """Runs the awaitables side by side and returns their results in order. If one raises,
    the others are cancelled (not left running with a key in their hands) and it is re-raised."""
    tasks = [asyncio.ensure_future(item) for item in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _letter_date(now: str) -> str:
    try:
        moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{moment:%B} {moment.day}, {moment.year}"


async def _read_posting(session: ModelSession, job: Job) -> tuple[Step0Result | None, list[str]]:
    if not job.description:
        return None, ["The job has no description to read, so the resume is not tailored to it."]
    try:
        return await read_job(session, job.description), []
    except ApiError as e:
        if e.code != "RUN_FAILED":
            raise
        return None, [
            "The model's reading of the job could not be used, so bullets were matched "
            "only on the skills you list that the job mentions."
        ]


async def run_apply(
    session: ModelSession,
    *,
    resume_template: dict[str, Any],
    job_snapshot: dict[str, Any],
    now: str,
    density: Density,
    locale: str | None,
    page_count_override: int | None,
    bullet_lead_in: str | None,
    summary_mode: str,
    show_gpa: bool,
    show_nationality: bool,
    generate_cover_letter: bool,
    dealbreaker_assertions: list[str] | None,
    force_generate: bool,
    header_layout: dict[str, Any] | None,
) -> ForgeApplyResult:
    try:
        async with asyncio.timeout(APPLY_TIMEOUT_SECONDS):
            return await _apply(
                session,
                resume_template=resume_template,
                job_snapshot=job_snapshot,
                now=now,
                density=density,
                locale=locale,
                page_count_override=page_count_override,
                bullet_lead_in=bullet_lead_in,
                summary_mode=summary_mode,
                show_gpa=show_gpa,
                show_nationality=show_nationality,
                generate_cover_letter=generate_cover_letter,
                dealbreaker_assertions=dealbreaker_assertions,
                force_generate=force_generate,
                header_layout=header_layout,
            )
    except TimeoutError as e:
        raise ApiError(
            "PROVIDER_UNAVAILABLE",
            "Writing your documents took too long. Try again in a moment.",
            retryable=True,
        ) from e


async def _apply(
    session: ModelSession,
    *,
    resume_template: dict[str, Any],
    job_snapshot: dict[str, Any],
    now: str,
    density: Density,
    locale: str | None,
    page_count_override: int | None,
    bullet_lead_in: str | None,
    summary_mode: str,
    show_gpa: bool,
    show_nationality: bool,
    generate_cover_letter: bool,
    dealbreaker_assertions: list[str] | None,
    force_generate: bool,
    header_layout: dict[str, Any] | None,
) -> ForgeApplyResult:
    corpus: Corpus = build_corpus(resume_template)
    job = prepare_job(job_snapshot)
    notes = ignored_settings(
        locale=locale,
        show_nationality=show_nationality,
        dealbreaker_assertions=dealbreaker_assertions,
        force_generate=force_generate,
        bullet_lead_in=bullet_lead_in,
    )

    step0, step0_notes = await _read_posting(session, job)
    notes += step0_notes
    read: JobRead = combine_terms(corpus, job, step0)
    try:
        parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        parsed_now = datetime.now(UTC)
    shape = choose_shape(corpus, page_count_override, density, parsed_now)
    plan = build_plan(corpus, read, shape)

    stages: list[Awaitable[Any]] = [
        write_body(session, job, read, plan),
        write_summary(session, job, read, corpus, summary_mode),
    ]
    if generate_cover_letter:
        stages.append(write_cover_letter(session, job, read, corpus, parsed_now))
    results = await _first_failure(stages)
    body: BodyResult = results[0]
    summary: SummaryResult = results[1]
    cover: CoverResult | None = results[2] if generate_cover_letter else None

    assembled = assemble(
        corpus,
        plan,
        read,
        body,
        summary.text,
        show_gpa=show_gpa,
        header_layout=header_layout,
    )
    resume_text = LatexText()
    resume_tex = render_resume(assembled.doc, resume_text)

    claim_warnings = [*body.claim_warnings, *summary.claim_warnings]
    notes += [*body.notes, *summary.notes, *assembled.warnings, *resume_text.warnings()]

    cover_letter: dict[str, Any] | None = None
    if cover is not None:
        personal = flat_personal(corpus.template)
        letter_text = LatexText()
        letter_tex = render_cover_letter(
            name=personal["name"],
            chips=resolve_chips(personal, header_layout),
            separator=separator_of(header_layout),
            company=job.company,
            title=job.title,
            date_text=_letter_date(now),
            content=cover.content,
            esc=letter_text,
        )
        cover_letter = {"latex": letter_tex, "word_count": cover.content.words()}
        claim_warnings += cover.claim_warnings
        notes += [*cover.notes, *(f"Cover letter: {w}" for w in letter_text.warnings())]

    logger.info(
        "generic engine run finished",
        extra={
            "ctx": {
                "model_calls": session.calls,
                "claims_flagged": len(claim_warnings),
                "cover_letter": cover is not None,
                "pages": shape.pages,
            }
        },
    )
    try:
        return ForgeApplyResult(
            resume={"latex": resume_tex},
            cover_letter=cover_letter,
            ats_attempts=[],
            regenerated=False,
            gate=None,
            fit=None,
            shape_report={
                "target_pages": shape.pages,
                "pins_honored": assembled.pins_honored,
                "warnings": notes,
            },
            violations=[],
            claim_warnings=claim_warnings,
            evidence_pointers=list(dict.fromkeys(assembled.evidence_pointers)),
        )
    except ValidationError as e:  # the result is built from typed parts; this is a bug if it fires
        raise ApiError("INTERNAL_ERROR", "The built-in engine produced an invalid result.") from e
