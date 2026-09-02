-- Job Finder P5c (live-search-track.md) -- the registry-lane query for
-- the live-search merge. Reuses the existing GIN-indexed jd_tsv column
-- (P1's own migration, already populated on every row by the poller,
-- P2-P3e -- no new column, no backfill needed) rather than standing up a
-- second search index for the same data, same instinct
-- discovery_store.py's own n8n-facade search already had.
--
-- A plain SQL function (not asyncpg, unlike discovery_store.py's own
-- n8n-facade queries) -- job_registry_postings lives in THIS project's
-- own Supabase project, not a separate database this platform is a
-- read-only guest of, so it goes through the same service-role-client +
-- RPC pattern every other job_registry_* query in this codebase already
-- uses (select_due_job_registry_companies, etc.), not a special case.
--
-- jd_text is truncated in SQL (left(...,500)), not excluded like
-- discovery_store.py's own list view -- unlike that read-only facade,
-- this result feeds directly into a SearchResult.snippet field callers
-- expect populated, and truncating server-side avoids dragging a full
-- job description over the wire for every result in a list.
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
  snippet text
)
language sql
security definer
set search_path = public
stable
as $$
  select
    p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
    p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
    left(coalesce(p.jd_text, ''), 500)
  from public.job_registry_postings p
  join public.job_registry_companies c on c.id = p.company_id
  where p.status = 'active'
    and (search_query = '' or p.jd_tsv @@ plainto_tsquery('english', search_query))
  order by p.posted_at desc nulls last
  limit result_limit;
$$;

revoke execute on function public.search_job_registry_postings(text, integer)
  from public, anon, authenticated;
