"""The fabrication check (engines/generic/provenance.py): what it accepts, what it refuses, and
what it says it cannot see.

A new sentence may reword its source; it may not bring in a number, a name, a tool, a term from
the job posting, a leadership claim, markup or a link the source does not have.
"""

from __future__ import annotations

import time

import pytest

from between_jobs.api.skills import SKILL_ALIASES
from between_jobs.engines.generic import provenance
from between_jobs.engines.generic.provenance import (
    Finding,
    check,
    evidence_of,
    link_key,
    tenure_claims,
    usable_job_terms,
)

SOURCE = (
    "Reduced p95 API latency by 40% across 12 services using Python, Kafka and k8s at Acme",
    "Python, Kafka, Kubernetes, PostgreSQL",
    "Staff Engineer",
    "Acme",
)


def _kinds(text: str, *source: str, job_terms: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    evidence = evidence_of(*(source or SOURCE))
    return [(f.kind, f.token) for f in check(text, evidence, job_terms)]


@pytest.mark.parametrize(
    "text",
    [
        # verbatim, and rewordings that keep every fact token
        "Reduced p95 API latency by 40% across 12 services using Python, Kafka and k8s at Acme",
        "Cut p95 API latency 40% across 12 services with Python and Kafka",
        "Reduced latency by 40 percent across twelve services",
        "Reduced Latency By 40% Across 12 Services",  # title case: each word is in the source
        "Cut API latency 40% across 12 services; the work ran on Kubernetes",  # k8s <-> Kubernetes
        "Cut latency 40% for 12 services at Acme with Python",
        "Reduced latency across the services",  # no new token at all
        "Used k8s",  # an alias of a skill the source names in full
    ],
)
def test_a_rewording_that_keeps_every_fact_is_accepted(text: str) -> None:
    assert _kinds(text) == []


@pytest.mark.parametrize(
    ("text", "kind", "token"),
    [
        ("Cut p95 API latency 45% across 12 services", "number", "45%"),
        ("Cut p95 API latency 4% across 12 services", "number", "4%"),
        ("Cut p95 API latency 40x across 12 services", "number", "40x"),
        ("Cut latency 40% across 120 services", "number", "120"),
        ("Cut latency 40% across 12 services in 2019", "number", "2019"),
        ("Saved $1.2M while cutting latency 40%", "number", "1.2M"),
        ("Cut latency 40% for Google", "name", "Google"),
        ("Cut latency 40% using PyTorch", "skill", "PyTorch"),  # in the skills table
        ("Cut latency 40% using Redis", "name", "Redis"),  # not in the table: a capitalized name
        ("Cut latency 40% using the AWS stack", "skill", "AWS"),
        (
            "Cut latency 40% using the INITECH stack",
            "name",
            "INITECH",
        ),  # an acronym not in the table
        ("Cut latency 40% using Docker", "skill", "Docker"),
        ("Cut latency 40% using Terraform", "skill", "Terraform"),
        ("Led a team to cut latency 40%", "scope", "led"),
        ("Managed the effort to cut latency 40%", "scope", "managed"),
        ("Mentored juniors while cutting latency 40%", "scope", "mentored"),
        ("Founded the platform team and cut latency 40%", "scope", "founded"),
        (r"Cut latency 40% \input{/etc/passwd}", "markup", "\\"),
        ("Cut latency 40% {x}", "markup", "{"),
        ("Cut latency 40% see https://evil.example.com", "link", "link"),
        ("Cut latency 40%, mail me at a@evil.example.com", "link", "link"),
        ("Cut latency 40% using gRPC", "name", "gRPC"),  # CamelCase it does not have
        ("Cut latency 40% with a p99 target", "name", "p99"),  # identifier with digits
    ],
)
def test_a_fact_the_source_does_not_have_is_found(text: str, kind: str, token: str) -> None:
    assert (kind, token) in _kinds(text)


def test_a_number_must_match_whole_not_as_a_piece_of_another() -> None:
    evidence = evidence_of("Handled 400 requests and 2 outages")
    assert check("Handled 40 requests", evidence)  # 40 is not in 400
    assert check("Handled 4 outages", evidence)
    assert not check("Handled 400 requests and 2 outages", evidence)


def test_numbers_are_compared_by_value_and_unit_not_spelling() -> None:
    evidence = evidence_of("Grew revenue from $1,200,000 to 2.50x and cut churn 12.0 percent")
    for ok in ("$1200000", "2.5x", "12%", "12 percent"):
        assert not check(f"Reached {ok}", evidence), ok
    for bad in ("2.5", "12", "$1,200", "2.5%"):
        assert check(f"Reached {bad}", evidence), bad


def test_a_plus_in_the_source_supports_the_bare_number_but_not_the_other_way_round() -> None:
    assert not check("Served 10 teams", evidence_of("Served 10+ teams"))
    assert check("Served 10+ teams", evidence_of("Served 10 teams"))


def test_spelled_out_numbers_in_the_source_support_digits_in_the_new_text() -> None:
    assert not check("Trained 12 analysts", evidence_of("Trained twelve analysts"))
    # ... but a number word in the new text is not a claim the check can read
    assert not check("Trained twelve analysts", evidence_of("Trained analysts"))


def test_digits_inside_names_are_names_not_numbers() -> None:
    evidence = evidence_of("Moved storage to S3 and ran k8s clusters")
    assert not check("Moved storage to S3 on k8s", evidence)
    assert ("name", "p95") in [(f.kind, f.token) for f in check("Tuned p95", evidence)]


def test_the_first_word_of_a_sentence_is_not_a_name_but_a_mid_sentence_capital_is() -> None:
    evidence = evidence_of("shipped the importer")
    assert not check("Delivered the importer", evidence)  # an ordinary verb that starts the bullet
    assert [(f.kind, f.token) for f in check("Delivered the importer for Initech", evidence)] == [
        ("name", "Initech")
    ]
    # after a full stop, a capital starts a new sentence
    assert not check("Shipped it. Documented the importer", evidence_of("shipped it documented"))


def test_plurals_do_not_count_as_new_words() -> None:
    evidence = evidence_of("Built the data pipeline for Acme")
    assert not check("Built data Pipelines for Acme", evidence)


def test_acronym_plurals_match_the_acronym() -> None:
    assert not check("Documented the APIs", evidence_of("Documented the API"))
    assert check("Documented the APIs", evidence_of("Documented the service"))


@pytest.mark.parametrize(
    ("source", "text", "supported"),
    [
        ("Deployed to k8s", "Deployed to Kubernetes", True),
        ("Deployed to Kubernetes", "Deployed to k8s", True),
        ("Orchestrated workloads with Terraform", "Wrote infrastructure as code", True),
        ("Wrote infrastructure as code", "Wrote Terraform modules", False),  # product vs concept
        ("Built data pipelines", "Built Airflow DAGs", False),
        ("Built LLM orchestration", "Built LangChain agents", False),
        ("Built agents with LangChain", "Built LLM orchestration", True),
        ("Used postgres daily", "Used PostgreSQL daily", True),
        ("Used psql daily", "Used PostgreSQL daily", True),
    ],
)
def test_skill_spellings_are_one_skill_but_a_product_is_not_its_concept(
    source: str, text: str, supported: bool
) -> None:
    findings = [f for f in check(text, evidence_of(source)) if f.kind in {"skill", "name"}]
    assert (findings == []) is supported, findings


def test_every_product_alias_the_check_treats_as_a_product_is_still_in_the_skills_table() -> None:
    assert set(SKILL_ALIASES) >= provenance._PRODUCT_ALIASES


def test_a_job_term_may_not_be_brought_into_text_whose_source_lacks_it() -> None:
    terms = usable_job_terms(["Kafka", "gRPC", "data mesh", "experience"])
    evidence = evidence_of(*SOURCE)

    assert [(f.kind, f.token) for f in check("Cut latency with a data mesh", evidence, terms)] == [
        ("job_term", "data mesh")
    ]
    assert not check("Processed events on Kafka", evidence, terms)  # the source has Kafka


def test_ordinary_words_are_never_guarded_job_terms() -> None:
    assert usable_job_terms(["experience", "team", "strong", "ab", "Kafka", "kafka"]) == ("kafka",)


def test_a_scope_word_is_allowed_when_the_source_already_claims_that_role() -> None:
    evidence = evidence_of("Team lead for the ingestion squad")
    assert not check("Led the ingestion squad", evidence)
    assert check("Mentored the ingestion squad", evidence)


def test_markup_characters_are_allowed_only_if_the_source_has_them() -> None:
    assert check("Used {braces}", evidence_of("Used braces"))
    assert not check("Used {braces}", evidence_of("Used {braces}"))


def test_text_is_compared_with_its_accents_folded_away() -> None:
    source = evidence_of("Shipped the Z\u00fcrich importer for Acme")

    # the same word, accented or not, on either side
    assert not check("Shipped the Zurich importer for Acme", source)
    assert not check("Shipped the Z\u00fcrich importer for Acme", source)
    assert not check("Shipped the Zurich importer", evidence_of("Shipped the Zurich importer"))
    # an accent is not a way to write a name the source does not have
    assert [(f.kind, f.token) for f in check("Built a cache with R\u00e9dis", source)] == [
        ("name", "Redis")
    ]
    assert [(f.kind, f.token) for f in check("Built a cache with Kubern\u00e9tes", source)] == [
        ("skill", "Kubernetes")
    ]
    # a letter with no ASCII spelling prints as "?", which is not a name
    assert not check("Used \u0420ython", evidence_of("Used Python"))


def test_the_check_does_not_choke_on_odd_input() -> None:
    evidence = evidence_of("")
    for text in ("", " ", "\u0130stanbul", "\x00\x01", "A" * 5000, "1" * 400, "\u202e"):
        assert isinstance(check(text, evidence), list)


def test_what_the_check_cannot_catch_is_written_down_and_really_is_not_caught() -> None:
    """The module docstring names its blind spots. This pins that they are real (so the
    documentation does not drift into claiming more), and that the reader is told."""
    docstring = provenance.__doc__ or ""
    for blind_spot in (
        "overstated verb",
        "scale in words",
        "causal claim",
        "lower case",
        "plain lower-case words",
        "unit or period the source also uses",
        '"over", "more than", "nearly"',
        "ordinary opener",
    ):
        assert blind_spot in docstring, blind_spot

    evidence = evidence_of("Helped with the migration of the billing service")
    for overstated in (
        "Architected the migration of the billing service",
        "Delivered a global, enterprise-scale migration of the billing service",
        "Migrated the billing service, which drove company growth",
        "Migrated the billing service using redis",
        "Migrated the billing service for a unicorn startup",
    ):
        assert check(overstated, evidence) == [], overstated

    assert check("Served over 10K users", evidence_of("Served 10K users")) == []
    assert check("Cut latency by fifty percent", evidence_of("Cut latency by 40%")) == []
    # the source has "hours" somewhere, so a swap to hours is not seen
    assert check("Waited 40 hours", evidence_of("Waited 40 minutes, then 10 hours")) == []
    # a name that looks like an ordinary opener ("-ing") is let through at the start of a sentence
    assert check("Boeing adopted it", evidence_of("adopted it")) == []
    # a statement about a company in plain words
    assert check("Globex is the industry leader in streaming", evidence_of("Globex")) == []


def test_a_finding_describes_itself_against_the_place_it_should_have_come_from() -> None:
    assert (
        Finding("number", "45%").describe("the source bullet(s)")
        == 'the number "45%" is not in the source bullet(s)'
    )
    assert "job posting" in Finding("job_term", "gRPC").describe("your profile")
    assert "letter never states" in Finding("policy", "work authorization").describe("x")


# -- present-tense roles, ownership and seniority ------------------------------------------------

_RUNBOOK = "Wrote the on-call runbook used by a team of 8 engineers"


@pytest.mark.parametrize(
    "verb",
    [
        *(
            "Lead",
            "Manage",
            "Mentor",
            "Oversee",
            "Direct",
            "Own",
            "Owned",
            "Supervise",
            "Spearhead",
        ),
        *("Coach", "Owns", "Manages", "Oversees", "Head", "Leads", "Directs", "Mentors"),
    ],
)
def test_a_role_verb_in_any_tense_that_the_source_does_not_claim_is_found(verb: str) -> None:
    findings = _kinds(f"{verb} the on-call runbook used by a team of 8 engineers", _RUNBOOK)

    assert ("scope", verb.lower()) in findings, findings
    assert not [f for f in findings if f[0] == "number"], findings


@pytest.mark.parametrize(
    "text",
    [
        "I manage the on-call runbook used by a team of 8 engineers",  # a letter's voice
        "Wrote the runbook and lead a team of 8 engineers",
        "Wrote the runbook to lead a team of 8 engineers",
        "Wrote the runbook, and own it for a team of 8 engineers",
    ],
)
def test_a_role_word_that_acts_as_a_verb_is_found_after_and_or_to_too(text: str) -> None:
    assert "scope" in [kind for kind, _ in _kinds(text, _RUNBOOK)], text


@pytest.mark.parametrize(
    "text",
    [
        "Reduced lead time by 40%",
        "Built a direct integration",
        "Shipped on their own cadence",
        "Cut the head count of the queue",
        "Wrote a lead scoring model",
        "Tracked leads in the CRM",
    ],
)
def test_a_word_that_is_also_an_ordinary_noun_is_not_a_claim_when_it_is_not_acting_as_a_verb(
    text: str,
) -> None:
    source = "Reduced time by 40%, built an integration, shipped on a cadence, wrote a model"
    assert [f for f in _kinds(text, source) if f[0] == "scope"] == [], text


@pytest.mark.parametrize("source", ["Lead of the platform team", "Manage the platform team"])
@pytest.mark.parametrize("new", ["Led the team", "Manage the team", "Oversee the team"])
def test_a_source_that_claims_a_role_supports_every_word_of_that_kind(
    source: str, new: str
) -> None:
    assert not [f for f in check(new, evidence_of(source)) if f.kind == "scope"]


def test_a_leadership_word_in_the_source_does_not_support_mentoring_or_founding() -> None:
    evidence = evidence_of("Led the platform team")
    assert check("Mentor the platform team", evidence)
    assert check("Founded the platform team", evidence)


# The words written out here, not read back from the module: taking one out of the module's
# sets has to fail a test, and the parametrisation below would shrink with the set.
_LEADERSHIP = [
    "led",
    "leading",
    "manage",
    "manages",
    "managed",
    "managing",
    "directs",
    "directed",
    "directing",
    "headed",
    "heading",
    "oversee",
    "oversees",
    "oversaw",
    "overseeing",
    "supervise",
    "supervises",
    "supervised",
    "supervising",
    "spearhead",
    "spearheads",
    "spearheaded",
    "spearheading",
    "owns",
    "owned",
    "owning",
    "ownership",
    "owner",
]
_MENTORING = [
    "mentored",
    "mentoring",
    "mentor",
    "mentors",
    "coached",
    "coaching",
    "coach",
    "coaches",
]
_FOUNDING = ["founded", "cofounded", "founder", "cofounder"]


@pytest.mark.parametrize("verb", _LEADERSHIP)
def test_every_leadership_word_is_found_when_the_source_has_none(verb: str) -> None:
    assert verb in provenance._LEADERSHIP_NEW
    assert ("scope", verb) in _kinds(f"The {verb} was clear", "The importer was clear")


@pytest.mark.parametrize("verb", [*_MENTORING, *_FOUNDING])
def test_every_mentoring_and_founding_word_is_found_when_the_source_has_none(verb: str) -> None:
    assert verb in provenance._MENTORING_NEW | provenance._FOUNDING_NEW
    assert ("scope", verb) in _kinds(f"The {verb} was clear", "The importer was clear")


_LEVEL_CLAIMS = [
    ("An expert engineer who builds data pipelines", "expert"),
    ("Principal engineer on the request batching", "principal"),
    ("Built pipelines as the team's go-to expert", "expert"),
    ("A veteran of data pipelines", "veteran"),
    ("Staff engineer building data pipelines", "staff"),
    ("An engineer with lead-level skills", "lead"),
    ("A lead developer of the importer", "lead"),
    ("A senior engineer on the importer", "senior"),
]


@pytest.mark.parametrize(("text", "word"), _LEVEL_CLAIMS)
def test_a_word_that_claims_a_level_is_found_unless_the_source_uses_it(
    text: str, word: str
) -> None:
    source = "Built the data importer and the pipelines"

    assert "level" in [kind for kind, _ in _kinds(text, source)], text
    with_word = evidence_of(source, f"{word} engineer")
    assert [f for f in check(text, with_word) if f.kind == "level"] == [], text


@pytest.mark.parametrize(
    "text", ["Reduced lead time by 40%", "Trained support staff", "Wrote the staff handbook"]
)
def test_the_ordinary_uses_of_staff_and_lead_do_not_claim_a_level(text: str) -> None:
    source = "Reduced time, trained people, wrote a handbook"
    assert [f for f in _kinds(text, source) if f[0] == "level"] == []


# -- units, periods, currency and "+" --------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "new", "token"),
    [
        ("Saved $120K per year", "Saved $120K per month", "month"),
        ("Handled 2M events per day", "Handled 2M events per second", "second"),
        ("Cut 40 minutes", "Cut 40 hours", "hours"),
        ("Cut 200 ms", "Cut 200 seconds", "seconds"),
        ("Waited 3 weeks", "Waited 3 years", "years"),
        ("Stored 5 GB", "Stored 5 TB", "tb"),
        ("Saved EUR 5M", "Saved $5M", "$"),
        ("Saved 5M", "Saved $5M", "$"),
        ("Saved 5M", "Saved 5M dollars", "dollars"),
    ],
)
def test_a_unit_period_or_currency_the_source_does_not_use_is_found(
    source: str, new: str, token: str
) -> None:
    assert ("unit", token) in _kinds(new, source)


