"""Job Finder P6b (live-search-track.md's own P6 scoping) -- the
`company_health` half of P6a's batch fit-scorer composite. Real
Fortune-500-based tier data (n8n's real `data/reference/
company_tiers.json`, CC BY 4.0 base + a hand-curated MAANGO/top-
fintech-quant/hot-AI-startup/other-dream/Big-4 overlay -- ALREADY
merged into the file by n8n's own build step, confirmed directly
rather than assumed: `company_tier_overrides.json` is n8n's build-time
INPUT, not something this project re-merges itself), imported via the
same "algorithm=code, data=private Supabase import" precedent P5d's
gazetteer already established -- `scripts/import_company_tiers.py`
mirrors `import_geo_gazetteer.py` structurally.

`normalize_company_name` is ported verbatim from n8n's own real
`normalizeCompanyName` (duplicated in n8n's own source between `Parse
Scorer Output.js` and `seed_company_tier_weights.js`, kept in sync by a
code comment there -- between-jobs has exactly one copy, used both to
build the lookup at import time and to look a job's company name up at
score time, closing the duplication n8n's own reference never did)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from supabase import AsyncClient

from .supabase_helpers import fetch_all_pages

_NAME_SUFFIX_RX = re.compile(
    r"\b(incorporated|corporation|company|limited|holdings?|group|llc|"
    r"inc|corp|co|ltd|llp|plc|gmbh|ag|sa|nv|bv)\b\.?"
)
_PUNCTUATION_RX = re.compile(r"[.,'\"()]")
_WHITESPACE_RX = re.compile(r"\s+")


def normalize_company_name(raw: str | None) -> str:
    normalized = (raw or "").lower().replace("&", "and")
    normalized = _PUNCTUATION_RX.sub("", normalized)
    normalized = _NAME_SUFFIX_RX.sub("", normalized)
    return _WHITESPACE_RX.sub(" ", normalized).strip()


@dataclass(frozen=True)
class CompanyTierIndex:
    weights_by_normalized_name: dict[str, float]

    def lookup(self, company: str | None) -> float | None:
        if not company:
            return None
        return self.weights_by_normalized_name.get(normalize_company_name(company))


_EMPTY_INDEX = CompanyTierIndex(weights_by_normalized_name={})
_cached_index: CompanyTierIndex | None = None
_FETCH_PAGE_SIZE = 1000


async def _fetch_all_rows(supabase: AsyncClient) -> list[dict[str, Any]]:
    """Same PostgREST-1000-row-default bug class already found and fixed
    for `import_job_registry_seed.py` and `geo_gazetteer._fetch_all_
    city_rows` -- 576 rows today is well under that cap, but the fix
    costs nothing and this project has been burned by trusting a
    row count "for now" before."""

    async def _page(start: int, end: int) -> list[dict[str, Any]]:
        result = (
            await supabase.table("company_tiers")
            .select("normalized_name, weight")
            .range(start, end)
            .execute()
        )
        return cast("list[dict[str, Any]]", result.data)

    return await fetch_all_pages(_page, page_size=_FETCH_PAGE_SIZE)


async def get_company_tier_index(supabase: AsyncClient) -> CompanyTierIndex:
    """Loads and caches the tier index for this process's lifetime --
    static reference data, so a per-call DB round-trip (or worse, one
    per job in a 30-job scoring batch) would be pure waste. Fails open
    to an empty index (never raises) if the table is empty or
    unreachable, matching `geo_gazetteer.get_gazetteer`'s own contract:
    every lookup then misses, and `company_health` falls back to
    inapplicable rather than the scoring call failing outright."""
    global _cached_index
    if _cached_index is not None:
        return _cached_index
    try:
        rows = await _fetch_all_rows(supabase)
        weights = {r["normalized_name"]: float(r["weight"]) for r in rows}
    except Exception:
        weights = {}
    _cached_index = (
        CompanyTierIndex(weights_by_normalized_name=weights) if weights else _EMPTY_INDEX
    )
    return _cached_index
