"""Tests for the shared scrape-endpoint host deny-list (outreach-v2-
search-first.md's own "The line" section) -- code-level enforcement, not
just a documented policy.
"""

from __future__ import annotations

from between_jobs.api.scrape_denylist import is_denied_scrape_host


def test_denies_linkedin() -> None:
    assert is_denied_scrape_host("https://www.linkedin.com/jobs/view/12345")
    assert is_denied_scrape_host("https://linkedin.com/in/someone")


def test_denies_linkedin_china_domain() -> None:
    """A real, live LinkedIn domain confirmed via a real request during
    review -- not a lookalike."""
    assert is_denied_scrape_host("https://www.linkedin.cn/jobs/view/12345")
    assert is_denied_scrape_host("https://linkedin.cn/jobs/view/12345")


def test_denies_x_and_twitter() -> None:
    assert is_denied_scrape_host("https://x.com/someone/status/123")
    assert is_denied_scrape_host("https://twitter.com/someone")
    assert is_denied_scrape_host("https://www.twitter.com/someone")


def test_denies_a_scheme_less_url() -> None:
    """Regression test for a real, high-severity bypass an adversarial
    review caught: urlparse gives no hostname at all for a bare string
    with no "://" -- exactly what a browser address bar shows with the
    protocol hidden, or what someone types/pastes by hand."""
    assert is_denied_scrape_host("linkedin.com/jobs/view/4123456789")
    assert is_denied_scrape_host("www.x.com/someone")
    assert is_denied_scrape_host("twitter.com/someone")


def test_denies_a_trailing_dot_fqdn() -> None:
    """ "linkedin.com." (with a trailing DNS root-label dot) resolves to
    the exact same real site as "linkedin.com" -- a real, live hostname-
    filter evasion trick, confirmed via DNS during review."""
    assert is_denied_scrape_host("https://linkedin.com./in/someone")
    assert is_denied_scrape_host("https://x.com./someone")


def test_denies_real_linkedin_subdomains() -> None:
    """Real, currently-live LinkedIn subdomains (confirmed via DNS during
    review to resolve to the same infrastructure as www.linkedin.com) --
    genuine LinkedIn real estate, not exempt just for being unlisted."""
    assert is_denied_scrape_host("https://m.linkedin.com/in/someone")
    assert is_denied_scrape_host("https://careers.linkedin.com/jobs/1")
    assert is_denied_scrape_host("https://mobile.linkedin.com/in/someone")


def test_allows_an_ordinary_ats_url() -> None:
    assert not is_denied_scrape_host("https://boards.greenhouse.io/acme/jobs/123")
    assert not is_denied_scrape_host("https://www.coinbase.com/careers/positions/8105437")


def test_does_not_false_positive_on_a_lookalike_domain() -> None:
    """A suffix match anchored to the END of the host, not a substring
    check -- "notlinkedin.com" or a spoofed subdomain must never be
    conflated with the real thing."""
    assert not is_denied_scrape_host("https://notlinkedin.com/jobs/1")
    assert not is_denied_scrape_host("https://linkedin.com.evil.example/jobs/1")


def test_case_insensitive() -> None:
    assert is_denied_scrape_host("https://WWW.LINKEDIN.COM/jobs/view/12345")


def test_handles_malformed_input_without_crashing() -> None:
    assert not is_denied_scrape_host("")
    assert not is_denied_scrape_host("not a url")