@pytest.mark.parametrize(
    ("source", "new"),
    [
        ("Waited 5 yr", "Waited 5 years"),
        ("Waited 5 years", "Waited 5 yr"),
        ("Cut 200 ms", "Cut 200 milliseconds"),
        ("Cut 3 hrs", "Cut 3 hours"),
        ("Ran jobs per day", "Ran jobs daily"),
        ("Ran jobs every week", "Ran jobs weekly"),
        ("Saved $120K annual", "Saved $120K per year"),
        ("Stored 2 GB", "Stored 2 gb"),
        ("Handled 2M events per second", "Handled 2M events each second"),
        ("Saved $5M", "Saved $5M"),
        ("Saved USD 5M", "Saved 5M dollars"),
    ],
)
def test_a_unit_the_source_has_under_another_spelling_is_not_found(source: str, new: str) -> None:
    assert [f for f in check(new, evidence_of(source)) if f.kind == "unit"] == [], (source, new)


@pytest.mark.parametrize(
    "text",
    [
        "Over the years I have built pipelines",
        "In recent months I shipped it",
        "This week I joined",
        "A day in the life of the importer",
        "Worked on it for years",
        "Handled day-to-day operations",
        "Took the second iteration live",
        "Ran a second pass",
    ],
)
def test_ordinary_phrases_that_contain_a_unit_word_are_not_a_unit_claim(text: str) -> None:
    assert [
        f for f in check(text, evidence_of("Handled operations, took it live")) if f.kind == "unit"
    ] == []


