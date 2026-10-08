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

## Behaviour with no reference implementation (the Discord adapter)

The rules above are for behaviour ported from a proven implementation. The Discord adapter has
none: it is new, written from Discord's published documentation, and no run of anything exists to
capture. Its tests (`tests/discord_fakes.py` explains each piece) therefore pin its own contract
with **synthetic** fixtures, and say so: interactions written by hand in the shape Discord
documents, signed with a throwaway Ed25519 key generated in the test process and used nowhere
else, against a recording fake of Discord's REST API. Nothing in them is, or was derived from, real
Discord data, a real application, a real token or a real person.

This is not an exception to rule 1: nothing here claims to match a reference. What these tests
cannot show is how the real Discord behaves, and they do not pretend to; the list below says what
only the real service can answer. The Telegram bot's golden file
(`telegram_bot/expected/telegram_calls.json`) is untouched by the Discord work and still has to
pass byte for byte.

### Not verified against the real Discord

Each of these is checked by hand against a real application before a release that changes the
code around it, and until then is an assumption taken from Discord's documentation:

- That Discord accepts the endpoint's answer to its signed ping, and to the requests with an
  invalid signature it sends on purpose when an Interactions Endpoint URL is saved.
- That the acknowledgement lands inside Discord's three seconds under real network latency (the
  tests bound the work done before it, not the network).
- Whether an app that a person installed to their own account only (no shared server) can open a
  direct message and send into it unprompted, and the exact status and error code of a refused
  message (the code treats 50007 and 40003 as Discord's documentation describes them).
- How many follow-ups an interaction from such an install may make, and how long its token lasts
  in practice.
- Downloading an attached file from Discord's CDN: the address shape, how long it stays valid, and
  that no redirect is involved.
- That Discord accepts the slash-command definitions the registration script sends, including the
  installation types and the direct-message-only context.
- How Discord's client draws the escaped markdown the app sends. Discord does not publish its
  tokenizer; the escaping is written against the address pattern that discord.py and the
  simple-markdown library under Discord's client both use.
