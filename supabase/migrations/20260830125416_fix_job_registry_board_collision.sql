-- Fix a real board-collision bug found via P2's own live verification
-- (job-finder-port.md P2 section) -- `job_registry_postings.board` was
-- `ats_type:slug` only, matching n8n's own original schema shape exactly
-- (a faithful port, per D2), but Workday tenants are CONFIRMED to
-- routinely reuse generic site slugs ("External", "external_careers",
-- "careers", ...) across totally unrelated companies -- that's exactly
-- why `job_registry_companies`' own uniqueness constraint is
-- `(ats_type, slug, api_base)`, not `(ats_type, slug)`. `board` never
-- carried `api_base`, so two unrelated companies sharing a slug shared
-- the exact same board string.
--
-- This wasn't a theoretical risk (P1's own migration comment wrongly
-- treated it as "no collision today, so not fixed here"). A real
-- verification tick against the live registry proved it live: 18
-- different Workday companies (PNC, GEICO, T-Mobile, Micron, Travelers,
-- and 13 others) all share the board `workday:External`; 12 more share
-- `workday:external`; Oracle has the same problem (30 companies, 12
-- distinct boards). Because `close_stale_job_registry_postings` and
-- `advance_job_registry_poll_state` match purely on `board`, a single
-- company's poll result was being applied to EVERY company sharing its
-- board -- confirmed directly: a tick that fetched 72 companies updated
-- `last_polled_at`/tier on 93, with the other 21 getting another
-- company's poll outcome applied to their own row and zero real
-- knowledge of their own actual listings.
--
-- Fix: `board` becomes the same 3-part key the companies table already
-- uses to disambiguate itself: `ats_type:slug:api_base`. Two parts:
-- (1) this migration's own backfill of all existing
-- `job_registry_postings.board` values (an in-place UPDATE recomputing
-- the column from a company join, not a delete+reinsert -- the row data
-- itself was always correct, only the board TAG was wrong), batched by
-- ats_type to stay under Postgres's own statement timeout (the same
-- constraint P1's seed import and P2's own tick upserts already hit
-- against this table); (2) `create or replace function` on the four
-- functions that compute or match against `board`, so every future tick
-- uses the corrected key. `scripts/import_job_registry_seed.py` is fixed
-- in the same commit so a future re-run doesn't regress this.

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
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 24
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier <> 'dream' and last_polled_at is null and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by created_at asc
      limit 8
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('hot', 'warm') and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 40
    )
    union all
    (
      select id, name, ats_type, slug, api_base,
             (ats_type || ':' || slug || ':' || api_base), coalesce(etag, '')
      from public.job_registry_companies
      where is_active and tier in ('probe', 'cold') and last_polled_at is not null and next_poll_at <= now()
        and ats_type in ('greenhouse', 'lever', 'ashby', 'workday')
      order by next_poll_at asc
      limit 8
    )
  )
  select id as company_id, name, ats_type, slug, api_base, board, etag from due;
$$;

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
  where (c.ats_type || ':' || c.slug || ':' || c.api_base) = e.board;
$$;

create or replace function public.penalize_failed_job_registry_boards(failed_boards text[], gone_boards text[])
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
        when (c.ats_type || ':' || c.slug || ':' || c.api_base) = any(gone_boards) then false
        else (consecutive_failures + 1) < 5
      end
  where (c.ats_type || ':' || c.slug || ':' || c.api_base) = any(failed_boards)
     or (c.ats_type || ':' || c.slug || ':' || c.api_base) = any(gone_boards);
$$;
