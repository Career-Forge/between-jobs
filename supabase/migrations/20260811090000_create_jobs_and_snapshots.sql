-- Jobs and immutable snapshots (Sprint 2.6b) -- Proposal.md §18, DDL verbatim.
--
-- A job identity is separate from the content observed at a point in
-- time. Every generated artifact will reference a specific snapshot, so
-- if a JD changes later, old applications stay reproducible against what
-- they were actually built from.
--
-- No `user_id` on either table -- per §18's own DDL. These are shared
-- reference data (a company job board), not per-user rows: two users who
-- both paste the same posting should be able to share one `jobs` row.
-- RLS below reflects that -- any authenticated user may read the whole
-- catalog; there is no insert/update policy for the authenticated role at
-- all, since this backend always writes through the service-role client
-- (which bypasses RLS, same as every other table in this project) and a
-- direct client write to shared reference data has no legitimate case.

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  canonical_url text,
  company_name text not null,
  external_requisition_id text,
  ats_type text,
  ats_tenant text,
  created_at timestamptz not null default now()
);

create index jobs_canonical_url_idx
  on public.jobs using btree (canonical_url)
  where canonical_url is not null;

alter table public.jobs enable row level security;

create policy jobs_select_any_authenticated on public.jobs
  for select
  to authenticated
  using (true);

create table public.job_snapshots (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs (id) on delete cascade,
  source_url text not null,
  title text not null,
  company_name text not null,
  location_text text,
  description_text text not null,
  structured_json jsonb not null,
  content_hash text not null,
  fetched_at timestamptz not null,
  source_kind text not null,
  unique (job_id, content_hash)
);

create index job_snapshots_job_id_idx
  on public.job_snapshots using btree (job_id);

alter table public.job_snapshots enable row level security;

create policy job_snapshots_select_any_authenticated on public.job_snapshots
  for select
  to authenticated
  using (true);
