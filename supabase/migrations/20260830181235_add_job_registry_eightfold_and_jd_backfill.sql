-- Job Finder registry poller, P3c (job-finder-port.md) -- adds Eightfold
-- to the implemented-ats_type allowlist, plus the two functions behind
-- its own separate JD-backfill lane (job_registry_poller.
-- run_eightfold_jd_backfill). Confirmed live (P3c research): Eightfold's
-- list endpoint NEVER returns real description text on either of its
-- two tiers, so unlike every other P2/P3a/P3b adapter, Eightfold rows
-- can only ever get real jd_text/salary/sponsorship data through this
-- separate lane, not the main per-tick list fetch.

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
          'oracle', 'eightfold'
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
          'oracle', 'eightfold'
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
          'oracle', 'eightfold'
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
          'oracle', 'eightfold'
        )
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag from due;
$$;

-- `Select JD Backfill Batch` -- up to 12 Eightfold rows/tick whose
-- jd_text never made it past the list-fetch's own permanent blank
-- (confirmed live: both list tiers always return an empty description),
-- most-recently-seen first so actively-live postings get backfilled
-- before ones about to close. The `apply_url ~ '/careers/job/[0-9]+'`
-- guard mirrors extract_eightfold_position_id's own regex -- a row this
-- can't extract a position id from can't be backfilled at all.
create function public.select_eightfold_jd_backfill_candidates()
returns table (id uuid, apply_url text, api_base text, slug text)
language sql
security definer
set search_path = public
stable
as $$
  select p.id, p.apply_url, c.api_base, c.slug
  from public.job_registry_postings p
  join public.job_registry_companies c on c.id = p.company_id
  where c.ats_type = 'eightfold'
    and p.status = 'active'
    and length(coalesce(p.jd_text, '')) < 50
    and p.apply_url ~ '/careers/job/[0-9]+'
  order by p.last_seen desc
  limit 12;
$$;

revoke execute on function public.select_eightfold_jd_backfill_candidates()
  from public, anon, authenticated;

-- `Update Job Descriptions` -- writes back only what the backfill
-- itself fetched: jd_text, apply_url (only when non-empty -- a failed/
-- empty canonicalPositionUrl must never blank a working link),
-- salary/sponsorship extraction. Deliberately does NOT touch `remote`
-- -- confirmed live (P3c research) the detail endpoint's own
-- work_location_option comes back null for every company tested, even
-- when the list endpoint had a real value for the same job.
create function public.backfill_job_registry_posting_descriptions(updates jsonb)
returns void
language sql
security definer
set search_path = public
as $$
  update public.job_registry_postings j
  set jd_text = u.jd_text,
      apply_url = case when u.apply_url <> '' then u.apply_url else j.apply_url end,
      salary_min = u.salary_min,
      salary_max = u.salary_max,
      salary_currency = u.salary_currency,
      salary_period = u.salary_period,
      sponsorship_signal = coalesce(u.sponsorship_signal, 'unknown'),
      extracted_at = u.extracted_at,
      extraction_version = u.extraction_version
  from jsonb_to_recordset(updates) as u(
    job_id uuid,
    jd_text text,
    apply_url text,
    salary_min numeric,
    salary_max numeric,
    salary_currency text,
    salary_period text,
    sponsorship_signal text,
    extracted_at timestamptz,
    extraction_version integer
  )
  where j.id = u.job_id;
$$;

revoke execute on function public.backfill_job_registry_posting_descriptions(jsonb)
  from public, anon, authenticated;
