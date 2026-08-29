-- Company intelligence (Horizon Sprint 5.0) -- Proposal §26, scoped to
-- the company-level dossier only (no named individuals -- that's §27,
-- deliberately a separate, later sprint given its real privacy weight).
--
-- Two tables, mirroring artifact_versions' own "immutable run, never
-- edited in place" shape rather than resume_documents' mutable one: a
-- dossier is a point-in-time research result, not something a user
-- hand-edits. `company_intel_runs` is one research run (one call to the
-- pipeline, real You.com/Firecrawl/LLM spend); `company_intel_claims` are
-- the individual sourced facts that run produced. "Current dossier" for
-- an application = the claims belonging to its most recent run --
-- re-running (a refresh) never mutates or deletes a prior run's claims,
-- it just becomes the new most-recent one.
--
-- Every claim carries a real `source_url` (not null) -- Proposal §26's
-- own claim shape starts from "a dossier consists of claims, not one
-- opaque Markdown blob," and this platform's own "unknown labeled as
-- unknown, never guessed" rule makes an unsourced claim a contradiction
-- in terms, not something to store with a nullable column and hope
-- nothing reads it wrong.

create table public.company_intel_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  company_name text not null,
  providers_used text[] not null default '{}',
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index company_intel_runs_application_idx
  on public.company_intel_runs using btree (application_id, created_at desc);

alter table public.company_intel_runs enable row level security;

create policy company_intel_runs_select_own
  on public.company_intel_runs for select
  using (auth.uid() = user_id);

create table public.company_intel_claims (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.company_intel_runs (id) on delete cascade,
  category text not null,
  claim_text text not null,
  source_url text not null,
  source_title text,
  confidence text not null,
  created_at timestamptz not null default now()
);

create index company_intel_claims_run_idx
  on public.company_intel_claims using btree (run_id);

alter table public.company_intel_claims enable row level security;

-- No direct policy on claims -- reachable only by joining through a run
-- the user owns, and this backend always reads via the service-role
-- client anyway (same defense-in-depth stance as every other table
-- here); RLS stays enabled with select denied by default rather than
-- duplicating the ownership check as a claims-table policy.
