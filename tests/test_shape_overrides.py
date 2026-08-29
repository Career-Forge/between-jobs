"""Tests for R6's shape-overrides precedence and system defaults
(resumeforge-shape-and-fit.md)."""

from __future__ import annotations

from between_jobs.api.shape_overrides import merge, resolve


def test_merge_prefers_per_application_over_master_field_by_field() -> None:
    master = {"page_count": "1", "density": "compact", "show_gpa": True}
    per_application = {"page_count": "2"}

    merged = merge(master, per_application)

    assert merged["page_count"] == "2"
    assert merged["density"] == "compact"
    assert merged["show_gpa"] is True


def test_merge_with_no_per_application_returns_master_untouched() -> None:
    master = {"summary": "on"}
    assert merge(master, None) == {"summary": "on"}
    assert merge(master, {}) == {"summary": "on"}


def test_merge_with_no_master_returns_per_application() -> None:
    per_application = {"region": "UK"}
    assert merge(None, per_application) == {"region": "UK"}


def test_merge_with_neither_returns_empty() -> None:
    assert merge(None, None) == {}
    assert merge({}, {}) == {}


def test_resolve_with_nothing_set_returns_the_system_defaults() -> None:
    resolved = resolve({})
    assert resolved.page_count_override is None
    assert resolved.density == "balanced"
    assert resolved.summary_mode == "off"
    assert resolved.bullet_lead_in == "none"
    assert resolved.region is None
    assert resolved.show_gpa is False
    assert resolved.show_nationality is False


def test_resolve_page_count_auto_and_explicit_values() -> None:
    assert resolve({"page_count": "auto"}).page_count_override is None
    assert resolve({"page_count": "1"}).page_count_override == 1
    assert resolve({"page_count": "2"}).page_count_override == 2


def test_resolve_density_passes_through_each_value_directly() -> None:
    # S6 (honest-score-surfaces.md): a direct passthrough now that
    # forge-engines has a real third rendering tier -- no boolean collapse.
    assert resolve({"density": "compact"}).density == "compact"
    assert resolve({"density": "balanced"}).density == "balanced"
    assert resolve({"density": "spacious"}).density == "spacious"


def test_resolve_bullet_style_translates_to_forge_engines_lead_in_names() -> None:
    assert resolve({"bullet_style": "plain"}).bullet_lead_in == "none"
    assert resolve({"bullet_style": "bold_lead_in"}).bullet_lead_in == "bold_keyword"


def test_resolve_summary_passes_through_explicit_choices() -> None:
    assert resolve({"summary": "auto"}).summary_mode == "auto"
    assert resolve({"summary": "on"}).summary_mode == "on"
    assert resolve({"summary": "off"}).summary_mode == "off"


def test_resolve_region_has_no_system_default() -> None:
    assert resolve({}).region is None
    assert resolve({"region": "IN"}).region == "IN"


def test_resolve_show_gpa_passes_through_explicit_true() -> None:
    assert resolve({"show_gpa": True}).show_gpa is True
    assert resolve({"show_gpa": False}).show_gpa is False


def test_resolve_show_nationality_defaults_off_and_passes_through_explicit_true() -> None:
    # S5 (honest-score-surfaces.md, D1): opt-in, unlike show_gpa's own
    # "generalized existing rule" framing -- this is a brand new default.
    assert resolve({}).show_nationality is False
    assert resolve({"show_nationality": True}).show_nationality is True
    assert resolve({"show_nationality": False}).show_nationality is False


def test_merge_then_resolve_end_to_end_per_application_overrides_master() -> None:
    master = {"page_count": "1", "summary": "on", "show_gpa": True}
    per_application = {"page_count": "2"}

    resolved = resolve(merge(master, per_application))

    assert resolved.page_count_override == 2
    assert resolved.summary_mode == "on"
    assert resolved.show_gpa is True
