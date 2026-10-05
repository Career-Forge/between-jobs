"""Tests for skill canonicalization v0 (Sprint 3.3c)."""

from __future__ import annotations

from typing import Any

import pytest

from between_jobs.api.positioning_brief import gap_candidates, strength_candidates
from between_jobs.api.skills import (
    AMBIGUOUS_TERMS,
    EXACT_ONLY_SKILLS,
    GAP_ADJACENCY,
    SKILL_ALIASES,
    SKILL_CATEGORIES,
    SKILL_FAMILIES,
    _has_family_sibling,
    canonicalize_skill,
    classify_skill,
    find_gap_bridge,
)

_PROFILE = {
    "skills": {
        "programming": ["Python"],
        "ai_ml": ["PyTorch"],
        "cloud_devops": [],
        "data_mlops": [],
        "tools": [],
        "other": [],
    },
    "experience": [
        {
            "bullets": ["Built a RAG pipeline with vector search."],
            "skills": ["FastAPI"],
        }
    ],
    "projects": [
        {"bullets": ["Deployed with Docker."], "tech": ["Kubernetes"]},
    ],
}


def test_canonicalize_skill_maps_known_alias() -> None:
    assert canonicalize_skill("LangChain") == "LLM Orchestration"
    assert canonicalize_skill("genai") == "Generative AI"


def test_canonicalize_skill_falls_back_to_input_for_unknown_skill() -> None:
    assert canonicalize_skill("Rust") == "Rust"
    assert canonicalize_skill("  Rust  ") == "Rust"


def test_classify_skill_verified_when_explicitly_declared() -> None:
    assert classify_skill("Python", _PROFILE) == "verified"


def test_classify_skill_verified_via_alias_match() -> None:
    # profile has "PyTorch" declared; requesting the same thing under a
    # different raw phrase should still resolve to verified via the
    # shared canonical name.
    assert classify_skill("pytorch", _PROFILE) == "verified"


def test_classify_skill_supported_when_only_in_evidence_text() -> None:
    # "vector search" appears in a bullet, not in the declared skills list.
    assert classify_skill("vector search", _PROFILE) == "supported"


def test_classify_skill_supported_via_project_tech() -> None:
    assert classify_skill("Kubernetes", _PROFILE) == "supported"


def test_classify_skill_adjacent_when_same_category_but_not_evidenced() -> None:
    # "tensorflow" is ai_ml, same category as declared "PyTorch", but
    # never mentioned anywhere -- adjacent, not supported/verified.
    assert classify_skill("TensorFlow", _PROFILE) == "adjacent"


def test_classify_skill_unsupported_when_no_connection_at_all() -> None:
    # "AWS" is cloud_devops -- profile has nothing in that category at all.
    assert classify_skill("AWS", _PROFILE) == "unsupported"


def test_classify_skill_unsupported_for_an_unrecognized_skill_with_no_evidence() -> None:
    assert classify_skill("Cobol", _PROFILE) == "unsupported"


def test_classify_skill_handles_empty_profile() -> None:
    empty_profile: dict[str, object] = {}
    assert classify_skill("Python", empty_profile) == "unsupported"


_CV_PROFILE = {
    "skills": {
        "programming": ["Python"],
        "ai_ml": [],
        "cloud_devops": [],
        "data_mlops": [],
        "tools": [],
        "other": [],
    },
    "experience": [{"bullets": ["Preprocessed images with OpenCV before training."]}],
    "projects": [],
}


def test_find_gap_bridge_finds_a_real_bridge_in_evidence_text() -> None:
    # "computer vision" itself is nowhere in _CV_PROFILE, but "opencv" is --
    # a genuine, curated, indirect connection, not a literal keyword match.
    assert find_gap_bridge(["Computer Vision"], _CV_PROFILE) == "opencv"


def test_find_gap_bridge_is_case_insensitive_on_the_keyword() -> None:
    assert find_gap_bridge(["COMPUTER VISION"], _CV_PROFILE) == "opencv"


def test_find_gap_bridge_returns_none_when_no_curated_entry_exists() -> None:
    assert find_gap_bridge(["Cobol Mainframes"], _CV_PROFILE) is None


def test_find_gap_bridge_returns_none_when_entry_exists_but_no_bridge_term_present() -> None:
    # "kubernetes" has a curated entry, but _PROFILE (the base fixture) has
    # neither Kubernetes-adjacent evidence nor the JD term itself.
    empty_profile: dict[str, object] = {"skills": {}, "experience": [], "projects": []}
    assert find_gap_bridge(["Kubernetes"], empty_profile) is None


def test_find_gap_bridge_checks_every_keyword_in_the_cluster() -> None:
    # First keyword has no entry; second does, and the profile has its bridge.
    assert find_gap_bridge(["Cobol", "Computer Vision"], _CV_PROFILE) == "opencv"


def test_find_gap_bridge_handles_empty_keywords_and_empty_profile() -> None:
    assert find_gap_bridge([], _CV_PROFILE) is None
    assert find_gap_bridge(["Computer Vision"], {}) is None


