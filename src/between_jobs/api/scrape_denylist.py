"""Host deny-list for every scrape-capable call path (outreach-v2-
search-first.md's own "The line" section) -- enforced in code, not just
prompt/policy text. A search-endpoint query with `site:linkedin.com` in
its terms is fine (it queries the provider's own index, never touches
linkedin.com -- see contact_research.py); pointing a SCRAPE/crawl
endpoint at linkedin.com or x.com/twitter.com, or any real subdomain of
either, is never fine, regardless of caller or purpose. Phase J's
JD-link scraper is this module's first real caller; any future scrape-
capable feature (Phase K's X lane included) must check here too, not
re-implement its own list.
"""

from __future__ import annotations

from urllib.parse import urlparse

_DENIED_SCRAPE_BASE_DOMAINS = frozenset(
    {
        "linkedin.com",
        "linkedin.cn",  # LinkedIn's own real China-market domain -- verified live.
        "x.com",
        "twitter.com",
    }
)
"""Matched by SUFFIX (host == base or host.endswith("." + base)), not an
exact-hostname set -- an adversarial review confirmed real, currently-
live LinkedIn subdomains (m.linkedin.com, careers.linkedin.com, and
others) all resolve to LinkedIn's own real infrastructure and must be
denied too; a fixed set of exact hostnames would need updating every
time LinkedIn/X stood up a new one. The suffix check is anchored to the
END of the host specifically (never a bare substring check) so
"notlinkedin.com" or "linkedin.com.evil.example" -- neither of which
actually ENDS with ".linkedin.com" -- are correctly never denied."""


def _normalize_host(url: str) -> str:
    """A bare scheme-less string (e.g. "linkedin.com/jobs/1", exactly
    what a browser address bar shows with the protocol hidden, or what
    someone types by hand) has no parseable authority component at all
    -- `urlparse(...).hostname` is `None` for it, verified live -- so a
    naive parse silently treats it as "no host, not denied." Prepending a
    scheme when none is present closes that gap. A trailing DNS root-
    label dot ("linkedin.com.", a real, live, byte-for-byte-identical-
    site hostname-filter evasion trick, confirmed via a real DNS lookup)
    is stripped for the same reason."""
    candidate = url.strip()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        host = (urlparse(candidate).hostname or "").lower()
    except ValueError:
        return ""
    return host.removesuffix(".")


def is_denied_scrape_host(url: str) -> bool:
    host = _normalize_host(url)
    if not host:
        return False
    return any(host == base or host.endswith(f".{base}") for base in _DENIED_SCRAPE_BASE_DOMAINS)