@pytest.mark.parametrize(
    ("source", "new"),
    [("10K", "10K+"), ("2M", "2M+"), ("40%", "40%+"), ("3x", "3x+"), ("10", "10+")],
)
def test_a_plus_after_a_number_claims_more_than_the_bare_number_in_every_unit(
    source: str, new: str
) -> None:
    assert check(f"Reached {new} users", evidence_of(f"Reached {source} users"))
    # and the other way round: the source's "+" supports the bare number
    assert not check(f"Reached {source} users", evidence_of(f"Reached {new} users"))


# -- the languages and names a word rule cannot see -------------------------------------------

_TOOLS = "Built data tools in Python"


@pytest.mark.parametrize(
    "text",
    [
        "Built data tools in Python and R",
        "Built data tools in Python and C",
        "Built data tools in Python and C#",
        "Built data tools in Python and C++",
        "Built data tools in Python and F#",
    ],
)
def test_a_language_named_by_a_letter_or_a_symbol_is_a_name_like_any_other(text: str) -> None:
    assert [kind for kind, _ in _kinds(text, _TOOLS)] == ["name"]


def test_a_symbol_language_is_supported_by_itself_and_not_by_its_plain_letter() -> None:
    assert not check("Built tools in C++", evidence_of("Wrote C++ code"))
    assert check("Built tools in C++", evidence_of("Wrote C code"))
    assert not check("Built tools in C", evidence_of("Wrote C code"))
    assert not check("Built tools in C#", evidence_of("Wrote c# code"))


