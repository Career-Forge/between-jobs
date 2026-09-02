-- Job Finder registry, P1 (job-finder-port.md D2) -- a faithful port of
-- n8n's own companies/jobs polling-registry schema (n8n/db/schema.sql,
-- CareerForge_ATS_Poller.json), not a fresh design merely informed by
-- reading it. Column names, comments, and dedup constraints mirror the
-- real, already-debugged reference schema line for line; only the primary
-- key type (uuid, not bigserial) and RLS follow this codebase's own
-- convention instead of n8n's, matching D2's "translated into this
-- project's own idiom, not redesigned" scope.
--
-- Named job_registry_* rather than companies/jobs specifically to avoid
-- any collision with this project's OWN "jobs"/"job_snapshots" tables
-- (create_jobs_and_snapshots.sql) -- those hold a per-application job
-- snapshot a user is tracking; this holds a shared, cross-user polling
-- registry of every job posting the platform's own poller has ever seen.
-- The two are related only at the application layer (Discover's "Track"
-- action reads a row here and writes a fresh, independent row there,
-- exactly like today's n8n-facade version already does) -- never a
-- foreign key between them.
--
-- Shared, cross-user reference data -- no user_id, same "no direct write
-- policy, every write goes through the service-role backend" stance
-- interview_process_registry (InterviewForge R1) already established as
-- this schema's precedent for a genuinely shared table. Only the poller
-- (Job Finder P2, not yet built) and this migration's own one-time seed
-- import write here; every other reader is a plain SELECT.
--
-- The `embedding vector(1024)` column and its HNSW index are included now
-- for schema fidelity to the real reference (bge-m3 via a local embedding
-- call in n8n's own pipeline) -- POPULATING it is explicitly deferred to
-- whichever later phase actually builds hybrid search; every row this
-- migration's own seed import creates has `embedding = NULL`, and nothing
-- in this phase computes one.
--
-- `tier` is deliberately left unconstrained (no CHECK), matching n8n's own
-- real schema exactly -- confirming n8n's precise tier vocabulary
-- (dream/hot/warm/probe/cold/never-polled per the poller's own scheduling
-- code) is Job Finder P2's job, not this one; adding a guessed CHECK now
-- risks rejecting a real value P2 turns out to need. `status` and
-- `sponsorship_signal` DO get a CHECK here -- both vocabularies are
-- already stated explicitly, in n8n's own schema comments, as closed
-- 2- and 3-value sets.

create extension if not exists vector;

create table public.job_registry_companies (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  ats_type text not null, -- greenhouse|lever|ashby|workable|recruitee|personio|smartrecruiters|workday|avature|...
  slug text not null, -- board token / site / account id -- NOT globally unique alone (see below)
  api_base text not null default '', -- tenant/override, e.g. workday "tenant.wdN", avature portal[/listing page]
  is_active boolean not null default true,
  poll_interval interval not null default '6 hours',
  next_poll_at timestamptz not null default now(),
  last_polled_at timestamptz,
  etag text, -- HTTP caching where supported
  last_modified text,
  consecutive_failures integer not null default 0,
  tier text not null default 'probe', -- poller priority lane; vocabulary confirmed + enforced in P2
  relevant_yield integer not null default 0, -- self-growth yield tracking, advanced by P2's poller
  tier_weight numeric, -- 0-1 company-quality weight; null = untiered, never guessed
  created_at timestamptz not null default now(),
  -- (ats_type, slug) alone is NOT enough -- Workday tenants routinely reuse
  -- generic site slugs ("External", "External_Career_Site"); api_base (the
  -- real tenant) is the actual disambiguator. n8n's own history (s74) has a
  -- real incident of two seeds silently colliding without this 3rd column.
  unique (ats_type, slug, api_base)
);

-- "which boards are due to poll" -- the poller's hot query (P2).
create index job_registry_companies_due_idx
  on public.job_registry_companies (next_poll_at)
  where is_active;

alter table public.job_registry_companies enable row level security;

create policy job_registry_companies_select_all on public.job_registry_companies
  for select
  to authenticated
  using (true);

create table public.job_registry_postings (
  id uuid primary key default gen_random_uuid(),
  company_id uuid references public.job_registry_companies (id) on delete cascade,
  board text not null, -- ats_type:slug, denormalized so the per-board liveness close (P2) is one WHERE, no join
  external_id text not null, -- ATS-native job id
  title text not null,
  jd_text text not null default '',
  location text,
  remote boolean,
  apply_url text not null,
  posted_at timestamptz,
  status text not null default 'active' check (status in ('active', 'closed')),
  first_seen timestamptz not null default now(),
  last_seen timestamptz not null default now(),
  closed_at timestamptz,
  embedding vector(1024), -- bge-m3 dims, matching the real reference; population deferred past P1
  salary_min numeric,
  salary_max numeric,
  salary_currency text,
  salary_period text, -- 'year' | 'month' | 'hour'
  sponsorship_signal text not null default 'unknown'
    check (sponsorship_signal in ('explicit_yes', 'explicit_no', 'unknown')),
  extracted_at timestamptz,
  extraction_version integer,
  jd_tsv tsvector generated always as (
    to_tsvector('english', coalesce(title, '') || ' ' || coalesce(jd_text, ''))
  ) stored,
  unique (board, external_id) -- upsert conflict target for the poller (P2)
);

create index job_registry_postings_board_status_idx on public.job_registry_postings (board, status);
create index job_registry_postings_board_lastseen_idx on public.job_registry_postings (board, last_seen); -- liveness diff (P2)
create index job_registry_postings_posted_idx on public.job_registry_postings (posted_at desc);
create index job_registry_postings_active_posted_idx
  on public.job_registry_postings (posted_at desc)
  where status = 'active';
create index job_registry_postings_tsv_idx on public.job_registry_postings using gin (jd_tsv);
create index job_registry_postings_embedding_idx
  on public.job_registry_postings using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
create index job_registry_postings_sponsorship_idx
  on public.job_registry_postings (sponsorship_signal)
  where status = 'active' and sponsorship_signal <> 'unknown';
create index job_registry_postings_salary_idx
  on public.job_registry_postings (salary_min)
  where status = 'active' and salary_min is not null;

alter table public.job_registry_postings enable row level security;

create policy job_registry_postings_select_all on public.job_registry_postings
  for select
  to authenticated
  using (true);
