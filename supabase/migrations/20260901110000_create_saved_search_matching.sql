-- Job Finder P9b (today-feed-job-matching.md) -- the background
-- matcher's own SQL surface: a "new registry postings since a per-search
-- watermark" query, the companion table carrying a high-fit match's own
-- job data, and the atomic two-table insert that creates a Today item
-- for it.
--
-- A NEW function, not an edit to `search_job_registry_postings` (P5c) --
-- that function underwent real, hard-won performance tuning after a live
-- statement-timeout incident against the real 89,000+-row table; adding
-- a new predicate to it risks regressing an already-delicate query
-- rather than composing cleanly. Mirrors its own real fixes directly
-- instead of relearning them: no ORDER BY on the text-search branch
-- (whatever calls this always re-ranks/filters afterward, same reasoning
-- P5c's own fix documented), and the browse-recent branch limits INSIDE
-- a subquery before joining to companies, not after.
create index if not exists job_registry_postings_active_first_seen_idx
  on public.job_registry_postings (first_seen desc)
  where status = 'active';

create function public.search_new_job_registry_postings(
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
begin
  if search_query = '' then
    return query
      select p.id, p.title, c.name, p.location, p.remote, p.apply_url, p.posted_at,
             p.salary_min, p.salary_max, p.salary_currency, p.sponsorship_signal,
             left(coalesce(p.jd_text, ''), 500)
      from (
        select * from public.job_registry_postings
        where status = 'active' and first_seen > since_timestamp
        order by first_seen desc
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
        where status = 'active' and first_seen > since_timestamp
          and jd_tsv @@ plainto_tsquery('english', search_query)
        limit result_limit
      ) p
      join public.job_registry_companies c on c.id = p.company_id;
  end if;
end;
$$;

revoke execute on function public.search_new_job_registry_postings(text, timestamptz, integer)
  from public, anon, authenticated;

-- The job-specific payload for a `kind = 'high_fit_job'` today_item --
-- a separate normalized table (never nullable columns bolted onto
-- `today_items`, never a JSON blob in `detail`), mirroring the existing
-- `company_intel_runs`/`claims` and `interview_sessions`/`interview_
-- session_questions` precedent for "the core table stays lean, a
-- companion table carries one kind's own structured extra data."
--
-- `unique (saved_search_id, apply_url)` (D6) is the real dedup boundary:
-- the SAME job must never produce a second Today item for the SAME
-- saved search across multiple matcher runs, even under retry or a
-- second concurrent worker -- enforced by Postgres, not just app logic.
create table public.today_item_job_matches (
  id uuid primary key default gen_random_uuid(),
  today_item_id uuid not null unique references public.today_items (id) on delete cascade,
  saved_search_id uuid not null references public.saved_searches (id) on delete cascade,
  apply_url text not null,
  title text not null,
  company text,
  location text,
  score100 integer not null,
  bin text not null,
  snippet text not null default '',
  provider text not null,
  created_at timestamptz not null default now(),
  unique (saved_search_id, apply_url)
);

alter table public.today_item_job_matches enable row level security;

-- No RLS policies at all, matching `today_items`' own precedent -- the
-- GET /today route reads via the service-role client with a verified
-- user_id filter (joined through today_items), same as every other
-- table in this codebase; there is no legitimate direct-client path.

-- The atomic two-table insert (D7/D8): `change_application_stage`'s own
-- precedent for "two tables change together, or neither does" -- one
-- `security definer` function, both inserts in the same implicit
-- transaction. A unique-violation on EITHER constraint (source_outbox_
-- event_id's idempotency boundary, or the (saved_search_id, apply_url)
-- dedup boundary) rolls back both inserts and propagates as a real
-- Postgres error `digest_listener.handle_batch`'s own existing
-- `except APIError: if e.code != _UNIQUE_VIOLATION: raise` already knows
-- how to handle -- no new Python-side idempotency logic needed.
create function public.insert_high_fit_job_today_item(
  p_user_id uuid,
  p_headline text,
  p_detail text,
  p_source_outbox_event_id uuid,
  p_saved_search_id uuid,
  p_apply_url text,
  p_title text,
  p_company text,
  p_location text,
  p_score100 integer,
  p_bin text,
  p_snippet text,
  p_provider text
)
returns public.today_items
language plpgsql
security definer
set search_path = public
as $$
declare
  v_today_item public.today_items;
begin
  insert into public.today_items (
    user_id, application_id, kind, headline, detail, source_outbox_event_id
  ) values (
    p_user_id, null, 'high_fit_job', p_headline, p_detail, p_source_outbox_event_id
  )
  returning * into v_today_item;

  insert into public.today_item_job_matches (
    today_item_id, saved_search_id, apply_url, title, company, location,
    score100, bin, snippet, provider
  ) values (
    v_today_item.id, p_saved_search_id, p_apply_url, p_title, p_company, p_location,
    p_score100, p_bin, p_snippet, p_provider
  );

  return v_today_item;
end;
$$;

revoke execute on function public.insert_high_fit_job_today_item(
  uuid, text, text, uuid, uuid, text, text, text, text, integer, text, text, text
) from public, anon, authenticated;
