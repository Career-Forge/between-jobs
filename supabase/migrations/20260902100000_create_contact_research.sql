-- ContactFinder / Outreach (outreach-contactfinder.md Phase A/B) --
-- Proposal §26.1 stages 4-6 and §27. Deliberately per-user and immutable-
-- run shaped, like company_intel_runs/claims -- NOT the shared cross-user
-- registry pattern job_registry_companies/interview_process_registry use.
-- Contact evidence is about named private individuals; it's assembled
-- fresh per (user, application) request, never pooled globally across
-- users. See contact_research.py's own module docstring.
--
-- Three tables, one level deeper than company_intel's two: a run (one
-- research pass) has many candidates (resolved people), and each
-- candidate has many evidence rows (Proposal §27.3's exact
-- ContactEvidence shape, one row per source backing that candidate).
-- Splitting candidate from evidence, rather than denormalizing evidence
-- fields onto the candidate row, mirrors Proposal's own schema directly
-- and lets one candidate be backed by multiple independent sources
-- without repeating person_name/claimed_title per row.

create table public.contact_research_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  company_name text not null,
  providers_used text[] not null default '{}',
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index contact_research_runs_application_idx
  on public.contact_research_runs using btree (application_id, created_at desc);

alter table public.contact_research_runs enable row level security;

create policy contact_research_runs_select_own
  on public.contact_research_runs for select
  using (auth.uid() = user_id);

-- persona: which of Proposal §27.1's target-portfolio tiers this
-- candidate's own query came from (hiring_lead/recruiter/manager/
-- senior_leader/senior_ic) -- drives priority_score's base weight.
create table public.contact_candidates (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.contact_research_runs (id) on delete cascade,
  person_name text not null,
  claimed_title text,
  claimed_team text,
  company text not null,
  persona text not null,
  relevance_reason text not null,
  priority_score numeric not null,
  created_at timestamptz not null default now()
);

create index contact_candidates_run_idx
  on public.contact_candidates using btree (run_id, priority_score desc);

alter table public.contact_candidates enable row level security;
-- No direct policy -- reachable only by joining through a run the user
-- owns, same defense-in-depth stance as company_intel_claims (this
-- backend always reads via the service-role client).

-- confidence: Proposal §27.3's own Literal exactly
-- ("verified"/"strong"/"inferred"/"unsupported").
create table public.contact_candidate_evidence (
  id uuid primary key default gen_random_uuid(),
  candidate_id uuid not null references public.contact_candidates (id) on delete cascade,
  source_url text not null,
  source_title text not null,
  source_snippet text not null,
  observed_at timestamptz not null,
  evidence_kind text not null,
  confidence text not null,
  created_at timestamptz not null default now()
);

create index contact_candidate_evidence_candidate_idx
  on public.contact_candidate_evidence using btree (candidate_id);

alter table public.contact_candidate_evidence enable row level security;
-- No direct policy -- reachable only by joining through a candidate ->
-- run the user owns.