# -- skill families ---------------------------------------------------------------------------


def _profile(
    declared: dict[str, list[str]] | None = None,
    bullets: list[str] | None = None,
    tech: list[str] | None = None,
) -> dict[str, Any]:
    skills: dict[str, list[str]] = {category: [] for category in SKILL_CATEGORIES}
    skills.update(declared or {})
    return {
        "skills": skills,
        "experience": [{"bullets": bullets or [], "skills": tech or []}],
        "projects": [],
    }


# (family, requested skill, a sibling, the sibling's category, a sentence that evidences it)
_FAMILY_CASES = [
    ("BI tools", "Tableau", "Looker", "tools", "Built weekly dashboards in Looker."),
    (
        "browser test automation",
        "Playwright",
        "Selenium",
        "tools",
        "Wrote the end-to-end suite with Selenium.",
    ),
    ("load testing", "Gatling", "JMeter", "tools", "Ran the nightly load test in JMeter."),
    (
        "front-end frameworks",
        "Vue",
        "Next.js",
        "tools",
        "Built the marketing site in Next.js.",
    ),
    (
        "cloud data warehouses",
        "BigQuery",
        "Snowflake",
        "data_mlops",
        "Modeled the finance marts in Snowflake.",
    ),
    (
        "pipeline orchestration",
        "Dagster",
        "Airflow",
        "data_mlops",
        "Scheduled the nightly loads with Airflow.",
    ),
    (
        "data processing and streaming",
        "Kafka",
        "Spark",
        "data_mlops",
        "Ran the nightly batch jobs on Spark.",
    ),
    ("JVM languages", "Kotlin", "Java", "programming", "Wrote the billing service in Java."),
    (
        "infrastructure as code",
        "Pulumi",
        "Terraform",
        "cloud_devops",
        "Provisioned the VPCs with Terraform.",
    ),
    (
        "observability",
        "New Relic",
        "Datadog",
        "cloud_devops",
        "Built the on-call board in Datadog.",
    ),
    ("ML frameworks", "JAX", "PyTorch", "ai_ml", "Trained the ranking model in PyTorch."),
]
_FAMILY_IDS = [case[0] for case in _FAMILY_CASES]


@pytest.mark.parametrize(
    ("family", "requested", "sibling", "category", "sentence"), _FAMILY_CASES, ids=_FAMILY_IDS
)
def test_a_declared_sibling_makes_a_family_skill_adjacent(
    family: str, requested: str, sibling: str, category: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile({category: [sibling]})) == "adjacent"


@pytest.mark.parametrize(
    ("family", "requested", "sibling", "category", "sentence"), _FAMILY_CASES, ids=_FAMILY_IDS
)
def test_a_sibling_that_is_only_in_the_evidence_makes_a_family_skill_adjacent(
    family: str, requested: str, sibling: str, category: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile(bullets=[sentence])) == "adjacent"
    assert classify_skill(requested, _profile(tech=[sibling])) == "adjacent"


@pytest.mark.parametrize(
    ("family", "requested", "sibling", "category", "sentence"), _FAMILY_CASES, ids=_FAMILY_IDS
)
def test_unrelated_skills_in_the_same_category_do_not_make_a_family_skill_adjacent(
    family: str, requested: str, sibling: str, category: str, sentence: str
) -> None:
    """The old rule (any other skill in the same category) would call all of these adjacent: a
    profile with only Git would make Tableau "adjacent"."""
    profile = _profile({category: ["Git", "Jira", "Linux"], "other": ["Mentoring"]})

    assert classify_skill(requested, profile) == "unsupported"


@pytest.mark.parametrize(
    ("family", "requested", "sibling", "category", "sentence"), _FAMILY_CASES, ids=_FAMILY_IDS
)
def test_verified_still_wins_over_adjacent(
    family: str, requested: str, sibling: str, category: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile({category: [requested, sibling]})) == "verified"


@pytest.mark.parametrize(
    ("family", "requested", "sibling", "category", "sentence"), _FAMILY_CASES, ids=_FAMILY_IDS
)
def test_supported_still_wins_over_adjacent(
    family: str, requested: str, sibling: str, category: str, sentence: str
) -> None:
    profile = _profile(
        {category: [sibling]}, bullets=[f"Used {requested} for the quarterly reporting work."]
    )

    assert classify_skill(requested, profile) == "supported"


def test_a_sibling_must_be_a_different_skill_not_the_same_one_under_another_spelling() -> None:
    # Power BI under three spellings is still one skill: requesting it is verified, and no
    # sibling is needed for that.
    assert classify_skill("Power BI", _profile({"tools": ["PowerBI"]})) == "verified"
    assert classify_skill("powerbi", _profile({"tools": ["Power-BI"]})) == "verified"


