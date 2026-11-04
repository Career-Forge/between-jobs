"""The link check's decisions (api/link_check.py): what a saved link means, and what each platform's
answers add up to. Everything runs against `FakeWeb` (httpx.MockTransport, no network) with the real
answers captured on 2026-11-04 (tests/fixtures/link_check/README.md says how, and what is not real)."""

from __future__ import annotations

import ast
import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from link_check_fakes import (
    ASHBY,
    GH,
    LEVER,
    LEVER_EU,
    NOW,
    NTT_BASE,
    NVIDIA_BASE,
    ZERO_UUID,
    FakeClock,
    FakeWeb,
    fixture,
    make_checker,
)
from rendered_logs import rendered

from between_jobs.api import link_check, link_fetch
from between_jobs.api.link_check import (
    Refusal,
    Target,
    classify_link,
    disabled_error,
    link_check_enabled,
    requisition_token,
)

_GH_ID = str(fixture("greenhouse_board_list")["body"]["jobs"][0]["id"])
_LEVER_ID = fixture("lever_postings_list")["body"][0]["id"]
_ASHBY_ID = fixture("ashby_job_board")["body"]["jobs"][0]["id"]
_NVIDIA_SLUG = "US-CA-Santa-Clara/Senior-Solutions-Architect--AI-Hyperscalers_JR2017895"
_NTT_SLUG = "Bangalore-India/AI-Engineer_R-151594"

_GH_LINK = f"https://boards.greenhouse.io/stripe/jobs/{_GH_ID}"
_LEVER_LINK = f"https://jobs.lever.co/leverdemo/{_LEVER_ID}"
_ASHBY_LINK = f"https://jobs.ashbyhq.com/ashby/{_ASHBY_ID}"
_NVIDIA_LINK = (
    f"https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/{_NVIDIA_SLUG}"
)
_NTT_LINK = f"https://nttlimited.wd3.myworkdayjobs.com/NTT_Careers/job/{_NTT_SLUG}"


# -- what a saved link means -------------------------------------------------------------------

_UUID = "0F1E2D3C-4B5A-6978-8796-A5B4C3D2E1F0"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Greenhouse, three hosts, and the ways a real link carries extras
        ("https://boards.greenhouse.io/stripe/jobs/123", Target("greenhouse", "stripe", "123")),
        ("https://job-boards.greenhouse.io/stripe/jobs/123", Target("greenhouse", "stripe", "123")),
        (
            "https://job-boards.eu.greenhouse.io/stripe/jobs/123",
            Target("greenhouse", "stripe", "123"),
        ),
        (
            "https://boards.greenhouse.io/stripe/jobs/123?gh_src=x",
            Target("greenhouse", "stripe", "123"),
        ),
        ("https://boards.greenhouse.io/stripe/jobs/123/", Target("greenhouse", "stripe", "123")),
        (
            "https://boards.greenhouse.io/stripe/jobs/123#apply",
            Target("greenhouse", "stripe", "123"),
        ),
        ("  https://BOARDS.Greenhouse.io/stripe/jobs/123  ", Target("greenhouse", "stripe", "123")),
        ("http://boards.greenhouse.io/stripe/jobs/123", Target("greenhouse", "stripe", "123")),
        ("https://boards.greenhouse.io:443/stripe/jobs/123", Target("greenhouse", "stripe", "123")),
        (
            "https://boards.greenhouse.io/my.board_1-x/jobs/9",
            Target("greenhouse", "my.board_1-x", "9"),
        ),
        # Lever: both regions, the apply page, an upper-case id
        (
            f"https://jobs.lever.co/acme/{_UUID}",
            Target("lever", "acme", _UUID.lower(), host="jobs.lever.co"),
        ),
        (
            f"https://jobs.eu.lever.co/acme/{_UUID}",
            Target("lever", "acme", _UUID.lower(), host="jobs.eu.lever.co"),
        ),
        (
            f"https://jobs.lever.co/acme/{_UUID}/apply",
            Target("lever", "acme", _UUID.lower(), host="jobs.lever.co"),
        ),
        # Ashby
        (f"https://jobs.ashbyhq.com/acme/{_UUID}", Target("ashby", "acme", _UUID.lower())),
        (
            f"https://jobs.ashbyhq.com/acme/{_UUID}/application",
            Target("ashby", "acme", _UUID.lower()),
        ),
        # Workday: with and without a locale, an apply suffix, one and several location segments
        (
            _NVIDIA_LINK,
            Target(
                "workday",
                "nvidia",
                _NVIDIA_SLUG,
                host="nvidia.wd5.myworkdayjobs.com",
                site="NVIDIAExternalCareerSite",
            ),
        ),
        (
            _NTT_LINK,
            Target(
                "workday",
                "nttlimited",
                _NTT_SLUG,
                host="nttlimited.wd3.myworkdayjobs.com",
                site="NTT_Careers",
            ),
        ),
        (
            f"{_NTT_LINK}/apply",
            Target(
                "workday",
                "nttlimited",
                _NTT_SLUG,
                host="nttlimited.wd3.myworkdayjobs.com",
                site="NTT_Careers",
            ),
        ),
        (
            "https://acme.wd1.myworkdayjobs.com/External/job/Remote_JR1234567",
            Target(
                "workday",
                "acme",
                "Remote_JR1234567",
                host="acme.wd1.myworkdayjobs.com",
                site="External",
            ),
        ),
    ],
)
def test_a_plain_job_link_on_a_covered_platform_is_read_into_its_pieces(
    url: str, expected: Target
) -> None:
    assert classify_link(url) == expected


