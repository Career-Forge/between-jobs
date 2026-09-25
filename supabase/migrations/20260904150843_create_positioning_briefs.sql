-- Positioning brief (outreach-v2-search-first.md Phase I) -- Proposal
-- §27.6's "gap-analysis sleeper feature," direction (B) then (A) from the
-- 2026-09-04 brainstorm. Flat, per-application, immutable-run shape
-- (mirroring outreach_drafts, per a real research pass comparing this
-- codebase's own two existing "run" shapes) rather than the run+child
-- pattern company_intel_runs/contact_research_runs use -- this table
-- holds three fixed singular fields from exactly one LLM call per
-- generation, with no genuine one-to-many fan-out to justify a second
-- table the way a variable-length list of claims/candidates does.
--
-- Regenerating creates a new row rather than editing one in place, same
-- reasoning as outreach_drafts: "the current brief" for an application
-- is whichever row is most recent.

create table public.positioning_briefs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  lead_with text not null,
  lead_with_citation text not null,
  gap_that_matters text not null,
  gap_citation text not null,
  recommended_project text not null,
  rubric_warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index positioning_briefs_application_idx
  on public.positioning_briefs using btree (application_id, created_at desc);

alter table public.positioning_briefs enable row level security;

create policy positioning_briefs_select_own
  on public.positioning_briefs for select
  using (auth.uid() = user_id);
