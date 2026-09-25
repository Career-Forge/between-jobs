-- Interview practice sessions (InterviewForge R3, interviewforge-v1.md).
--
-- Mirrors company_intel_runs/claims' own normalized parent-run + child-rows
-- shape (20260821132652_create_company_intel.sql) -- NOT a JSONB-blob-per-
-- row shape like command-center's own `interview_sessions` table -- same
-- convention every other session-shaped table in this schema already uses.
-- Per-user RLS, unlike the shared `interview_process_registry` (R1): a
-- practice session is the candidate's own private record, not shared
-- research.
--
-- `resume_evidence` is snapshotted onto the session at creation time rather
-- than re-fetched (via forge-engines' /ingest + /personal) for every
-- answer -- both cheaper (one round trip, not one per answer) and more
-- correct: the same evidence used to generate the questions is the evidence
-- used to score every answer in that session, even if the candidate edits
-- their profile mid-session.
--
-- `registry_entry_id` is nullable and `on delete set null` -- a session
-- that started company-blind (no registry entry existed yet) stays a valid,
-- readable record even if a registry entry for that company appears later,
-- or if the one it used is ever removed.

create table public.interview_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  company_name text not null,
  resume_evidence text not null default '',
  registry_entry_id uuid references public.interview_process_registry (id) on delete set null,
  status text not null default 'in_progress'
    check (status = any (array['in_progress', 'completed'])),
  started_at timestamptz not null default now(),
  completed_at timestamptz
);

create index interview_sessions_application_idx
  on public.interview_sessions using btree (application_id, started_at desc);

alter table public.interview_sessions enable row level security;

create policy interview_sessions_select_own
  on public.interview_sessions for select
  using (auth.uid() = user_id);

create table public.interview_session_questions (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.interview_sessions (id) on delete cascade,
  ordinal integer not null,
  question_text text not null,
  question_type text not null,
  target_skill text not null default '',
  grounded_in text,
  answer_text text,
  score integer check (score is null or (score between 0 and 10)),
  feedback jsonb,
  answered_at timestamptz,
  unique (session_id, ordinal)
);

create index interview_session_questions_session_idx
  on public.interview_session_questions using btree (session_id, ordinal);

alter table public.interview_session_questions enable row level security;
-- No direct policy -- reachable only by joining through a session the user
-- owns, same defense-in-depth stance company_intel_claims already uses.
