# Between Jobs

The open-source job-search platform -- [between-jobs.tech](https://between-jobs.tech).

Job searching is expensive at exactly the moment you can least afford it. Between Jobs
is built to be genuinely free for the people who need it most: bring your own LLM keys
(BYOK) and the platform runs standalone -- job discovery, tailored application
documents, application tracking, and interview prep -- with your data staying on your
machine.

**Status: pre-alpha.** The spine (a FastAPI app + Supabase-backed session store, real
auth) runs, and a Telegram bridge can receive messages and auto-link an identity -- but
there's no agent runtime behind it yet, so it can't actually do anything a user would
recognize as the product.

## Principles

- **BYOK-first.** Fully usable with your own keys and the built-in prompts. Not a demo
  shell for a paid service.
- **You always click submit.** The platform drafts, tailors, and prepares; it never
  auto-applies or auto-sends anything on your behalf.
- **Privacy.** Self-hosted means your resume and your data never leave your machine.

## License

AGPL-3.0 -- see [LICENSE](LICENSE).