def test_a_skill_is_never_its_own_sibling() -> None:
    """The sibling check skips the requested skill's own key, so its alias spellings never count
    as a different family member. (A requested skill's own spellings are caught earlier, as
    verified or supported, so this is the guard's own test, not reached through
    `classify_skill`.)"""
    assert not _has_family_sibling("Power BI", "powerbi\npower-bi\npower bi", "built powerbi")
    assert not _has_family_sibling("Vue", "vuejs\nvue.js", "")
    assert _has_family_sibling("Power BI", "looker", "")
    assert _has_family_sibling("Power BI", "powerbi", "built dashboards in tableau")


def test_a_sibling_is_found_by_whole_word_only() -> None:
    # "java" is inside "javascript" and "react" is inside "reaction"; neither is the sibling.
    assert classify_skill("Kotlin", _profile({"programming": ["JavaScript"]})) == "unsupported"
    assert classify_skill("Vue", _profile(bullets=["Cut reaction time by 40%."])) == "unsupported"
    assert (
        classify_skill("Dagster", _profile(bullets=["Fixed an adbt-style typo."])) == "unsupported"
    )


def test_a_sibling_spelled_with_an_alias_counts() -> None:
    assert classify_skill("Tableau", _profile(bullets=["Dashboards in Microsoft power-bi"])) == (
        "adjacent"
    )
    assert classify_skill("Vue", _profile({"tools": ["React.js"]})) == "adjacent"
    assert classify_skill("Pulumi", _profile(tech=["terraform"])) == "adjacent"
    assert classify_skill("JAX", _profile({"ai_ml": ["sklearn"]})) == "adjacent"


def test_scikit_learn_stays_a_bridge_to_deep_learning_frameworks() -> None:
    """A profile with classical ML and no deep-learning framework has always been "adjacent" to
    PyTorch; putting PyTorch in a family must not take that away."""
    assert classify_skill("PyTorch", _profile({"ai_ml": ["scikit-learn"]})) == "adjacent"
    assert classify_skill("TensorFlow", _profile({"ai_ml": ["Regression"]})) == "unsupported"


def test_terraform_and_iac_are_not_the_same_thing_for_adjacency() -> None:
    docker_only = _profile({"cloud_devops": ["Docker"]})

    # the concept keeps the original category rule ...
    assert classify_skill("IaC", docker_only) == "adjacent"
    # ... the product is a family skill and needs a real sibling
    assert classify_skill("Terraform", docker_only) == "unsupported"
    assert classify_skill("Terraform", _profile({"cloud_devops": ["Pulumi"]})) == "adjacent"
    # Terraform declared is IaC evidence, exactly as before (same canonical name)
    assert classify_skill("IaC", _profile({"cloud_devops": ["Terraform"]})) == "verified"


def test_airflow_is_a_family_skill_but_etl_keeps_the_category_rule() -> None:
    spark_only = _profile({"data_mlops": ["Spark"]})

    assert classify_skill("ETL", spark_only) == "adjacent"
    assert classify_skill("Airflow", spark_only) == "unsupported"
    assert classify_skill("Airflow", _profile({"data_mlops": ["dbt"]})) == "adjacent"
    assert classify_skill("dbt", _profile({"data_mlops": ["Airflow"]})) == "adjacent"


def test_skills_without_a_family_keep_the_category_rule() -> None:
    # LangChain is in no family: any other skill in the category is adjacent
    assert classify_skill("LangChain", _profile({"ai_ml": ["Regression"]})) == "adjacent"
    # Spark and Kafka are family members now: adjacent through each other, not through the
    # category (see the regression tests for newly aliased skills)
    assert classify_skill("Spark", _profile({"data_mlops": ["Kafka"]})) == "adjacent"
    assert classify_skill("Spark", _profile({"programming": ["Python"]})) == "unsupported"
    # and an unknown skill has no category to be adjacent through
    assert classify_skill("Appium", _profile({"tools": ["Selenium"]})) == "unsupported"


def test_python_and_go_stay_unaliased_so_they_stay_unsupported_without_evidence() -> None:
    assert "python" not in SKILL_ALIASES
    assert "go" not in SKILL_ALIASES
    assert classify_skill("Go", _profile({"programming": ["Java"]})) == "unsupported"


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("power-bi", "Power BI"),
        ("PowerBI", "Power BI"),
        ("ms excel", "Excel"),
        ("big query", "BigQuery"),
        ("nextjs", "Next.js"),
        ("next js", "Next.js"),
        ("vue.js", "Vue"),
        ("ReactJS", "React"),
        ("csharp", "C#"),
        ("c sharp", "C#"),
        ("pyspark", "Spark"),
        ("newrelic", "New Relic"),
        ("aws cloudformation", "CloudFormation"),
        ("dbt core", "dbt"),
        ("JAX", "JAX"),
    ],
)
def test_new_aliases_canonicalize(raw: str, canonical: str) -> None:
    assert canonicalize_skill(raw) == canonical


def test_every_alias_has_one_of_the_six_categories() -> None:
    assert {category for _, category in SKILL_ALIASES.values()} <= set(SKILL_CATEGORIES)


