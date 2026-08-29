"""Tests for deterministic requirement-coverage matching (Sprint 3.3b)."""

from __future__ import annotations

from between_jobs.api.tailor import ClusterCoverage, compute_coverage, pick_gap_interview_questions

_CLUSTERS = [
    {"name": "Python", "priority": "must_have", "keywords": ["python", "django"]},
    {"name": "Cloud", "priority": "preferred", "keywords": ["aws", "kubernetes"]},
    {"name": "Nothing Matches", "priority": "preferred", "keywords": ["cobol"]},
]

_FACTS = [
    {
        "id": "fact-1",
        "value_json": {
            "title": "Backend Engineer",
            "company": "Acme",
            "bullets": ["Built services in Python and Django.", "Deployed to AWS."],
        },
    },
    {
        "id": "fact-2",
        "value_json": {"name": "VectorBench", "tech": ["Kubernetes", "Docker"]},
    },
    {
        "id": "fact-3",
        "value_json": {"degree": "B.S.", "field": "Computer Science"},
    },
]


def test_compute_coverage_counts_matching_facts_per_cluster() -> None:
    coverage = compute_coverage(_CLUSTERS, _FACTS)

    by_name = {c["name"]: c for c in coverage}
    assert by_name["Python"]["coverage_count"] == 1
    assert by_name["Python"]["matched_fact_ids"] == ["fact-1"]
    assert by_name["Cloud"]["coverage_count"] == 2
    assert set(by_name["Cloud"]["matched_fact_ids"]) == {"fact-1", "fact-2"}
    assert by_name["Nothing Matches"]["coverage_count"] == 0
    assert by_name["Nothing Matches"]["matched_fact_ids"] == []


def test_compute_coverage_preserves_cluster_order() -> None:
    coverage = compute_coverage(_CLUSTERS, _FACTS)
    assert [c["name"] for c in coverage] == ["Python", "Cloud", "Nothing Matches"]


def test_compute_coverage_is_case_insensitive() -> None:
    clusters = [{"name": "Python", "priority": "must_have", "keywords": ["PYTHON"]}]
    facts = [{"id": "fact-1", "value_json": {"bullets": ["wrote python code"]}}]
    coverage = compute_coverage(clusters, facts)
    assert coverage[0]["coverage_count"] == 1


def test_compute_coverage_handles_empty_clusters_and_facts() -> None:
    assert compute_coverage([], []) == []
    assert compute_coverage(_CLUSTERS, [])[0]["coverage_count"] == 0
    assert compute_coverage([], _FACTS) == []


def test_compute_coverage_ignores_unrecognized_fact_fields() -> None:
    clusters = [{"name": "X", "priority": "must_have", "keywords": ["secret"]}]
    facts = [{"id": "fact-1", "value_json": {"some_unknown_field": "secret sauce"}}]
    coverage = compute_coverage(clusters, facts)
    assert coverage[0]["coverage_count"] == 0


_CV_PROFILE = {
    "skills": {
        "programming": [],
        "ai_ml": [],
        "cloud_devops": [],
        "data_mlops": [],
        "tools": [],
        "other": [],
    },
    "experience": [{"bullets": ["Preprocessed images with OpenCV before training."]}],
    "projects": [],
}


def _coverage(
    name: str, priority: str, count: int, keywords: list[str] | None = None
) -> ClusterCoverage:
    return {
        "name": name,
        "priority": priority,
        "keywords": keywords or ["computer vision"],
        "coverage_count": count,
        "matched_fact_ids": [],
    }


def test_pick_gap_interview_questions_picks_a_zero_coverage_must_have_with_a_real_bridge() -> None:
    coverage = [_coverage("Computer Vision", "must_have", 0)]
    result = pick_gap_interview_questions(coverage, _CV_PROFILE)
    assert result == [{"cluster_name": "Computer Vision", "bridge_skill": "opencv"}]


def test_pick_gap_interview_questions_skips_a_cluster_that_already_has_coverage() -> None:
    coverage = [_coverage("Computer Vision", "must_have", 1)]
    assert pick_gap_interview_questions(coverage, _CV_PROFILE) == []


def test_pick_gap_interview_questions_skips_a_preferred_cluster_even_at_zero_coverage() -> None:
    coverage = [_coverage("Computer Vision", "preferred", 0)]
    assert pick_gap_interview_questions(coverage, _CV_PROFILE) == []


def test_pick_gap_interview_questions_skips_when_no_curated_bridge_exists() -> None:
    coverage = [_coverage("Cobol Mainframes", "must_have", 0, keywords=["cobol"])]
    assert pick_gap_interview_questions(coverage, _CV_PROFILE) == []


def test_pick_gap_interview_questions_skips_when_bridge_terms_are_not_actually_in_profile() -> None:
    empty_profile: dict[str, object] = {"skills": {}, "experience": [], "projects": []}
    coverage = [_coverage("Computer Vision", "must_have", 0)]
    assert pick_gap_interview_questions(coverage, empty_profile) == []


def test_pick_gap_interview_questions_caps_at_three_in_coverage_order() -> None:
    coverage = [
        _coverage(f"Computer Vision {i}", "must_have", 0, keywords=["computer vision"])
        for i in range(5)
    ]
    result = pick_gap_interview_questions(coverage, _CV_PROFILE)
    assert [c["cluster_name"] for c in result] == [
        "Computer Vision 0",
        "Computer Vision 1",
        "Computer Vision 2",
    ]


def test_pick_gap_interview_questions_handles_empty_coverage() -> None:
    assert pick_gap_interview_questions([], _CV_PROFILE) == []


def test_pick_gap_interview_questions_mixes_eligible_and_ineligible_clusters() -> None:
    coverage = [
        _coverage("Already Covered", "must_have", 2),
        _coverage("Preferred Gap", "preferred", 0),
        _coverage("Computer Vision", "must_have", 0),
        _coverage("No Bridge", "must_have", 0, keywords=["cobol"]),
    ]
    result = pick_gap_interview_questions(coverage, _CV_PROFILE)
    assert [c["cluster_name"] for c in result] == ["Computer Vision"]
