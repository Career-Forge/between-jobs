"""Tests for the deterministic resume-template validation and import
pipeline (Sprint 2.5b). No I/O, no LLM -- pure function tests."""

from __future__ import annotations

import json
from typing import Any

import pytest

from between_jobs.api.profile import (
    ImportedProfile,
    ProfileImportError,
    append_bullet_to_entity,
    entity_candidates,
    import_profile,
)

_MINIMAL_VALID: dict[str, Any] = {
    "personal": {
        "name": "Jane Doe",
        "headline": "Software Engineer",
        "emails": [{"address": "jane@example.com", "primary": True}],
        "phones": [],
        "links": {},
        "location": {},
        "work_authorization": "",
    },
    "summary_bullets": [],
    "experience": [
        {
            "title": "Software Engineer",
            "company": "Example Corp",
            "location": "Remote",
            "start_date": "2022-01",
            "end_date": "present",
            "is_current": True,
            "bullets": ["Built things."],
            "skills": ["Python"],
            "metrics": [],
        }
    ],
    "projects": [],
    "education": [
        {
            "degree": "B.S. Computer Science",
            "institution": "Example University",
            "start_date": "2018-08",
            "end_date": "2022-05",
        }
    ],
    "skills": {"programming": ["Python", "SQL"]},
    "achievements": [],
}


