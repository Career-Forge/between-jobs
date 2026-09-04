-- Events warm-path engine (outreach-contactfinder.md Phase D) -- Proposal
-- §27.5 / MASTER_PLAN §5.10b, "the best idea of the session." Same
-- immutable-run shape as company_intel_runs/claims (flat, no separate
-- evidence-per-item table -- an event IS its own source, unlike a
-- contact_candidate which can be backed by several independent sources).
-- Per-user, deliberately not the shared cross-user registry pattern, same
-- reasoning as contact_research_runs.

create table public.warm_path_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  company_name text not null,
  providers_used text[] not null default '{}',
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);

create index warm_path_runs_application_idx
  on public.warm_path_runs using btree (application_id, created_at desc);

alter table public.warm_path_runs enable row level security;

create policy warm_path_runs_select_own
  on public.warm_path_runs for select
  using (auth.uid() = user_id);

-- certainty: Proposal §27.5's own three-state rule, never a boolean --
-- "confirmed_speaker" / "likely_staffed_sponsor" / "company_adjacent".
-- speaker_name/speaker_title/talk_topic are only ever populated when
-- certainty = "confirmed_speaker" AND the source's own text literally
-- names that speaker -- an ungrounded speaker claim is downgraded to
-- "company_adjacent" with those three fields cleared, never dropped
-- outright (the event/company relationship itself can still be real
-- even when a specific named-speaker claim isn't grounded).
create table public.warm_path_events (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.warm_path_runs (id) on delete cascade,
  event_name text not null,
  event_url text not null,
  event_date text,
  location text,
  certainty text not null,
  speaker_name text,
  speaker_title text,
  talk_topic text,
  source_title text not null,
  source_snippet text not null,
  created_at timestamptz not null default now()
);

create index warm_path_events_run_idx
  on public.warm_path_events using btree (run_id);

alter table public.warm_path_events enable row level security;
-- No direct policy -- reachable only by joining through a run the user
-- owns, same defense-in-depth stance as contact_candidates/
-- company_intel_claims.
