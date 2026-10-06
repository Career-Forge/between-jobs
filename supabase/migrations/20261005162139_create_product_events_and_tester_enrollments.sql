-- Minimal funnel instrumentation: who is in the tester program, and a small append-only log of
-- product events, so a cohort report can say how far each tester got without reading anyone's
-- data.
--
-- Two tables, both written only by the backend (service_role). Nothing here is read or written by
-- a user token except a tester reading their own enrollment row.
--
-- 1. tester_enrollments: one row per enrolled user -- which role cohort and seniority band they
--    were recruited into, and the consent they gave (which text, when, and when they withdrew).
--    A user can read their own row (so the app can show "you are enrolled, version X"); every
--    write goes through the API.
--
-- 2. product_events: one row per thing that happened (a search ran, a resume was generated, a
--    PDF was downloaded, an autofill finished, ...). Typed columns only, no JSON blob, so what
--    can be stored is exactly what the columns allow. It is a count log, not a content log:
--
--      NEVER stored here: an IP address, a user agent, a URL or path, any resume, job
--      description or answer text, any form-field value or label, a third-party SDK's id, or a
--      session replay. There is deliberately no column that could hold any of those (a test
--      fails if one is ever added), and the two free-text-looking columns, `capability` and
--      `ats_type`, are closed lists.
--
--    Append-only: service_role may insert and read, and nothing may update or delete a row. A
--    user's events go when the user does (on delete cascade); deleting an application only
--    detaches it from the events that mention it (on delete set null).
--
--    What n_a and n_b mean depends on the event:
--      discover_search      n_a = candidates found before filtering, n_b = results scored
--      prepare_finished     n_a = number of unsupported-claim warnings on what was generated
--      artifact_downloaded  n_a = document (1 = resume, 2 = cover letter)
--      extension_fill       n_a = fields attempted, n_b = fields filled (never the values)
--      artifact_rated       n_a = rating (1 = send as is, 2 = minor edits, 3 = major edits,
--                           4 = unusable), n_b = 1 when the person flagged "contains something I
--                           never did", else 0
--      setup_required, copy_panel_opened   neither is used
--
-- Both tables reference auth.users, so user_owned_row_counts counts them, the account-deletion
-- path removes them through the cascade, and merge_user_data (next migration) has to say what a
-- link does with them.

-- ---------------------------------------------------------------------------------------------
-- tester_enrollments
-- ---------------------------------------------------------------------------------------------

create table public.tester_enrollments (
  user_id uuid primary key references auth.users (id) on delete cascade,
  -- Eight core cohorts, which gate the program, then two that are reported but do not gate.
  role_cohort text not null check (
    role_cohort in (
      'data_analyst',
      'data_engineer',
      'data_scientist',
      'ai_ml_engineer',
      'software_engineer',
      'frontend_engineer',
      'devops_sre',
      'qa_sdet',
      'product_manager',
      'business_analyst'
    )
  ),
  seniority text not null check (
    seniority in ('new_grad', 'early_career', 'mid', 'senior', 'lead_plus')
  ),
  -- Optional. Null means the tester was not asked or declined; it is never read as "no".
  needs_sponsorship boolean,
  -- Which version of the consent text the tester agreed to.
  consent_version text not null check (consent_version <> '' and char_length(consent_version) <= 40),
  consented_at timestamptz not null,
  withdrawn_at timestamptz,
  created_at timestamptz not null default now()
);

comment on column public.tester_enrollments.needs_sponsorship is
  'Immigration-adjacent, so handled as sensitive: optional (null = not asked or declined), shown to the tester with the reason it is asked, never used for any product logic, never exported outside the operator''s cohort report queries, deleted with the rest of the cohort data 30 days after the program ends, and disclosed by name in the privacy page.';

alter table public.tester_enrollments enable row level security;

-- A tester can see their own enrollment. There is no write policy of any kind: the API writes
-- with the service role, so a user token cannot enroll itself, change its cohort, or edit its
-- consent record.
create policy tester_enrollments_select_own on public.tester_enrollments
  for select
  using (auth.uid() = user_id);

-- Prod's default privileges grant new tables to anon, authenticated and service_role (every
-- privilege, TRUNCATE and REFERENCES included); a fresh stack grants nothing. Revoke everything
-- from all four, then grant back exactly what is used, so both end in the same state. Deleting a
-- row is not granted: a user's row goes with the user (the cascade needs no grant), and the
-- operator removes cohort data as the table owner.
revoke all on table public.tester_enrollments from public, anon, authenticated, service_role;
grant select on table public.tester_enrollments to authenticated;
grant select, insert, update on table public.tester_enrollments to service_role;

-- ---------------------------------------------------------------------------------------------
-- product_events
-- ---------------------------------------------------------------------------------------------

create table public.product_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  event text not null check (
    event in (
      'discover_search',
      'setup_required',
      'prepare_finished',
      'artifact_downloaded',
      'extension_fill',
      'artifact_rated',
      'copy_panel_opened'
    )
  ),
  -- The capability a setup_required event was about. Exactly the keys the backend resolves a
  -- credential for, plus 'profile' (the missing-resume setup step).
  capability text check (
    capability in (
      'application_answer_generation',
      'company_intel',
      'contact_enrichment',
      'contact_research',
      'gmail_draft',
      'gmail_reply_check',
      'hiring_signals',
      'interview_practice',
      'job_scoring',
      'job_url_ingest',
      'linkedin_discovery',
      'outreach_writer',
      'positioning_brief',
      'prepare_application',
      'profile',
      'warm_path_events'
    )
  ),
  application_id uuid references public.applications (id) on delete set null,
  -- Which applicant-tracking system the posting is on, when it is known. A closed list, never a
  -- host name or a URL.
  ats_type text check (
    ats_type in (
      'amazon',
      'apple',
      'ashby',
      'avature',
      'bamboohr',
      'deshaw',
      'eightfold',
      'google',
      'greenhouse',
      'icims',
      'jobvite',
      'lever',
      'microsoft',
      'oracle',
      'personio',
      'recruitee',
      'smartrecruiters',
      'successfactors',
      'taleo',
      'workable',
      'workday',
      'yc'
    )
  ),
  outcome text check (outcome in ('ok', 'partial', 'failed', 'setup_required')),
  n_a integer check (n_a >= 0),
  n_b integer check (n_b >= 0),
  duration_ms integer check (duration_ms >= 0),
  created_at timestamptz not null default now(),
  -- A fill cannot fill more fields than it tried, so a fill rate can never exceed 100%.
  constraint product_events_fill_counts check (
    event <> 'extension_fill' or (n_a is not null and n_b is not null and n_b <= n_a)
  )
);

create index product_events_event_created_idx on public.product_events (event, created_at);
create index product_events_user_created_idx on public.product_events (user_id, created_at);
-- Deleting an application sets its events' application_id to null; without this the database
-- would scan the whole table for them once per deleted application (and an account deletion
-- deletes all of the user's applications).
create index product_events_application_idx on public.product_events (application_id)
  where application_id is not null;

alter table public.product_events enable row level security;
-- No policy at all: a user token reads and writes nothing here. Only the backend (service_role,
-- which bypasses row-level security) and the operator do.

revoke all on table public.product_events from public, anon, authenticated, service_role;
grant select, insert on table public.product_events to service_role;
