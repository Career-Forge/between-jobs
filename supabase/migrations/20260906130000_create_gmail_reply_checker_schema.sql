-- Gmail reply/status parsing R3 (gmail-reply-status-parsing.md) -- the
-- poller's own two schema needs: (1) a way to find drafts due for a
-- reply check without an N+1 join in Python (outreach_drafts has no
-- direct application_id column -- the real link is three hops deep:
-- outreach_drafts.candidate_id -> contact_candidates.run_id ->
-- contact_research_runs.application_id -- so this is a real join, not a
-- single-table select), and (2) the Today-item companion table for a
-- below-threshold proposal, mirroring `today_item_job_matches`/
-- `insert_high_fit_job_today_item`'s own precedent (Job Finder P9b).

-- `applications.active_job_snapshot_id` is `not null`, so this is a plain
-- join, never a left join -- every application has a resolved snapshot.
create function public.list_drafts_due_for_reply_check(
  staleness_cutoff timestamptz, result_limit integer
)
returns table (
  draft_id uuid,
  user_id uuid,
  application_id uuid,
  gmail_thread_id text,
  sent_confirmed_at timestamptz,
  last_reply_checked_at timestamptz,
  company_name text,
  title text
)
language plpgsql
security definer
set search_path = public
stable
as $$
begin
  return query
    select
      d.id,
      d.user_id,
      r.application_id,
      d.gmail_thread_id,
      d.sent_confirmed_at,
      d.last_reply_checked_at,
      s.company_name,
      s.title
    from public.outreach_drafts d
    join public.contact_candidates c on c.id = d.candidate_id
    join public.contact_research_runs r on r.id = c.run_id
    join public.applications a on a.id = r.application_id
    join public.job_snapshots s on s.id = a.active_job_snapshot_id
    where d.gmail_thread_id is not null
      and (d.last_reply_checked_at is null or d.last_reply_checked_at < staleness_cutoff)
    order by d.last_reply_checked_at nulls first
    limit result_limit;
end;
$$;

-- Service-role-only, matching `search_new_job_registry_postings`'s own
-- precedent (P5c) -- there's no legitimate direct-client caller for this.
revoke execute on function public.list_drafts_due_for_reply_check(timestamptz, integer)
  from public, anon, authenticated;

-- The below-threshold proposal's own Today-item companion table. Unlike
-- `today_item_job_matches` (which duplicates SearchResult/ScoredJob
-- fields that have no other persisted home), `application_status_
-- proposals` already IS the canonical row -- this is a thin link, not a
-- data copy, so R4's Accept/Dismiss routes mutate the one real row
-- directly rather than two places drifting apart.
create table public.today_item_status_proposals (
  id uuid primary key default gen_random_uuid(),
  today_item_id uuid not null unique references public.today_items (id) on delete cascade,
  application_status_proposal_id uuid not null unique
    references public.application_status_proposals (id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.today_item_status_proposals enable row level security;

-- No RLS policies at all, matching `today_item_job_matches`' own
-- precedent -- the GET /today route reads via the service-role client
-- with a verified user_id filter (joined through today_items), same as
-- every other table in this codebase; there is no legitimate direct-
-- client path.

-- The atomic two-table insert (Job Finder P9b's `insert_high_fit_job_
-- today_item` is the direct precedent: one `security definer` function,
-- both inserts in the same implicit transaction, so a unique-violation
-- on either boundary rolls back both and propagates as a real Postgres
-- error `digest_listener.handle_batch`'s own existing `except APIError:
-- if e.code != _UNIQUE_VIOLATION: raise` already knows how to handle --
-- no new Python-side idempotency logic needed).
create function public.insert_status_proposal_today_item(
  p_user_id uuid,
  p_application_id uuid,
  p_headline text,
  p_detail text,
  p_source_outbox_event_id uuid,
  p_application_status_proposal_id uuid
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
    p_user_id, p_application_id, 'status_proposal', p_headline, p_detail, p_source_outbox_event_id
  )
  returning * into v_today_item;

  insert into public.today_item_status_proposals (
    today_item_id, application_status_proposal_id
  ) values (
    v_today_item.id, p_application_status_proposal_id
  );

  return v_today_item;
end;
$$;

revoke execute on function public.insert_status_proposal_today_item(
  uuid, uuid, text, text, uuid, uuid
) from public, anon, authenticated;
