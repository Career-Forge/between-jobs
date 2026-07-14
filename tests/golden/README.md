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
