# Between Jobs

The open-source job-search platform -- [between-jobs.tech](https://between-jobs.tech).

Job searching is expensive at exactly the moment you can least afford it. Between Jobs
is built to be genuinely free for the people who need it most: bring your own LLM keys
(BYOK) and the platform runs standalone -- job discovery, tailored application
documents, application tracking, and interview prep -- with your data staying on your
machine.

**Status: pre-alpha.** The spine (a FastAPI app + Supabase-backed session store, real
auth) runs. A Telegram bridge receives messages, auto-links an identity, and can take
you through the one real user-facing flow that exists so far: send it your resume as
JSON (a fixed template it'll walk you through), review the preview, and confirm --
that becomes your canonical profile. There's no agent runtime, job search, or document
generation wired up yet, so beyond that one flow it can't do anything a user would
recognize as the product.

## Principles

- **BYOK-first.** Fully usable with your own keys and the built-in prompts. Not a demo
  shell for a paid service.
- **You always click submit.** The platform drafts, tailors, and prepares; it never
  auto-applies or auto-sends anything on your behalf.
- **Privacy.** Self-hosted means your resume and your data never leave your machine.

## Run the API with Docker

```bash
cp .env.example .env   # then fill in SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
docker build -t between-jobs-api .
docker run --rm -p 8000:8000 --env-file .env between-jobs-api
curl localhost:8000/health
```

`--env-file` keeps the service-role key out of your shell history and out of `ps`. Docker
reads it as plain `KEY=value` lines (no quotes, no `export`), and the image never
contains it: the build copies only the package.

- **Apply the database migrations first** (`supabase db push`). Until they are there the
  background workers that need them report it and `/health` answers 503, on purpose.
- Configuration is environment variables; `.env.example` lists them all. Telegram is
  optional -- leave `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` unset to run
  web-only.
- **One container, one process.** The API runs its own background workers, so do not
  pass `--workers`. If a second copy ever starts (an overlapping deploy, a stray
  replica), the workers that would otherwise do the same work twice stand by instead.
- The container exits cleanly on SIGTERM. Give your platform a grace period longer than
  20 seconds before it escalates to SIGKILL (on Railway, set
  `RAILWAY_DEPLOYMENT_DRAINING_SECONDS`; its default is 0).

## License

AGPL-3.0 -- see [LICENSE](LICENSE).
