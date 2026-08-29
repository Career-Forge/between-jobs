-- Sessions + Telegram identity linking (Sprint 2.1).
--
-- Supabase Auth (auth.users) IS the identity system -- there is no separate
-- profiles/users table. Every app table references auth.users(id) directly.
--
-- Backfilled into the repo in Sprint 2.5 (was applied via MCP only before
-- that; see Proposal.md Part X §60 -- "checked-in migrations" was the first
-- item on that checklist). Matches the live schema exactly as introspected,
-- including one known gap: `sessions.updated_at` has no refresh trigger
-- (unlike `resumes`/`profile_versions` later), so it currently never
-- advances past its insert-time default. Left as-is rather than silently
-- fixed during a backfill -- a real but harmless gap since nothing reads
-- `sessions.updated_at` yet.

create table public.sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index sessions_user_id_idx on public.sessions using btree (user_id);

alter table public.sessions enable row level security;

create policy sessions_select_own on public.sessions
  for select
  using (auth.uid() = user_id);

create policy sessions_insert_own on public.sessions
  for insert
  with check (auth.uid() = user_id);

create policy sessions_update_own on public.sessions
  for update
  using (auth.uid() = user_id);

-- Maps a Telegram user id to the Supabase auth user that identity resolves
-- to (Sprint 2.3+ auto-provisions this row on first contact -- see
-- telegram_identity.py). No insert/update/delete policy: only the
-- service-role backend ever writes this table, which bypasses RLS by
-- design. The SELECT policy exists for a hypothetical future direct-client
-- read (e.g. Realtime), not because the backend needs it.

create table public.telegram_links (
  telegram_user_id bigint primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  linked_at timestamptz not null default now()
);

alter table public.telegram_links enable row level security;

create policy telegram_links_select_own on public.telegram_links
  for select
  using (auth.uid() = user_id);