_NO_LINK = Refusal("other", "no_link")
_COMPANY = Refusal("other", "company_page")
_DENIED = Refusal("other", "denylisted")
_NOT_COVERED = Refusal("other", "not_covered")


def _unreadable(ats: str) -> Refusal:
    return Refusal(ats, "board_missing_or_moved")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # nothing to read
        (None, _NO_LINK),
        ("", _NO_LINK),
        ("   ", _NO_LINK),
        ("not a url", _NO_LINK),
        ("boards.greenhouse.io/stripe/jobs/123", _NO_LINK),
        ("/stripe/jobs/123", _NO_LINK),
        ("ftp://boards.greenhouse.io/stripe/jobs/123", _NO_LINK),
        ("javascript:alert(1)", _NO_LINK),
        ("mailto:jobs@example.com", _NO_LINK),
        ("https://", _NO_LINK),
        ("https:///stripe/jobs/123", _NO_LINK),
        ("https://boards.greenhouse.io/stripe/jobs/12 3", _NO_LINK),
        ("https://boards.greenhouse.io/stripe/jobs/123\nhttps://evil.example", _NO_LINK),
        ("https://boards.greenhouse.io/stripe/jobs/123\x00", _NO_LINK),
        ("https://boards.greenhouse.io/stripe/jobs/" + "1" * 2100, _NO_LINK),
        ("https://boards.greenhouse.io:notaport/stripe/jobs/123", _NO_LINK),
        # hosts that only look like a covered platform: never a request
        ("https://boards.greenhouse.io.evil.example/stripe/jobs/123", _COMPANY),
        ("https://evilboards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://boards.greenhouse.io@evil.example/stripe/jobs/123", _COMPANY),
        ("https://evil.example/boards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://evil.example/?u=https://boards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://boards.greenhouse.io./stripe/jobs/123", _COMPANY),
        ("https://boards.greenhouse.io\\@evil.example/stripe/jobs/123", _COMPANY),
        ("https://evilmyworkdayjobs.com/External/job/Remote_JR1234567", _COMPANY),
        ("https://acme.wd1.myworkdayjobs.com.evil.example/External/job/Remote_JR1234567", _COMPANY),
        ("https://acme.myworkdayjobs.com/External/job/Remote_JR1234567", _COMPANY),
        ("https://wd1.myworkdayjobs.com/External/job/Remote_JR1234567", _COMPANY),
        (f"https://jobs.lever.co.evil.example/acme/{_UUID}", _COMPANY),
        (f"https://xjobs.ashbyhq.com/acme/{_UUID}", _COMPANY),
        # the platforms' own API hosts are not job-link hosts
        ("https://boards-api.greenhouse.io/v1/boards/stripe/jobs", _COMPANY),
        ("https://api.lever.co/v0/postings/leverdemo", _COMPANY),
        # userinfo and ports, even on a covered host
        ("https://user:secret@boards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://user@boards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://@boards.greenhouse.io/stripe/jobs/123", _COMPANY),
        ("https://boards.greenhouse.io:8443/stripe/jobs/123", _COMPANY),
        ("https://boards.greenhouse.io:80/stripe/jobs/123", _COMPANY),
        (f"https://jobs.lever.co:9999/acme/{_UUID}", _COMPANY),
        # IP literals and local names
        ("https://127.0.0.1/stripe/jobs/123", _COMPANY),
        ("https://169.254.169.254/latest/meta-data/", _COMPANY),
        ("https://[::1]/stripe/jobs/123", _COMPANY),
        ("https://[::ffff:127.0.0.1]/stripe/jobs/123", _COMPANY),
        ("https://2130706433/stripe/jobs/123", _COMPANY),
        ("https://localhost/stripe/jobs/123", _COMPANY),
        # a company's own site
        ("https://stripe.com/jobs/search?gh_jid=8172510", _COMPANY),
        ("https://careers.example.com/openings?gh_jid=123", _COMPANY),
        ("https://www.example.com/jobs/123", _COMPANY),
        # not read on purpose
        ("https://www.linkedin.com/jobs/view/123", _DENIED),
        ("https://linkedin.com/jobs/view/123", _DENIED),
        ("linkedin.com/jobs/view/123", _DENIED),
        ("https://user@linkedin.com/jobs/view/123", _DENIED),
        ("https://uk.indeed.com/viewjob?jk=abc", _DENIED),
        ("https://www.glassdoor.com/job-listing/x.htm", _DENIED),
        ("https://x.com/someone/status/1", _DENIED),
        ("https://twitter.com/someone/status/1", _DENIED),
        ("https://linkedin.com.evil.example/jobs/view/123", _COMPANY),
        ("https://notlinkedin.com/jobs/view/123", _COMPANY),
        ("https://jobs.smartrecruiters.com/Acme/123-engineer", _NOT_COVERED),
        ("https://apply.workable.com/acme/j/ABC123/", _NOT_COVERED),
        ("https://acme.workable.com/jobs/1", _NOT_COVERED),
        # a covered host with a path that is not a job we can name
        ("https://boards.greenhouse.io/stripe", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe/jobs", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe/jobs/abc", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe/jobs/1/extra", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io//stripe/jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe//jobs/123", _unreadable("greenhouse")),
        (
            "https://boards.greenhouse.io/embed/job_app?for=stripe&token=123",
            _unreadable("greenhouse"),
        ),
        ("https://boards.greenhouse.io/stripe/jobs/١٢٣", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe/jobs/%31%32%33", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/./jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/../jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/%2e%2e/jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/-stripe/jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/" + "a" * 81 + "/jobs/123", _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/stripe/jobs/" + "1" * 19, _unreadable("greenhouse")),
        ("https://boards.greenhouse.io/str ipe/jobs/123", _NO_LINK),
        ("https://boards.greenhouse.io/stripe;x/jobs/123", _unreadable("greenhouse")),
        ("https://jobs.lever.co/acme/not-a-uuid", _unreadable("lever")),
        ("https://jobs.lever.co/acme", _unreadable("lever")),
        (f"https://jobs.lever.co/acme/{_UUID}/other", _unreadable("lever")),
        (f"https://jobs.lever.co/../{_UUID}", _unreadable("lever")),
        ("https://jobs.ashbyhq.com/acme/123", _unreadable("ashby")),
        (f"https://jobs.ashbyhq.com/acme/{_UUID}/apply", _unreadable("ashby")),
        (
            "https://acme.wd1.myworkdayjobs.com/External/jobs/Remote_JR1234567",
            _unreadable("workday"),
        ),
        ("https://acme.wd1.myworkdayjobs.com/External/job", _unreadable("workday")),
        (
            "https://acme.wd1.myworkdayjobs.com/External/job/a/b/c/d/e_JR1234567",
            _unreadable("workday"),
        ),
        ("https://acme.wd1.myworkdayjobs.com/External/job/../x_JR1234567", _unreadable("workday")),
        ("https://acme.wd1.myworkdayjobs.com/External/job/a%2Fb_JR1234567", _unreadable("workday")),
        (
            "https://acme.wd1.myworkdayjobs.com/External/details/Remote_JR1234567",
            _unreadable("workday"),
        ),
    ],
)
def test_every_other_link_is_refused_with_its_own_reason(
    url: str | None, expected: Refusal
) -> None:
    assert classify_link(url) == expected


