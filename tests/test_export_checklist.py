"""Tests for the export validation checklist (Sprint 3.3f).

`build_checklist` is pure -- no network, no LLM, no Supabase client -- so
these tests just feed it real PDF bytes (generated with pypdf itself,
never hand-rolled byte fixtures per this repo's own testing discipline)
and assert on the returned three-state items.
"""

from __future__ import annotations

import io

from pypdf import PdfWriter

from between_jobs.api.export_checklist import ChecklistItem, build_checklist


def _pdf_with_pages(count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _find(items: list[ChecklistItem], key: str) -> ChecklistItem:
    return next(item for item in items if item["key"] == key)


def test_one_page_pdf_passes_the_page_count_check() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "one_page")["status"] == "pass"


def test_two_page_pdf_fails_the_page_count_check() -> None:
    items = build_checklist(_pdf_with_pages(2), warnings=[])
    item = _find(items, "one_page")
    assert item["status"] == "fail"
    assert "2 pages" in str(item["detail"])


def test_unreadable_pdf_bytes_fail_the_page_count_check() -> None:
    items = build_checklist(b"not a pdf", warnings=[])
    assert _find(items, "one_page")["status"] == "fail"


def test_no_warnings_passes_the_warnings_check() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "no_engine_warnings")["status"] == "pass"


def test_warnings_fail_the_warnings_check_and_are_surfaced_in_detail() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=["Borderline seniority match."])
    item = _find(items, "no_engine_warnings")
    assert item["status"] == "fail"
    assert "Borderline seniority match." in str(item["detail"])


# ── C4: claim verification (coverforge-port.md) ─────────────────────────────


def test_no_claim_warnings_passes_the_unsupported_claims_check() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "no_unsupported_claims")["status"] == "pass"


def test_a_claim_warning_fails_the_unsupported_claims_check_and_is_surfaced() -> None:
    items = build_checklist(
        _pdf_with_pages(1),
        warnings=['unsupported claim (contradicted): "Led 20 engineers" -- no match'],
    )
    item = _find(items, "no_unsupported_claims")
    assert item["status"] == "fail"
    assert "Led 20 engineers" in str(item["detail"])


def test_claim_warnings_do_not_leak_into_unrelated_checks() -> None:
    """A claim-verification finding fails ITS OWN check only. It rides the
    same shared warnings list, but `no_engine_warnings` skips it -- one
    flagged claim is one failure, not two."""
    items = build_checklist(
        _pdf_with_pages(1),
        warnings=['unsupported claim (unverifiable): "Reduced cost 90%" -- not found'],
    )
    assert _find(items, "no_unsupported_claims")["status"] == "fail"
    assert _find(items, "no_engine_warnings")["status"] == "pass"
    assert _find(items, "one_page")["status"] == "pass"


def test_engine_and_claim_warnings_each_fail_only_their_own_check() -> None:
    items = build_checklist(
        _pdf_with_pages(1),
        warnings=[
            "Borderline seniority match.",
            'unsupported claim (contradicted): "Led 20 engineers" -- no match',
        ],
    )
    engine = _find(items, "no_engine_warnings")
    claims = _find(items, "no_unsupported_claims")
    assert engine["status"] == "fail"
    assert engine["detail"] == "Borderline seniority match."
    assert claims["status"] == "fail"
    assert "Led 20 engineers" in claims["detail"]
    assert "Borderline seniority match." not in claims["detail"]


def test_a_non_claim_warning_does_not_fail_the_unsupported_claims_check() -> None:
    """Only entries with the real "unsupported claim" prefix count -- an
    unrelated engine warning (e.g. a pin conflict) must not be
    misattributed to claim verification."""
    items = build_checklist(_pdf_with_pages(1), warnings=["pinned item p1 requested 4 bullets..."])
    assert _find(items, "no_unsupported_claims")["status"] == "pass"
    assert _find(items, "no_engine_warnings")["status"] == "fail"


