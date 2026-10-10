"""How much fits and what is kept (engines/generic/plan.py, assemble.py).

Counted, not measured: sizes are estimated in lines from character counts, the shape follows from
counts and caps, pins are satisfied by code. These tests pin the arithmetic and the order in which
things are given up when a page is full. (That the estimates match what a real compile does is
tests/test_generic_engine_compile.py.)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from generic_engine_fakes import (
    CREDENTIAL,
    SNAPSHOT,
    STEP0_ANSWER,
    ScriptedModel,
    echo_body,
    profile,
)

from between_jobs.api.engine_contract import Step0Result
from between_jobs.engines.generic.assemble import Assembled, assemble, build_skills
from between_jobs.engines.generic.body import write_body
from between_jobs.engines.generic.job import JobRead, combine_terms, prepare_job
from between_jobs.engines.generic.llm import ModelSession
from between_jobs.engines.generic.plan import (
    EXPERIENCE_QUOTA,
    LAYOUTS,
    MAX_EXPERIENCE,
    Plan,
    build_plan,
    bullet_relevance,
    career_years,
    choose_shape,
    most_recent,
    rank_bullets,
    relevance,
)
from between_jobs.engines.generic.sources import Corpus, build_corpus

_NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _read(corpus: Corpus, step0: bool = True) -> JobRead:
    parsed = Step0Result.model_validate(json.loads(STEP0_ANSWER)) if step0 else None
    return combine_terms(corpus, prepare_job(SNAPSHOT), parsed)


def _job(n: int, **changes: Any) -> dict[str, Any]:
    return {
        "title": f"Engineer {n}",
        "company": f"Company {n}",
        "location": "City",
        "start_date": f"{2025 - 2 * n}-01",
        "end_date": "present" if n == 0 else f"{2027 - 2 * n}-01",
        "is_current": n == 0,
        "bullets": [
            f"Bullet {n}.{k} did a thing with Python across {k + 2} systems" for k in range(8)
        ],
        "skills": ["Python"],
        "metrics": [],
        **changes,
    }


# -- counting lines ----------------------------------------------------------------------------


def test_a_bullet_costs_the_lines_its_words_need_when_placed_greedily() -> None:
    layout = LAYOUTS["balanced"]
    forty = "a" * 40

    assert layout.wrapped("") == 1
    assert layout.wrapped(f"{forty} {forty}") == 1  # 81 characters on a 100 wide line
    assert layout.wrapped(f"{forty} {forty} {forty}") == 2  # the third does not fit
    assert layout.wrapped("b" * 250) == 1  # a single word is never split by the estimate
    # a bullet is indented: 96 characters of room, not 100
    ninety_eight = "x" * 48 + " " + "y" * 49
    assert layout.wrapped(ninety_eight) == 1 and layout.wrapped(ninety_eight, indented=True) == 2
    assert layout.bullet("short") == pytest.approx(1 + layout.bullet_gap)


def test_a_bullet_has_four_characters_less_room_than_a_line_of_text() -> None:
    layout = LAYOUTS["balanced"]
    fits = "x" * 48 + " " + "y" * 47  # 96 characters
    spills = "x" * 48 + " " + "y" * 48  # 97

    assert layout.wrapped(fits, indented=True) == 1
    assert layout.wrapped(spills, indented=True) == 2
    assert layout.wrapped(spills) == 1  # a plain line holds 100


def test_a_looser_density_costs_more_per_bullet_and_per_heading() -> None:
    compact, balanced, spacious = (LAYOUTS[d] for d in ("compact", "balanced", "spacious"))

    assert compact.bullet("x") < balanced.bullet("x") < spacious.bullet("x")
    assert compact.experience_heading < balanced.experience_heading < spacious.experience_heading
    assert {layout.lines_per_page for layout in LAYOUTS.values()} == {60}  # one font, one page


# -- how many pages -----------------------------------------------------------------------------


@pytest.mark.parametrize(("override", "pages", "source"), [(1, 1, "override"), (2, 2, "override")])
def test_a_page_count_the_person_chose_always_wins(override: int, pages: int, source: str) -> None:
    corpus = build_corpus(profile(experience=[_job(i) for i in range(8)]))  # long career

    shape = choose_shape(corpus, override, "balanced", _NOW)

    assert (shape.pages, shape.pages_source) == (pages, source)


@pytest.mark.parametrize("override", [None, 0, 3, -1])
def test_anything_else_means_the_engine_decides(override: int | None) -> None:
    shape = choose_shape(build_corpus(profile()), override, "balanced", _NOW)

    assert shape.pages_source == "auto"


@pytest.mark.parametrize(
    ("jobs", "pages"),
    [
        ([_job(0)], 1),
        ([_job(i) for i in range(3)], 1),  # 2019 to now: about 7 years
        ([_job(0, start_date="2014-01")], 2),  # ten years and nine months, current
        ([_job(i, start_date="2024-01", end_date="2025-01") for i in range(6)], 2),  # six jobs
        ([], 1),
    ],
)
def test_one_page_unless_the_career_is_long(jobs: list[dict[str, Any]], pages: int) -> None:
    shape = choose_shape(build_corpus(profile(experience=jobs)), None, "balanced", _NOW)

    assert shape.pages == pages


def test_unknown_dates_are_not_a_number_of_years() -> None:
    corpus = build_corpus(profile(experience=[_job(0, start_date="present", end_date="present")]))

    assert career_years(corpus, _NOW) == 0.0


def test_only_the_dated_jobs_count_towards_the_years_and_an_end_before_a_start_is_ignored() -> None:
    corpus = build_corpus(
        profile(
            experience=[
                _job(0, start_date="2024-10", end_date="present"),  # two years to _NOW
                _job(1, start_date="present", end_date="present"),  # no usable start
                _job(2, start_date="2015-01", end_date="2014-01", is_current=False),  # backwards
            ]
        )
    )

    assert career_years(corpus, _NOW) == pytest.approx(2.0)


def test_the_budget_is_the_pages_times_the_lines_less_a_little_slack() -> None:
    one = choose_shape(build_corpus(profile()), 1, "balanced", _NOW)
    two = choose_shape(build_corpus(profile()), 2, "balanced", _NOW)

    assert two.budget - one.budget == pytest.approx(60)
    assert 0 < one.budget < 60


# -- which jobs, and how many bullets each --------------------------------------------------------


def test_a_profile_within_the_cap_keeps_every_job_in_its_own_order_with_quotas_by_position() -> (
    None
):
    corpus = build_corpus(profile(experience=[_job(i) for i in range(3)]))
    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))

    assert [o.source.pointer for o in plan.experience] == [
        "/experience/0",
        "/experience/1",
        "/experience/2",
    ]
    assert [o.maximum for o in plan.experience] == [5, 4, 3]
    assert all(o.minimum == 2 for o in plan.experience)


def test_the_quotas_by_position_are_the_documented_ones() -> None:
    four = build_corpus(profile(experience=[_job(i) for i in range(4)]))
    one_page = build_plan(four, _read(four), choose_shape(four, 1, "balanced", _NOW))
    seven = build_corpus(profile(experience=[_job(i) for i in range(7)]))
    two_pages = build_plan(seven, _read(seven), choose_shape(seven, 2, "balanced", _NOW))

    assert [o.maximum for o in one_page.experience] == [5, 4, 3, 3]
    assert [o.maximum for o in two_pages.experience] == [6, 5, 4, 4, 3, 3, 3]


def test_over_the_cap_the_pinned_and_the_most_recent_are_kept_then_the_most_relevant() -> None:
    relevant = ["Used Kafka and Snowflake daily", "Wrote Terraform and dbt modules"]
    jobs = [_job(0, bullets=["Answered the phone", "Filed the reports"])]  # recent, not relevant
    jobs += [_job(i, bullets=relevant) for i in range(1, 7)]
    jobs[5]["pin"] = {"mandatory": True, "min_bullets": 3}
    jobs[5]["bullets"] = [*relevant, "Another thing"]
    jobs[1]["bullets"] = ["Used Kafka, Snowflake, Terraform and dbt", *relevant]  # most relevant
    corpus = build_corpus(profile(experience=jobs))

    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))

    kept = [o.source.pointer for o in plan.experience]
    assert len(kept) == MAX_EXPERIENCE[1] == 4
    assert "/experience/0" in kept  # the most recent job, though nothing in it matches
    assert "/experience/5" in kept  # the pinned one
    assert "/experience/1" in kept  # the one that mentions the most of the job's terms
    assert kept == sorted(kept)  # displayed in the candidate's own order
    pinned = next(o for o in plan.experience if o.source.pointer == "/experience/5")
    assert pinned.minimum == 3


def test_the_most_recent_job_is_found_by_date_not_by_position() -> None:
    oldest_first = [_job(2), _job(1), _job(0)]  # a profile written the other way round
    corpus = build_corpus(profile(experience=oldest_first))

    latest = most_recent(list(corpus.experience), corpus.template)

    assert latest is not None and latest.label == "Engineer 0 at Company 0"
    assert most_recent([], corpus.template) is None


def test_a_pin_may_ask_for_more_bullets_than_the_quota_and_never_more_than_exist() -> None:
    jobs = [
        _job(0, pin={"mandatory": True, "min_bullets": 6}),
        _job(1, bullets=["only one"], pin={"mandatory": True, "min_bullets": 4}),
        _job(2, bullets=[]),
    ]
    corpus = build_corpus(profile(experience=jobs))

    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))

    first, second, third = plan.experience
    assert (first.minimum, first.maximum) == (6, 6)  # quota 5 < pin 6: the pin wins
    assert (second.minimum, second.maximum) == (1, 1)  # it has one bullet
    assert (third.minimum, third.maximum) == (0, 0)


def test_two_pages_keep_more_jobs_with_more_bullets_each() -> None:
    corpus = build_corpus(profile(experience=[_job(i) for i in range(9)]))
    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 2, "balanced", _NOW))

    assert len(plan.experience) == MAX_EXPERIENCE[2]
    assert [o.maximum for o in plan.experience] == list(EXPERIENCE_QUOTA[2])
    assert plan.project_limit == 4


def test_projects_are_offered_most_relevant_first_with_pinned_ones_leading() -> None:
    projects = [
        {"name": "plain", "bullets": ["Wrote a parser"]},
        {"name": "relevant", "bullets": ["Built a Kafka consumer"], "tech": ["Kafka"]},
        {"name": "pinned", "bullets": ["Cleaned up"], "pin": {"mandatory": True}},
    ]
    corpus = build_corpus(profile(projects=projects))

    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))

    assert [o.source.label for o in plan.projects] == ["pinned", "relevant", "plain"]
    assert plan.project_limit == 2
    assert [o.maximum for o in plan.projects] == [1, 1, 1]  # each has one bullet to give


def test_a_project_may_give_two_bullets_on_one_page_and_three_on_two() -> None:
    projects = [
        {"name": f"p{i}", "bullets": [f"Did thing {i}.{k}" for k in range(6)]} for i in range(5)
    ]
    corpus = build_corpus(profile(projects=projects))

    one = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))
    two = build_plan(corpus, _read(corpus), choose_shape(corpus, 2, "balanced", _NOW))

    assert {o.maximum for o in one.projects} == {2} and one.project_limit == 2
    assert {o.maximum for o in two.projects} == {3} and two.project_limit == 4


def test_more_mandatory_projects_than_the_limit_raise_the_limit_and_soft_pins_do_not() -> None:
    def limit(pin: dict[str, Any] | None) -> int:
        projects = [
            {"name": f"p{i}", "bullets": ["Wrote a thing"], **({"pin": pin} if pin else {})}
            for i in range(3)
        ] + [{"name": "plain", "bullets": ["Wrote another"]}]
        corpus = build_corpus(profile(projects=projects))
        return build_plan(
            corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW)
        ).project_limit

    assert limit({"mandatory": True}) == 3
    assert limit({"mandatory": False}) == 2
    assert limit(None) == 2


@pytest.mark.parametrize(
    ("years_from", "pages"),
    [("2016-10", 2), ("2016-11", 1)],  # exactly ten years to October 2026, and a month short
)
def test_ten_years_is_a_long_career_and_a_month_short_of_it_is_not(
    years_from: str, pages: int
) -> None:
    corpus = build_corpus(profile(experience=[_job(0, start_date=years_from)]))

    assert choose_shape(corpus, None, "balanced", _NOW).pages == pages


def test_relevance_counts_the_jobs_terms_and_breaks_ties_by_the_candidates_own_order() -> None:
    corpus = build_corpus(
        profile(
            experience=[
                _job(
                    0, bullets=["Wrote docs", "Ran Kafka and Snowflake jobs 3 times", "Fixed bugs"]
                )
            ]
        )
    )
    read = _read(corpus)
    source = corpus.experience[0]

    assert rank_bullets(source, read) == [1, 0, 2]  # the relevant one first, the rest in order
    assert bullet_relevance("Ran Kafka", read) > bullet_relevance("Ran things", read)
    assert bullet_relevance("Ran 3 things", read) > bullet_relevance("Ran things", read)  # a number
    assert relevance(source, read) == pytest.approx(14.4)
    assert relevance(source, _read(corpus, step0=False)) == pytest.approx(14.7)


def test_a_term_earlier_in_the_jobs_list_counts_more_and_a_number_counts_a_little() -> None:
    corpus = build_corpus(
        profile(
            experience=[
                _job(0, skills=[], bullets=["Ran Kafka jobs"]),
                _job(1, skills=[], bullets=["Ran SQL jobs"]),
                _job(2, skills=[], bullets=["Ran Kafka jobs 3 times"]),
            ]
        )
    )
    read = JobRead(terms=("kafka", "sql"))
    kafka, sql, kafka_with_a_number = (relevance(s, read) for s in corpus.experience)

    assert kafka == pytest.approx(3.0 + 2.0) and sql == pytest.approx(3.0 + 1.9)
    assert kafka_with_a_number == pytest.approx(kafka + 0.2)


# -- giving things up when a page is full ----------------------------------------------------------


async def _assembled(
    changes: dict[str, Any],
    *,
    pages: int | None = 1,
    density: Any = "balanced",
    show_gpa: bool = True,
    header: dict[str, Any] | None = None,
    summary: str | None = None,
) -> tuple[Assembled, Corpus, Plan]:
    corpus = build_corpus(profile(**changes))
    job = prepare_job(SNAPSHOT)
    read = _read(corpus)
    plan = build_plan(corpus, read, choose_shape(corpus, pages, density, _NOW))
    model = ScriptedModel(replies={"body": [echo_body]})
    body = await write_body(ModelSession(CREDENTIAL, model), job, read, plan)
    result = assemble(corpus, plan, read, body, summary, show_gpa=show_gpa, header_layout=header)
    return result, corpus, plan


async def test_what_fits_is_all_kept_and_the_estimate_stays_within_the_budget() -> None:
    result, _corpus, plan = await _assembled({})

    assert result.estimated_lines <= plan.shape.budget
    assert result.pins_honored and result.warnings == []
    assert [b.primary for b in result.doc.experience] == [
        "Northwind Labs",
        "Contoso Analytics",
        "Fabrikam",
    ]
    assert result.doc.certifications and result.doc.achievements and result.doc.languages


async def test_a_full_page_gives_up_the_oldest_unpinned_job_first_and_says_so() -> None:
    long_bullets = ["word " * 60] * 8  # four lines each
    pinned = {"mandatory": True, "min_bullets": 6}
    jobs = [_job(i, bullets=long_bullets) for i in range(4)]
    jobs[0]["pin"] = jobs[1]["pin"] = pinned  # the two pins alone nearly fill the page
    jobs.reverse()  # written oldest first: position says nothing, dates do
    result, *_ = await _assembled(
        {"experience": jobs, "projects": [], "certifications": [], "achievements": []},
        density="spacious",
    )

    shown = [b.primary for b in result.doc.experience]
    assert shown == ["Company 1", "Company 0"] or set(shown) == {"Company 0", "Company 1"}
    left_off = [w for w in result.warnings if "left off to keep to 1 page(s)" in w]
    assert [("Company 3" in w, "Company 2" in w) for w in left_off] == [
        (True, False),
        (False, True),
    ]
    assert result.pins_honored is True


async def test_a_pin_is_kept_in_full_even_if_it_breaks_the_page_and_the_warning_says_so() -> None:
    jobs = [_job(i, bullets=["word " * 60] * 8) for i in range(4)]
    for job in jobs[2:]:
        job["pin"] = {"mandatory": True, "min_bullets": 6}
    result, _corpus, plan = await _assembled(
        {"experience": jobs, "projects": []}, density="spacious"
    )

    for block in result.doc.experience:
        if block.primary in {"Company 2", "Company 3"}:
            assert len(block.bullets) == 6
    assert result.pins_honored is True
    assert any("pinned entries make the resume longer than 1 page(s)" in w for w in result.warnings)
    assert result.estimated_lines > plan.shape.budget


async def test_a_pin_the_profile_cannot_fill_is_reported_and_not_honored() -> None:
    jobs = [_job(0), _job(1, bullets=["one"], pin={"mandatory": True, "min_bullets": 3})]

    result, *_ = await _assembled({"experience": jobs, "projects": []})

    assert result.pins_honored is False
    assert any("Company 1" in w and "has 1" in w for w in result.warnings)


async def test_extras_are_capped_by_pages_and_left_out_when_there_is_no_room() -> None:
    certs = [{"name": f"Cert {i}", "issuer": "Body"} for i in range(8)]

    one, _c, _p = await _assembled({"certifications": certs}, pages=1)
    two, _c2, _p2 = await _assembled({"certifications": certs}, pages=2)

    assert len(one.doc.certifications) == 3 and len(two.doc.certifications) == 6


async def test_gpa_follows_the_setting_and_coursework_only_appears_on_two_pages() -> None:
    shown, *_ = await _assembled({}, show_gpa=True, pages=1)
    hidden, *_ = await _assembled({}, show_gpa=False, pages=1)
    two_pages, *_ = await _assembled({}, show_gpa=False, pages=2)

    assert shown.doc.education[0].details == ["GPA: 3.7"]
    assert hidden.doc.education[0].details == []
    assert two_pages.doc.education[0].details == ["Coursework: Databases, Distributed Systems"]


async def test_education_is_capped_but_pinned_entries_always_stay() -> None:
    schools = [
        {
            "degree": f"Degree {i}",
            "institution": f"School {i}",
            **({"pin": {"mandatory": True}} if i == 4 else {}),
        }
        for i in range(5)
    ]

    result, *_ = await _assembled({"education": schools})

    kept = [e.institution for e in result.doc.education]
    assert len(kept) == 3 and "School 4" in kept
    assert kept == sorted(kept)  # in the candidate's order


def _long_bullets_job(n: int, words: int, bullets: int = 8) -> dict[str, Any]:
    return _job(
        n,
        bullets=[
            f"Bullet {n}.{k} " + " ".join(["did"] * words) + " with Python" for k in range(bullets)
        ],
    )


_FULL_PROFILE = {
    "projects": [
        {"name": f"proj{i}", "tech": ["Python"], "bullets": ["Built a thing with Python"] * 3}
        for i in range(4)
    ],
    "certifications": [{"name": f"Cert {i}", "issuer": "Body"} for i in range(6)],
    "achievements": [f"Achievement number {i} " + "word " * 8 for i in range(5)],
}


@pytest.mark.parametrize("density", ["compact", "balanced", "spacious"])
@pytest.mark.parametrize("pages", [1, 2])
@pytest.mark.parametrize("words", [10, 25, 45])
@pytest.mark.parametrize("jobs", [2, 4, 7])
async def test_nothing_unpinned_ever_takes_the_estimate_past_the_budget(
    density: str, pages: int, words: int, jobs: int
) -> None:
    changes = {**_FULL_PROFILE, "experience": [_long_bullets_job(i, words) for i in range(jobs)]}

    result, _corpus, plan = await _assembled(
        changes, pages=pages, density=density, summary="A long summary sentence. " * 8
    )

    assert result.estimated_lines <= plan.shape.budget + 0.01, (density, pages, words, jobs)
    assert result.pins_honored


async def test_what_does_not_fit_is_left_out_and_what_does_is_kept() -> None:
    crowded = {**_FULL_PROFILE, "experience": [_long_bullets_job(i, 45) for i in range(7)]}
    roomy = {**_FULL_PROFILE, "experience": [_long_bullets_job(i, 10) for i in range(2)]}

    tight, _c, tight_plan = await _assembled(crowded, pages=1)
    loose, _c2, loose_plan = await _assembled(roomy, pages=2)

    offered = sum(o.maximum for o in tight_plan.experience)
    assert sum(len(b.bullets) for b in tight.doc.experience) < offered  # bullets were left out
    assert tight.doc.certifications == [] and tight.doc.achievements == []
    assert len(tight.doc.projects) < 2
    assert sum(len(b.bullets) for b in loose.doc.experience) == sum(
        o.maximum for o in loose_plan.experience
    )
    assert len(loose.doc.certifications) == 6 and len(loose.doc.achievements) == 5
    assert len(loose.doc.projects) == 4


async def test_a_project_that_does_not_fit_is_left_out_or_stops_where_the_page_ends() -> None:
    crowded = {**_FULL_PROFILE, "experience": [_long_bullets_job(i, 45) for i in range(2)]}

    result, _c, plan = await _assembled(crowded, pages=1)

    assert result.estimated_lines <= plan.shape.budget + 0.01
    assert len(result.doc.projects) < 2 or all(len(p.bullets) < 3 for p in result.doc.projects)


_BARE: dict[str, Any] = {
    "experience": [],
    "projects": [],
    "education": [],
    "skills": {},
    "certifications": [],
    "achievements": [],
    "languages": [],
    "summary_bullets": [],
}
"""A profile with a name and nothing else, so only the header costs anything."""


async def test_the_summary_costs_its_lines_plus_a_gap() -> None:
    summary = "A long summary sentence. " * 8
    bare = {**_BARE, "personal": {"name": "Sam Rowe"}}

    without, _c, plan = await _assembled(bare, summary=None)
    with_one, *_ = await _assembled(bare, summary=summary)

    assert with_one.estimated_lines - without.estimated_lines == pytest.approx(
        plan.shape.layout.wrapped(summary) + 0.4
    )


async def test_the_header_and_the_headline_cost_what_the_template_takes() -> None:
    nothing, *_ = await _assembled({**_BARE, "personal": {"name": "Sam Rowe"}})
    headlined, *_ = await _assembled(
        {**_BARE, "personal": {"name": "Sam Rowe", "headline": "Engineer"}}
    )

    # a name and nothing else: the header alone; with a headline, 1.1 lines more (written out
    # here so that changing the template's constants is a decision, not a side effect)
    assert nothing.estimated_lines == pytest.approx(2.7)
    assert headlined.estimated_lines == pytest.approx(2.7 + 1.1)


async def test_when_the_mandatory_part_overflows_by_a_little_the_oldest_job_goes_and_only_it() -> (
    None
):
    jobs = [_long_bullets_job(i, 70) for i in range(4)]

    result, _corpus, plan = await _assembled(
        {"experience": jobs, "projects": [], "certifications": [], "achievements": []}
    )

    left_off = [w for w in result.warnings if "left off to keep to 1 page(s)" in w]
    assert len(left_off) == 1 and "Company 3" in left_off[0]
    assert [b.primary for b in result.doc.experience] == ["Company 0", "Company 1", "Company 2"]
    assert result.estimated_lines <= plan.shape.budget + 0.01


async def test_the_most_each_extra_may_print_depends_on_the_pages() -> None:
    extras = {
        "certifications": [{"name": f"Cert {i}"} for i in range(8)],
        "publications": [{"title": f"Paper {i}"} for i in range(8)],
        "patents": [{"title": f"Patent {i}"} for i in range(8)],
        "volunteering": [{"organization": f"Org {i}", "bullets": ["Helped out"]} for i in range(8)],
        "achievements": [f"Won prize {i}" for i in range(8)],
        "experience": [],
        "projects": [],
    }

    one, *_ = await _assembled(extras, pages=1)
    two, *_ = await _assembled(extras, pages=2)

    def counts(result: Assembled) -> tuple[int, int, int, int, int]:
        doc = result.doc
        return (
            len(doc.certifications),
            len(doc.publications),
            len(doc.patents),
            len(doc.volunteering),
            len(doc.achievements),
        )

    assert counts(one) == (3, 2, 1, 1, 2)
    assert counts(two) == (6, 5, 3, 3, 5)


async def test_extras_are_left_out_when_the_page_is_full() -> None:
    full = {
        "experience": [_long_bullets_job(i, 45) for i in range(4)],
        "certifications": [{"name": f"Cert {i}"} for i in range(3)],
        "projects": [],
        "achievements": [],
    }

    result, _c, plan = await _assembled(full, pages=1)
    roomy, *_ = await _assembled({**full, "experience": []}, pages=1)

    assert result.doc.certifications == [] and len(roomy.doc.certifications) == 3
    assert result.estimated_lines <= plan.shape.budget + 0.01


# -- pins, for projects and the ones that are not mandatory ----------------------------------


def _project(n: int, **changes: Any) -> dict[str, Any]:
    return {
        "name": f"proj{n}",
        "tech": ["Python"],
        "bullets": [f"Built part {n}.{k} with Python" for k in range(3)],
        **changes,
    }


async def test_a_pinned_project_is_printed_with_its_minimum_even_when_the_page_is_over() -> None:
    jobs = [_long_bullets_job(i, 70, bullets=8) for i in range(4)]
    for job in jobs[:2]:
        job["pin"] = {"mandatory": True, "min_bullets": 6}
    projects = [_project(0, pin={"mandatory": True, "min_bullets": 2}), _project(1)]

    result, _c, plan = await _assembled(
        {"experience": jobs, "projects": projects, "certifications": [], "achievements": []},
        density="spacious",
    )

    assert result.estimated_lines > plan.shape.budget  # the pins alone fill more than a page
    assert [p.name for p in result.doc.projects] == ["proj0"]  # the pinned one, not the other
    assert len(result.doc.projects[0].bullets) == 2  # exactly what the pin asked for


async def test_a_project_pin_that_is_not_mandatory_does_not_pin() -> None:
    jobs = [_long_bullets_job(i, 70, bullets=8) for i in range(4)]
    for job in jobs[:2]:
        job["pin"] = {"mandatory": True, "min_bullets": 6}
    soft = [_project(0, pin={"mandatory": False, "min_bullets": 2}), _project(1)]

    result, *_ = await _assembled(
        {"experience": jobs, "projects": soft, "certifications": [], "achievements": []},
        density="spacious",
    )

    assert result.doc.projects == []


def test_a_pin_that_is_not_mandatory_is_not_a_pin_on_a_job_or_a_project() -> None:
    soft = {"mandatory": False, "min_bullets": 3}
    corpus = build_corpus(
        profile(
            experience=[_job(i) for i in range(5)] + [_job(5, pin=soft)],
            projects=[
                _project(0, pin=soft),
                _project(1, pin={"mandatory": True, "min_bullets": 2}),
            ],
        )
    )

    assert [s.pinned for s in corpus.experience] == [False] * 6
    assert all(s.min_bullets is None for s in corpus.experience)
    assert [(s.pinned, s.min_bullets) for s in corpus.projects] == [(False, None), (True, 2)]


def test_a_soft_pinned_old_job_is_left_out_like_any_other_when_there_are_too_many() -> None:
    soft_oldest = _job(5, pin={"mandatory": False, "min_bullets": 3})
    corpus = build_corpus(profile(experience=[*(_job(i) for i in range(5)), soft_oldest]))

    plan = build_plan(corpus, _read(corpus), choose_shape(corpus, 1, "balanced", _NOW))

    assert "/experience/5" not in {o.source.pointer for o in plan.experience}
    mandatory = build_corpus(
        profile(
            experience=[*(_job(i) for i in range(5)), {**soft_oldest, "pin": {"mandatory": True}}]
        )
    )
    kept = build_plan(mandatory, _read(mandatory), choose_shape(mandatory, 1, "balanced", _NOW))
    assert "/experience/5" in {o.source.pointer for o in kept.experience}


# -- blank entries and the lines of the extras -----------------------------------------------


@pytest.mark.parametrize(
    "certs",
    [
        [{"name": " "}, {"name": "AWS Certified Developer"}],
        [{"name": "AWS Certified Developer"}, {"name": " "}, {"name": "GCP Professional"}],
        [{"name": "\u200b"}, {"name": "\u200b"}, {"name": "AWS Certified Developer"}],
    ],
)
async def test_a_blank_looking_certification_is_left_out_not_printed_as_an_empty_line(
    certs: list[dict[str, str]],
) -> None:
    from between_jobs.engines.generic.latex import LatexText
    from between_jobs.engines.generic.templates import render_resume

    result, *_ = await _assembled({"certifications": certs}, pages=2)

    assert result.doc.certifications
    assert all(line.strip() and line.strip("\u200b") for line in result.doc.certifications)
    latex = render_resume(result.doc, LatexText())
    assert not [line for line in latex.splitlines() if line.strip() == "\\\\"]


async def test_blank_extras_do_not_use_up_the_places_there_are_for_real_ones() -> None:
    result, *_ = await _assembled(
        {
            "certifications": [{"name": " "}] * 3 + [{"name": "Real cert"}],
            "patents": [{"title": " "}, {"title": "Real patent"}],
            "publications": [{"title": " "}] + [{"title": f"Paper {i}"} for i in range(2)],
        },
        pages=1,
    )

    assert result.doc.certifications == ["Real cert"]
    assert result.doc.patents == ["Real patent"]  # one place on a page, and the blank had it
    assert result.doc.publications == ["Paper 0", "Paper 1"]
    # the evidence points at the entries that were printed, by their place in the profile
    assert {"/publications/1", "/publications/2", "/patents/1"} <= set(result.evidence_pointers)
    assert "/publications/0" not in result.evidence_pointers


@pytest.mark.parametrize(
    ("publication", "line"),
    [
        (
            {
                "title": "Why is connectivity hard?",
                "venue": "Journal of Examples",
                "date": "2022-05",
            },
            "Why is connectivity hard? Journal of Examples, May 2022",
        ),
        ({"title": "Connectivity classification."}, "Connectivity classification."),
        ({"title": "No venue but date", "date": "2021"}, "No venue but date. 2021"),
        ({"title": "Only venue", "venue": "Proc. of X"}, "Only venue. Proc. of X"),
        ({"title": "Full", "venue": "Venue", "date": "2020-03"}, "Full. Venue, Mar 2020"),
        ({"title": "Loud!", "venue": "Venue"}, "Loud! Venue"),
        ({"title": "Plain title"}, "Plain title"),
        ({"title": "Spaced  ", "venue": " ", "date": " "}, "Spaced"),
    ],
)
async def test_a_publication_line_has_one_full_stop_and_dates_written_like_the_rest(
    publication: dict[str, str], line: str
) -> None:
    result, *_ = await _assembled({"publications": [publication]}, pages=2)

    assert result.doc.publications == [line]


async def test_the_evidence_is_every_entry_that_was_printed() -> None:
    result, *_ = await _assembled(
        {"publications": [{"title": "A paper"}], "patents": [{"title": "A patent"}]}
    )

    assert set(result.evidence_pointers) >= {
        "/education/0",
        "/experience/0",
        "/projects/0",
        "/publications/0",
        "/patents/0",
    }


# -- skills --------------------------------------------------------------------------------


@pytest.mark.parametrize(("pages", "most"), [(1, 30), (2, 52)])
async def test_a_page_has_room_for_so_many_skills_and_no_more(pages: int, most: int) -> None:
    skills = [f"Skill{n}" for n in range(70)]
    result, *_ = await _assembled({"skills": {"programming": skills}}, pages=pages)

    printed = ", ".join(value for _label, value in result.doc.skills).split(", ")
    assert len(printed) == most


def test_skills_are_the_candidates_own_deduplicated_and_the_jobs_first() -> None:
    corpus = build_corpus(
        profile(
            skills={
                "programming": ["Python", "Go"],
                "data_mlops": ["Postgres", "Kafka"],
                "cloud_devops": ["Docker"],
                "tools": ["Git"],
            },
            experience=[_job(0, skills=["PostgreSQL", "Terraform", "Snowflake"])],
            projects=[{"name": "p", "tech": ["Rust", "kafka"], "bullets": ["x"]}],
        )
    )

    lines = dict(build_skills(corpus, _read(corpus), 30))

    # the job mentions Kafka, Snowflake and Terraform: they lead their category
    assert lines["Data / MLOps"].split(", ")[:2] == ["Kafka", "Snowflake"]
    assert lines["Cloud / DevOps"].startswith("Terraform")
    # Postgres and PostgreSQL are one skill, printed once as first written; kafka once
    flat = ", ".join(lines.values()).lower()
    assert flat.count("postgres") == 1 and flat.count("kafka") == 1
    assert "Rust" in ", ".join(lines.values())  # a project's technology is a skill too


def test_the_skills_limit_drops_the_ones_the_job_did_not_ask_for() -> None:
    corpus = build_corpus(
        profile(
            skills={"programming": [f"Lang{i}" for i in range(10)] + ["Python"], "tools": ["Kafka"]}
        )
    )

    lines = dict(build_skills(corpus, _read(corpus), 3))

    shown = ", ".join(lines.values())
    assert "Python" in shown and "Kafka" in shown  # both are in the posting
    assert shown.count(",") == 2  # three skills in all


def test_a_skill_the_skills_table_does_not_find_verified_or_supported_is_never_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every skill the profile lists is verified or supported by construction, so the filter
    cannot fire on a real profile; this makes it fire to show it is the gate it is meant to be."""
    from between_jobs.api.skills import classify_skill

    corpus = build_corpus(profile())

    def adjacent_docker(skill: str, canonical: dict[str, Any]) -> str:
        return "adjacent" if skill == "Docker" else classify_skill(skill, canonical)

    monkeypatch.setattr("between_jobs.engines.generic.assemble.classify_skill", adjacent_docker)

    printed = ", ".join(value for _label, value in build_skills(corpus, _read(corpus), 30))

    assert "Docker" not in printed and "Python" in printed