def _text(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def test_minimal_valid_template_imports_successfully() -> None:
    result = import_profile(_text(_MINIMAL_VALID))
    assert isinstance(result, ImportedProfile)
    assert result.schema_version == "1.3"
    assert result.stats["experience"] == 1
    assert result.stats["education"] == 1
    assert result.stats["skills"] == 2
    assert len(result.career_facts) == 2  # 1 experience + 1 education


def test_projects_only_satisfies_the_evidence_requirement() -> None:
    payload = {**_MINIMAL_VALID, "experience": [], "education": []}
    payload["projects"] = [
        {"name": "Side Project", "url": "", "tech": ["Python"], "bullets": ["Did a thing."]}
    ]
    result = import_profile(_text(payload))
    assert result.stats["projects"] == 1
    assert len(result.career_facts) == 1
    assert result.career_facts[0].fact_type == "project"


def test_no_evidence_at_all_is_rejected() -> None:
    payload = {**_MINIMAL_VALID, "experience": [], "projects": []}
    with pytest.raises(ProfileImportError, match="at least one of"):
        import_profile(_text(payload))


def test_publications_only_satisfies_the_evidence_requirement() -> None:
    # The PhD-track shape: no experience, no projects, but real evidence
    # via publications. v1.0's business rule rejected this outright.
    payload = {**_MINIMAL_VALID, "experience": [], "projects": []}
    payload["publications"] = [
        {
            "title": "Adaptive Graph Neural Networks for Spatiotemporal Forecasting",
            "authors": "Doe, J., Patel, R.",
            "venue": "KDD 2024",
            "date": "2024",
            "url": "https://doi.org/10.1000/example",
        }
    ]
    result = import_profile(_text(payload))
    assert result.stats["publications"] == 1
    pub_facts = [f for f in result.career_facts if f.fact_type == "publication"]
    assert len(pub_facts) == 1
    assert pub_facts[0].source_pointer == "/publications/0"
    # PhD shape is complete evidence -- no "missing experience" warning.
    assert not any("experience" in w.lower() for w in result.warnings)


def test_patents_only_satisfies_the_evidence_requirement() -> None:
    payload = {**_MINIMAL_VALID, "experience": [], "projects": []}
    payload["patents"] = [
        {
            "title": "Method for Distributed Cache Invalidation",
            "patent_number": "US 11,000,000",
            "status": "granted",
            "date": "2023",
            "url": "https://patents.example.com/11000000",
        }
    ]
    result = import_profile(_text(payload))
    assert result.stats["patents"] == 1
    pat_facts = [f for f in result.career_facts if f.fact_type == "patent"]
    assert len(pat_facts) == 1
    assert pat_facts[0].source_pointer == "/patents/0"
    assert not any("experience" in w.lower() for w in result.warnings)


def test_patent_missing_a_title_is_rejected() -> None:
    payload = {**_MINIMAL_VALID, "patents": [{"status": "filed"}]}
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_volunteering_only_satisfies_the_evidence_requirement() -> None:
    payload = {**_MINIMAL_VALID, "experience": [], "projects": []}
    payload["volunteering"] = [
        {"organization": "JerseySTEM", "role": "Mentor", "bullets": ["Taught Python basics."]}
    ]
    result = import_profile(_text(payload))
    assert result.stats["volunteering"] == 1


def test_languages_and_certifications_round_trip() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["languages"] = [
        {"language": "Hindi", "fluency": "Native"},
        {"language": "English", "fluency": "Fluent"},
    ]
    payload["certifications"] = [
        {"name": "AWS Certified Solutions Architect", "issuer": "AWS", "date": "2024-06"}
    ]
    result = import_profile(_text(payload))
    assert result.stats["languages"] == 2
    assert result.stats["certifications"] == 1
    assert result.canonical_json["languages"][0]["language"] == "Hindi"


def test_scholar_and_other_links_round_trip() -> None:
    payload = {
        **_MINIMAL_VALID,
        "personal": {
            **_MINIMAL_VALID["personal"],
            "links": {
                "scholar": "scholar.google.com/citations?user=abc",
                "other": [{"label": "ORCID", "url": "orcid.org/0000-0000-0000-0000"}],
            },
        },
    }
    result = import_profile(_text(payload))
    assert result.canonical_json["personal"]["links"]["scholar"].startswith("scholar.google")
    assert result.canonical_json["personal"]["links"]["other"][0]["label"] == "ORCID"


def test_v1_0_shaped_payload_still_validates_unchanged() -> None:
    # Every v1.1 addition is optional -- a payload with none of the new
    # keys present must still import exactly as it did under v1.0.
    result = import_profile(_text(_MINIMAL_VALID))
    assert result.canonical_json["publications"] == []
    assert result.canonical_json["languages"] == []
    assert result.canonical_json["certifications"] == []
    assert result.canonical_json["volunteering"] == []


def test_missing_name_is_rejected() -> None:
    payload = {**_MINIMAL_VALID, "personal": {**_MINIMAL_VALID["personal"], "name": ""}}
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_placeholder_name_is_rejected() -> None:
    payload = {**_MINIMAL_VALID, "personal": {**_MINIMAL_VALID["personal"], "name": "TODO"}}
    with pytest.raises(ProfileImportError, match="placeholder"):
        import_profile(_text(payload))


def test_invalid_json_syntax_is_rejected() -> None:
    with pytest.raises(ProfileImportError, match="wasn't valid JSON"):
        import_profile("{not: valid json,,,")


def test_multiple_violations_are_all_reported_not_just_the_first() -> None:
    # Regression test: a real resume with an extra field repeated across
    # every array entry (e.g. "label" on every email/phone, or stray
    # dates on every project) used to surface only errors()[0], forcing a
    # one-fix-at-a-time resubmit loop. Every problem must show up at once.
    payload = dict(_MINIMAL_VALID)
    payload["personal"] = {
        **_MINIMAL_VALID["personal"],
        "emails": [{"address": "a@example.com", "primary": True, "label": "extra"}],
    }
    payload["projects"] = [
        {"name": "Side Project", "url": "", "tech": [], "bullets": [], "start_date": None}
    ]
    try:
        import_profile(_text(payload))
        raise AssertionError("expected ProfileImportError")
    except ProfileImportError as e:
        message = str(e)
        assert "emails" in message and "label" in message
        assert "projects" in message and "start_date" in message
        assert "2 problem(s)" in message


def test_json_array_at_top_level_is_rejected() -> None:
    with pytest.raises(ProfileImportError, match="not a resume object"):
        import_profile("[1, 2, 3]")


def test_unknown_top_level_key_is_rejected() -> None:
    payload = {**_MINIMAL_VALID, "unexpected_field": "surprise"}
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_bad_date_format_is_rejected() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [{**_MINIMAL_VALID["experience"][0], "start_date": "January 2022"}]
    with pytest.raises(ProfileImportError, match="start_date"):
        import_profile(_text(payload))


def test_present_is_a_valid_end_date_case_insensitive() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [{**_MINIMAL_VALID["experience"][0], "end_date": "Present"}]
    result = import_profile(_text(payload))
    assert result.stats["experience"] == 1


def test_leading_bom_is_stripped() -> None:
    result = import_profile("﻿" + _text(_MINIMAL_VALID))
    assert result.stats["experience"] == 1


def test_warns_when_no_primary_email() -> None:
    payload = {
        **_MINIMAL_VALID,
        "personal": {
            **_MINIMAL_VALID["personal"],
            "emails": [{"address": "jane@example.com", "primary": False}],
        },
    }
    result = import_profile(_text(payload))
    assert any("primary email" in w for w in result.warnings)


def test_warns_when_no_education() -> None:
    payload = {**_MINIMAL_VALID, "education": []}
    result = import_profile(_text(payload))
    assert any("education" in w for w in result.warnings)


def test_content_hash_is_stable_across_key_order() -> None:
    reordered = {
        "achievements": [],
        **_MINIMAL_VALID,
    }
    a = import_profile(_text(_MINIMAL_VALID))
    b = import_profile(_text(reordered))
    assert a.content_hash == b.content_hash


def test_content_hash_changes_when_content_changes() -> None:
    a = import_profile(_text(_MINIMAL_VALID))
    changed = dict(_MINIMAL_VALID)
    changed["summary_bullets"] = ["Something new."]
    b = import_profile(_text(changed))
    assert a.content_hash != b.content_hash


def test_career_facts_have_distinct_entity_keys_and_source_pointers() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        _MINIMAL_VALID["experience"][0],
        {**_MINIMAL_VALID["experience"][0], "company": "Second Corp"},
    ]
    result = import_profile(_text(payload))
    exp_facts = [f for f in result.career_facts if f.fact_type == "experience"]
    assert len(exp_facts) == 2
    assert exp_facts[0].entity_key != exp_facts[1].entity_key
    assert {f.source_pointer for f in exp_facts} == {"/experience/0", "/experience/1"}