async def test_a_link_that_is_not_a_plain_job_link_never_causes_a_request() -> None:
    web = FakeWeb()
    checker = make_checker(web)
    hostile = [
        "https://boards.greenhouse.io.evil.example/stripe/jobs/123",
        "https://evilmyworkdayjobs.com/External/job/Remote_JR1234567",
        "https://boards.greenhouse.io@evil.example/stripe/jobs/123",
        "https://127.0.0.1/stripe/jobs/123",
        "https://boards.greenhouse.io:8443/stripe/jobs/123",
        "https://stripe.com/jobs/search?gh_jid=8172510",
        "https://www.linkedin.com/jobs/view/123",
        "https://jobs.smartrecruiters.com/Acme/123-engineer",
        "",
        None,
    ]

    results = [await checker.check(url) for url in hostile]

    assert {r.state for r in results} == {"cant_tell"}
    assert web.requests == []


@pytest.mark.parametrize(
    ("slug", "token"),
    [
        ("US-CA-Santa-Clara/Senior-Solutions-Architect--AI-Hyperscalers_JR2017895", "JR2017895"),
        ("Bangalore-India/AI-Engineer_R-151594", "R-151594"),
        ("Remote_JR1996000-1", "JR1996000"),
        ("Remote_REQ-123456", "REQ-123456"),
        ("Title_R0012345", "R0012345"),
        ("Title_2301234", "2301234"),
        ("Title_12345-2", "12345"),
        ("No-Underscore-Here", None),
        ("Title_ABC", None),
        ("Title_R-12", None),
        ("Title_2024-12345", None),
        ("Title_JR1234567_extra", None),
        ("Title_", None),
    ],
)
def test_the_requisition_id_is_the_one_at_the_end_of_the_slug_or_nothing(
    slug: str, token: str | None
) -> None:
    assert requisition_token(slug) == token


# -- Greenhouse --------------------------------------------------------------------------------


def _greenhouse_web(**answers: Any) -> FakeWeb:
    web = FakeWeb()
    web.on("GET", GH, answers.get("list", "greenhouse_board_list"))
    return web


async def test_greenhouse_a_job_on_the_boards_list_is_still_listed() -> None:
    web = _greenhouse_web()

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason, result.ats) == (
        "still_listed",
        "still_listed",
        "greenhouse",
    )
    assert result.checked_at == "2026-11-04T18:30:00+00:00"
    assert web.urls() == [f"GET {GH}"]


async def test_greenhouse_absent_from_the_list_and_the_single_job_404s_is_gone() -> None:
    web = _greenhouse_web()
    web.on("GET", f"{GH}/1", "greenhouse_job_404")

    result = await make_checker(web).check("https://boards.greenhouse.io/stripe/jobs/1")

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {GH}", f"GET {GH}/1"]


async def test_greenhouse_absent_from_the_list_but_the_single_job_exists_is_not_a_guess() -> None:
    web = _greenhouse_web()
    web.on("GET", f"{GH}/1", status=200, json_body={"id": 1})

    result = await make_checker(web).check("https://boards.greenhouse.io/stripe/jobs/1")

    assert (result.state, result.reason) == ("cant_tell", "signals_disagree")


