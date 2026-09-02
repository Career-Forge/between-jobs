-- Job Finder registry poller, P2 (job-finder-port.md D2/D8) -- a faithful
-- port of n8n's own CareerForge_ATS_Poller.json scheduling/closure/backoff
-- SQL, read directly from the live workflow (nodes `Select Due Companies`,
-- `Close Stale Jobs`, `Advance Poll State`, `Penalize Failed Boards`,
-- `Upsert Jobs`) -- not redesigned from a description of what it does.
--
-- D8 (Pranav, 2026-08-30): between-jobs stores every posting from a board,
-- not just AI/ML-title-matching ones (n8n's own scope, since its bot served
-- one person's own job search). This changes what "relevant" means for the
-- tier-promotion CASE logic below -- see job_registry_poller.py's own
-- module docstring for the full rationale -- but the CASE logic ITSELF is
-- untouched: same thresholds, same dream-tier stickiness, same one-rung
-- demotion ladder, same exponential backoff. Only the caller-computed input
-- number changes meaning, not this SQL.
--
-- `tier` now gets the real vocabulary this research confirmed:
-- dream/hot/warm/probe/cold are the only STORED values (P1's migration left
-- this open pending this confirmation). "never_polled" is a derived query
-- condition (last_polled_at is null), never written to the column.
alter table public.job_registry_companies
  add constraint job_registry_companies_tier_check
  check (tier in ('dream', 'hot', 'warm', 'probe', 'cold'));

-- Every function below processes the ENTIRE cross-user registry with no
-- user_id filter at all -- same class of function as
-- claim_and_publish_outbox_batch, and the same lesson applies: SECURITY
-- DEFINER + a hard revoke from public/anon/authenticated from the start,
-- not patched in after the fact.

-- `Select Due Companies` -- four UNION ALL lanes, same caps, same ordering.
-- P2's own adaptation (not in n8n's query): filtered to the ats_types P2
-- actually has an adapter for, so the ~140 companies on unimplemented
-- platforms don't sit in next_poll_at's past forever, perpetually
-- occupying the scarce never-polled/probe+cold lane slots without ever
-- being able to advance. Revisit this literal list in P3 when more
-- adapters land.
create function public.select_due_job_registry_companies()
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
             (ats_type || ':' || slug) as board, coalesce(etag, '') as etag
      from public.job_registry_companies
      where is_active and tier = 'dream' and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 24
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier <> 'dream' and last_polled_at is null and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by created_at asc
      limit 8
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('hot', 'warm') and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 40
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('probe', 'cold') and last_polled_at is not null and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag from due;
$$;

revoke execute on function public.select_due_job_registry_companies()
  from public, anon, authenticated;

