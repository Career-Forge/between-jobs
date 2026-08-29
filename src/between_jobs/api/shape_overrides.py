"""Shape-overrides precedence + system defaults (R6, resumeforge-shape-and-
fit.md) -- mirrors `locale_resolver.py`'s document-override-then-fallback
shape, but for the rest of a user's resume settings (page count, density,
summary, bullet style, GPA) rather than just region.

Two stored `shape_overrides` jsonb blobs feed `merge()` -- a master
document's (defaults) and a per-application document's (overrides), each
already `ShapeOverrides.model_dump(exclude_none=True)` at write time, so
every key present in either dict is a real, deliberate choice and "absent"
and "None" mean the same thing throughout this module. `resolve()` then
applies the actual system defaults (decisions #2/#3: bullet_style defaults
to forge-engines' own "none" lead-in, summary defaults OFF) to produce the
`ResolvedShapeSettings` `prepare_orchestrator.py` passes to `call_apply`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BulletLeadIn = Literal["none", "bold_keyword"]
Density = Literal["compact", "balanced", "spacious"]

_BULLET_STYLE_TO_LEAD_IN: dict[str, BulletLeadIn] = {
    "plain": "none",
    "bold_lead_in": "bold_keyword",
}

_PAGE_COUNT_TO_OVERRIDE: dict[str, int] = {"1": 1, "2": 2}


@dataclass(frozen=True, slots=True)
class ResolvedShapeSettings:
    """Every field always has a real, non-None value (except `region`,
    which genuinely has no system default -- `None` there means "let
    `locale_resolver`'s own job-text-detection fallback decide"). This is
    what actually reaches forge-engines' `/apply`; the raw, possibly-sparse
    `ShapeOverrides` dicts never do."""

    page_count_override: int | None
    """`None` means "auto" -- forge-engines' own locale-derived norm,
    unchanged from R4. Only "1"/"2" produce a real override."""
    density: Density
    """S6 (honest-score-surfaces.md): a direct passthrough now that
    forge-engines has a real third rendering tier -- no more boolean
    collapse. See `ShapeOverrides.density`'s own docstring."""
    summary_mode: Literal["auto", "on", "off"]
    bullet_lead_in: BulletLeadIn
    region: str | None
    show_gpa: bool
    show_nationality: bool
    """S5 (honest-score-surfaces.md, D1): the candidate's opt-in. Whether a
    chip actually renders is a SEPARATE, backend-side decision (forge-engines'
    `nationality_expected(locale)`) -- this field alone never guarantees
    render, only permits it."""


def merge(master: dict[str, Any] | None, per_application: dict[str, Any] | None) -> dict[str, Any]:
    """Field-by-field: a per-application value wins when present, else the
    master's, else absent (meaning "use the system default", applied by
    `resolve()`)."""
    merged: dict[str, Any] = dict(master or {})
    merged.update(per_application or {})
    return merged


def resolve(merged: dict[str, Any]) -> ResolvedShapeSettings:
    """Applies system defaults on top of `merge()`'s result:
    `page_count` -> "auto" (locale-derived), `density` -> "balanced",
    `summary` -> "off" (decision #3), `bullet_style`
    -> "plain" i.e. forge-engines' `bullet_lead_in="none"` (decision #2),
    `show_gpa` -> `False`, `show_nationality` -> `False` (D1: opt-in only).
    `region` has no system default here -- `None` flows into
    `locale_resolver.resolve_locale_for_prepare` as its `user_default`,
    which itself falls back to job-text detection."""
    page_count = merged.get("page_count") or "auto"
    density: Density = merged.get("density") or "balanced"
    bullet_style = merged.get("bullet_style") or "plain"
    return ResolvedShapeSettings(
        page_count_override=_PAGE_COUNT_TO_OVERRIDE.get(page_count),
        density=density,
        summary_mode=merged.get("summary") or "off",
        bullet_lead_in=_BULLET_STYLE_TO_LEAD_IN.get(bullet_style, "none"),
        region=merged.get("region"),
        show_gpa=bool(merged.get("show_gpa") or False),
        show_nationality=bool(merged.get("show_nationality") or False),
    )
