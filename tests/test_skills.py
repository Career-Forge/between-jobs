"""Tests for skill canonicalization v0 (Sprint 3.3c)."""

from __future__ import annotations

from between_jobs.api.skills import canonicalize_skill, classify_skill, find_gap_bridge

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
