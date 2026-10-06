"""The first-run checklist for the chat: the same five steps the web app's Today page shows,
worked out from what the account holds.

`web/src/lib/firstRun.ts` is the other implementation of this. The two must agree on every
account, and `tests/shared/first_run_steps.json` is how they are held to it: one table of
accounts and the steps each must come out with, read by a test on each side (`tests/
test_first_run.py` and `web/src/lib/firstRunShared.test.ts`). Change a rule in one place and the
other side's test fails until it follows.

THE FIVE STEPS tick from real data and never from a click: an active profile exists; a validated
model key exists; a first search happened; an application is tracked; an application has a
resume.

THREE STATES, NEVER TWO. A step is done, todo, or UNKNOWN. A fact that could not be read (a
failed query) makes the steps that depend on it unknown, never done and never todo: guessing
"todo" would nag someone who finished the step, and guessing "done" would hide one they did not.

"FIRST SEARCH" LEAVES NO ROW. A search is not stored, so the step rests on what only a search
leaves behind: a saved search, or an application tracked FROM Discover (`source_channel`
"discover"; one pasted, added by URL or sent over a chat says nothing about a search). The web
app also remembers a first search in the browser, which a chat cannot see: it passes `searched`.
The bot passes None, so its first-search step is done when the account holds one of those two
things and UNKNOWN otherwise, never todo: the person may well have searched in their browser.

Facts are read through the stores, each filtered by the verified user id.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Literal

from supabase import AsyncClient

from .applications_store import list_applications
from .artifact_versions_store import artifact_id_for, get_existing_artifact_ids
from .profile_store import get_active_version
from .provider_credentials_store import list_credentials
from .saved_searches_store import list_saved_searches

logger = logging.getLogger(__name__)

StepId = Literal["profile", "model_key", "first_search", "track_job", "generate_resume"]
StepStatus = Literal["done", "todo", "unknown"]

STEP_IDS: tuple[StepId, ...] = (
    "profile",
    "model_key",
    "first_search",
    "track_job",
    "generate_resume",
)

STEP_LABELS: dict[StepId, str] = {
    "profile": "Add your profile",
    "model_key": "Add a model key",
    "first_search": "Run a first search",
    "track_job": "Track a job",
    "generate_resume": "Generate a resume",
}
"""The step names, as the web checklist words them."""

STEP_PAGES: dict[StepId, str] = {
    "profile": "/profile",
    "model_key": "/profile/integrations",
    "first_search": "/discover",
    "track_job": "/applications",
    "generate_resume": "/applications",
}
"""The page of the web app where each step is done. (The web links "generate a resume" straight
to one application's panel when it knows which; a chat has no such application to name.)"""


@dataclass(frozen=True)
class ApplicationFacts:
    count: int
    from_discover: int
    """How many were tracked from a Discover search: the only applications that show one ran."""
    with_resume: int


@dataclass(frozen=True)
class FirstRunFacts:
    """What the account holds. None means the fact could not be read, which is not the same as
    "no": see the module docstring."""

    profile: bool | None
    model_key: bool | None
    saved_searches: int | None
    applications: ApplicationFacts | None


@dataclass(frozen=True)
class FirstRunStep:
    id: StepId
    label: str
    page: str
    status: StepStatus


@dataclass(frozen=True)
class FirstRunView:
    steps: tuple[FirstRunStep, ...]
    done_count: int
    next_step: FirstRunStep | None
    """The first step KNOWN to be todo, or None when there is none. A step that could not be
    checked is never named as the next one: that would tell the person to do something it does
    not know they have not done."""


def _from_flag(fact: bool | None) -> StepStatus:
    if fact is None:
        return "unknown"
    return "done" if fact else "todo"


def _first_search_status(facts: FirstRunFacts, searched: bool | None) -> StepStatus:
    if searched is True:
        return "done"
    if facts.saved_searches is not None and facts.saved_searches > 0:
        return "done"
    if facts.applications is not None and facts.applications.from_discover > 0:
        return "done"
    # Nothing shows a search. That is "not yet" only if everything that could have shown one
    # was actually read, the browser's memory of one included; otherwise it is not known.
    read_everything = (
        facts.saved_searches is not None and facts.applications is not None and searched is False
    )
    return "todo" if read_everything else "unknown"


def derive_first_run(facts: FirstRunFacts, searched: bool | None = None) -> FirstRunView:
    """The five steps for an account. `searched` is the web app's browser-side memory of a first
    search (True, False, or None when it cannot be read); a chat, which has none, passes None."""
    applications = facts.applications
    statuses: dict[StepId, StepStatus] = {
        "profile": _from_flag(facts.profile),
        "model_key": _from_flag(facts.model_key),
        "first_search": _first_search_status(facts, searched),
        "track_job": (
            "unknown" if applications is None else ("done" if applications.count > 0 else "todo")
        ),
        "generate_resume": (
            "unknown"
            if applications is None
            else ("done" if applications.with_resume > 0 else "todo")
        ),
    }
    steps = tuple(
        FirstRunStep(step_id, STEP_LABELS[step_id], STEP_PAGES[step_id], statuses[step_id])
        for step_id in STEP_IDS
    )
    return FirstRunView(
        steps=steps,
        done_count=sum(1 for step in steps if step.status == "done"),
        next_step=next((step for step in steps if step.status == "todo"), None),
    )


# -- reading the facts -----------------------------------------------------------------------


async def _settle[T](fact: str, read: Awaitable[T]) -> T | None:
    """One fact, or None when it could not be read: one failing leaves the others. Logged with
    the fact's name and the error's type and traceback, never a row."""
    try:
        return await read
    except Exception:
        logger.warning(
            "could not read a first-run fact; its steps are shown as unknown",
            exc_info=True,
            extra={"ctx": {"fact": fact}},
        )
        return None


async def _has_profile(supabase: AsyncClient, user_id: str) -> bool:
    return await get_active_version(supabase, user_id) is not None


async def _has_validated_model_key(supabase: AsyncClient, user_id: str) -> bool:
    """A validated key for a model. Saving one points the default capability at it, so "a
    validated model key" and "a validated key for the default capability" are one fact."""
    rows = await list_credentials(supabase, user_id, service="llm")
    return any(row.get("is_validated") is True for row in rows)


async def _count_saved_searches(supabase: AsyncClient, user_id: str) -> int:
    return len(await list_saved_searches(supabase, user_id))


async def _application_facts(supabase: AsyncClient, user_id: str) -> ApplicationFacts:
    applications = await list_applications(supabase, user_id)
    resume_ids = {artifact_id_for(str(application["id"]), "resume") for application in applications}
    existing = await get_existing_artifact_ids(supabase, user_id, sorted(resume_ids))
    return ApplicationFacts(
        count=len(applications),
        from_discover=sum(1 for a in applications if a.get("source_channel") == "discover"),
        with_resume=sum(
            1 for a in applications if artifact_id_for(str(a["id"]), "resume") in existing
        ),
    )


async def load_first_run_facts(supabase: AsyncClient, user_id: str) -> FirstRunFacts:
    """The account's facts, read concurrently through the stores. Only reads: nothing is
    written, and nothing here can spend a person's rate-limited budget."""
    profile, model_key, saved_searches, applications = await asyncio.gather(
        _settle("profile", _has_profile(supabase, user_id)),
        _settle("model_key", _has_validated_model_key(supabase, user_id)),
        _settle("saved_searches", _count_saved_searches(supabase, user_id)),
        _settle("applications", _application_facts(supabase, user_id)),
    )
    return FirstRunFacts(
        profile=profile,
        model_key=model_key,
        saved_searches=saved_searches,
        applications=applications,
    )
