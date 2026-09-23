"""Export validation checklist (Sprint 3.3f, upgraded R6/C4) -- Proposal
§24.5.7, scoped down to what's genuinely automatable today rather than
faking the rest green. §24.5.7 lists seven checks (one page, links valid,
text selectable, fonts embedded, no unsupported claims, no duplicate
bullets, JD coverage summary); three of those still need capabilities this
platform doesn't have yet (link reachability, PDF font/text introspection,
bullet-similarity analysis). Per this project's own "unknown labeled as
unknown, never guessed" rule, those report as `not_checked`, not a silent
pass -- a three-state status (pass/fail/not_checked) instead of a boolean,
matching the same convention `credential_resolver.py`'s execution modes
and every other tri-state field in this codebase already uses.

C4 (coverforge-port.md) closes the "no unsupported claims" gap: forge-
engines now runs a real claim-verification pass on every generation, and
`_no_unsupported_claims` below reads its findings back off the SAME
`warnings` list `_no_engine_warnings` reads, and `_no_engine_warnings`
skips them so one flagged claim fails one item, not two. See
`_no_unsupported_claims`' own docstring for the one accepted,
honestly-named limitation (a verifier outage and "nothing to flag" read
the same from this list alone).

R6 (resumeforge-shape-and-fit.md) adds `page_fill`, `pins_honored`, and
`page_count_within_shape` as REAL checks against forge-engines'
`ShapeReport`, persisted on the artifact_versions row (`shape_report`
jsonb) specifically so this checklist -- called later, from a separate
HTTP request that has no other access to the in-memory object from
generation time -- can read it back. `shape_report` is optional and,
when absent or empty (a pre-R6 row, or a generation that never completed),
these three report `not_checked` rather than guessing.

`page_count_within_shape` deliberately does NOT replace `one_page` below:
`one_page` is a naive, locale-blind "must be exactly 1 page" check that
predates R4/R6's locale-aware page norms and will always fail a
legitimately 2-page UK/India resume even though that's CORRECT for that
locale; `page_count_within_shape` is its locale-aware replacement. Both
ship side by side rather than one replacing the other -- reconciling that
overlap (most naturally by retiring `one_page`'s hardcoded assumption) is
left to R7's invariant validator, which already owns a "page <= max"
invariant of its own.

Deterministic and pure: no network calls, no LLM, no Supabase client --
just PDF bytes, the warnings already computed by `prepare_application`,
and (R6) the shape report from the same run. Callers own fetching the PDF
(via latex_service_client.call_compile) and the artifact row.
"""

from __future__ import annotations

import io
from typing import Any, Literal, TypedDict

from pypdf import PdfReader
from pypdf.errors import PdfReadError

ChecklistStatus = Literal["pass", "fail", "not_checked"]

_PAGE_FILL_THRESHOLD = 0.75


class ChecklistItem(TypedDict):
    key: str
    label: str
    status: ChecklistStatus
    detail: str


def build_checklist(
    pdf_bytes: bytes, warnings: list[str], shape_report: dict[str, Any] | None = None
) -> list[ChecklistItem]:
    page_count = _page_count(pdf_bytes)
    items: list[ChecklistItem] = [
        _one_page(page_count),
        _no_engine_warnings(warnings),
        _no_unsupported_claims(warnings),
        _page_fill(shape_report),
        _page_count_within_shape(page_count, shape_report),
        _pins_honored(shape_report),
    ]
    items.extend(_not_checked_items())
    return items


def _page_count(pdf_bytes: bytes) -> int | None:
    try:
        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except PdfReadError:
        return None


def _one_page(page_count: int | None) -> ChecklistItem:
    if page_count is None:
        return ChecklistItem(
            key="one_page",
            label="Fits on one page",
            status="fail",
            detail="Couldn't read the compiled PDF to count pages.",
        )
    if page_count == 1:
        return ChecklistItem(
            key="one_page", label="Fits on one page", status="pass", detail="1 page."
        )
    return ChecklistItem(
        key="one_page",
        label="Fits on one page",
        status="fail",
        detail=f"{page_count} pages -- trim content or hide a section.",
    )


_CLAIM_WARNING_PREFIX = "unsupported claim"


def _no_engine_warnings(warnings: list[str]) -> ChecklistItem:
    """Skips claim-verification entries: those belong to
    `_no_unsupported_claims`, and counting them here too would fail two
    items for one flagged claim."""
    engine_warnings = [w for w in warnings if not w.startswith(_CLAIM_WARNING_PREFIX)]
    if not engine_warnings:
        return ChecklistItem(
            key="no_engine_warnings",
            label="No engine warnings",
            status="pass",
            detail="The generation engine raised no cautions; claim flags are checked separately.",
        )
    return ChecklistItem(
        key="no_engine_warnings",
        label="No engine warnings",
        status="fail",
        detail="; ".join(engine_warnings),
    )