async def test_greenhouse_a_missing_board_is_not_a_closed_job() -> None:
    """A missing board and a missing job answer with byte-identical bodies (both captured), so a
    404 on the list says the board is not where we expected and nothing about the job, and the
    single-job endpoint is never asked."""
    web = _greenhouse_web(list="greenhouse_board_404")

    result = await make_checker(web).check("https://boards.greenhouse.io/stripe/jobs/1")

    assert (result.state, result.reason) == ("cant_tell", "board_missing_or_moved")
    assert web.urls() == [f"GET {GH}"]
    assert fixture("greenhouse_board_404")["body"] == fixture("greenhouse_job_404")["body"]


async def test_greenhouse_an_empty_list_says_nothing_about_any_job() -> None:
    web = _greenhouse_web()
    web.on("GET", GH, status=200, json_body={"jobs": [], "meta": {"total": 0}})

    result = await make_checker(web).check("https://boards.greenhouse.io/stripe/jobs/1")

    assert (result.state, result.reason) == ("cant_tell", "empty_listing")
    assert web.urls() == [f"GET {GH}"]


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": 200, "text": "<html>one moment please</html>"}, "unreadable_answer"),
        ({"status": 200, "json_body": {"jobs": "nope"}}, "unreadable_answer"),
        ({"status": 200, "json_body": ["not", "an", "object"]}, "unreadable_answer"),
        ({"status": 200, "json_body": {"jobs": [{"title": "no id"}, "x"]}}, "unreadable_answer"),
        ({"status": 200, "json_body": {"jobs": [{"id": "8172510"}]}}, "unreadable_answer"),
        ({"status": 403, "text": "denied"}, "blocked"),
        ({"status": 401, "text": "denied"}, "blocked"),
        ({"status": 500, "text": "oops"}, "status_500"),
        ({"status": 503, "text": "oops"}, "status_503"),
        ({"status": 410, "text": "gone"}, "status_410"),
    ],
)
async def test_greenhouse_a_list_that_is_not_the_documented_answer_is_labelled_not_read(
    kwargs: dict[str, Any], reason: str
) -> None:
    web = _greenhouse_web()
    web.on("GET", GH, **kwargs)

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason) == ("cant_tell", reason)
    assert web.urls() == [f"GET {GH}"]


async def test_greenhouse_a_redirect_is_not_followed_and_is_not_an_answer() -> None:
    """Hosted posting URLs of customers with their own careers site redirect to the company's
    page. The check never follows one: it reports the redirect as what it is."""
    web = _greenhouse_web()
    web.on("GET", GH, status=302, headers={"location": "https://stripe.com/jobs"})

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason) == ("cant_tell", "status_302")
    assert web.urls() == [f"GET {GH}"]


@pytest.mark.parametrize(
    ("status", "reason"), [(500, "status_500"), (403, "blocked"), (301, "status_301")]
)
async def test_greenhouse_a_single_job_answer_other_than_404_or_200_is_labelled(
    status: int, reason: str
) -> None:
    web = _greenhouse_web()
    web.on("GET", f"{GH}/1", status=status, text="")

    result = await make_checker(web).check("https://boards.greenhouse.io/stripe/jobs/1")

    assert (result.state, result.reason) == ("cant_tell", reason)


# -- Lever -------------------------------------------------------------------------------------


def _lever_web() -> FakeWeb:
    web = FakeWeb()
    web.on("GET", LEVER, "lever_postings_list")
    return web


async def test_lever_a_posting_on_the_list_is_still_listed_whatever_the_case_of_the_link() -> None:
    web = _lever_web()

    result = await make_checker(web).check(f"https://jobs.lever.co/leverdemo/{_LEVER_ID.upper()}")

    assert (result.state, result.reason, result.ats) == ("still_listed", "still_listed", "lever")
    assert web.urls() == [f"GET {LEVER}"]


async def test_lever_absent_from_the_list_and_the_single_posting_404s_is_gone() -> None:
    web = _lever_web()
    single = f"https://api.lever.co/v0/postings/leverdemo/{ZERO_UUID}?mode=json"
    web.on("GET", single, "lever_posting_404")

    result = await make_checker(web).check(f"https://jobs.lever.co/leverdemo/{ZERO_UUID}")

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {LEVER}", f"GET {single}"]


async def test_lever_absent_from_the_list_but_the_single_posting_exists_is_not_a_guess() -> None:
    web = _lever_web()
    web.on("GET", f"https://api.lever.co/v0/postings/leverdemo/{ZERO_UUID}?mode=json", status=200)

    result = await make_checker(web).check(f"https://jobs.lever.co/leverdemo/{ZERO_UUID}")

    assert (result.state, result.reason) == ("cant_tell", "signals_disagree")


async def test_lever_an_empty_list_says_nothing_about_any_job() -> None:
    web = FakeWeb()
    web.on("GET", LEVER, status=200, json_body=[])

    result = await make_checker(web).check(_LEVER_LINK)

    assert (result.state, result.reason) == ("cant_tell", "empty_listing")
    assert web.urls() == [f"GET {LEVER}"]


