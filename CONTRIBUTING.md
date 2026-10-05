# Contributing

Between Jobs is early -- pre-alpha -- and moving fast. Issues and small PRs are
welcome; for anything large, open an issue first so effort isn't wasted on a direction
that won't merge.

## CLA

Outside contributions require a signed Contributor License Agreement. Nothing in CI checks
for one yet and the signing flow is not set up yet, so there is no CLA text in this
repository to sign today; the maintainer will arrange it before an outside pull request is
merged. Opening a pull request signals you are willing to sign.

## Dev setup

The repository has four parts, each with its own toolchain: the Python API (repository
root), the web app (`web/`), the Chrome extension (`extension/`) and the LaTeX service
(`latex-service/`). [README.md](README.md) has the full setup, including the Supabase
database; the short version:

Requires Python 3.12+, Node 22.22.2+ on the 22 line, 24.15+ on the 24 line or 26+ (CI
uses Node 24; other releases, such as 23 or 25, make `npm ci` in `extension/` print engine
warnings), Docker and the Supabase CLI.

Run the API and the web dev server each in its own terminal, since both keep running.
Every block below starts in the repository root.

```
# terminal 1: the database and the API
supabase start                    # a local database; applies the migrations on first start
supabase status -o env            # API_URL and SERVICE_ROLE_KEY for .env, PUBLISHABLE_KEY for web/ and extension/

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY; every other variable is optional
uvicorn between_jobs.api.app:app --reload --port 8012
```

```
# terminal 2: the web app
cd web && npm ci && cp .env.example .env.local   # fill in the Supabase values
npm run dev                       # http://localhost:5173, proxies /api to the API on port 8012
```

```
# terminal 3: the extension
cd extension && npm ci && cp .env.example .env.local
npm run build                     # load extension/build/chrome-mv3 unpacked in chrome://extensions
```

To receive real Telegram updates locally, the API needs a public HTTPS URL (any tunnel
will do) and the webhook registered once -- the `TELEGRAM_WEBHOOK_SECRET` comment in
`.env.example` shows how to run `scripts/set_telegram_webhook.py` (try `--dry-run` first).
Telegram is optional.

Resume and cover-letter generation calls a separate engine service that is not in this
repository (see "What works today" in the README), so those paths cannot be exercised from
a clean checkout; the tests use fakes for it.

## Checks

Before pushing, all of these must pass.

The API (from the repository root, with the virtualenv active):

```
pytest
ruff check .
ruff format --check .
mypy
```

`pytest` skips the tests marked `local_supabase`. They run against a throwaway local
Supabase stack (`supabase start`, never a hosted project) and CI runs them in their own
job:

```
pytest -m local_supabase
```

The web app (`web/`):

```
npm run typecheck
npm test
VITE_API_BASE_URL=https://api.example.com npm run build   # the build needs an https, non-local API origin; this is a stand-in
```

The extension (`extension/`):

```
npm run typecheck
npm test
npm run build
```

The LaTeX service (`latex-service/`) has its own checks; see its README.

## Database migrations

Schema changes are new files in `supabase/migrations/`, made with `supabase migration new
<name>` and applied through the Supabase CLI -- never SQL run in the dashboard, and never
an edit to a migration that has already been applied. [CLAUDE.md](CLAUDE.md) has the rules,
including the explicit grants every new table or function needs.

## Testing discipline

- New behavior needs tests.
- Behavior ported from a reference implementation needs parity tests against golden
  fixtures -- see [tests/golden/README.md](tests/golden/README.md).

## Commit messages

Imperative, meaningful, human-written. No AI attribution or co-author trailers.
