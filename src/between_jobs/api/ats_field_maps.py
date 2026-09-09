"""Browser extension E3c (browser-extension.md) -- reads the latest
Ed25519-signed field map for a given ATS type, published entirely
offline by `scripts/sign_and_publish_ats_field_map.py`. This module never
holds or needs the signing private key; it only serves whatever row that
script already wrote, verbatim.

Deliberately does NOT follow `geo_gazetteer.py`/`company_tiers.py`'s own
fail-open, process-lifetime-cache contract, for two real reasons this
data doesn't share with theirs: (1) there is no legitimate "half-
configured self-host, missing data is a normal degraded state" case the
way there is for an optional location filter -- an ATS with no published
map means the extension can do NOTHING on it, so a confirmed-absent map
(zero rows) and a genuine Supabase error are kept distinguishable here
(the caller turns the former into a clean 404, the latter propagates as
an ordinary unhandled exception -> 500) rather than both quietly
collapsing into the same "empty" result; (2) a single-row-per-ats_type
lookup is cheap enough (one indexed query) that a process-lifetime cache
would only slow down rotating a broken map during an incident, with no
real performance benefit to offset that.
"""

from __future__ import annotations

from typing import Any, cast

from supabase import AsyncClient


async def get_latest_field_map(supabase: AsyncClient, ats_type: str) -> dict[str, Any] | None:
    result = (
        await supabase.table("ats_field_maps")
        .select("ats_type, version, schema, payload_canonical, signature_b64, signing_key_id")
        .eq("ats_type", ats_type)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return cast("dict[str, Any]", result.data[0])
