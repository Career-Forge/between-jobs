-- Make the registry poller's writes cheap enough to finish (launch plan P0.6a).
--
-- The poller had been dying on statement timeouts since 2026-08-31: the API
-- roles run with an 8s statement_timeout, and upsert_job_registry_postings
-- averaged 690ms with a max of 7.97s, so a slow batch -- or the close-stale
-- step after it -- was cancelled with 57014 and, before worker supervision,
-- ended the poller for good.
--
-- The cost came from re-seen postings. Most postings in a tick are ones the
-- registry already has, unchanged, but the upsert rewrote every column of
-- them: the job description (re-TOASTed), the generated jd_tsv (recomputed
-- and re-TOASTed) and, because last_seen sat in an index, a non-HOT update
-- that re-inserted the full tsvector into both GIN indexes. Only 2,787 of
-- 1.33M updates had been HOT.
--
-- Now:
--   1. The upsert first touches unchanged postings -- last_seen, status and
--      closed_at only -- and runs the full insert-or-update only for new or
--      changed ones. "Unchanged" means the full update would have stored
--      exactly what is there already; the extraction timestamp is ignored,
--      since the poller stamps it on every tick even when the extracted
--      values are identical.
--   2. last_seen leaves every index (close-stale finds a board's rows through
--      the (board, status) index instead), and the table keeps 15% of each
--      page free, so a touch can be a HOT update that writes no index at all.
--      The free space applies to pages written from now on.
--   3. The full-table GIN index on jd_tsv goes: every search reads active
--      postings through job_registry_postings_active_tsv_idx, and the full
--      index had been scanned once. D-19: the never-populated embedding column
--      and its HNSW index go too.
--   4. The poller's own functions -- background work, never on a request's
--      path -- get a 60s statement_timeout instead of the API roles' 8s.

drop index if exists public.job_registry_postings_tsv_idx;
drop index if exists public.job_registry_postings_board_lastseen_idx;
drop index if exists public.job_registry_postings_embedding_idx;
alter table public.job_registry_postings drop column if exists embedding;
alter table public.job_registry_postings set (fillfactor = 85);

