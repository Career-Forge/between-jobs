-- Job Finder P4c (live-search-track.md) -- Adzuna and USAJobs both need a
-- SECOND real value alongside their main secret (Adzuna: app_id + app_key;
-- USAJobs: an Authorization-Key AND a registered email sent as User-Agent),
-- and `provider_credentials` was built around exactly one secret per
-- (user, service, provider) row. Rather than force either provider's
-- second value into `base_url` (semantically wrong -- neither is an
-- endpoint override) or add provider-specific columns, this adds ONE
-- generic nullable `secret_2_encrypted` column -- meaning varies per
-- provider (documented at the call site, not in the schema), matching the
-- existing `model`/`base_url` precedent of typed-but-provider-agnostic
-- optional columns rather than a JSON blob (this project's own
-- "shared state lives in Postgres rows... never a mutable JSON blob"
-- rule). Nullable and unused by every existing single-secret provider
-- (OpenRouter, You.com, Firecrawl, Serper, Brave, JSearch) -- this is a
-- pure addition, no backfill needed.
alter table public.provider_credentials
  add column secret_2_encrypted text;