def test_no_skill_is_invented_for_a_profile_that_lists_none() -> None:
    corpus = build_corpus(
        {
            "personal": {"name": "Sam Rowe"},
            "projects": [{"name": "p", "bullets": ["Used Terraform"]}],
        }
    )

    assert build_skills(corpus, _read(corpus), 30) == []  # the posting's Kafka is not Sam's skill


def test_a_long_list_of_skills_costs_a_bounded_number_of_checks_and_little_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`classify_skill` reads the whole profile, so checking every skill made a long list cost
    time in proportion to its square, in code the event loop waits for."""
    import time

    from between_jobs.api.skills import classify_skill

    skills = [f"Skill{i} " + "x" * 12 for i in range(6000)]
    skills.insert(10, "Kafka")  # the job asks for it: it leads, wherever it sits in the list
    corpus = build_corpus(profile(skills={"programming": skills}))
    read = _read(corpus)
    calls: list[str] = []

    def counted(skill: str, canonical: dict[str, Any]) -> str:
        calls.append(skill)
        return classify_skill(skill, canonical)

    monkeypatch.setattr("between_jobs.engines.generic.assemble.classify_skill", counted)

    started = time.perf_counter()
    lines = build_skills(corpus, read, 30)
    elapsed = time.perf_counter() - started

    assert len(calls) <= 4 * 30
    assert elapsed < 3.0, elapsed
    printed = ", ".join(value for _label, value in lines)
    assert printed.count(",") + 1 == 30
    assert printed.startswith("Kafka")  # the job's skill leads


def test_the_choice_of_skills_is_the_same_as_checking_them_all() -> None:
    corpus = build_corpus(profile())
    read = _read(corpus)

    lines = build_skills(corpus, read, 52)

    assert lines[0][0] == "Programming"
    flat = ", ".join(value for _label, value in lines).split(", ")
    assert len(flat) == len(set(s.lower() for s in flat))
    assert {"Python", "Kafka", "Kubernetes", "Snowflake"} <= set(flat)