def test_every_family_key_is_a_known_skill_and_every_family_has_siblings() -> None:
    canonical_names = {canonical for canonical, _ in SKILL_ALIASES.values()}
    for key in SKILL_FAMILIES:
        assert key in canonical_names or key in SKILL_ALIASES, key
    families: dict[str, list[str]] = {}
    for key, family in SKILL_FAMILIES.items():
        families.setdefault(family, []).append(key)
    assert len(families) == 11
    assert all(len(members) >= 2 for members in families.values())


# -- gap bridges ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("jd_term", "bridge"),
    [
        ("Tableau", "looker"),
        ("Power BI", "tableau"),
        ("Selenium", "playwright"),
        ("Cypress", "selenium"),
        ("React", "next.js"),
        ("Snowflake", "redshift"),
        ("Airflow", "dagster"),
        ("Java", "kotlin"),
        ("Terraform", "pulumi"),
        ("Datadog", "grafana"),
        ("JMeter", "gatling"),
    ],
)
def test_the_new_gap_bridges_are_found_in_declared_skills_or_evidence(
    jd_term: str, bridge: str
) -> None:
    assert find_gap_bridge([jd_term], _profile(tech=[bridge])) == bridge
    assert find_gap_bridge([jd_term], _profile(bullets=[f"Worked with {bridge} daily."])) == bridge


def test_the_new_gap_bridges_are_different_words_from_their_key() -> None:
    for key, bridges in GAP_ADJACENCY.items():
        assert key not in bridges
        assert bridges, key


def test_a_short_bridge_term_does_not_match_inside_an_ordinary_word() -> None:
    # "scala" is in "scalable", "angular" is in "triangular": neither is a bridge to Java/React
    assert find_gap_bridge(["Java"], _profile(bullets=["Built scalable services."])) is None
    assert find_gap_bridge(["React"], _profile(bullets=["Used triangular meshes."])) is None
    # both are also ordinary words, so only a technology list can name them as a bridge
    assert find_gap_bridge(["Java"], _profile(tech=["Scala"])) == "scala"
    assert find_gap_bridge(["React"], _profile(tech=["Angular"])) == "angular"
    assert find_gap_bridge(["Java"], _profile({"programming": ["Scala"]})) == "scala"


def test_the_existing_substring_bridges_still_find_inflected_forms() -> None:
    # "container" has always matched "containerized"; the whole-word rule is only for the
    # short terms listed explicitly.
    assert find_gap_bridge(["Kubernetes"], _profile(bullets=["Shipped containerized apps."])) == (
        "container"
    )


# -- skills that are aliased but belong to no family -------------------------------------------


def test_the_exact_only_skills_are_the_ones_aliased_only_for_their_spellings() -> None:
    assert set(EXACT_ONLY_SKILLS) == {"SQL", "Excel", "C#"}
    for name in EXACT_ONLY_SKILLS:
        assert name not in SKILL_FAMILIES


@pytest.mark.parametrize(
    ("requested", "profile_skills"),
    [
        # a Git-only profile must not make Excel adjacent (and neither must a Jira one)
        ("Excel", {"tools": ["Git", "Jira"]}),
        ("MS Excel", {"tools": ["Git"]}),
        # a Python-only profile must not make SQL or C# adjacent
        ("SQL", {"programming": ["Python"]}),
        ("C#", {"programming": ["Python"]}),
        ("csharp", {"programming": ["Python", "Go"]}),
        ("c sharp", {"programming": ["Python"]}),
        # a PostgreSQL-only profile must not make Kafka or Spark adjacent
        ("Kafka", {"data_mlops": ["PostgreSQL"]}),
        ("Spark", {"data_mlops": ["PostgreSQL"]}),
        ("PySpark", {"data_mlops": ["PostgreSQL", "Postgres"]}),
        ("Apache Kafka", {"data_mlops": ["PostgreSQL"]}),
    ],
)
def test_a_newly_aliased_skill_is_not_made_adjacent_by_an_unrelated_neighbour_in_its_category(
    requested: str, profile_skills: dict[str, list[str]]
) -> None:
    assert classify_skill(requested, _profile(profile_skills)) == "unsupported"


def test_verified_and_supported_paths_of_the_newly_aliased_skills_are_unchanged() -> None:
    assert classify_skill("Excel", _profile({"tools": ["MS Excel"]})) == "verified"
    assert classify_skill("PySpark", _profile({"data_mlops": ["Spark"]})) == "verified"
    assert classify_skill("C#", _profile({"programming": ["csharp"]})) == "verified"
    assert classify_skill("SQL", _profile(bullets=["Tuned the SQL behind the dashboards."])) == (
        "supported"
    )
    assert classify_skill("MS Excel", _profile(tech=["Excel"])) == "supported"
    assert classify_skill("Spark", _profile(bullets=["Wrote PySpark jobs."])) == "supported"


