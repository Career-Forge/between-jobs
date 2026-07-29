# Contributing

Between Jobs is early -- pre-alpha -- and moving fast. Issues and small PRs are
welcome; for anything large, open an issue first so effort isn't wasted on a direction
that won't merge.

## CLA

All outside contributions require a signed Contributor License Agreement. CLA signing
(via [cla-assistant](https://cla-assistant.io)) will be wired into CI before the first
external PR is merged; opening a PR signals you're willing to sign it.

## Dev setup

Requires Python 3.12+.

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # fill in SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY to run the API
```

Run the API locally: `uvicorn between_jobs.api.app:app --reload`

Before pushing, all of these must pass:

```
pytest
ruff check .
ruff format --check .
mypy
```

## Testing discipline

- New behavior needs tests.
- Behavior ported from a reference implementation needs parity tests against golden
  fixtures -- see [tests/golden/README.md](tests/golden/README.md).

## Commit messages

Imperative, meaningful, human-written. No AI attribution or co-author trailers.