async def test_lever_a_site_the_global_api_does_not_have_is_looked_for_in_the_eu_region() -> None:
    """No real EU capture exists (the fixtures README says so): the EU API is a stub that answers
    with the real global fixtures."""
    web = FakeWeb()
    web.on("GET", LEVER, "lever_posting_404")
    web.on("GET", LEVER_EU, "lever_postings_list")

    result = await make_checker(web).check(_LEVER_LINK)

    assert (result.state, result.reason) == ("still_listed", "still_listed")
    assert web.urls() == [f"GET {LEVER}", f"GET {LEVER_EU}"]


async def test_lever_the_single_posting_is_asked_of_the_region_that_had_the_site() -> None:
    web = FakeWeb()
    web.on("GET", LEVER, "lever_posting_404")
    web.on("GET", LEVER_EU, "lever_postings_list")
    single = f"https://api.eu.lever.co/v0/postings/leverdemo/{ZERO_UUID}?mode=json"
    web.on("GET", single, "lever_posting_404")

    result = await make_checker(web).check(f"https://jobs.lever.co/leverdemo/{ZERO_UUID}")

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {LEVER}", f"GET {LEVER_EU}", f"GET {single}"]


async def test_lever_a_link_on_the_eu_host_asks_the_eu_api_first() -> None:
    web = FakeWeb()
    web.on("GET", LEVER_EU, "lever_postings_list")

    result = await make_checker(web).check(f"https://jobs.eu.lever.co/leverdemo/{_LEVER_ID}")

    assert (result.state, result.reason) == ("still_listed", "still_listed")
    assert web.urls() == [f"GET {LEVER_EU}"]


async def test_lever_a_site_neither_region_has_is_missing_after_exactly_two_list_requests() -> None:
    web = FakeWeb()
    web.on("GET", LEVER, "lever_posting_404")
    web.on("GET", LEVER_EU, "lever_posting_404")

    result = await make_checker(web).check(_LEVER_LINK)

    assert (result.state, result.reason) == ("cant_tell", "board_missing_or_moved")
    assert web.urls() == [f"GET {LEVER}", f"GET {LEVER_EU}"]


async def test_lever_an_error_from_the_first_region_is_not_a_reason_to_try_the_second() -> None:
    web = FakeWeb()
    web.on("GET", LEVER, status=500, text="")

    result = await make_checker(web).check(_LEVER_LINK)

    assert (result.state, result.reason) == ("cant_tell", "status_500")
    assert web.urls() == [f"GET {LEVER}"]


@pytest.mark.parametrize("body", [{"postings": []}, "text"])
async def test_lever_a_list_that_is_not_an_array_is_labelled_not_read(body: Any) -> None:
    web = FakeWeb()
    web.on("GET", LEVER, status=200, json_body=body)

    result = await make_checker(web).check(_LEVER_LINK)

    assert (result.state, result.reason) == ("cant_tell", "unreadable_answer")


# -- Ashby -------------------------------------------------------------------------------------


def _ashby_web() -> FakeWeb:
    web = FakeWeb()
    web.on("GET", ASHBY, "ashby_job_board")
    return web


async def test_ashby_a_listed_job_on_the_board_is_still_listed() -> None:
    web = _ashby_web()

    result = await make_checker(web).check(_ASHBY_LINK)

    assert (result.state, result.reason, result.ats) == ("still_listed", "still_listed", "ashby")
    assert web.urls() == [f"GET {ASHBY}"]


async def test_ashby_a_job_that_is_on_the_board_but_not_listed_is_reachable_by_link_only() -> None:
    """No real `isListed: false` was seen (the fixtures README says so): the real fixture, with
    that one field changed."""
    board = fixture("ashby_job_board")["body"]
    board["jobs"][0]["isListed"] = False
    web = FakeWeb()
    web.on("GET", ASHBY, status=200, json_body=board)

    result = await make_checker(web).check(_ASHBY_LINK)

    assert (result.state, result.reason) == ("cant_tell", "unlisted_direct_link")
    assert web.urls() == [f"GET {ASHBY}"]


async def test_ashby_absent_from_the_board_and_the_hosted_page_404s_is_gone() -> None:
    web = _ashby_web()
    hosted = f"https://jobs.ashbyhq.com/ashby/{ZERO_UUID}"
    web.on("GET", hosted, "ashby_hosted_404")

    result = await make_checker(web).check(hosted)

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {ASHBY}", f"GET {hosted}"]


async def test_ashby_absent_from_the_board_but_the_hosted_page_loads_is_not_a_guess() -> None:
    web = _ashby_web()
    hosted = f"https://jobs.ashbyhq.com/ashby/{ZERO_UUID}"
    web.on("GET", hosted, status=200, text="<html>a job</html>")

    result = await make_checker(web).check(hosted)

    assert (result.state, result.reason) == ("cant_tell", "signals_disagree")


async def test_ashby_a_hosted_page_that_redirects_is_not_followed() -> None:
    web = _ashby_web()
    hosted = f"https://jobs.ashbyhq.com/ashby/{ZERO_UUID}"
    web.on("GET", hosted, status=302, headers={"location": "https://jobs.ashbyhq.com/ashby"})

    result = await make_checker(web).check(hosted)

    assert (result.state, result.reason) == ("cant_tell", "status_302")
    assert web.urls() == [f"GET {ASHBY}", f"GET {hosted}"]