def test_a_frontend_profile_lists_sql_csharp_and_excel_as_real_gaps() -> None:
    """The gaps must survive into the positioning brief: it only counts "unsupported" skills."""
    profile = _profile(
        {
            "programming": ["TypeScript", "JavaScript", "HTML", "CSS"],
            "tools": ["Storybook", "Figma", "Git"],
        },
        bullets=["Built the design system's component library."],
        tech=["React"],
    )
    terms = ["SQL", "C#", "MS Excel", "TypeScript", "Kotlin"]
    skills = [
        {"skill": canonicalize_skill(t), "requested_as": t, "state": classify_skill(t, profile)}
        for t in terms
    ]

    assert gap_candidates(skills, []) == ["C#", "Excel", "Kotlin", "SQL"]
    assert strength_candidates(skills, []) == ["TypeScript"]


# -- a requested skill is found as a whole word, under its own spellings -----------------------


@pytest.mark.parametrize(
    ("requested", "sentence"),
    [
        ("MS Excel", "Delivered excellent customer service."),
        ("Excel", "Delivered excellent customer service."),
        ("ReactJS", "Cut reaction time by 40%."),
        ("react.js", "Cut reaction time by 40%."),
        ("React", "Wrote reactive pipelines with Project Reactor."),
        ("PySpark", "Added a sparkline chart."),
        ("Spark", "Sparked a 20% gain in retention."),
        ("Scala", "Built scalable payment services."),
        ("Java", "Built a JavaScript SPA."),
        ("Angular", "Used triangular meshes."),
        ("Vue.js", "Reviewed the revue."),
    ],
)
def test_a_skill_is_not_supported_by_an_ordinary_word_that_contains_its_name(
    requested: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile(bullets=[sentence])) == "unsupported"


@pytest.mark.parametrize(
    ("requested", "sentence"),
    [
        ("MS Excel", "Used Excel daily for the close."),
        ("PySpark", "Wrote PySpark jobs for the nightly load."),
        ("React", "Rebuilt the settings page in ReactJS."),
        ("ReactJS", "Rebuilt the settings page in React."),
        ("Vue.js", "Shipped the app in Vue."),
        ("Scala", "Services in Scala."),
        ("C#", "Wrote the importer in csharp."),
        ("Microservice", "Split the monolith into microservices."),
        ("REST API", "Designed public REST APIs."),
        ("Kubernetes", "Ran workloads on Kubernetes (EKS)."),
    ],
)
def test_a_skill_is_supported_by_its_own_name_or_an_alias_spelling_as_a_whole_word(
    requested: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile(bullets=[sentence])) == "supported"


@pytest.mark.parametrize(
    ("requested", "alias", "sibling_category", "sibling"),
    [
        ("Power BI", "PowerBI", "tools", "Looker"),
        ("Power BI", "Power-BI", "tools", "Looker"),
        ("Next.js", "NextJS", "tools", "Vue"),
        ("New Relic", "NewRelic", "cloud_devops", "Datadog"),
        ("BigQuery", "Big Query", "data_mlops", "Snowflake"),
    ],
)
def test_a_family_skills_own_alias_spelling_in_evidence_is_supported_with_or_without_a_sibling(
    requested: str, alias: str, sibling_category: str, sibling: str
) -> None:
    sentence = f"Built the reporting layer in {alias}."

    assert classify_skill(requested, _profile(bullets=[sentence])) == "supported"
    assert classify_skill(requested, _profile(tech=[alias])) == "supported"
    assert (
        classify_skill(requested, _profile({sibling_category: [sibling]}, bullets=[sentence]))
        == "supported"
    )


def test_a_short_alias_of_a_non_family_skill_is_not_evidence_of_it() -> None:
    # "CV" in a bullet is a curriculum vitae, not computer vision
    assert classify_skill("Computer Vision", _profile(bullets=["Rewrote the CV."])) == "unsupported"
    assert classify_skill("TypeScript", _profile(bullets=["Shipped in ts."])) == "unsupported"


def test_a_curated_skill_name_is_matched_exactly_not_as_a_verb_form() -> None:
    assert classify_skill("Excel", _profile(bullets=["She excels at mentoring."])) == "unsupported"
    assert classify_skill("React", _profile(bullets=["The team reacts quickly."])) == "unsupported"
    assert classify_skill("Spark", _profile(bullets=["It sparks joy."])) == "unsupported"


# -- a skill name that is also an ordinary word is trusted only in a technology list -----------


@pytest.mark.parametrize(
    ("requested", "sentence"),
    [
        ("Vue", "Trained the team to react to on-call alerts within minutes."),
        ("Angular", "Trained the team to react to on-call alerts within minutes."),
        ("Next.js", "Trained the team to react to on-call alerts within minutes."),
        ("Selenium", "Shipped firmware tooling for Cypress Semiconductor."),
        ("Playwright", "Shipped firmware tooling for Cypress Semiconductor."),
        ("Gatling", "Cleared a locust swarm from the test field."),
        ("Dagster", "Served as the prefect of the house."),
        ("Vue", "Modelled angular momentum for the simulator."),
        ("Java", "Attended a show at La Scala."),
    ],
)
def test_an_ambiguous_word_in_a_bullet_does_not_make_its_neighbours_adjacent(
    requested: str, sentence: str
) -> None:
    assert classify_skill(requested, _profile(bullets=[sentence])) == "unsupported"


