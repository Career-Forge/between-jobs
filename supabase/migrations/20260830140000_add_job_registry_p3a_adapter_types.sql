-- Job Finder registry poller, P3a (job-finder-port.md) -- extends the
-- implemented-ats_type allowlist in select_due_job_registry_companies()
-- to include the 6 new mechanical long-tail adapters (SmartRecruiters,
-- Workable, Recruitee, Amazon, Apple, D.E. Shaw), so their real 76
-- companies actually get selected for polling instead of sitting in
-- next_poll_at's past forever (the same reasoning P2's own migration
-- documented for this literal-list-widens-per-phase pattern).
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
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw'
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
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw'
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
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw'
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
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw'
        )
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag from due;
$$;