@pytest.mark.parametrize(
    "text",
    ["Tuned Kafka I/O", "Ran A/B tests on Kafka", "Read X-ray data with Kafka", "I built Kafka"],
)
def test_one_letter_words_and_joined_letters_are_not_names(text: str) -> None:
    findings = check(text, evidence_of("Tuned Kafka, ran tests, read data, I built it"))

    assert [f.token for f in findings if f.kind == "name" and len(f.token) == 1] == []


# -- names at the start of a sentence ------------------------------------------------------------

_SHIPPED = "Built a pipeline for event data"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Stripe engineers adopted the pipeline for event data", "Stripe"),
        ("Netflix relies on the pipeline for event data", "Netflix"),
        ("Netflix-style pipeline for event data", "Netflix"),
        ("Built a pipeline for event data. Google scale was the goal", "Google"),
        ("Built a pipeline for event data: Stripe webhooks", "Stripe"),
        ("Built a cache layer: Redis for sessions", "Redis"),
        ("Built a pipeline; Google scale", "Google"),
        ("Initech shipped it", "Initech"),
        ("Berlin office adopted it", "Berlin"),
    ],
)
def test_an_invented_name_is_found_where_a_sentence_starts_too(text: str, name: str) -> None:
    assert ("name", name) in _kinds(text, _SHIPPED)


