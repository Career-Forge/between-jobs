-- Resume documents (Sprint 3.2a) -- Proposal §24.5.1: "A resume document
-- is a per-application composition: included evidence, section order and
-- visibility, header layout, display modes, accepted AI patches, and a
-- template reference. It stores selections and layout only -- values stay
-- in career memory. It references one profile version and one job
-- snapshot, so it remains reproducible after either changes."
--
-- Anticipated by name in two earlier sprints' own comments
-- (profile.py's "no resume_documents, no Resume Studio ... Revisit when
-- Sprint 3.2 gives it a real consumer"; web/src/lib/sectionFieldConfigs.ts's
-- "Personal/header ... the Header Composer") -- this migration is that
-- consumer.
--
-- Deliberately MUTABLE, unlike profile_versions/artifact_versions'
-- immutable-append-only pattern: those are durable history (a resume you
-- generated, a profile snapshot you activated); this is live editing
-- state (drag a chip, toggle a section) that would be wasteful and
-- meaningless to version on every micro-edit. Same shape as
-- capability_preferences (Sprint 2.7d) -- upsert-on-a-natural-key mutable
-- "current state," not a log. The thing that DOES get versioned when a
-- user actually generates output from this composition is artifact_versions
-- (Sprint 2.7a/3.0e), unchanged by this table's existence.
--
-- `application_id` nullable: null means the MASTER/default document (the
-- profile editor's own composition, Proposal §37.6), matching the plan's
-- own "per-application or master-default" phrasing. A per-application
-- document needs `job_snapshot_id` too (so header/section choices can be
-- JD-aware); a master document has neither an application nor a job.
--
-- `header_layout` holds the full Header Composer state per §24.5.3 in one
-- jsonb blob (chip order + per-chip display mode + name-block config +
-- spacing config) rather than one column per concern -- that shape is
-- still being designed in the UI layer (Sprint 3.2c) and jsonb lets it
-- evolve without a migration each time a field is added, the same
-- tradeoff artifact_versions.warnings/evidence_fact_ids already accepted
-- for similarly UI-shaped, still-evolving data.
--
-- No `document_kind` column (unlike artifact_versions) -- forge-engines
-- generates resumes only today (no cover letters, no application
-- answers), and Proposal §24.5.1 itself only ever says "a resume
-- document." Add it if/when a second document kind gets a real composing
-- surface, not speculatively now.

create table public.resume_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid references public.applications (id) on delete cascade,
  profile_version_id uuid not null references public.profile_versions (id),
  job_snapshot_id uuid references public.job_snapshots (id),
  section_order text[] not null default '{}',
  section_visibility jsonb not null default '{}'::jsonb,
  header_layout jsonb not null default '{}'::jsonb,
  selected_evidence_fact_ids uuid[] not null default '{}',
  accepted_patches jsonb not null default '[]'::jsonb,
  template_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, application_id)
);

-- The plain unique(user_id, application_id) above only dedupes NON-NULL
-- application_id rows (Postgres treats each NULL as distinct in a unique
-- constraint) -- this partial index is what actually caps a user at one
-- master document.
create unique index resume_documents_one_master_per_user
  on public.resume_documents (user_id)
  where application_id is null;

create index resume_documents_application_id_idx
  on public.resume_documents using btree (application_id);

alter table public.resume_documents enable row level security;

create policy resume_documents_select_own on public.resume_documents
  for select
  using (auth.uid() = user_id);

create policy resume_documents_insert_own on public.resume_documents
  for insert
  with check (auth.uid() = user_id);

create policy resume_documents_update_own on public.resume_documents
  for update
  using (auth.uid() = user_id);

create policy resume_documents_delete_own on public.resume_documents
  for delete
  using (auth.uid() = user_id);

create trigger resume_documents_set_updated_at
  before update on public.resume_documents
  for each row
  execute function public.set_updated_at();
