-- P0.8 -- the registry lane must not present a stale posting as live.
--
-- Production had 70,027 of 116,517 "active" postings last seen more than 7 days ago
-- (61,219 more than 21), and Discover stamped every one of them "link verified". Three
-- things change here, because the obvious fix (one more WHERE clause) is not safe:
--
-- 1. Freshness is a bounded scan, not a bare filter. `last_seen` is in no index on
--    purpose (P0.6a, 20260926060736: an indexed `last_seen` made every poll touch a
--    non-HOT update that re-inserted the whole tsvector into the GIN index). So a
--    `last_seen > now() - 7 days` predicate is a heap-side filter. Measured on the real
--    table on 2026-10-03 (EXPLAIN ANALYZE, with every row treated as stale, which is what
--    the table looks like whenever the poller has been down) it cost 1.8 s on the browse
--    branch and 7.8 s on a text query -- past the API role's 8 s statement timeout, the same
--    57014 that P5c fixed. So each branch first takes a CANDIDATE window from the index it
--    already walks (ten times the result limit), then filters that window by freshness,
--    then limits. In the healthy case Postgres stops fetching heap rows as soon as
--    `result_limit` have passed the filter, so the cost stays close to the old one; in the
--    all-stale case it reads at most the window and returns fewer rows (38 ms for the text
--    branch on the same table). The price is stated plainly: if fewer than a tenth of a
--    query's candidates are fresh, the lane returns fewer rows than exist. That only
--    happens while the poller is behind, and the lane fills again once a poller is running.
--
-- 2. The browse branch never used its index. `job_registry_postings_active_posted_idx`
--    is `(posted_at desc)`, which sorts NULLs FIRST; the query orders `posted_at desc
--    nulls last`, which that index cannot serve. P5c's comment says the browse branch
--    uses "the existing posted_at index"; it did not -- EXPLAIN on production shows a
--    parallel seq scan plus top-N sort, 2.4 s for 150 rows, against 85 ms when the
--    ORDER BY matches an index. The new index below matches it. (The old index is left
--    alone; dropping an index is a separate decision.)
--
-- 3. A 304 is a confirmation. About 9,500 boards (Greenhouse, Lever, Ashby, Workable,
--    Recruitee) answer `If-None-Match` with 304 when nothing changed. A 304 tick advances
--    `last_polled_at` but touches no posting, so a quiet board would age out of a
--    7-day window while being perfectly live. `confirm_job_registry_boards` refreshes
--    `last_seen` for such a board's active rows, at most once a day per row (the window
--    is 7 days; a daily refresh is plenty and keeps the write volume small). It is only
--    called for adapters whose 304 covers the WHOLE board in one request; SmartRecruiters
--    and Amazon are paginated and stop sending If-None-Match in the same change.
--    Write cost: this is strictly lighter than what a board that returns 200 already
--    costs, because a complete poll's upsert rewrites every row of the board on every poll
--    (every 3 hours for the hot and dream tiers), while a confirmed row is written at most
--    once a day. The rows sit on pages written before fillfactor 85 (P0.6a), so many of those
--    updates are not HOT and re-insert into the GIN index; the first pass after the poller
--    comes back from an outage is one normal poll cycle's worth. Watch the HOT ratio
--    (cohort_queries.sql in the private repo) when it does.
--
-- `search_job_registry_postings` also returns `link_fresh`: true only if the posting was
-- seen within 3 days AND its company's last poll was within 48 hours AND that company
-- has no consecutive failures. 3 days is not arbitrary: a complete poll refreshes every
-- row, a 304 refreshes a row at most a day late (the throttle above), and a company
-- polled within 48 hours has therefore been confirmed within 1 + 2 days. `last_polled_at`
-- alone is not a confirmation -- a failed poll advances it too (penalize), and a capped
-- board's unfetched tail keeps an old `last_seen` while the board shows as just polled.
-- So `link_fresh` means "listed on the company's own board recently", not "the link was
-- fetched". A row past 3 days but inside the 7-day window is still returned, unverified:
-- a genuine "we do not know", and the UI already has the copy for it.
--
-- Plpgsql note: every column of the inner subqueries is qualified (`jp.`). The function's
-- output columns share names with table columns (`title`, `location`, `posted_at`, ...),
-- and an unqualified reference to a name that is also an output column raises 42702 at
-- first call, not when the migration applies.
--
-- `search_new_job_registry_postings` (the saved-search matcher's query) keeps its return
-- shape and gets only the same 7-day exclusion and candidate window; the matcher never
-- stamps `link_checked`, and a missing company poll is a recoverable condition the
-- matcher's advancing watermark must not turn into a permanent skip.

create index if not exists job_registry_postings_active_posted_nulls_last_idx
  on public.job_registry_postings (posted_at desc nulls last)
  where status = 'active';

-- RETURNS TABLE gains a column, which `create or replace` refuses (42P13), and dropping
-- the function discards its grants: restated below, nothing relies on a default.
drop function public.search_job_registry_postings(text, integer);

create function public.search_job_registry_postings(search_query text, result_limit integer)
returns table (
  posting_id uuid,
  title text,
  company_name text,
  location text,
  remote boolean,
  apply_url text,
  posted_at timestamptz,
  salary_min numeric,
  salary_max numeric,
  salary_currency text,
  sponsorship_signal text,
  snippet text,
  link_fresh boolean
)
language plpgsql
security definer
set search_path = public
stable
as $$
declare
  v_candidates integer := greatest(coalesce(result_limit, 0), 1) * 10;
begin
  if search_query = '' then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500),
             coalesce(
               c.last_polled_at >= now() - interval '48 hours'
                 and c.consecutive_failures = 0
                 and p.last_seen >= now() - interval '3 days',
               false)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active'
          order by jp.posted_at desc nulls last
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  else
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500),
             coalesce(
               c.last_polled_at >= now() - interval '48 hours'
                 and c.consecutive_failures = 0
                 and p.last_seen >= now() - interval '3 days',
               false)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active'
            and jp.jd_tsv @@ plainto_tsquery('english', search_query)
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_job_registry_postings(text, integer)
  from public, anon, authenticated;
