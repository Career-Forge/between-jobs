-- Hiring Signals P1 -- storage only, no app code yet (three tables, one
-- migration, per this feature's own build order). Each table has a
-- different owner and a different lifetime, so each gets its own RLS
-- shape rather than one shared pattern.
--
-- `hiring_signal_saves` -- user-owned, sparse pointer rows (a URL +
-- activity id, nothing else). Mirrors `applications`' own
-- select_own/insert_own policy idiom verbatim. Deliberately no update
-- policy: these rows are never edited after creation, only created and
-- deleted. `application_id` is nullable -- null means the save came from
-- the standalone tab rather than a specific tracked application -- and
-- cascades with its application the same way `application_events` rows
-- cascade with theirs.
create table public.hiring_signal_saves (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  application_id uuid references public.applications (id) on delete cascade,
  url text not null,
  activity_id text not null,
  source text not null default 'linkedin',
  discovered_via_query text,
  created_at timestamptz not null default now()
);

create index hiring_signal_saves_user_id_idx
  on public.hiring_signal_saves using btree (user_id, created_at desc);

create index hiring_signal_saves_application_id_idx
  on public.hiring_signal_saves using btree (application_id)
  where application_id is not null;

alter table public.hiring_signal_saves enable row level security;

create policy hiring_signal_saves_select_own on public.hiring_signal_saves
  for select
  using (auth.uid() = user_id);

create policy hiring_signal_saves_insert_own on public.hiring_signal_saves
  for insert
  with check (auth.uid() = user_id);

create policy hiring_signal_saves_delete_own on public.hiring_signal_saves
  for delete
  using (auth.uid() = user_id);

-- `hiring_signal_query_cache` -- system-level, no `user_id` at all. Holds
-- a raw provider search-API response for a given (query, day) so a second
-- user asking the same role+metro question within the cache window
-- doesn't re-spend a BYOK search credit -- same shared-cache principle as
-- the existing company-intel cache, just for this lane's own provider
-- calls. `query_key` is whatever normalized (query, day) key the caller
-- derives; the unique index is what lets a lookup hit the cache directly
-- instead of scanning. RLS is enabled with zero policies for
-- `authenticated`/`anon`, mirroring `event_outbox`'s own "service-role
-- only, no client policies at all" shape exactly -- only the backend's
-- service-role key (which bypasses RLS) ever touches this table. No TTL
-- or cleanup job here; `created_at` exists so a later phase's staleness
-- check has something to compare against.
create table public.hiring_signal_query_cache (
  id uuid primary key default gen_random_uuid(),
  query_key text not null,
  provider text not null,
  response_json jsonb not null,
  created_at timestamptz not null default now()
);

create unique index hiring_signal_query_cache_query_key_idx
  on public.hiring_signal_query_cache using btree (query_key);

alter table public.hiring_signal_query_cache enable row level security;

-- `hiring_signal_searches` -- plain per-user CRUD for the standalone
-- tab's saved role+metro searches. Deliberately its own table, NOT a row
-- in `saved_searches` (Job Finder's own table): `saved_search_matcher.py`
-- (`_select_active_saved_searches()`) scans every `is_active` row in that
-- table on a schedule and scores it with a real LLM call against
-- `job_registry_postings`, with no column that could tell it "this row
-- isn't a job-registry search, skip it." A hiring-signal row parked there
-- would eventually get scored against job postings it was never meant to
-- match and could produce a bogus Today-feed notification. This table has
-- no `companies`, `remote_only`, `is_active`, or `last_matched_at` column
-- on purpose -- nothing ever background-scans it, so it carries none of
-- the shape that scan depends on, and `saved_search_matcher.py` never has
-- to change (or even know this feature exists) as a result. RLS mirrors
-- `hiring_signal_saves` above, plus update (these rows are user-editable,
-- not write-once pointers).
create table public.hiring_signal_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  query text not null,
  location text,
  created_at timestamptz not null default now()
);

create index hiring_signal_searches_user_id_idx
  on public.hiring_signal_searches using btree (user_id, created_at desc);

alter table public.hiring_signal_searches enable row level security;

create policy hiring_signal_searches_select_own on public.hiring_signal_searches
  for select
  using (auth.uid() = user_id);

create policy hiring_signal_searches_insert_own on public.hiring_signal_searches
  for insert
  with check (auth.uid() = user_id);

create policy hiring_signal_searches_update_own on public.hiring_signal_searches
  for update
  using (auth.uid() = user_id);

create policy hiring_signal_searches_delete_own on public.hiring_signal_searches
  for delete
  using (auth.uid() = user_id);
