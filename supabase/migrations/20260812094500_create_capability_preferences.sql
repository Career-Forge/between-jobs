-- Capability preferences (Sprint 2.7d) -- Proposal §41-42: execution mode,
-- provider, and model choice are a separate concern from the credential
-- itself (provider_credentials, Sprint 2.7c) -- joined together only at
-- the moment of resolution (CredentialResolver, Sprint 2.7e), never
-- denormalized. Shape mirrors careerforge-command-center's
-- `user_model_preferences` (frozen reference repo), narrowed to what
-- this platform's resolver actually needs right now.
--
-- `capability` follows Proposal §42's own naming (e.g. "company_intel" in
-- its SETUP_REQUIRED example); `'default'` is the global fallback row a
-- capability with no specific override falls back to -- same
-- tool-specific-then-default precedence command-center's resolver uses.
--
-- `execution_mode` is unconstrained text (§41's vocabulary: self_host,
-- byok_generic, byok_first_party, hosted_credit, disabled) -- no CHECK
-- constraint, matching this project's existing precedent for
-- applications.status: the DB doesn't enforce the vocabulary, the
-- resolver's own code does. `hosted_credit` isn't handled by Sprint
-- 2.7e's resolver (this platform issues no hosted credits yet), but the
-- column isn't narrowed to exclude it -- storing a preference the
-- resolver doesn't yet act on is harmless and avoids a future migration
-- just to widen a CHECK constraint.
--
-- No `runtime_config`/fallback-model tables yet, unlike command-center's
-- later-evolved schema -- nothing in this codebase needs per-capability
-- temperature/max-tokens tuning or ordered fallback models today; adding
-- either without a real caller would be exactly the kind of speculative
-- schema this project's engineering rules warn against.
--
-- Delete policy included, same reasoning as provider_credentials: a
-- preference is normal mutable user config, not history to preserve.

create table public.capability_preferences (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  capability text not null,
  execution_mode text not null,
  provider text,
  model text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, capability)
);

alter table public.capability_preferences enable row level security;

create policy capability_preferences_select_own on public.capability_preferences
  for select
  using (auth.uid() = user_id);

create policy capability_preferences_insert_own on public.capability_preferences
  for insert
  with check (auth.uid() = user_id);

create policy capability_preferences_update_own on public.capability_preferences
  for update
  using (auth.uid() = user_id);

create policy capability_preferences_delete_own on public.capability_preferences
  for delete
  using (auth.uid() = user_id);

create trigger capability_preferences_set_updated_at
  before update on public.capability_preferences
  for each row
  execute function public.set_updated_at();
