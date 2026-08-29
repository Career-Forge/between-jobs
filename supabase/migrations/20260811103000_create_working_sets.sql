-- Shared reference state (Sprint 2.6e) -- Proposal.md §21, DDL verbatim
-- (RLS added, same per-user pattern as every other user-owned table in
-- this project -- §21 doesn't spell it out either).
--
-- "When the user says 'apply to #3,' the number belongs to a working
-- set, not global memory." No consumer resolves "#3" yet -- that's
-- Telegram's numbered-reference UX, Stage D -- so this sprint only
-- proves the mechanic: create a working set, look one up, resolve an
-- index against it. Same precedent as Sprint 2.6d's outbox: the
-- mechanism ships before anything depends on it.
--
-- No update or delete policy: a working set is never revised in place
-- here (nothing yet needs to bump `version`, so this store doesn't
-- manage it beyond its DB default) -- a changed list just becomes a new
-- working set with a fresh id. Stale ones age out via `expires_at`;
-- nothing actively deletes them yet either.

create table public.working_sets (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  kind text not null,
  source_channel text not null,
  items jsonb not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  version integer not null default 1
);

-- Powers "the current working set of this kind for this user" -- latest
-- created row, same "latest wins" shape as profile_versions_user_activated_idx.
create index working_sets_user_kind_idx
  on public.working_sets using btree (user_id, kind, created_at desc);

alter table public.working_sets enable row level security;

create policy working_sets_select_own on public.working_sets
  for select
  using (auth.uid() = user_id);

create policy working_sets_insert_own on public.working_sets
  for insert
  with check (auth.uid() = user_id);
