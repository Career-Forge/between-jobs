-- Job Finder P5c (live-search-track.md) -- a real statement timeout
-- (57014) hit on the very first live verification of
-- search_job_registry_postings() against the real 89,489+-row registry,
-- not caught by any unit test (the fakes used there have no real row
-- count or query planner to expose this). Root-caused via EXPLAIN
-- (ANALYZE, BUFFERS, TIMING) directly against the real project, not
-- guessed at:
--
-- 1. `job_registry_postings_active_posted_idx` (posted_at desc where
--    status='active') isn't actually selective for a full-text search --
--    virtually every active row matches `status='active'` (~85,808 of
--    them), so BitmapAnd-ing it against the tsv match set added real
--    overhead (building/merging an 85k-row bitmap) for close to zero
--    filtering benefit. Fixed with a NEW partial GIN index scoped to
--    `where status = 'active'` on `jd_tsv` itself, so the active filter
--    is baked into the index the search already needs, not a second
--    index to merge against.
-- 2. Sorting the full tsv-match candidate set (thousands of rows,
--    scattered across the table) by `posted_at` before applying LIMIT
--    forced touching ~1,900+ heap pages just to return 150 rows --
--    confirmed via EXPLAIN's own "Heap Blocks" and buffer counts. Fixed
--    by DROPPING the ORDER BY for the search-query path entirely: this
--    function's own result order was never load-bearing to begin with --
--    `search_aggregation.aggregate_jobs()` (P5a) always re-sorts the
--    FULL merged result set (all lanes) by tier+recency before a caller
--    ever sees it, so the registry lane returning an unordered-but-
--    correct candidate set costs nothing downstream.
-- 3. The browse-recent path (empty search_query, no tsv filter) had a
--    SEPARATE real slowness: joining to job_registry_companies BEFORE
--    applying LIMIT forced Postgres into a full hash join across both
--    tables (41,579 estimated active postings x 15,964 companies) before
--    sorting and limiting. Fixed by restructuring to LIMIT first (inside
--    a subquery, using the existing posted_at index), THEN joining --
--    the join now only ever touches the 150 already-limited rows.
--
-- Point 2 needs real conditional logic (skip ORDER BY on one path, keep
-- it on the other) that a single `language sql` statement can't express
-- cleanly without risking the planner not optimizing a CASE-based sort
-- key away -- switched to `language plpgsql` (already an established
-- pattern in this codebase, e.g. merge_and_consume_link_code) with two
-- explicit branches instead.
--
-- Verified live against the real project before writing this migration,
-- not assumed: both branches went from consistently exceeding a real
-- 57014 timeout (multi-second, cold-cache execution) to 161-333ms warm
-- and ~700ms cold -- comfortably inside any real statement timeout.

create index if not exists job_registry_postings_active_tsv_idx
  on public.job_registry_postings using gin (jd_tsv)
  where status = 'active';

create or replace function public.search_job_registry_postings(search_query text, result_limit integer)
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
begin
  if search_query = '' then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select * from public.job_registry_postings
        where status = 'active'
        order by posted_at desc nulls last
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  else
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select * from public.job_registry_postings
        where status = 'active' and jd_tsv @@ plainto_tsquery('english', search_query)
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_job_registry_postings(text, integer)
  from public, anon, authenticated;
