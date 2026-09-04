-- OutreachWriter (outreach-contactfinder.md Phase E) -- Proposal §27.4.
-- In-app only: no send integration exists yet (Phase F, a separate OAuth
-- surface). One draft per generation, immutable -- regenerating creates a
-- new row rather than editing one in place, same "current = most recent"
-- convention as company_intel_runs/contact_research_runs.
--
-- Directly user-owned (not reached only by joining through a run), unlike
-- contact_candidates/contact_candidate_evidence -- a draft references
-- exactly one candidate, not a whole run's worth of rows, so a direct
-- RLS policy is the simpler correct shape here.

create table public.outreach_drafts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  candidate_id uuid not null references public.contact_candidates (id) on delete cascade,
  subject text not null,
  email_body text not null,
  linkedin_message text not null,
  follow_up_message text not null,
  hook_evidence_id uuid references public.contact_candidate_evidence (id) on delete set null,
  rubric_warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index outreach_drafts_candidate_idx
  on public.outreach_drafts using btree (candidate_id, created_at desc);

alter table public.outreach_drafts enable row level security;

create policy outreach_drafts_select_own
  on public.outreach_drafts for select
  using (auth.uid() = user_id);