def test_unautomated_checks_report_not_checked_rather_than_a_silent_pass() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    not_checked_keys = {item["key"] for item in items if item["status"] == "not_checked"}
    assert not_checked_keys == {
        "links_valid",
        "text_selectable",
        "fonts_embedded",
        "no_duplicate_bullets",
        # R6: real checks, but honestly not_checked with no shape_report --
        # see the tests below for their real pass/fail behavior.
        "page_fill",
        "page_count_within_shape",
        "pins_honored",
    }


# ── R6: shape_report-driven checks ──────────────────────────────────────────


def test_page_fill_not_checked_without_a_shape_report() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "page_fill")["status"] == "not_checked"


def test_page_fill_passes_at_or_above_the_threshold() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[], shape_report={"fill_ratio": 0.75})
    assert _find(items, "page_fill")["status"] == "pass"


def test_page_fill_fails_below_the_threshold() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[], shape_report={"fill_ratio": 0.5})
    item = _find(items, "page_fill")
    assert item["status"] == "fail"
    assert "50%" in str(item["detail"])


def test_pins_honored_not_checked_without_a_shape_report() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "pins_honored")["status"] == "not_checked"


def test_pins_honored_passes_when_true() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[], shape_report={"pins_honored": True})
    assert _find(items, "pins_honored")["status"] == "pass"


def test_pins_honored_fails_when_false() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[], shape_report={"pins_honored": False})
    assert _find(items, "pins_honored")["status"] == "fail"


def test_page_count_within_shape_not_checked_without_a_shape_report() -> None:
    items = build_checklist(_pdf_with_pages(1), warnings=[])
    assert _find(items, "page_count_within_shape")["status"] == "not_checked"


def test_page_count_within_shape_passes_at_or_under_the_target() -> None:
    items = build_checklist(_pdf_with_pages(2), warnings=[], shape_report={"target_pages": 2})
    assert _find(items, "page_count_within_shape")["status"] == "pass"


def test_page_count_within_shape_fails_when_it_exceeds_the_target() -> None:
    items = build_checklist(_pdf_with_pages(2), warnings=[], shape_report={"target_pages": 1})
    item = _find(items, "page_count_within_shape")
    assert item["status"] == "fail"
    assert "2 pages exceeds" in str(item["detail"])


def test_page_count_within_shape_not_checked_when_pdf_is_unreadable_even_with_a_report() -> None:
    items = build_checklist(b"not a pdf", warnings=[], shape_report={"target_pages": 1})
    assert _find(items, "page_count_within_shape")["status"] == "not_checked"


def test_one_page_and_page_count_within_shape_can_legitimately_disagree() -> None:
    """A 2-page-target locale (UK/IE/IN at most tiers) rendering to 2 real
    pages is CORRECT for that locale -- `one_page` (locale-blind, predates
    R4) still fails it, `page_count_within_shape` (locale-aware) passes it.
    Deliberate, not a bug -- see this module's own docstring on why both
    checks ship side by side rather than one replacing the other."""
    items = build_checklist(_pdf_with_pages(2), warnings=[], shape_report={"target_pages": 2})
    assert _find(items, "one_page")["status"] == "fail"
    assert _find(items, "page_count_within_shape")["status"] == "pass"


def test_an_empty_shape_report_is_treated_the_same_as_no_report() -> None:
    """`{}` is the persisted default for pre-R6 artifact_versions rows
    (the column backfills to '{}'::jsonb) -- must read as "nothing to
    check against," not as a report whose every field happens to be
    missing/None triggering some other code path."""
    items = build_checklist(_pdf_with_pages(1), warnings=[], shape_report={})
    assert _find(items, "page_fill")["status"] == "not_checked"
    assert _find(items, "pins_honored")["status"] == "not_checked"
    assert _find(items, "page_count_within_shape")["status"] == "not_checked"
