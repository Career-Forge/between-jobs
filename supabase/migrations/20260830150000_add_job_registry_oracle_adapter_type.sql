-- Job Finder registry poller, P3b (job-finder-port.md) -- extends the
-- implemented-ats_type allowlist in select_due_job_registry_companies()
-- to include 'oracle' (39 real companies -- Ford, JPMorgan Chase,
-- Goldman Sachs, Marriott, and 35 others), matching the same
-- literal-list-widens-per-phase pattern P2's and P3a's own migrations
-- documented.
create or replace function public.select_due_job_registry_companies()
returns table (
  company_id uuid,
  name text,
  ats_type text,
  slug text,
  api_base text,
  board text,
  etag text
)
language sql
security definer
set search_path = public
stable
as $$
  with due as (
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base) as board, coalesce(etag, '') as etag
      from public.job_registry_companies
      where is_active and tier = 'dream' and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle'
        )
      order by next_poll_at asc
      limit 24
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier <> 'dream' and last_polled_at is null and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle'
        )
      order by created_at asc
      limit 8
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('hot', 'warm') and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle'
        )
      order by next_poll_at asc
      limit 40
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('probe', 'cold') and last_polled_at is not null and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle'
        )
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag from due;
$$;
