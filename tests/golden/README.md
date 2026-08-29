# Golden-fixture parity tests

When behavior is ported from a proven reference implementation, the port ships only
when it reproduces the reference's outputs on captured fixtures -- diff-clean.

## Rules

1. **Fixtures come from real runs.** Capture actual inputs and outputs from the
   reference implementation. Never invent parity fixtures by hand -- a synthetic
   fixture proves the port matches your imagination, not the reference.
2. **Sanitize before committing.** No real personal data (resumes, names, emails,
   phone numbers), no credentials, no proprietary material. Replace consistently so
   the fixture still exercises the same code paths.
3. **A fixture that can't be made public stays out.** Keep it in a private fixture
   store and make the corresponding test skip with an explicit reason
   (`pytest.mark.skip(reason="private fixture: <name>")`) -- never let it silently
   pass.

## Layout

```
tests/golden/<feature>/
    inputs/       # captured inputs
    expected/     # captured reference outputs
```

Parity tests live in `tests/` and load from here. A parity test asserts equality (or
an explicitly documented tolerance) between the port's output and `expected/` --
if the diff isn't clean, the port isn't done.

## Deliberate divergence (ResumeForge shape-and-fit, R1-R7, 2026-08-22/23)

Not every departure from n8n's captured output is a bug to fix. Decision #5 of
`resumeforge-shape-and-fit.md` explicitly approved diverging from the n8n reference
wherever it was proven wrong, while keeping what's proven and untouched (assembly
calibration, the ATS rubric, seniority/gates). A golden-fixture diff against these
paths is EXPECTED, not a regression:

- The allocator's caps-only selection (n8n) vs. cap-AND-floor (`_fill_to_line_budget`,
  R2b) -- n8n never fills a section back up when it's under-budget, this port does.
- Bullet lead-in defaults to `none` (decision #2), not n8n's keyword-bolded style --
  a per-user/per-document override, not a removal.
- Bullet truncation is real Lato-glyph-width line measurement (R3, `text_metrics.py`)
  in place of n8n's flat character-count budgets -- the two will disagree on exactly
  where a long bullet gets cut.
- Locale/region page-length and field norms (R4's `locale_profiles_v2.json`) correct
  several real bugs in n8n's own reference data (UK/Ireland's page-length inversion,
  Canada wrongly merged with the US, Netherlands wrongly grouped with the Nordics on
  an opposite photo/DOB convention, among others) -- diverging here is the fix, not
  a gap.

`n8n_legacy` fixtures stay frozen exactly as captured either way -- they document
what the reference actually did, which is what makes a deliberate divergence
checkable at all.