@pytest.mark.parametrize(
    ("requested", "ambiguous"),
    [
        ("Vue", "React"),
        ("Vue", "Angular"),
        ("Selenium", "Cypress"),
        ("Gatling", "Locust"),
        ("Dagster", "Prefect"),
        ("Java", "Scala"),
    ],
)
def test_an_ambiguous_skill_name_still_counts_when_the_person_lists_it(
    requested: str, ambiguous: str
) -> None:
    assert classify_skill(requested, _profile(tech=[ambiguous])) == "adjacent"
    assert classify_skill(requested, _profile({"tools": [ambiguous]})) == "adjacent"


def test_an_unambiguous_alias_of_an_ambiguous_skill_counts_in_a_bullet() -> None:
    assert classify_skill("Vue", _profile(bullets=["Built the UI in ReactJS."])) == "adjacent"
    assert classify_skill("Vue", _profile(bullets=["Built the UI in react.js."])) == "adjacent"


def test_the_ambiguous_terms_are_the_ordinary_words_among_the_family_and_bridge_names() -> None:
    assert set(AMBIGUOUS_TERMS) == {"react", "cypress", "locust", "prefect", "angular", "scala"}


# -- precedence: declared beats evidence beats a sibling ---------------------------------------


@pytest.mark.parametrize(
    ("requested", "category", "declared", "bullet"),
    [
        ("Tableau", "tools", "Tableau", "Built Tableau dashboards for finance."),
        ("pytorch", "ai_ml", "PyTorch", "Trained the ranking model in PyTorch."),
        ("Python", "programming", "Python", "Wrote the billing service in Python."),
    ],
)
def test_a_skill_that_is_both_declared_and_evidenced_is_verified_not_supported(
    requested: str, category: str, declared: str, bullet: str
) -> None:
    profile = _profile({category: [declared]}, bullets=[bullet])

    assert classify_skill(requested, profile) == "verified"


# -- the pinned contents of the tables ---------------------------------------------------------

_EXPECTED_FAMILIES = {
    "BI tools": {"Tableau", "Power BI", "Looker", "Qlik"},
    "browser test automation": {"Selenium", "Cypress", "Playwright"},
    "load testing": {"JMeter", "Gatling", "Locust", "k6"},
    "front-end frameworks": {"React", "Vue", "Angular", "Next.js"},
    "cloud data warehouses": {"Snowflake", "BigQuery", "Redshift"},
    "pipeline orchestration": {"airflow", "dbt", "Dagster", "Prefect"},
    "data processing and streaming": {"Spark", "Kafka"},
    "JVM languages": {"Java", "Kotlin", "Scala"},
    "infrastructure as code": {"terraform", "Pulumi", "CloudFormation"},
    "observability": {"Datadog", "New Relic", "Grafana", "Prometheus"},
    "ML frameworks": {"PyTorch", "TensorFlow", "JAX", "Scikit-learn"},
}


def test_the_family_table_has_exactly_the_intended_members() -> None:
    families: dict[str, set[str]] = {}
    for key, family in SKILL_FAMILIES.items():
        families.setdefault(family, set()).add(key)

    assert families == _EXPECTED_FAMILIES


def _display(key: str) -> str:
    return {"airflow": "Airflow", "terraform": "Terraform"}.get(key, key)


_SAME_FAMILY_PAIRS = [
    (a, b)
    for members in _EXPECTED_FAMILIES.values()
    for a in sorted(members)
    for b in sorted(members)
    if a != b
]
_CROSS_FAMILY_PAIRS = [
    (sorted(first)[0], sorted(second)[0])
    for name, first in _EXPECTED_FAMILIES.items()
    for other, second in _EXPECTED_FAMILIES.items()
    if name != other
]


@pytest.mark.parametrize(("requested", "declared"), _SAME_FAMILY_PAIRS)
def test_every_member_of_a_family_is_adjacent_to_every_other(requested: str, declared: str) -> None:
    # the declared skill goes under "other" so the category rule cannot be what makes it adjacent
    profile = _profile({"other": [_display(declared)]})

    assert classify_skill(_display(requested), profile) == "adjacent"


@pytest.mark.parametrize(("requested", "declared"), _CROSS_FAMILY_PAIRS)
def test_a_member_of_another_family_makes_nothing_adjacent(requested: str, declared: str) -> None:
    profile = _profile({"other": [_display(declared)]})

    assert classify_skill(_display(requested), profile) == "unsupported"


