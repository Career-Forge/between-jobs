-- Canonical profile (Sprint 2.5) -- Proposal.md §15-16.
--
-- Replaces the raw_text stub in `resumes` (retired in the next migration)
-- with the real contract: an immutable, versioned JSON snapshot of a
-- user's resume, plus normalized `career_facts` rows derived from it that
-- later generated claims can cite by id.
--
-- `activated_at` is one addition beyond Proposal §15's literal DDL, needed
-- to represent the Telegram preview-then-confirm flow without a separate
-- mutable staging blob (the project's own "Postgres rows, never a mutable
-- JSON blob" invariant -- master plan §10.14). A freshly imported version
-- is inserted with `activated_at` null ("pending" -- what the preview
-- message is built from); confirming sets it; cancelling deletes the row
-- outright (career_facts cascade with it). "The current profile" for a
-- user is the row with the latest non-null `activated_at`, which also
-- naturally supports re-activating an older version later without any
-- extra schema. A version's `canonical_json` itself is never mutated after
-- insert -- only this one lifecycle timestamp changes.

create table public.profile_versions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  schema_version text not null,
  source_kind text not null,
  source_artifact_id uuid,
  canonical_json jsonb not null,
  content_hash text not null,
  created_at timestamptz not null default now(),
  activated_at timestamptz,
  supersedes_id uuid references public.profile_versions (id),
  unique (user_id, content_hash)
);

create index profile_versions_user_created_idx
  on public.profile_versions using btree (user_id, created_at desc);

-- Powers "what's the user's current profile" -- latest row where
-- activated_at is not null, per user.
create index profile_versions_user_activated_idx
  on public.profile_versions using btree (user_id, activated_at desc)
  where activated_at is not null;

alter table public.profile_versions enable row level security;

create policy profile_versions_select_own on public.profile_versions
  for select
  using (auth.uid() = user_id);

create policy profile_versions_insert_own on public.profile_versions
  for insert
  with check (auth.uid() = user_id);

-- Only activated_at ever changes post-insert (confirming a pending
-- version); this policy doesn't need to restrict which columns, since it
-- governs WHO can update their own row, not what they change.
create policy profile_versions_update_own on public.profile_versions
  for update
  using (auth.uid() = user_id);

-- Normalized facts derived from a profile version at import time --
-- Proposal §15: "Generated claims cite career fact IDs or source pointers.
-- A factual verifier rejects unsupported metrics, dates, titles,
-- institutions, and technologies." v1 derives facts for experience,
-- projects, and education entries only (the evidence generated documents
-- actually cite) -- not for summary bullets, achievements, or individual
-- skills, which stay queryable inside canonical_json without their own
-- fact rows for now. Cheap to widen later; nothing downstream depends on
-- the narrower set yet.

create table public.career_facts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  profile_version_id uuid not null references public.profile_versions (id) on delete cascade,
  fact_type text not null,
  entity_key text not null,
  value_json jsonb not null,
  source_pointer text not null,
  sensitivity text not null default 'normal',
  valid_from date,
  valid_to date,
  created_at timestamptz not null default now()
);

create index career_facts_profile_version_idx
  on public.career_facts using btree (profile_version_id);

create index career_facts_user_fact_type_idx
  on public.career_facts using btree (user_id, fact_type);

alter table public.career_facts enable row level security;

create policy career_facts_select_own on public.career_facts
  for select
  using (auth.uid() = user_id);

create policy career_facts_insert_own on public.career_facts
  for insert
  with check (auth.uid() = user_id);
