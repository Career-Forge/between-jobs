"""The `company_health` half of the batch fit-scorer composite: a lookup from a
company name to a 0-1 tier weight, backed by the `company_tiers` table.

The table is reference data, loaded by `scripts/import_company_tiers.py` from a
file the operator supplies (none ships with this repository), the same
"algorithm is code, data is the operator's own Supabase import" split the city
gazetteer uses (`scripts/import_geo_gazetteer.py` mirrors it structurally).

`normalize_company_name` is ported verbatim from the reference implementation's
`normalizeCompanyName`. There is exactly one copy here, used both to check the
lookup keys at import time and to look a job's company name up at score time, so
a stored key and a looked-up name are never normalized two different ways."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, cast

from supabase import AsyncClient

from .supabase_helpers import fetch_all_pages

logger = logging.getLogger(__name__)

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
    """PostgREST silently caps an unranged `.select()` at 1,000 rows -- the
    same bug class `geo_gazetteer._fetch_all_city_rows` pages around, and
    one that once made a bulk import silently skip most of its rows. The
    table is well under that cap today, but the fix costs nothing and a
    row count that is fine "for now" is how this bug ships."""

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
        logger.exception("company tier index failed to load; company_health scores as inapplicable")
        weights = {}
    _cached_index = (
        CompanyTierIndex(weights_by_normalized_name=weights) if weights else _EMPTY_INDEX
    )
    return _cached_index