# ── R5: mandatory pins (resumeforge-shape-and-fit.md) ───────────────────────


def test_pinned_experience_entry_imports_successfully() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {**_MINIMAL_VALID["experience"][0], "pin": {"mandatory": True, "min_bullets": 3}}
    ]
    result = import_profile(_text(payload))
    assert result.canonical_json["experience"][0]["pin"] == {
        "mandatory": True,
        "min_bullets": 3,
    }


def test_pin_is_optional_and_absent_by_default() -> None:
    result = import_profile(_text(_MINIMAL_VALID))
    assert result.canonical_json["experience"][0]["pin"] is None


def test_pin_min_bullets_out_of_bounds_is_rejected() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {**_MINIMAL_VALID["experience"][0], "pin": {"mandatory": True, "min_bullets": 99}}
    ]
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_six_pinned_entries_is_the_max_allowed() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {
            **_MINIMAL_VALID["experience"][0],
            "company": f"Company {i}",
            "pin": {"mandatory": True},
        }
        for i in range(6)
    ]
    result = import_profile(_text(payload))
    assert result.stats["experience"] == 6


def test_seven_pinned_entries_is_rejected() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {
            **_MINIMAL_VALID["experience"][0],
            "company": f"Company {i}",
            "pin": {"mandatory": True},
        }
        for i in range(7)
    ]
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_pins_are_counted_across_experience_projects_and_education_together() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {**_MINIMAL_VALID["experience"][0], "pin": {"mandatory": True}} for _ in range(3)
    ]
    payload["projects"] = [{"name": f"Project {i}", "pin": {"mandatory": True}} for i in range(3)]
    payload["education"] = [
        {**_MINIMAL_VALID["education"][0], "pin": {"mandatory": True}} for _ in range(1)
    ]
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


def test_pin_with_mandatory_false_does_not_count_toward_the_cap() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {
            **_MINIMAL_VALID["experience"][0],
            "company": f"Company {i}",
            "pin": {"mandatory": False},
        }
        for i in range(7)
    ]
    result = import_profile(_text(payload))
    assert result.stats["experience"] == 7


def test_unrecognized_pin_field_is_rejected_like_everything_else() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["experience"] = [
        {**_MINIMAL_VALID["experience"][0], "pin": {"mandatory": True, "typo_field": 1}}
    ]
    with pytest.raises(ProfileImportError):
        import_profile(_text(payload))


# ── entity_candidates / append_bullet_to_entity (S4b, honest-score-surfaces.md) ──


