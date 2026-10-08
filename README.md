# Between Jobs

An open-source, bring-your-own-keys job-search workbench: find roles, track applications,
prepare application documents, practice interviews. This repository is the open-source
core of Between Jobs. A hosted version at [between-jobs.tech](https://between-jobs.tech)
is coming soon; it is not live yet.

**Status: pre-alpha.** The API, the web app and the browser extension all run from this
repository. One piece does not: resume and cover-letter generation needs a separate
engine service that is not part of this repository yet. Read "What works today" before
you start.

## Principles

- **BYOK-first.** You supply your own LLM and search keys; the platform has no shared
  key behind it. The scoring and research prompts shipped here are generic ones.
- **The human always performs the submit.** The platform drafts and prepares; it never
  submits an application or sends an email for you. The browser extension fills a form
  and stops. Gmail integration creates drafts and never sends; it also asks for read
  access, so that a background worker can read the replies to those drafts (see "What
  works today").
- **Your data lives in your own Supabase project.** Resume content, job descriptions and
  similar text are also sent to the LLM and search providers whose keys you supply, and
  if you connect Gmail, the text of replies to the drafts the app created also goes to
  your LLM provider. There are no third-party analytics or tracking SDKs in this repository.

## What works today

Running the API against a Supabase project, with an [OpenRouter](https://openrouter.ai)
key saved in the web app (Integrations page), these work. Where a feature also needs a
search or scraping key, it says so:

- **Sign-in and your profile.** Email and password through Supabase Auth (Google sign-in
  if you enable that provider). Your resume is imported as structured JSON in a fixed
  template; it is validated deterministically, versioned, and one version is active.
- **Applications.** Add a posting by pasting it, or from a URL (a posting already in the
  job registry needs nothing; any other URL needs a Firecrawl key). Track it on a board or
  list through the stages saved, applied, screening, interviewing, offer, rejected and
  withdrawn.
- **Discover.** Search the job registry (see "Self-hosting the job registry") and the
  live-search providers you have keys for, merge and de-duplicate the results, check that
  live-search links are still alive, and score fit against your profile with one batched
  LLM call. Saved searches run in the background and put strong new matches on your
  Today feed.
- **Company research and outreach support.** Research a company with cited sources and
  find named contacts (both need a You.com or Firecrawl key; Apollo, Hunter and Exa keys
  are optional enrichment), and draft outreach.
- **Gmail (optional).** Creates drafts in your own Gmail and never sends. It asks Google
  for `gmail.compose` and `gmail.readonly`. A background reply checker (on by default;
  set `DISABLE_GMAIL_REPLY_CHECKER` to turn it off) reads the thread of each draft the
  app created, to see whether you sent it and whether anyone replied. It has your LLM key
  classify the newest reply, moves that application to the matching stage when it is
  confident (0.85 or above), and queues anything else it proposes for your review on the
  Today feed.
- **Hiring signals.** Hiring-intent posts found through search-index results; needs a
  Brave, Serper, Firecrawl or You.com key saved on the Integrations page. The code never
  fetches linkedin.com itself.
- **Telegram bridge (optional).** Import a resume, paste a posting, list applications,
  and receive a message when a saved search finds a strong match. A resume generation is one
  message the bot edits as the work advances. `/privacy` summarises what is stored and who
  handles it, and answers every chat (it reads no data; a chat with no web account gets the
  wording that applies to it). `/learn` walks the first-run checklist and answers only a chat
  that is linked to a web account.
- **Browser extension (Chrome).** On the Lever, Greenhouse and Ashby application page of a
  posting you are tracking, it fills your contact fields from your profile and drafts
  answers to custom questions with your own key. It never clicks submit.

### What needs an engine service that is not in this repository yet

Resume and cover-letter generation is done by a separate HTTP service, called
`forge-engines` in the code. **It does not exist in this repository yet, so generation
does not work from a clean checkout.** Until a public engine lands, these features fail
with a retryable `PROVIDER_UNAVAILABLE` error ("Couldn't reach the resume engine"):

- generating a resume and cover letter for an application (`POST /applications/{id}/prepare`,
  and the Telegram bot's "Generate resume" button and "apply to #N", which run the same
  preparation), and with them everything that works from the generated documents -- PDF
  download, the export checklist, and attaching a resume or cover letter in the extension;
- the Tailor panel's coverage analysis, the gap interview, and the resume header preview;
- interview practice (it builds its context through the engine's ingest step).

The API reaches the engine at `FORGE_ENGINES_BASE_URL` (default `http://localhost:5682`).
The HTTP client, with every endpoint it calls (`/apply`, `/step0`, `/gap-interview`,
`/gap-interview/draft`, `/ingest`, `/personal`, `/header/resolve`), is
`src/between_jobs/api/forge_engines_client.py`, and the response types are in
`src/between_jobs/api/engine_contract.py`. The protocols in `src/between_jobs/engines/`
are the seam a bring-your-own engine is meant to implement; nothing implements them yet,
and the HTTP client does not go through them today. PDF output also needs the LaTeX
service in `latex-service/` (see its README).

## Repository layout

| Path | What it is |
| --- | --- |
| `src/between_jobs/api/` | The FastAPI service: routes, stores, background workers, provider clients |
| `src/between_jobs/engines/` | Protocols for a resume or score engine (no implementation yet) |
| `web/` | The web app: React and TypeScript, a thin client over the API |
| `extension/` | The Chrome extension (WXT, React): ATS autofill |
| `latex-service/` | A small, separate service that compiles LaTeX to PDF |
| `supabase/migrations/` | The database schema, as migration files |
| `scripts/` | Operator scripts, run by hand (reference-data imports, registry sampling, Telegram webhook registration, account deletion) |
| `tests/` | The Python test suite |

## Set it up

You need Python 3.12 or newer, Node 22.22.2 or newer on the 22 line, 24.15 or newer on the
24 line, or 26 or newer (CI uses Node 24; other releases, such as 23 or 25, make `npm ci`
in `extension/` print engine warnings), Docker, and the
[Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).

Steps 2 and 3 each start a server that keeps its terminal, so run each in its own
terminal, and step 4 in a third (or after stopping the dev server). Open each one in the
repository root: every `cd` below starts from there.

### 1. The database (Supabase)

Everything the API stores lives in Supabase: auth, Postgres, storage. The schema is the
files in `supabase/migrations/`, and **migrations are applied with the Supabase CLI,
never by running SQL in the dashboard** (SQL run there records no migration version,
which breaks the repository's migration drift check).

A local stack, which needs Docker and is the easiest way to try it:

```bash
supabase start                 # starts the stack; a first start applies every migration
supabase status -o env         # prints API_URL, SERVICE_ROLE_KEY, PUBLISHABLE_KEY, ...
```

(On a local stack that already holds data from an older checkout, `supabase db reset
--local` replays all the migrations, and erases that local data.)

A hosted project instead: create one in the Supabase dashboard, then

```bash
supabase link --project-ref <your-project-ref>
supabase db push --dry-run --linked    # read what would run
supabase db push --linked
```

(These are the commands `CLAUDE.md` and the CI workflows use; they have not yet been run
against a hosted project from this page.)

Do not skip the migrations: until they are applied the API's background workers report
`lease_unknown` and `/health` answers 503, on purpose.

### 2. The API

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # ".[dev]" adds the test tools; plain "." is enough to run it
cp .env.example .env           # set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
uvicorn between_jobs.api.app:app --reload --port 8012   # keeps this terminal; leave it running
```

From another terminal, check that it is up:

```bash
curl localhost:8012/health
```

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are the only required variables (from
`supabase status -o env` for a local stack, or the dashboard's Project Settings -> API for
a hosted project). `.env.example` lists every variable the API reads, with its default.
Port 8012 is what the web app's dev proxy and the extension's example setting expect.
The API starts its background workers in its own process; see "One container, one process"
below before running more than one copy.

### 3. The web app

```bash
cd web
npm ci
cp .env.example .env.local     # set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
npm run dev                    # http://localhost:5173
```

The dev server forwards `/api/*` to the API on `localhost:8012`, so leave
`VITE_API_BASE_URL` unset while developing. A production build (`npm run build`) refuses to
run without `VITE_API_BASE_URL` set to the API's public `https://` origin; `web/.env.example`
explains why. Create an account on the sign-in page, open Integrations and save an
OpenRouter key, then import your resume JSON on the Profile page.

### 4. The browser extension

```bash
cd extension
npm ci
cp .env.example .env.local     # set WXT_SUPABASE_URL, WXT_SUPABASE_PUBLISHABLE_KEY, WXT_API_BASE_URL
npm run build                  # writes the unpacked extension to build/chrome-mv3/
```

In Chrome 148 or newer, open `chrome://extensions`, turn on Developer mode, choose "Load
unpacked" and select `extension/build/chrome-mv3`. Sign in from the side panel with the
same account. See `extension/README.md` for what it does, what it sends to the API, and
the store-build rules; it is not published to the Chrome Web Store.

### Optional: Telegram, Gmail, the LaTeX service

These are off until configured. The Telegram bridge needs `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_WEBHOOK_SECRET` and a public HTTPS URL for the webhook, which
`scripts/set_telegram_webhook.py` registers; Gmail drafts need a Google OAuth client, and
the Google consent includes read access to Gmail (see "What works today"); PDF output
needs `latex-service/` running. `.env.example` explains each one. Setting `WEB_APP_URL` to
the web app's public https address lets the bot's `/privacy` and `/learn` link to its pages.

## Run the API with Docker

```bash
cp .env.example .env   # then fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
docker build -t between-jobs-api .
docker run -d --rm --name between-jobs-api -p 8000:8000 --env-file .env between-jobs-api
curl localhost:8000/health     # give it a few seconds to start; `docker logs between-jobs-api` if it does not answer
docker stop between-jobs-api   # stops it and removes the container
```

`--env-file` keeps the service-role key out of your shell history and out of `ps`. Docker
reads it as plain `KEY=value` lines (no quotes, no `export`), and the image never
contains it: the build copies only the package.

- **Apply the database migrations first** (`supabase db push`). Until they are there the
  background workers that need them report it and `/health` answers 503, on purpose.
- Inside the container `127.0.0.1` is the container itself, so it cannot reach a local
  Supabase stack at the address `supabase status` prints. Point `SUPABASE_URL` at the
  host instead (on Docker Desktop, `http://host.docker.internal:54321`), or use a hosted
  project.
- Configuration is environment variables; `.env.example` lists them all. Telegram is
  optional -- leave `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` unset to run
  web-only.
- **One container, one process.** The API runs its own background workers, so do not
  pass `--workers`. If a second copy ever starts (an overlapping deploy, a stray
  replica), the workers that would otherwise do the same work twice stand by instead.
- The container exits cleanly on SIGTERM. Give your platform a grace period longer than
  30 seconds before it escalates to SIGKILL (on Railway, set
  `RAILWAY_DEPLOYMENT_DRAINING_SECONDS`; its default is 0): the API waits up to 20 seconds
  for open requests, then up to 5 more for a Telegram-started resume generation to finish
  before it cancels one. A generation cut off by a restart is not resumed: the person
  sends the request again.

## Operations

Nothing here reports to anyone unless you configure it (`.env.example` lists every setting).
Set `SENTRY_DSN` and the API sends Sentry its unhandled exceptions, API errors answered with a
5xx, failed background-worker ticks and chat-started generations that crashed, with request
bodies, query strings, cookies, all but a few ordinary headers, local variables, user email and
address and the messages of database, validation, provider and API errors left out, and known
secret shapes (JWTs, bearer tokens, API keys, bot tokens, email addresses) removed from the text
that remains. That last step is a safety net, not a guarantee: read `src/between_jobs/api/sentry_scrub.py` before pointing it at real traffic. Set a
`HEALTHCHECKS_URL_*` setting and the matching worker sends that Healthchecks.io check an empty
GET after each successful tick, so you hear when it goes quiet; it carries no user data. For an
uptime monitor use `/health`, which answers 503 when a worker has died or stalled.

## Self-hosting the job registry

The job registry is the list of company job boards that the API's background poller
reads, plus every posting it has seen; Discover searches it alongside the live-search
providers. **The hosted service's registry data is not part of this repository.** The
migrations create the tables empty, so a self-hosted install starts with an empty
registry and relies on the live-search providers (RemoteOK and Arbeitnow need no key;
the others use the keys you save on the Integrations page) until you load your own.

To load your own, add rows to `job_registry_companies`: a `name`, an `ats_type` (one of
greenhouse, lever, ashby, workday, smartrecruiters, workable, recruitee, amazon, apple,
deshaw, oracle, eightfold, avature, successfactors, google), the board's `slug` and, for the
ATS types that need one, an `api_base`. That is data, not schema, so a plain SQL insert or
the Supabase table editor is fine. What `slug` and `api_base` mean depends on the ATS
(values below are placeholders):

| `ats_type` | `slug` | `api_base` |
| --- | --- | --- |
| greenhouse, lever, ashby | The board token in the public board URL (`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`) | Leave empty |
| smartrecruiters, workable | The company identifier in the public board URL (`jobs.smartrecruiters.com/<slug>`, `apply.workable.com/<slug>`) | Leave empty |
| recruitee | The subdomain in `<slug>.recruitee.com` | Leave empty |
| workday | The career-site name, the first path segment of the board URL (`External` in `acme.wd5.myworkdayjobs.com/External`) | Required: `tenant.wdN`, for example `acme.wd5`. A bare tenant name fails |
| oracle | The candidate-experience site number, the segment after `/sites/` in the board URL (for example `CX_1001`) | Required: the Oracle Fusion pod host, no scheme (for example `abcd.fa.us2.oraclecloud.com`) |
| eightfold | The `domain` value the tenant's search requests carry, usually the company's own domain (for example `acme.com`) | Required: the host that serves the tenant, no scheme (for example `acme.eightfold.ai`) |
| successfactors | Only a label for the board (it is part of the board's key, not of the request) | Required: the career-site host, no scheme |
| avature | The tenant subdomain in `<slug>.avature.net`, or the full host when the board runs on its own domain (a `slug` containing a dot) | Optional: `portal` or `portal/listingPage`; the default is `careers/SearchJobs` |
| amazon, apple, deshaw, google | Any label: each reads one fixed board and ignores both columns | Leave empty |

`(ats_type, slug, api_base)` must be unique. A row with a missing or malformed `api_base`
does not fail on insert: it shows up as a failed poll, with `consecutive_failures` going
up. The poller starts with up to eight never-polled boards on each 15-minute tick. There
is no importer for a raw company list in `scripts/`; what is there:

- `scripts/copy_registry_sample.py` copies a sample of a registry from one Supabase
  project to another, so it needs a project that already has one.
- `scripts/import_geo_gazetteer.py` and `scripts/import_company_tiers.py` each load an
  optional reference table (a city gazetteer for location matching, and company tiers for
  the company-health score) from a JSON file you point them at with `--source`. The data
  files are not in this repository, so you build them: each script's `--help` gives the
  exact shape, the gazetteer can come from GeoNames' `cities15000` dump (CC BY 4.0, so
  keep the attribution), and the tier weights are a list you curate. `--dry-run` counts
  the rows (and, for the tiers, checks the keys) without writing. Discover still works
  without either table: the location filter then does nothing, and the company-health
  score is marked not applicable.
- `scripts/sign_and_publish_ats_field_map.py` signs the extension's per-ATS field maps;
  its docstring explains the key handling. The signed maps are not in this repository
  either, so the extension runs on its open-source generic defaults.
- `scripts/set_telegram_webhook.py` points a Telegram bot at the API's webhook; the
  `TELEGRAM_WEBHOOK_SECRET` comment in `.env.example` shows how to run it.
- `scripts/delete_account.py` and `scripts/check_migration_drift.py` are operator tools.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and checks, and
[CLAUDE.md](CLAUDE.md) for the engineering conventions.

## License

AGPL-3.0 -- see [LICENSE](LICENSE).