grant execute on function public.search_job_registry_postings(text, integer) to service_role;

-- Same return shape as before, so `create or replace` is allowed (and keeps the grants;
-- they are restated anyway).
create or replace function public.search_new_job_registry_postings(
  search_query text, since_timestamp timestamptz, result_limit integer
)
returns table (
  posting_id uuid,
  title text,
  company_name text,
  location text,
  remote boolean,
  apply_url text,
  posted_at timestamptz,
  salary_min numeric,
  salary_max numeric,
  salary_currency text,
  sponsorship_signal text,
  snippet text
)
language plpgsql
security definer
set search_path = public
stable
as $$
declare
  v_candidates integer := greatest(coalesce(result_limit, 0), 1) * 10;
begin
  if search_query = '' then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active' and jp.first_seen > since_timestamp
          order by jp.first_seen desc
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  else
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select w.* from (
          select * from public.job_registry_postings jp
          where jp.status = 'active' and jp.first_seen > since_timestamp
            and jp.jd_tsv @@ plainto_tsquery('english', search_query)
          limit v_candidates
        ) w
        where w.last_seen > now() - interval '7 days'
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_new_job_registry_postings(text, timestamptz, integer)
  from public, anon, authenticated;
grant execute on function public.search_new_job_registry_postings(text, timestamptz, integer)
  to service_role;

-- A board that answered 304 is unchanged since the last complete fetch, which confirmed
-- every posting it still lists (and closed the ones it no longer did). Refresh `last_seen`
-- on its active rows, at most once a day each. Touches only `last_seen`: no index
-- contains it, so a page with free space takes the update HOT; status and closed_at are
-- left alone, so a closed posting is never resurrected by a 304. Returns the number of
-- rows touched. The 60 s timeout is the poller's other bookkeeping functions' budget; the
-- caller sends a few boards per call so one large board cannot exhaust it.
create function public.confirm_job_registry_boards(boards text[])
returns integer
language sql
security definer
set search_path = public
set statement_timeout = '60s'
as $$
  with touched as (
    update public.job_registry_postings
    set last_seen = now()
    where board = any(boards)
      and status = 'active'
      and last_seen < now() - interval '1 day'
    returning 1
  )
  select count(*)::integer from touched;
$$;

revoke execute on function public.confirm_job_registry_boards(text[])
  from public, anon, authenticated;
grant execute on function public.confirm_job_registry_boards(text[]) to service_role;

-- The newest successful-or-not poll across active boards. Discover uses it only to explain
-- an EMPTY registry lane: no rows because nothing matched, or no rows because the whole
-- registry is out of date (the poller has been down) -- two situations that look identical
-- to the user otherwise. Reads the ~16k-row companies table, never the postings table.
create function public.registry_last_polled_at()
returns timestamptz
language sql
security definer
set search_path = public
stable
as $$
  select max(last_polled_at) from public.job_registry_companies where is_active;
$$;

revoke execute on function public.registry_last_polled_at() from public, anon, authenticated;
grant execute on function public.registry_last_polled_at() to service_role;