async def test_ashby_a_missing_board_and_an_empty_board_are_each_labelled() -> None:
    missing = FakeWeb()
    missing.on("GET", ASHBY, status=404, text="")
    empty = FakeWeb()
    empty.on("GET", ASHBY, status=200, json_body={"apiVersion": "1", "jobs": []})

    one = await make_checker(missing).check(_ASHBY_LINK)
    two = await make_checker(empty).check(_ASHBY_LINK)

    assert (one.state, one.reason) == ("cant_tell", "board_missing_or_moved")
    assert (two.state, two.reason) == ("cant_tell", "empty_listing")
    assert missing.urls() == empty.urls() == [f"GET {ASHBY}"]


# -- Workday -----------------------------------------------------------------------------------

_NVIDIA_DETAIL = f"{NVIDIA_BASE}/job/{_NVIDIA_SLUG}"
_NTT_DETAIL = f"{NTT_BASE}/job/{_NTT_SLUG}"


async def test_workday_an_open_posting_is_still_listed() -> None:
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, "workday_detail_200")

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason, result.ats) == ("still_listed", "still_listed", "workday")
    assert web.urls() == [f"GET {_NVIDIA_DETAIL}"]


async def test_workday_a_posting_that_can_no_longer_be_applied_to_is_gone() -> None:
    """No real `canApply: false` was seen (the fixtures README says so): the real fixture with that
    one field changed."""
    closed = fixture("workday_detail_200")["body"]
    closed["jobPostingInfo"]["canApply"] = False
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, status=200, json_body=closed)

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason) == ("gone", "gone")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 200, "text": "<html>challenge</html>"},
        {"status": 200, "json_body": {"somethingElse": {}}},
        {"status": 200, "json_body": {"jobPostingInfo": "x"}},
        {"status": 200, "json_body": [1]},
    ],
)
async def test_workday_a_200_that_is_not_a_job_posting_is_labelled_not_read(
    kwargs: dict[str, Any],
) -> None:
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, **kwargs)

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason) == ("cant_tell", "unreadable_answer")


async def test_workday_the_permission_denied_error_of_a_closed_posting_is_gone() -> None:
    web = FakeWeb()
    web.on("GET", _NTT_DETAIL, "workday_detail_s22_403")

    result = await make_checker(web).check(_NTT_LINK)

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {_NTT_DETAIL}"]


async def test_workday_permission_denied_in_words_without_the_error_code_is_gone_too() -> None:
    web = FakeWeb()
    web.on(
        "GET",
        _NTT_DETAIL,
        status=403,
        json_body={"httpStatus": 403, "message": "Permission Denied"},
    )

    result = await make_checker(web).check(_NTT_LINK)

    assert (result.state, result.reason) == ("gone", "gone")


@pytest.mark.parametrize(
    "kwargs",
    [
        # hand-written: no bare 403 was captured (the fixtures README says so)
        {"status": 403, "text": "<html>Access denied</html>"},
        {"status": 403, "json_body": {"message": "Forbidden"}},
        {"status": 403, "json_body": ["S22"]},
        {"status": 403, "text": ""},
    ],
)
async def test_workday_a_403_that_is_not_the_permission_error_is_a_refusal_not_a_closure(
    kwargs: dict[str, Any],
) -> None:
    web = FakeWeb()
    web.on("GET", _NTT_DETAIL, **kwargs)

    result = await make_checker(web).check(_NTT_LINK)

    assert (result.state, result.reason) == ("cant_tell", "blocked")
    assert web.urls() == [f"GET {_NTT_DETAIL}"]


_NOTHING_SLUG = "Nowhere/Nothing-To-See_JR0000000"
_NOTHING_LINK = f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/{_NOTHING_SLUG}"


async def test_workday_a_404_is_gone_only_when_the_tenants_own_search_has_no_match() -> None:
    web = FakeWeb()
    web.on("GET", f"{NVIDIA_BASE}/job/{_NOTHING_SLUG}", "workday_detail_404")
    web.on("POST", f"{NVIDIA_BASE}/jobs", "workday_search_no_match")

    result = await make_checker(web).check(_NOTHING_LINK)

    assert (result.state, result.reason) == ("gone", "gone")
    assert web.urls() == [f"GET {NVIDIA_BASE}/job/{_NOTHING_SLUG}", f"POST {NVIDIA_BASE}/jobs"]
    search = [r for r in web.requests if r.method == "POST"][0]
    assert search.headers["content-type"] == "application/json"
    assert search.read() == (b'{"appliedFacets":{},"limit":20,"offset":0,"searchText":"JR0000000"}')


async def test_workday_a_404_whose_requisition_the_search_finds_is_not_a_closure() -> None:
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, "workday_detail_404")
    web.on("POST", f"{NVIDIA_BASE}/jobs", "workday_search_match")

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason) == ("cant_tell", "signals_disagree")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 500, "text": "oops"},
        {"status": 403, "text": "no"},
        {"status": 200, "text": "<html>not json</html>"},
        {"status": 200, "json_body": {"jobPostings": []}},  # no `total`: not the documented shape
        {"status": 200, "json_body": {"total": 0, "jobPostings": "x"}},
        {"status": 200, "json_body": ["x"]},
    ],
)
async def test_workday_a_404_the_search_cannot_confirm_is_unconfirmed(
    kwargs: dict[str, Any],
) -> None:
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, "workday_detail_404")
    web.on("POST", f"{NVIDIA_BASE}/jobs", **kwargs)

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason) == ("cant_tell", "detail_404_unconfirmed")


