-- Applications are state plus history (Sprint 2.6c) -- Proposal.md §19,
-- DDL verbatim (RLS added -- §19 doesn't spell it out, same pattern as
-- every other per-user table in this project).
--
-- `applications` is the current projection; `application_events` is the
-- append-only audit trail -- "never try to reconstruct history from
-- updated_at" (§19's own words). No delete policy on either table:
-- applications transition status, they don't get removed, and events are
-- a log, never edited or erased.
--
-- `status` is deliberately left as unconstrained text, matching the DDL --
-- no CHECK constraint, no Postgres enum. The store module documents the
-- small vocabulary this backend actually uses; nothing at the DB layer
-- enforces it, so a future real stage machine can tighten this without a
-- migration.
--
-- Reuses `public.set_updated_at()` (created in the resumes migration,
-- survives that table's drop) for `applications.updated_at`.

create table public.applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  job_id uuid not null references public.jobs (id),
  active_job_snapshot_id uuid not null references public.job_snapshots (id),
  status text not null,
  source_channel text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, job_id)
);

create index applications_user_id_idx
  on public.applications using btree (user_id, created_at desc);

alter table public.applications enable row level security;

create policy applications_select_own on public.applications
  for select
  using (auth.uid() = user_id);

create policy applications_insert_own on public.applications
  for insert
  with check (auth.uid() = user_id);

create policy applications_update_own on public.applications
  for update
  using (auth.uid() = user_id);

create trigger applications_set_updated_at
  before update on public.applications
  for each row
  execute function public.set_updated_at();

create table public.application_events (
  id uuid primary key default gen_random_uuid(),
  application_id uuid not null references public.applications (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  actor_type text not null,
  actor_id text not null,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  unique (user_id, idempotency_key)
);

create index application_events_application_id_idx
  on public.application_events using btree (application_id, created_at);

alter table public.application_events enable row level security;

create policy application_events_select_own on public.application_events
  for select
  using (auth.uid() = user_id);

create policy application_events_insert_own on public.application_events
  for insert
  with check (auth.uid() = user_id);