@pytest.mark.parametrize(
    "text",
    [
        "Delivered the importer",
        "Build the importer",
        "Builds the importer",
        "Spearheaded the importer",
        "Developing the importer",
        "Quickly shipped the importer",
        "Collaboration on the importer",
        "Led the importer. Documented it",
        "Result: Improved the importer",
        "Built it; Improved the importer",
        "Senior engineers reviewed the importer",
        "The importer shipped. Thanks to the team",
        "Cross-functional work on the importer",
        "Full-stack importer",
        "Data importer",
        "I built the importer",
        "My importer shipped",
        "Having shipped the importer",
        "Over two weeks the importer shipped",
        "Wrote the importer",
        "Cut the importer",
    ],
)
def test_the_ordinary_ways_to_start_a_sentence_are_not_names(text: str) -> None:
    assert [f for f in _kinds(text, "shipped it, result, the importer") if f[0] == "name"] == [], (
        text
    )


def test_a_sentence_opener_that_is_in_the_source_is_never_a_name() -> None:
    assert not check("Stripe adopted it", evidence_of("Worked with Stripe and adopted it"))


# -- links and addresses -------------------------------------------------------------------------

_SHIPPER = "Built a log shipper for event data"


@pytest.mark.parametrize(
    "tail",
    [
        ", details at evil.com",
        ", open sourced at github.com/acme/logship",
        ", see bit.ly/3xYz",
        ", details at evil.com/apply",
        ", see www.evil.example",
        ", mail me at a@evil.example.com",
        ", see https://evil.example.com",
        ", see sub.domain.evil.co.uk/path",
    ],
)
def test_an_address_the_source_does_not_have_is_found_with_or_without_a_scheme(tail: str) -> None:
    assert ("link", "link") in _kinds(_SHIPPER + tail, _SHIPPER)


