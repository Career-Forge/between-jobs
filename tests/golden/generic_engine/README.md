# Built-in engine structure fixtures (synthetic, structure-only)

These are the fixtures for `tests/test_generic_engine_structure.py`. They are the documented
exception to this repository's golden convention ([../README.md](../README.md)), and the reason
is stated here so nobody reads them as something they are not.

**What the convention asks.** A fixture ported from a proven reference implementation is
captured from a real run of that reference, and the port ships only when it reproduces it.

**Why this directory is an exception.** The built-in engine (`src/between_jobs/engines/generic/`)
is not a port. Its prompts, its template and its selection rules were written for this
repository from the public contracts, and nothing was captured from any other engine. There is
no reference to match, so there is nothing to capture and **no parity with any other engine
exists or is claimed.** Every input here is made up (a made-up candidate, employer and job
posting, and a made-up profile full of characters that are special to LaTeX), and every
"expected" file was produced by this engine itself.

**What they pin instead.** Only the *structure* of what the engine writes, so that a change to a
template or to the escaping shows up as a reviewed diff, not as a surprise in somebody's PDF:

- the packages a document loads (and that they are all on the PDF renderer's allowlist),
- the order of the resume's sections and how many `\item`s and entries each has,
- which of the escapes in `latex.py` appear (`\&`, `\%`, `\textbackslash{}` ...),
- that braces balance and every environment closes,
- the cover letter's skeleton: header, date, subject, greeting, paragraphs, sign-off.

They deliberately do not pin the model's wording (the test's model is scripted and says exactly
what the test tells it to) or the estimated page fill.

## Layout

```
inputs/     typical_profile.json, special_profile.json, job.json
expected/   <name>.structure.json, one per document the test builds
```

## Updating

When a change to the templates or the escaping is intended, regenerate and review the diff:

```
GENERIC_ENGINE_UPDATE_GOLDENS=1 pytest tests/test_generic_engine_structure.py
git diff tests/golden/generic_engine/expected
```

A diff here is a change to what every user's document looks like. Read it as one.
