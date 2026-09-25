# Between Jobs -- Engineering Conventions

Open-source core of [between-jobs.tech](https://between-jobs.tech). AGPL-3.0. This repo
is public: every byte committed here will be read by strangers. Write code, comments,
and commit messages accordingly.

## Stack

- Python 3.12+ with FastAPI -- the core service (orchestrator, agents, channel adapters).
- Supabase -- auth, Postgres (+ pgvector), RLS multi-tenancy, storage, Realtime.
- Web frontend (later phase): React + TypeScript, strictly a thin client. Channels
  (web, Telegram, and whatever comes next) are renderers; logic lives in the core service.
- Tooling: ruff (lint + format), mypy (strict), pytest.

## Commands

```
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                      # tests
ruff check . && ruff format --check .   # lint + formatting
mypy                        # types (strict)
```

All of the above must pass before any commit.

## Database migrations

```
supabase migration new <name>          # create the file; never hand-pick its timestamp
supabase db reset --local              # replay every migration on a local stack
supabase db push --dry-run --linked    # see exactly what would run on the linked project
supabase db push --linked              # ship it
```

Every file in `supabase/migrations/` carries the version prod recorded when it was
applied, so `supabase migration list --linked` must show local and remote matched. Push
to a separate dev project first once one exists; until then the linked project is prod,
so read the dry run before every push. Never change the database any other way: SQL run
in the dashboard records no version at all, and MCP `apply_migration` records a version
no file has. Never change the SQL of a migration that has already been applied -- ship
the change as a new migration. Never run `supabase config push`: `supabase/config.toml`
holds local-development auth settings, not prod's.

## Testing & parity discipline

- Behavior ported from a proven reference implementation ships only when it reproduces
  the reference's outputs on real captured fixtures -- diff-clean, no exceptions.
  Convention and rules: [tests/golden/README.md](tests/golden/README.md).
- Parity fixtures are captured from real runs, never invented by hand. Sanitize before
  committing (see the never-commit list below).
- New behavior needs tests. Small verified increments over big-bang drops.

## Engineering rules

- Deterministic code owns structure and shape; LLMs choose words only.
- Every retry, regeneration, or agent loop has a hard cap.
- Shared state lives in Postgres rows and transactions -- never a mutable JSON blob.
- Unknown means labeled as unknown, never guessed. Where certainty varies, model it
  explicitly (three-state over boolean).
- The human always performs the final irreversible action -- submitting an application,
  sending an email. The platform drafts and prepares; it never fires.
- BYOK-first: this repo must be genuinely useful standalone with user-supplied keys and
  its built-in generic prompts. No demo shells.
- Content from third-party MCP servers, scraped pages, or parsed emails is untrusted
  input. Treat it like user input from a stranger.

## Never commit (repo policy)

- Secrets: `.env` files, API keys, tokens, credentials of any kind.
- Personal data: real resumes, contact lists, email contents -- including inside test
  fixtures. Sanitize first.
- Hosted-service internals: prompt packs, scoring rubrics, calibration data, ATS
  selector maps, registry or interview-intel datasets. Those belong to the hosted
  services, not this repo.
- Private planning documents and local notes. `CLAUDE.local.md` is gitignored --
  keep it that way.

## Commits & contributions

- Commit messages are meaningful, imperative, and human-written. No AI attribution,
  no co-author trailers, no tool fingerprints -- ever.
- Outside contributions require a signed CLA. See [CONTRIBUTING.md](CONTRIBUTING.md).
