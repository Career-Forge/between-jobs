-- Job Finder P9a (today-feed-job-matching.md) -- saved searches, the
-- ONLY thing the background matcher (P9b) ever scores against. This is
-- the real answer to a genuine N x M cost problem: scoring literally
-- every newly-polled registry posting against every user's profile would
-- mean an LLM call per (posting, user) pair on every poller tick -- a
-- saved search's own query/location/companies/remote_only filters
-- candidates BEFORE any scoring call, the same way `/discover`'s own
-- filters already do (P8). A user with no saved search never triggers
-- any background scoring at all.
--
-- Reuses `/discover`'s own filter shape exactly (D2) -- the real UX is
-- "save this search" off a search the user already ran, not a separate
-- form asking them to re-specify criteria from scratch.
--
-- `last_matched_at` (D4) is a PER-SEARCH watermark, not one global
-- cursor: the matcher only considers registry postings first-seen after
-- THIS search's own watermark. Defaults to `now()` at creation time so a
-- newly-saved search starts matching only postings from after it was
-- saved -- no backfill flood the moment someone saves a broad search
-- against the existing 89,000+-row registry.
create table public.saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  query text not null default '',
  location text,
  companies text[] not null default '{}',
  remote_only boolean not null default false,
  is_active boolean not null default true,
  last_matched_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Serves both real access patterns: a user's own list (`where user_id =
-- ...`) and the matcher's own per-tick scan (`where is_active`) -- this
-- table is expected to stay small (a handful of saved searches per user
-- at most), so one index covering both is a deliberate choice, not an
-- oversight of a busier table's own tuning needs.
create index saved_searches_user_idx on public.saved_searches (user_id);
create index saved_searches_active_idx on public.saved_searches (id) where is_active;

alter table public.saved_searches enable row level security;

create policy saved_searches_select_own on public.saved_searches
  for select
  using (auth.uid() = user_id);

create policy saved_searches_insert_own on public.saved_searches
  for insert
  with check (auth.uid() = user_id);

create policy saved_searches_update_own on public.saved_searches
  for update
  using (auth.uid() = user_id);

create policy saved_searches_delete_own on public.saved_searches
  for delete
  using (auth.uid() = user_id);

create trigger saved_searches_set_updated_at
  before update on public.saved_searches
  for each row
  execute function public.set_updated_at();