@pytest.mark.parametrize(
    "text",
    [
        "Used Node.js and Next.js with Vue.js",
        "Wrote main.py, setup.py and run.sh",
        "Read README.md and CHANGELOG.md",
        "Shipped it, e.g. for the U.S. market, i.e. quickly",
        "Moved to v1.2.3 and cut 4.5% of cost",
        "Ran server.js on port 8080",
    ],
)
def test_file_and_framework_names_are_not_addresses(text: str) -> None:
    assert ("link", "link") not in _kinds(text, "x")


def test_an_address_the_source_has_is_allowed_in_any_spelling_but_only_that_address() -> None:
    evidence = evidence_of("Hosted the docs at https://www.Acme.io/docs/")
    assert not [f for f in check("Hosted the docs at acme.io/docs", evidence) if f.kind == "link"]
    assert not [f for f in check("See WWW.ACME.IO/docs.", evidence) if f.kind == "link"]
    # one address in the source does not license every address
    assert ("link", "link") in [(f.kind, f.token) for f in check("See evil.com/apply", evidence)]


def test_link_keys_ignore_scheme_www_case_and_closing_punctuation() -> None:
    assert link_key("HTTPS://WWW.Acme.io/docs/),") == "acme.io/docs"
    assert link_key("a@B.example.com.") == "a@b.example.com"