create or replace function public.upsert_job_registry_postings(postings jsonb)
returns table (board text, is_new boolean)
language plpgsql
security definer
set search_path = public
set statement_timeout = '60s'
as $$
#variable_conflict use_column
begin
  -- Unchanged postings: touch only. The conditions mirror the full update
  -- below -- a field the full update would keep (an empty jd_text, a non-http
  -- apply_url, a null posted_at, extraction absent) can't make a row "changed".
  return query
  with incoming as (
    select
      p ->> 'board' as board,
      p ->> 'external_id' as external_id,
      p ->> 'title' as title,
      coalesce(p ->> 'jd_text', '') as jd_text,
      p ->> 'location' as location,
      (p ->> 'remote')::boolean as remote,
      p ->> 'apply_url' as apply_url,
      (p ->> 'posted_at')::timestamptz as posted_at,
      (p ->> 'salary_min')::numeric as salary_min,
      (p ->> 'salary_max')::numeric as salary_max,
      p ->> 'salary_currency' as salary_currency,
      p ->> 'salary_period' as salary_period,
      coalesce(p ->> 'sponsorship_signal', 'unknown') as sponsorship_signal,
      (p ->> 'extracted_at')::timestamptz as extracted_at,
      (p ->> 'extraction_version')::integer as extraction_version
    from jsonb_array_elements(postings) as p
  ),
  touched as (
    update public.job_registry_postings j
    set last_seen = now(), status = 'active', closed_at = null
    from incoming i
    where j.board = i.board
      and j.external_id = i.external_id
      and j.title is not distinct from i.title
      and j.location is not distinct from i.location
      and j.remote is not distinct from i.remote
      and (i.posted_at is null or j.posted_at is not distinct from i.posted_at)
      and (i.jd_text = '' or j.jd_text = i.jd_text)
      and (coalesce(i.apply_url, '') not like 'http%' or j.apply_url is not distinct from i.apply_url)
      and (
        i.extracted_at is null
        or (
          j.salary_min is not distinct from i.salary_min
          and j.salary_max is not distinct from i.salary_max
          and j.salary_currency is not distinct from i.salary_currency
          and j.salary_period is not distinct from i.salary_period
          and j.sponsorship_signal is not distinct from i.sponsorship_signal
          and j.extraction_version is not distinct from i.extraction_version
        )
      )
    returning j.board
  )
  select t.board, false from touched t;

  -- New or changed postings: the original insert-or-update, unchanged, for
  -- every incoming row the touch above didn't take. A touched row carries
  -- last_seen = now(), the transaction's own timestamp, which nothing written
  -- before this call can have.
  return query
  insert into public.job_registry_postings as j (
    company_id, board, external_id, title, jd_text, location, remote,
    apply_url, posted_at, status, last_seen, closed_at,
    salary_min, salary_max, salary_currency, salary_period,
    sponsorship_signal, extracted_at, extraction_version
  )
  select
    (p ->> 'company_id')::uuid,
    p ->> 'board',
    p ->> 'external_id',
    p ->> 'title',
    coalesce(p ->> 'jd_text', ''),
    p ->> 'location',
    (p ->> 'remote')::boolean,
    p ->> 'apply_url',
    (p ->> 'posted_at')::timestamptz,
    'active',
    now(),
    null,
    (p ->> 'salary_min')::numeric,
    (p ->> 'salary_max')::numeric,
    p ->> 'salary_currency',
    p ->> 'salary_period',
    coalesce(p ->> 'sponsorship_signal', 'unknown'),
    (p ->> 'extracted_at')::timestamptz,
    (p ->> 'extraction_version')::integer
  from jsonb_array_elements(postings) as p
  where not exists (
    select 1
    from public.job_registry_postings seen
    where seen.board = p ->> 'board'
      and seen.external_id = p ->> 'external_id'
      and seen.last_seen = now()
  )
  on conflict (board, external_id) do update set
    last_seen = now(),
    status = 'active',
    closed_at = null,
    title = excluded.title,
    location = excluded.location,
    remote = excluded.remote,
    posted_at = coalesce(excluded.posted_at, j.posted_at),
    jd_text = case when excluded.jd_text <> '' then excluded.jd_text else j.jd_text end,
    apply_url = case
      when excluded.apply_url like 'http%' then excluded.apply_url
      else j.apply_url
    end,
    salary_min = case
      when excluded.extracted_at is not null then excluded.salary_min
      else j.salary_min
    end,
    salary_max = case
      when excluded.extracted_at is not null then excluded.salary_max
      else j.salary_max
    end,
    salary_currency = case
      when excluded.extracted_at is not null then excluded.salary_currency
      else j.salary_currency
    end,
    salary_period = case
      when excluded.extracted_at is not null then excluded.salary_period
      else j.salary_period
    end,
    sponsorship_signal = case
      when excluded.extracted_at is not null then excluded.sponsorship_signal
      else j.sponsorship_signal
    end,
    extracted_at = case
      when excluded.extracted_at is not null then excluded.extracted_at
      else j.extracted_at
    end,
    extraction_version = case
      when excluded.extracted_at is not null then excluded.extraction_version
      else j.extraction_version
    end
  returning j.board, (j.xmax = 0);
end;
$$;

revoke execute on function public.upsert_job_registry_postings(jsonb)
  from public, anon, authenticated;
grant execute on function public.upsert_job_registry_postings(jsonb) to service_role;

alter function public.close_stale_job_registry_postings(jsonb) set statement_timeout = '60s';
alter function public.advance_job_registry_poll_state(jsonb) set statement_timeout = '60s';
alter function public.penalize_failed_job_registry_boards(text[], text[])
  set statement_timeout = '60s';
alter function public.select_due_job_registry_companies() set statement_timeout = '60s';
alter function public.select_eightfold_jd_backfill_candidates() set statement_timeout = '60s';
alter function public.backfill_job_registry_posting_descriptions(jsonb)
  set statement_timeout = '60s';