@pytest.mark.parametrize(
    ("raw", "canonical", "category"),
    [
        ("tableau", "Tableau", "tools"),
        ("power bi", "Power BI", "tools"),
        ("power-bi", "Power BI", "tools"),
        ("powerbi", "Power BI", "tools"),
        ("looker", "Looker", "tools"),
        ("qlik", "Qlik", "tools"),
        ("excel", "Excel", "tools"),
        ("ms excel", "Excel", "tools"),
        ("sql", "SQL", "programming"),
        ("selenium", "Selenium", "tools"),
        ("cypress", "Cypress", "tools"),
        ("playwright", "Playwright", "tools"),
        ("jmeter", "JMeter", "tools"),
        ("gatling", "Gatling", "tools"),
        ("locust", "Locust", "tools"),
        ("k6", "k6", "tools"),
        ("react", "React", "tools"),
        ("reactjs", "React", "tools"),
        ("react.js", "React", "tools"),
        ("vue", "Vue", "tools"),
        ("vuejs", "Vue", "tools"),
        ("vue.js", "Vue", "tools"),
        ("angular", "Angular", "tools"),
        ("next.js", "Next.js", "tools"),
        ("nextjs", "Next.js", "tools"),
        ("next js", "Next.js", "tools"),
        ("snowflake", "Snowflake", "data_mlops"),
        ("bigquery", "BigQuery", "data_mlops"),
        ("big query", "BigQuery", "data_mlops"),
        ("redshift", "Redshift", "data_mlops"),
        ("amazon redshift", "Redshift", "data_mlops"),
        ("dbt", "dbt", "data_mlops"),
        ("dbt core", "dbt", "data_mlops"),
        ("dagster", "Dagster", "data_mlops"),
        ("prefect", "Prefect", "data_mlops"),
        ("spark", "Spark", "data_mlops"),
        ("apache spark", "Spark", "data_mlops"),
        ("pyspark", "Spark", "data_mlops"),
        ("kafka", "Kafka", "data_mlops"),
        ("apache kafka", "Kafka", "data_mlops"),
        ("java", "Java", "programming"),
        ("kotlin", "Kotlin", "programming"),
        ("scala", "Scala", "programming"),
        ("c#", "C#", "programming"),
        ("csharp", "C#", "programming"),
        ("c sharp", "C#", "programming"),
        ("datadog", "Datadog", "cloud_devops"),
        ("new relic", "New Relic", "cloud_devops"),
        ("newrelic", "New Relic", "cloud_devops"),
        ("grafana", "Grafana", "cloud_devops"),
        ("prometheus", "Prometheus", "cloud_devops"),
        ("pulumi", "Pulumi", "cloud_devops"),
        ("cloudformation", "CloudFormation", "cloud_devops"),
        ("aws cloudformation", "CloudFormation", "cloud_devops"),
        ("jax", "JAX", "ai_ml"),
    ],
)
def test_every_alias_added_for_the_skill_families_is_pinned(
    raw: str, canonical: str, category: str
) -> None:
    assert SKILL_ALIASES[raw] == (canonical, category)
    assert canonicalize_skill(raw) == canonical


# -- gap bridges are alias-aware and matched as whole words ------------------------------------


@pytest.mark.parametrize(
    ("jd_term", "spelling", "bridge"),
    [
        ("Tableau", "PowerBI", "power bi"),
        ("Tableau", "Power-BI", "power bi"),
        ("React", "Vue.js", "vue"),
        ("React", "VueJS", "vue"),
        ("React", "NextJS", "next.js"),
        ("Snowflake", "Amazon Redshift", "redshift"),
        ("Snowflake", "Big Query", "bigquery"),
        ("Datadog", "NewRelic", "new relic"),
        ("Airflow", "dbt Core", "dbt"),
        ("Terraform", "AWS CloudFormation", "cloudformation"),
        ("Kubernetes", "K8s", "kubernetes"),
    ],
)
def test_a_bridge_is_found_under_an_alias_spelling_in_declared_skills_a_tech_list_or_a_bullet(
    jd_term: str, spelling: str, bridge: str
) -> None:
    if bridge == "kubernetes":
        # a bridge for a different JD term: "container orchestration" is bridged by Kubernetes
        jd_term = "Container Orchestration"
    assert find_gap_bridge([jd_term], _profile({"tools": [spelling]})) == bridge
    assert find_gap_bridge([jd_term], _profile(tech=[spelling])) == bridge
    assert find_gap_bridge([jd_term], _profile(bullets=[f"Built it with {spelling}."])) == bridge


@pytest.mark.parametrize(
    ("jd_term", "bridge"),
    [("PowerBI", "tableau"), ("Power-BI", "tableau"), ("ReactJS", "vue"), ("k8s", "docker")],
)
def test_a_jd_keyword_in_an_alias_spelling_finds_its_curated_entry(
    jd_term: str, bridge: str
) -> None:
    assert find_gap_bridge([jd_term], _profile(tech=[bridge])) == bridge