def _no_unsupported_claims(warnings: list[str]) -> ChecklistItem:
    """C4 (coverforge-port.md): real now, not `not_checked` -- filters the
    SAME `warnings` list `_no_engine_warnings` reads (and skips) for entries forge-
    engines' claim-verification Judge added
    (`forge_engines.claim_verify.flagged_claim_warnings`'s own
    "unsupported claim (...)" prefix), rather than a separate stored
    field. Named limitation, not hidden: a `pass` here means no flagged
    claims are PRESENT in the stored warnings -- it does not distinguish
    "the check ran and found nothing" from the rare case where the
    verifier call itself failed (fails open, see `claim_verify.py`'s own
    docstring) -- both look identical from this list alone."""
    flagged = [w for w in warnings if w.startswith(_CLAIM_WARNING_PREFIX)]
    if not flagged:
        return ChecklistItem(
            key="no_unsupported_claims",
            label="No unsupported claims",
            status="pass",
            detail="Claim verification found nothing to flag.",
        )
    return ChecklistItem(
        key="no_unsupported_claims",
        label="No unsupported claims",
        status="fail",
        detail="; ".join(flagged),
    )


def _page_fill(shape_report: dict[str, Any] | None) -> ChecklistItem:
    fill_ratio = shape_report.get("fill_ratio") if shape_report else None
    if not isinstance(fill_ratio, (int, float)):
        return ChecklistItem(
            key="page_fill",
            label="Page is well-filled",
            status="not_checked",
            detail="No shape report on this generation to check fill against.",
        )
    if fill_ratio >= _PAGE_FILL_THRESHOLD:
        return ChecklistItem(
            key="page_fill",
            label="Page is well-filled",
            status="pass",
            detail=f"{fill_ratio:.0%} of the page's line budget filled.",
        )
    return ChecklistItem(
        key="page_fill",
        label="Page is well-filled",
        status="fail",
        detail=(
            f"Only {fill_ratio:.0%} filled (target >= {_PAGE_FILL_THRESHOLD:.0%}) -- "
            "candidate's material may be thin for this tier, not a bug to paper over."
        ),
    )


def _page_count_within_shape(
    page_count: int | None, shape_report: dict[str, Any] | None
) -> ChecklistItem:
    target_pages = shape_report.get("target_pages") if shape_report else None
    if page_count is None or not isinstance(target_pages, int):
        return ChecklistItem(
            key="page_count_within_shape",
            label="Page count matches the resolved shape",
            status="not_checked",
            detail="No shape report (or unreadable PDF) to check page count against.",
        )
    if page_count <= target_pages:
        return ChecklistItem(
            key="page_count_within_shape",
            label="Page count matches the resolved shape",
            status="pass",
            detail=(
                f"{page_count} page(s), within the {target_pages}-page target for this locale/tier."
            ),
        )
    return ChecklistItem(
        key="page_count_within_shape",
        label="Page count matches the resolved shape",
        status="fail",
        detail=f"{page_count} pages exceeds the {target_pages}-page target for this locale/tier.",
    )


def _pins_honored(shape_report: dict[str, Any] | None) -> ChecklistItem:
    pins_honored = shape_report.get("pins_honored") if shape_report else None
    if not isinstance(pins_honored, bool):
        return ChecklistItem(
            key="pins_honored",
            label="Pinned entries got their requested bullets",
            status="not_checked",
            detail="No shape report on this generation to check pins against.",
        )
    if pins_honored:
        return ChecklistItem(
            key="pins_honored",
            label="Pinned entries got their requested bullets",
            status="pass",
            detail="Every pinned entry reached its requested minimum bullet count.",
        )
    return ChecklistItem(
        key="pins_honored",
        label="Pinned entries got their requested bullets",
        status="fail",
        detail=(
            "At least one pinned entry fell short of its requested minimum -- it's still "
            "included (the hard guarantee never breaks), just shorter than asked. See the "
            "generation's own warnings for which entry."
        ),
    )


def _not_checked_items() -> list[ChecklistItem]:
    return [
        ChecklistItem(
            key="links_valid",
            label="Links are valid",
            status="not_checked",
            detail="Not automated yet -- review header/link fields by hand.",
        ),
        ChecklistItem(
            key="text_selectable",
            label="Text is selectable (not flattened to images)",
            status="not_checked",
            detail="Not automated yet -- open the PDF and try selecting text.",
        ),
        ChecklistItem(
            key="fonts_embedded",
            label="Fonts are embedded",
            status="not_checked",
            detail="Not automated yet -- check PDF properties in a viewer.",
        ),
        ChecklistItem(
            key="no_duplicate_bullets",
            label="No duplicate bullets",
            status="not_checked",
            detail="Not automated yet -- skim the resume for repeated phrasing.",
        ),
    ]