async def test_workday_a_404_on_a_slug_with_no_requisition_id_is_unconfirmed_without_a_search() -> (
    None
):
    slug = "Remote/Some-Title-Without-An-Id"
    web = FakeWeb()
    web.on("GET", f"{NVIDIA_BASE}/job/{slug}", "workday_detail_404")

    result = await make_checker(web).check(
        f"https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/{slug}"
    )

    assert (result.state, result.reason) == ("cant_tell", "detail_404_unconfirmed")
    assert web.urls() == [f"GET {NVIDIA_BASE}/job/{slug}"]


@pytest.mark.parametrize(
    ("status", "reason"), [(422, "status_422"), (500, "status_500"), (302, "status_302")]
)
async def test_workday_any_other_status_is_labelled(status: int, reason: str) -> None:
    web = FakeWeb()
    web.on("GET", _NVIDIA_DETAIL, status=status, text="")

    result = await make_checker(web).check(_NVIDIA_LINK)

    assert (result.state, result.reason) == ("cant_tell", reason)
    assert web.urls() == [f"GET {_NVIDIA_DETAIL}"]


# -- the whole check ---------------------------------------------------------------------------


async def test_a_429_answers_rate_limited_and_the_host_is_left_alone() -> None:
    web = _greenhouse_web()
    web.on("GET", GH, status=429, text="slow down")
    checker = make_checker(web)

    first = await checker.check(_GH_LINK)
    second = await checker.check(_GH_LINK)

    assert (first.state, first.reason) == ("cant_tell", "rate_limited")
    assert (second.state, second.reason) == ("cant_tell", "rate_limited")
    assert web.urls() == [f"GET {GH}"]


async def test_a_board_is_listed_once_for_five_checks_in_five_minutes() -> None:
    web = _greenhouse_web()
    web.on("GET", f"{GH}/1", "greenhouse_job_404")
    clock = FakeClock()
    checker = make_checker(web, clock)

    results = [await checker.check(_GH_LINK) for _ in range(5)]
    gone = await checker.check("https://boards.greenhouse.io/stripe/jobs/1")

    assert [r.state for r in results] == ["still_listed"] * 5
    assert gone.state == "gone"
    # one list for all six checks; the corroborating request is made fresh, never remembered
    assert web.urls() == [f"GET {GH}", f"GET {GH}/1"]

    clock.now += link_check.BOARD_CACHE_SECONDS + 1
    await checker.check(_GH_LINK)

    assert web.urls() == [f"GET {GH}", f"GET {GH}/1", f"GET {GH}"]


async def test_checks_racing_for_one_board_share_one_list_request() -> None:
    web = _greenhouse_web()
    checker = make_checker(web)

    results = await asyncio.gather(*(checker.check(_GH_LINK) for _ in range(5)))

    assert [r.state for r in results] == ["still_listed"] * 5
    assert web.urls() == [f"GET {GH}"]


async def test_an_empty_or_failed_list_is_not_remembered() -> None:
    web = FakeWeb()
    answers = iter([(200, {"jobs": [], "meta": {"total": 0}}), (500, None), (200, None)])

    def respond(_: httpx.Request) -> httpx.Response:
        status, body = next(answers)
        if body is None and status == 200:
            return httpx.Response(200, json=fixture("greenhouse_board_list")["body"])
        return httpx.Response(status, json=body)

    web.on("GET", GH, respond)
    checker = make_checker(web)

    reasons = [(await checker.check(_GH_LINK)).reason for _ in range(3)]

    assert reasons == ["empty_listing", "status_500", "still_listed"]
    assert web.urls() == [f"GET {GH}"] * 3


async def test_a_check_asks_at_most_two_lists_and_one_more_request() -> None:
    """The worst case there is: Lever, a site only the EU API has, a job that is absent."""
    web = FakeWeb()
    web.on("GET", LEVER, "lever_posting_404")
    web.on("GET", LEVER_EU, "lever_postings_list")
    web.on(
        "GET", f"https://api.eu.lever.co/v0/postings/leverdemo/{ZERO_UUID}?mode=json", status=200
    )

    await make_checker(web).check(f"https://jobs.lever.co/leverdemo/{ZERO_UUID}")

    assert len(web.urls()) == link_check.MAX_LIST_REQUESTS + 1


async def test_no_more_than_three_checks_run_at_once() -> None:
    in_flight = 0
    peak = 0
    web = FakeWeb()

    async def slow_list(_: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json=fixture("greenhouse_board_list")["body"])

    checker = make_checker(web)
    for board in "abcdefgh":
        web.on("GET", f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", slow_list)

    results = await asyncio.gather(
        *(checker.check(f"https://boards.greenhouse.io/{b}/jobs/{_GH_ID}") for b in "abcdefgh")
    )

    assert [r.state for r in results] == ["still_listed"] * 8
    assert link_check.MAX_CONCURRENT_CHECKS == 3
    assert peak == 3


async def test_the_whole_check_has_a_budget_and_runs_out_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_check, "CHECK_BUDGET_SECONDS", 0.05)
    web = _greenhouse_web()

    async def never(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("not reached")

    web.on("GET", GH, never)

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason) == ("cant_tell", "timeout")