def test_the_link_search_stays_fast_on_a_long_unbroken_token() -> None:
    long_token = "a" * 40000
    evidence = evidence_of(long_token)
    started = time.perf_counter()
    findings = check("Built it, see evil.com", evidence)
    check(long_token + " see a@evil.example.com", evidence)
    evidence_of(long_token + "@" + long_token + "." + long_token)
    elapsed = time.perf_counter() - started

    assert ("link", "link") in [(f.kind, f.token) for f in findings]
    assert elapsed < 2.0, elapsed


# -- markup ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("char", list(provenance._MARKUP_CHARACTERS))
def test_every_markup_character_is_found_unless_the_source_has_it(char: str) -> None:
    assert ("markup", char) in _kinds(f"Used {char}x", "Used x")
    assert ("markup", char) not in _kinds(f"Used {char}x", f"Used {char}x")


def test_the_markup_characters_are_the_ones_documented() -> None:
    assert set(provenance._MARKUP_CHARACTERS) == set("\\{}<>^~`|*_")
    for name in ("backslash", "braces", "angle brackets", "caret", "tilde", "backtick", "pipe"):
        assert name in (provenance.__doc__ or "")


def test_emphasis_marks_are_found_but_a_source_with_them_may_use_them() -> None:
    assert ("markup", "*") in _kinds("**Reduced** latency", "Reduced latency")
    assert ("markup", "_") in _kinds("_Reduced_ latency", "Reduced latency")
    assert not check("Used snake_case names", evidence_of("Used snake_case names"))


# -- the unit words and suffixes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "text", "supported"),
    [
        ("5 million", "5m", True),
        ("5m", "5 million", True),
        ("5 thousand", "5k", True),
        ("5k", "5 thousand", True),
        ("5 billion", "5b", True),
        ("5 billion", "5bn", True),
        ("5bn", "5 billion", True),
        ("5mm", "5m", True),
        ("40 percent", "40%", True),
        ("5 million", "5k", False),
        ("5 thousand", "5m", False),
        ("5 million", "5b", False),
        ("5 billion", "5m", False),
        ("5bn", "5m", False),
        ("5mm", "5k", False),
    ],
)
def test_number_suffixes_and_their_spelled_out_forms_are_one_unit_each(
    source: str, text: str, supported: bool
) -> None:
    findings = check(f"Grew to {text} users", evidence_of(f"Grew to {source} users"))
    assert (findings == []) is supported, findings


