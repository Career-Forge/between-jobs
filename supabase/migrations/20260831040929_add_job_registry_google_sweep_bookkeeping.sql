-- Job Finder registry poller, P3e (job-finder-port.md) -- Google's real
-- board (confirmed live: page 85 still returns a full 20-card page) is far
-- too large to fully re-walk inside one 15-minute tick, so it needs the
-- sweep-start-cutoff bookkeeping n8n's own real incident history
-- ("microsoft lost all 85 postings this way", s151) exists to prevent --
-- the only adapter in this registry that needs it, since every other
-- adapter here (including Workday, which also pages) completes its full
-- listing within a single tick.
--
-- Two genuinely new columns on job_registry_companies:
-- - sweep_started_at: when the CURRENT multi-tick sweep began (null when
--   no sweep is in progress). The page cursor itself reuses the EXISTING
--   `etag` column (already proven -- Google's real row already carries a
--   historical page value, "25", from P1's own seed import) -- no new
--   column needed for that half.
-- - sweep_posting_count: postings seen so far this sweep, across however
--   many ticks it has taken. Purely a zero-sweep-guard input (mirrors
--   n8n's real s151 fix): a sweep is only allowed to close stale postings
--   when it both completes (hit_end) AND has seen at least one posting
--   somewhere along the way -- a broken scraper returning zero on every
--   page of a whole sweep must never be read as "board is now empty."
--
-- Deliberately NOT guarded against: a sweep that resumes mid-board (e.g.
-- Google's real starting etag, "25", predates this bookkeeping entirely,
-- so its first sweep here will only walk pages 25 onward before
-- completing). A posting that's still genuinely live but sits on a page
-- this partial sweep never revisits will be closed at that sweep's
-- completion, then simply reopen itself (the upsert's own on-conflict
-- sets status back to 'active') once the NEXT sweep -- which always
-- starts fresh at page 1 -- reaches it again. Self-healing, not silent
-- data loss, and not the failure class the real incident was about (a
-- scraper wrongly treating total failure as a valid empty board,
-- forever) -- so this is left as an accepted, disclosed characteristic
-- rather than extra machinery to prevent it.
alter table public.job_registry_companies
  add column sweep_started_at timestamptz,
  add column sweep_posting_count integer not null default 0;

-- select_due_job_registry_companies()'s RETURNS TABLE shape is widening
-- (two new output columns), which create or replace function cannot do
-- for a return-type change -- drop and recreate.
drop function public.select_due_job_registry_companies();

create function public.select_due_job_registry_companies()
returns table (
  company_id uuid,
  name text,
  ats_type text,
  slug text,
  api_base text,
  board text,
  etag text,
  sweep_started_at timestamptz,
  sweep_posting_count integer
)
language sql
security definer
set search_path = public
stable
as $$
  with due as (
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base) as board, coalesce(etag, '') as etag,
             sweep_started_at, sweep_posting_count
      from public.job_registry_companies
      where is_active and tier = 'dream' and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle', 'eightfold', 'avature', 'successfactors', 'google'
        )
      order by next_poll_at asc
      limit 24
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, ''),
             sweep_started_at, sweep_posting_count
      from public.job_registry_companies
      where is_active and tier <> 'dream' and last_polled_at is null and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle', 'eightfold', 'avature', 'successfactors', 'google'
        )
      order by created_at asc
      limit 8
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, ''),
             sweep_started_at, sweep_posting_count
      from public.job_registry_companies
      where is_active and tier in ('hot', 'warm') and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle', 'eightfold', 'avature', 'successfactors', 'google'
        )
      order by next_poll_at asc
      limit 40
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, ''),
             sweep_started_at, sweep_posting_count
      from public.job_registry_companies
      where is_active and tier in ('probe', 'cold') and last_polled_at is not null and next_poll_at <= now()
        and ats_type in (
          'greenhouse', 'lever', 'ashby', 'workday',
          'smartrecruiters', 'workable', 'recruitee', 'amazon', 'apple', 'deshaw',
          'oracle', 'eightfold', 'avature', 'successfactors', 'google'
        )
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag,
         sweep_started_at, sweep_posting_count
  from due;
$$;

revoke execute on function public.select_due_job_registry_companies()
  from public, anon, authenticated;

-- advance_job_registry_poll_state()'s own parameter signature (results
-- jsonb) is unchanged, so create or replace applies cleanly here -- only
-- the jsonb_to_recordset record shape and the SET clause widen, to also
-- persist the two new sweep fields as a straight passthrough (the tier/
-- poll_interval/next_poll_at CASE logic itself, keyed on e.relevant, is
-- byte-identical to every prior phase's own -- sweep bookkeeping is
-- deliberately orthogonal to tiering, matching n8n's own real
-- architecture, where a paginated adapter's per-tick relevant count still
-- drives tiering independently of the separate multi-tick sweep).
create or replace function public.advance_job_registry_poll_state(results jsonb)
returns void
language sql
security definer
set search_path = public
as $$
  update public.job_registry_companies c
  set last_polled_at = now(),
      consecutive_failures = 0,
      etag = coalesce(e.etag, c.etag),
      relevant_yield = c.relevant_yield + greatest(e.relevant, 0),
      sweep_started_at = e.sweep_started_at,
      sweep_posting_count = e.sweep_posting_count,
      poll_interval = case
        when e.relevant >= 3 then least(c.poll_interval, interval '3 hours')
        when e.relevant >= 1 then least(c.poll_interval, interval '24 hours')
        when e.relevant = 0 and c.tier = 'hot' then interval '24 hours'
        when e.relevant = 0 and c.tier = 'warm' then interval '6 hours'
        when e.relevant = 0 and c.tier = 'probe' then interval '30 days'
        else c.poll_interval
      end,
      tier = case
        when c.tier = 'dream' then 'dream'
        when e.relevant >= 3 then 'hot'
        when e.relevant >= 1 and c.tier <> 'hot' then 'warm'
        when e.relevant = 0 and c.tier = 'hot' then 'warm'
        when e.relevant = 0 and c.tier = 'warm' then 'probe'
        when e.relevant = 0 and c.tier = 'probe' then 'cold'
        else c.tier
      end,
      next_poll_at = now() + (case
        when e.relevant >= 3 then least(c.poll_interval, interval '3 hours')
        when e.relevant >= 1 then least(c.poll_interval, interval '24 hours')
        when e.relevant = 0 and c.tier = 'hot' then interval '24 hours'
        when e.relevant = 0 and c.tier = 'warm' then interval '6 hours'
        when e.relevant = 0 and c.tier = 'probe' then interval '30 days'
        else c.poll_interval
      end)
  from jsonb_to_recordset(results) as e(
    board text, etag text, relevant int,
    sweep_started_at timestamptz, sweep_posting_count int
  )
  where (c.ats_type || ':' || c.slug || ':' || c.api_base) = e.board;
$$;
