-- Interview-process registry (InterviewForge R1, interviewforge-v1.md).
--
-- No `user_id` -- deliberately the first genuinely SHARED, cross-user table
-- in this schema, same "shared reference data, not per-user rows" reasoning
-- `jobs`/`job_snapshots` already use (20260811090000_create_jobs_and_
-- snapshots.sql): the whole point of a registry is that one user's real,
-- cited research benefits every future user researching the same company,
-- rather than each person re-researching (and re-paying for) it from
-- scratch. RLS mirrors that migration's own pattern exactly -- any
-- authenticated user may read the whole table; there is no insert/update
-- policy for the authenticated role at all, since this backend always
-- writes through the service-role client (bypasses RLS) and a direct client
-- write to shared registry data has no legitimate case.
--
-- Append-only, same immutable-run shape `company_intel_runs` already uses
-- (20260821130000_create_company_intel.sql) -- a new entry never edits or
-- replaces a prior one; "the registry" for a company is whichever entry is
-- most recent. This sidesteps concurrent-write conflict handling entirely.
--
-- `source_run_id` references the `company_intel_runs` row whose claims this
-- entry was synthesized from -- provenance/audit, not a foreign key the
-- read path depends on (a deleted source run doesn't invalidate a registry
-- entry already built from claims that were real at the time).

create table public.interview_process_registry (
  id uuid primary key default gen_random_uuid(),
  company_name text not null,
  rounds jsonb not null default '[]'::jsonb,
  typical_topics text[] not null default '{}',
  difficulty_signal text not null,
  values_signals text[] not null default '{}',
  confidence text not null,
  source_run_id uuid references public.company_intel_runs (id) on delete set null,
  created_at timestamptz not null default now()
);

create index interview_process_registry_company_idx
  on public.interview_process_registry using btree (company_name, created_at desc);

alter table public.interview_process_registry enable row level security;

create policy interview_process_registry_select_any_authenticated
  on public.interview_process_registry
  for select
  to authenticated
  using (true);