@pytest.mark.parametrize(
    "address", ["https://a.example.com", "www.example.com", "WWW.Example.com", "a@b.example.com"]
)
def test_an_address_is_found_unless_the_source_has_that_address(address: str) -> None:
    assert ("link", "link") in _kinds(f"Built it, see {address}", "Built it")
    assert ("link", "link") not in _kinds(f"Built it, see {address}", f"Built it, see {address}")


# -- the smaller rules ----------------------------------------------------------------------------


def test_plural_and_singular_forms_of_a_word_are_one_word() -> None:
    assert check("Reviewed Policies", evidence_of("Reviewed policy")) == []
    assert check("Reviewed Policy", evidence_of("Reviewed policies")) == []
    assert check("Ran Business", evidence_of("Ran businesses")) == []
    assert check("Ran Businesses", evidence_of("Ran business")) == []


def test_an_acronym_plural_of_any_length_matches_the_acronym() -> None:
    assert check("Shipped AIs", evidence_of("Shipped AI")) == []
    assert check("Shipped AIs", evidence_of("Shipped the service"))


def test_a_dozen_is_twelve_and_nothing_else() -> None:
    assert check("Shipped 12 releases", evidence_of("Shipped a dozen releases")) == []
    assert [(f.kind, f.token) for f in check("Shipped 13 releases", evidence_of("a dozen"))] == [
        ("number", "13")
    ]


def test_a_name_that_repeats_is_reported_once() -> None:
    assert [f.kind for f in check("Cut 45% and 45% again", evidence_of("Cut latency"))] == [
        "number"
    ]
    found = check("Used Redis with Redis", evidence_of("Used the cache"))
    assert [f.token for f in found] == ["Redis"]


def test_job_terms_are_clipped_in_length_and_in_number() -> None:
    assert usable_job_terms(["x" * 40, "x" * 41]) == ("x" * 40,)
    terms = [f"term{i:03d}" for i in range(60)]
    assert usable_job_terms(terms) == tuple(terms[:40])
    assert usable_job_terms(terms, limit=5) == tuple(terms[:5])


_ORDINARY = [
    "experience",
    "team",
    "teams",
    "work",
    "working",
    "skills",
    "skill",
    "ability",
    "strong",
    "knowledge",
    "years",
    "year",
    "role",
    "company",
    "environment",
    "fast",
    "paced",
    "good",
    "great",
    "excellent",
    "required",
    "preferred",
    "plus",
    "senior",
    "junior",
]


@pytest.mark.parametrize("word", _ORDINARY)
def test_every_ordinary_word_is_left_out_of_the_guarded_job_terms(word: str) -> None:
    assert word in provenance._GENERIC_JOB_TERMS
    assert usable_job_terms([word, word.capitalize()]) == ()


# -- years --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "spelled"),
    [
        ("I bring 15 years of experience", 15.0, False),
        ("with 10+ years building systems", 10.0, False),
        ("twelve years in data", 12.0, True),
        ("twenty-five years", 25.0, True),
        ("over a decade of experience", 10.0, True),
        ("two decades in the field", 20.0, True),
        ("decades of experience", 20.0, True),
        ("one year at Acme", 1.0, True),
        ("20 yrs", 20.0, False),
    ],
)
def test_a_stated_length_of_time_is_read_as_a_number_of_years(
    text: str, value: float, spelled: bool
) -> None:
    (claim,) = tenure_claims(text)
    assert (claim.value, claim.spelled) == (value, spelled)


@pytest.mark.parametrize(
    "text",
    [
        "Shipped 12 services",
        "Grew 40% per year",
        "Cut cost in 2019",
        "Worked for years",
        "Several years at Acme",
        "Every year the team grew",
        "Led 5 teams",
    ],
)
def test_text_that_states_no_number_of_years_has_no_tenure_claim(text: str) -> None:
    assert tenure_claims(text) == []