def test_entity_candidates_lists_experience_and_projects_with_readable_labels() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["projects"] = [{"name": "VectorBench", "bullets": []}]
    canonical_json = import_profile(_text(payload)).canonical_json
    assert entity_candidates(canonical_json) == [
        {"pointer": "/experience/0", "label": "Software Engineer at Example Corp"},
        {"pointer": "/projects/0", "label": "VectorBench"},
    ]


def test_entity_candidates_handles_no_experience_or_projects() -> None:
    # PhD shape: no experience/projects, publications carry the evidence
    # requirement instead (see test_publications_only_satisfies_the_..._).
    payload = {**_MINIMAL_VALID, "experience": [], "projects": []}
    payload["publications"] = [
        {
            "title": "Adaptive Graph Neural Networks for Spatiotemporal Forecasting",
            "authors": "Doe, J.",
            "venue": "KDD 2024",
            "date": "2024",
        }
    ]
    assert entity_candidates(import_profile(_text(payload)).canonical_json) == []


def test_append_bullet_to_entity_adds_a_bullet_to_the_chosen_experience() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    result = append_bullet_to_entity(
        canonical_json, "/experience/0", "Preprocessed images with OpenCV."
    )
    assert result.canonical_json["experience"][0]["bullets"] == [
        "Built things.",
        "Preprocessed images with OpenCV.",
    ]
    # Nothing else about the entry changed.
    assert result.canonical_json["experience"][0]["company"] == "Example Corp"


def test_append_bullet_to_entity_adds_a_bullet_to_a_project() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["projects"] = [{"name": "VectorBench", "bullets": ["Built a vector search demo."]}]
    canonical_json = import_profile(_text(payload)).canonical_json
    result = append_bullet_to_entity(canonical_json, "/projects/0", "Added a reranking stage.")
    assert result.canonical_json["projects"][0]["bullets"] == [
        "Built a vector search demo.",
        "Added a reranking stage.",
    ]


def test_append_bullet_to_entity_strips_whitespace() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    result = append_bullet_to_entity(canonical_json, "/experience/0", "  Padded bullet.  ")
    assert result.canonical_json["experience"][0]["bullets"][-1] == "Padded bullet."


def test_append_bullet_to_entity_rejects_empty_bullet_text() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    with pytest.raises(ProfileImportError):
        append_bullet_to_entity(canonical_json, "/experience/0", "   ")


def test_append_bullet_to_entity_rejects_a_malformed_pointer() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    with pytest.raises(ProfileImportError):
        append_bullet_to_entity(canonical_json, "/skills/0", "Something.")


def test_append_bullet_to_entity_rejects_an_out_of_range_index() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    with pytest.raises(ProfileImportError):
        append_bullet_to_entity(canonical_json, "/experience/5", "Something.")


def test_append_bullet_to_entity_rejects_projects_when_there_are_none() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    with pytest.raises(ProfileImportError):
        append_bullet_to_entity(canonical_json, "/projects/0", "Something.")


def test_append_bullet_to_entity_changes_the_content_hash() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    original_hash = import_profile(_text(_MINIMAL_VALID)).content_hash
    result = append_bullet_to_entity(canonical_json, "/experience/0", "Something new.")
    assert result.content_hash != original_hash


def test_append_bullet_to_entity_recomputes_stats_and_facts() -> None:
    payload = dict(_MINIMAL_VALID)
    payload["projects"] = [{"name": "VectorBench", "bullets": []}]
    canonical_json = import_profile(_text(payload)).canonical_json
    result = append_bullet_to_entity(canonical_json, "/projects/0", "Something new.")
    assert result.stats["experience"] == 1
    assert result.stats["projects"] == 1
    assert len(result.career_facts) == 3  # 1 experience + 1 education + 1 project
    assert any(f.fact_type == "project" for f in result.career_facts)


def test_append_bullet_to_entity_does_not_mutate_the_input_canonical_json() -> None:
    canonical_json = import_profile(_text(_MINIMAL_VALID)).canonical_json
    original_bullets = list(canonical_json["experience"][0]["bullets"])
    append_bullet_to_entity(canonical_json, "/experience/0", "Something new.")
    assert canonical_json["experience"][0]["bullets"] == original_bullets
