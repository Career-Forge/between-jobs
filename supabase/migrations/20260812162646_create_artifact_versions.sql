-- Artifact versions (Sprint 2.7a) -- Proposal.md §24.3, DDL verbatim with
-- two deliberate additions: `user_id` and `application_id`. §24.3's own
-- DDL has neither -- `artifact_id` is a stable logical key with no home
-- table of its own (confirmed: no `artifacts` table exists anywhere in
-- the Proposal; "the durable record stores an artifact ID and hash" is
-- the whole spec for it), so without an owner column this table can't be
-- RLS-scoped to a user or even queried by "which application does this
-- belong to" without a FK. Same kind of small, documented departure as
-- profile_versions' `activated_at` (Sprint 2.5a) and event_outbox's
-- `user_id` FK (Sprint 2.6d).
--
-- No `document_kind` column, deliberately -- Sprint 3.0 (the first real
-- consumer, `prepare_application`) hasn't been designed yet, and how a
-- caller distinguishes "the resume artifact" from "the cover letter
-- artifact" for a given application (a fixed `artifact_id` minted once
-- per document slot, a separate kind column, something else) is that
-- sprint's own call to make, not this one's to guess at.
--
-- Immutable: no update or delete policy. "Every revision creates a new
-- version" (§24.3's own words) -- approving/regenerating always inserts,
-- never mutates a prior row.

create table public.artifact_versions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid not null references public.applications (id) on delete cascade,
  artifact_id uuid not null,
  version integer not null,
  storage_key text not null,
  media_type text not null,
  sha256 text not null,
  generator text not null,
  generator_version text not null,
  profile_version_id uuid not null references public.profile_versions (id),
  job_snapshot_id uuid references public.job_snapshots (id),
  evidence_fact_ids uuid[] not null default '{}',
  warnings jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  unique (artifact_id, version)
);

create index artifact_versions_application_id_idx
  on public.artifact_versions using btree (application_id, created_at desc);

-- Powers "every version of this specific document, in order."
create index artifact_versions_artifact_id_idx
  on public.artifact_versions using btree (artifact_id, version desc);

alter table public.artifact_versions enable row level security;

create policy artifact_versions_select_own on public.artifact_versions
  for select
  using (auth.uid() = user_id);

create policy artifact_versions_insert_own on public.artifact_versions
  for insert
  with check (auth.uid() = user_id);