-- `Upsert Jobs` -- conditional-overwrite upsert, same rules as the
-- reference: jd_text/apply_url only overwrite when the new value is
-- non-empty/looks like a real URL (a tick that fetched a shorter/blank
-- value never wipes a previously-good one); salary/sponsorship/extraction
-- columns only overwrite when this tick's row actually ran extraction
-- (extracted_at is not null), so a row that didn't qualify for extraction
-- this tick never clobbers an earlier real extraction. Returns (board,
-- is_new) per upserted row via the classic `xmax = 0` trick -- this is how
-- job_registry_poller.py learns the new-posting count per board (P2's own
-- adaptation of what "relevant" measures, since D8 dropped the
-- title-match filter n8n used for the same purpose) in the same
-- statement as the write, no separate pre-check query and no race.
create function public.upsert_job_registry_postings(postings jsonb)
returns table (board text, is_new boolean)
language sql
security definer
set search_path = public
as $$
  insert into public.job_registry_postings (
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
  on conflict (board, external_id) do update set
    last_seen = now(),
    status = 'active',
    closed_at = null,
    title = excluded.title,
    location = excluded.location,
    remote = excluded.remote,
    posted_at = coalesce(excluded.posted_at, job_registry_postings.posted_at),
    jd_text = case when excluded.jd_text <> '' then excluded.jd_text else job_registry_postings.jd_text end,
    apply_url = case
      when excluded.apply_url like 'http%' then excluded.apply_url
      else job_registry_postings.apply_url
    end,
    salary_min = case
      when excluded.extracted_at is not null then excluded.salary_min
      else job_registry_postings.salary_min
    end,
    salary_max = case
      when excluded.extracted_at is not null then excluded.salary_max
      else job_registry_postings.salary_max
    end,
    salary_currency = case
      when excluded.extracted_at is not null then excluded.salary_currency
      else job_registry_postings.salary_currency
    end,
    salary_period = case
      when excluded.extracted_at is not null then excluded.salary_period
      else job_registry_postings.salary_period
    end,
    sponsorship_signal = case
      when excluded.extracted_at is not null then excluded.sponsorship_signal
      else job_registry_postings.sponsorship_signal
    end,
    extracted_at = case
      when excluded.extracted_at is not null then excluded.extracted_at
      else job_registry_postings.extracted_at
    end,
    extraction_version = case
      when excluded.extracted_at is not null then excluded.extraction_version
      else job_registry_postings.extraction_version
    end
  returning board, (xmax = 0) as is_new;
$$;

revoke execute on function public.upsert_job_registry_postings(jsonb)
  from public, anon, authenticated;

-- `Close Stale Jobs` -- pure absence-based closure. None of P2's 4 adapters
-- need the sweep-start-cutoff bookkeeping (job-finder-port.md's P1 research
-- confirmed that only matters for n8n's cross-tick-paginated google/
-- microsoft adapters, deferred to P3+) -- every P2 board completes its
-- full listing within one tick, so `cutoff` is always that tick's own
-- run_start, exactly like n8n's non-paginated adapters.
create function public.close_stale_job_registry_postings(close_targets jsonb)
returns void
language sql
security definer
set search_path = public
as $$
  update public.job_registry_postings j
  set status = 'closed', closed_at = now()
  from jsonb_to_recordset(close_targets) as c(board text, cutoff timestamptz)
  where j.board = c.board and j.status = 'active' and j.last_seen < c.cutoff;
$$;

revoke execute on function public.close_stale_job_registry_postings(jsonb)
  from public, anon, authenticated;

-- `Advance Poll State` -- the tier/poll_interval/next_poll_at state
-- machine, byte-identical CASE logic to n8n's own (read directly from the
-- live workflow, not paraphrased). `relevant = -1` is the 304-Not-Modified
-- sentinel (matches n8n exactly): it falls through every branch to ELSE,
-- so an unchanged board still resets last_polled_at/consecutive_failures
-- but never touches tier or poll_interval.
create function public.advance_job_registry_poll_state(results jsonb)
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
  from jsonb_to_recordset(results) as e(board text, etag text, relevant int)
  where (c.ats_type || ':' || c.slug) = e.board;
$$;

revoke execute on function public.advance_job_registry_poll_state(jsonb)
  from public, anon, authenticated;

-- `Penalize Failed Boards` -- real exponential backoff, same formula and
-- caps as n8n's own: 2^min(failures+1, 6) against the board's CURRENT
-- poll_interval (compounding on top of whatever tier already set),
-- hard-capped at 7 days. 404/410 (gone_boards) deactivates immediately
-- regardless of strike count; everything else (failed_boards) deactivates
-- only after the 5th consecutive failure.
create function public.penalize_failed_job_registry_boards(failed_boards text[], gone_boards text[])
returns void
language sql
security definer
set search_path = public
as $$
  update public.job_registry_companies c
  set consecutive_failures = consecutive_failures + 1,
      last_polled_at = now(),
      next_poll_at = now() + least(
        poll_interval * power(2, least(consecutive_failures + 1, 6)),
        interval '7 days'
      ),
      is_active = case
        when (c.ats_type || ':' || c.slug) = any(gone_boards) then false
        else (consecutive_failures + 1) < 5
      end
  where (c.ats_type || ':' || c.slug) = any(failed_boards)
     or (c.ats_type || ':' || c.slug) = any(gone_boards);
$$;

revoke execute on function public.penalize_failed_job_registry_boards(text[], text[])
  from public, anon, authenticated;