def test_a_bridge_for_a_broad_concept_is_not_triggered_by_another_alias_of_that_concept() -> None:
    # "airflow" is a bridge for MLOps, and its canonical name ("Data Pipelines") is shared by
    # "etl": an ETL mention is not Airflow
    assert find_gap_bridge(["MLOps"], _profile({"data_mlops": ["ETL"]})) is None
    assert find_gap_bridge(["MLOps"], _profile({"data_mlops": ["Airflow"]})) == "airflow"


@pytest.mark.parametrize(
    ("jd_term", "sentence"),
    [
        ("Airflow", "Managed the Osaka prefecture rollout."),
        ("Tableau", "Acted as onlooker on the review."),
        ("Selenium", "Visited Cypress, TX for the offsite."),
        ("Selenium", "Wrote for local playwrights."),
        ("Vector Search", "Leveraged storage tiers to cut cost."),
        ("Kubernetes", "Coped with the overwhelm of a migration."),
        ("Computer Vision", "Trained as a pilot."),
        ("JMeter", "Cleared the locusts."),
    ],
)
def test_a_bridge_term_does_not_match_inside_or_at_the_start_of_an_ordinary_word(
    jd_term: str, sentence: str
) -> None:
    assert find_gap_bridge([jd_term], _profile(bullets=[sentence])) is None


def test_a_bridge_term_is_still_found_when_written_as_itself() -> None:
    assert find_gap_bridge(["Airflow"], _profile(tech=["Prefect"])) == "prefect"
    assert find_gap_bridge(["Tableau"], _profile(bullets=["Built dashboards in Looker."])) == (
        "looker"
    )
    assert find_gap_bridge(["Prompt Engineering"], _profile(bullets=["Shipped LLMs."])) == "llms"
    assert find_gap_bridge(["Kubernetes"], _profile(bullets=["Shipped dockerized apps."])) == (
        "docker"
    )


# Written out, not read from GAP_ADJACENCY: a table built from the map follows it when a bridge is
# deleted, so it could never notice.
_TOOL_BRIDGE_PAIRS = [
    ("tableau", "power bi"),
    ("tableau", "looker"),
    ("tableau", "qlik"),
    ("power bi", "tableau"),
    ("power bi", "looker"),
    ("power bi", "qlik"),
    ("selenium", "cypress"),
    ("selenium", "playwright"),
    ("selenium", "webdriver"),
    ("cypress", "selenium"),
    ("cypress", "playwright"),
    ("react", "vue"),
    ("react", "angular"),
    ("react", "next.js"),
    ("snowflake", "bigquery"),
    ("snowflake", "redshift"),
    ("snowflake", "databricks"),
    ("airflow", "dbt"),
    ("airflow", "dagster"),
    ("airflow", "prefect"),
    ("java", "kotlin"),
    ("java", "scala"),
    ("terraform", "pulumi"),
    ("terraform", "cloudformation"),
    ("datadog", "new relic"),
    ("datadog", "grafana"),
    ("datadog", "prometheus"),
    ("jmeter", "gatling"),
    ("jmeter", "locust"),
    ("jmeter", "k6"),
]
_PLAIN_TOOL_BRIDGE_PAIRS = [
    (key, bridge) for key, bridge in _TOOL_BRIDGE_PAIRS if bridge not in AMBIGUOUS_TERMS
]


@pytest.mark.parametrize(("jd_term", "bridge"), _TOOL_BRIDGE_PAIRS)
def test_every_tool_bridge_is_found_in_a_tech_list(jd_term: str, bridge: str) -> None:
    assert find_gap_bridge([jd_term], _profile(tech=[bridge])) == bridge


@pytest.mark.parametrize(("jd_term", "bridge"), _TOOL_BRIDGE_PAIRS)
def test_every_tool_bridge_is_found_in_declared_skills_alone(jd_term: str, bridge: str) -> None:
    """Nothing in experience or projects, so only the declared-skills match can find it."""
    assert find_gap_bridge([jd_term], _profile({"tools": [bridge]})) == bridge


@pytest.mark.parametrize(("jd_term", "bridge"), _PLAIN_TOOL_BRIDGE_PAIRS)
def test_every_unambiguous_tool_bridge_is_found_in_a_bullet(jd_term: str, bridge: str) -> None:
    assert find_gap_bridge([jd_term], _profile(bullets=[f"Worked with {bridge} daily."])) == bridge


def test_the_tool_bridge_pairs_are_all_in_the_map() -> None:
    for key, bridge in _TOOL_BRIDGE_PAIRS:
        assert bridge in GAP_ADJACENCY[key], (key, bridge)


def test_every_ambiguous_tool_bridge_is_a_real_bridge_and_is_ignored_in_a_bullet() -> None:
    ambiguous = [(key, bridge) for key, bridge in _TOOL_BRIDGE_PAIRS if bridge in AMBIGUOUS_TERMS]

    assert {bridge for _, bridge in ambiguous} == {
        "cypress",
        "angular",
        "scala",
        "prefect",
        "locust",
    }
    for key, bridge in ambiguous:
        assert find_gap_bridge([key], _profile(bullets=[f"Worked with {bridge} daily."])) is None