async def test_a_request_that_takes_too_long_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link_fetch, "REQUEST_TIMEOUT_SECONDS", 0.05)
    web = _greenhouse_web()

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        raise AssertionError("not reached")

    web.on("GET", GH, slow)

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason) == ("cant_tell", "timeout")


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ConnectError("down"), "network"),
        (httpx.ReadError("reset"), "network"),
    ],
)
async def test_a_site_that_does_not_answer_is_a_timeout_or_a_network_failure(
    error: Exception, reason: str
) -> None:
    web = _greenhouse_web()

    def fail(_: httpx.Request) -> httpx.Response:
        raise error

    web.on("GET", GH, fail)

    result = await make_checker(web).check(_GH_LINK)

    assert (result.state, result.reason) == ("cant_tell", reason)


async def test_the_result_is_the_contracts_four_fields() -> None:
    result = await make_checker(_greenhouse_web()).check(_GH_LINK)

    assert result.as_body() == {
        "state": "still_listed",
        "reason": "still_listed",
        "ats": "greenhouse",
        "checked_at": NOW.isoformat(timespec="seconds"),
    }


@pytest.mark.parametrize(
    ("url", "ats"),
    [
        (None, "other"),
        ("https://stripe.com/jobs/search?gh_jid=1", "other"),
        ("https://boards.greenhouse.io/stripe", "greenhouse"),
        (f"https://jobs.lever.co/acme/{_UUID}/other", "lever"),
        (f"https://jobs.ashbyhq.com/acme/{_UUID}/apply", "ashby"),
        ("https://acme.wd1.myworkdayjobs.com/External/jobs/x", "workday"),
    ],
)
async def test_a_refusal_still_says_which_platform_the_link_was_read_as(
    url: str | None, ats: str
) -> None:
    result = await make_checker(FakeWeb()).check(url)

    assert (result.state, result.ats) == ("cant_tell", ats)


def test_every_reason_the_modules_can_give_is_in_the_documented_vocabulary() -> None:
    """The reasons are the contract with the web app, which has words for each. Every literal reason
    in link_check.py and link_fetch.py (the ones raised as `Undecided`, or given to a `Decision` or
    a `Refusal`) must be named in link_check's docstring, which is where the vocabulary lives."""
    found: set[str] = set()
    for module in (link_check, link_fetch):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            args = node.args
            reason = None
            if node.func.id == "Undecided" and args:
                reason = args[0]
            elif node.func.id in ("Decision", "Refusal") and len(args) == 2:
                reason = args[1]
            if isinstance(reason, ast.Constant) and isinstance(reason.value, str):
                found.add(reason.value)

    assert len(found) >= 15
    doc = link_check.__doc__ or ""
    undocumented = {code for code in found if not re.search(rf"\b{code}\b", doc)}
    assert undocumented == set()


# -- the log line ------------------------------------------------------------------------------


async def test_a_check_logs_the_platform_the_state_the_reason_and_the_time_and_nothing_else(
    caplog: pytest.LogCaptureFixture,
) -> None:
    web = _greenhouse_web()
    secret = "https://boards.greenhouse.io/stripe/jobs/" + _GH_ID + "?token=SECRET-QUERY&gh_src=abc"

    with caplog.at_level(logging.DEBUG):
        await make_checker(web).check(secret)
        await make_checker(FakeWeb()).check(
            "https://careers.example.com/jobs?gh_jid=1&private=SECRET-QUERY"
        )

    mine = [r for r in caplog.records if r.name == "between_jobs.api.link_check"]
    assert len(mine) == 2
    assert [sorted(r.ctx) for r in mine] == [["ats", "duration_ms", "reason", "state"]] * 2  # type: ignore[attr-defined]
    # httpx's own INFO line prints every url it requests; the service keeps httpx at WARNING
    # (logging_setup.py), so what is asserted on is what this service's own loggers write.
    text = rendered(r for r in caplog.records if r.name.startswith("between_jobs"))
    for leaked in ("SECRET-QUERY", "gh_src", "careers.example.com", "stripe", _GH_ID, "boards"):
        assert leaked not in text


async def test_no_page_text_reaches_the_log_even_when_the_answer_is_unreadable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    web = _greenhouse_web()
    web.on("GET", GH, status=200, text="<html>PAGE-TEXT-FROM-THE-EMPLOYER</html>")

    with caplog.at_level(logging.DEBUG):
        await make_checker(web).check(_GH_LINK)

    assert "PAGE-TEXT-FROM-THE-EMPLOYER" not in rendered(
        r for r in caplog.records if r.name.startswith("between_jobs")
    )


# -- the kill switch ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "enabled"), [(None, True), ("", True), ("1", False), ("0", False), ("false", False)]
)
def test_the_switch_is_any_non_empty_value(
    monkeypatch: pytest.MonkeyPatch, value: str | None, enabled: bool
) -> None:
    if value is None:
        monkeypatch.delenv("DISABLE_LINK_CHECK", raising=False)
    else:
        monkeypatch.setenv("DISABLE_LINK_CHECK", value)

    assert link_check_enabled() is enabled


def test_the_switch_answers_a_503_that_names_itself() -> None:
    error = disabled_error()

    assert error.status_code == 503
    assert error.details == {"reason": "link_check_disabled"}
