-- Transactional outbox (Sprint 2.6d) -- Proposal.md §20, DDL verbatim
-- with one deliberate addition: `user_id` gets a real FK to auth.users
-- with ON DELETE CASCADE, matching every other per-user table in this
-- project (§20's own DDL omits it). Same kind of small, documented
-- departure as profile_versions' `activated_at` addition in Sprint 2.5a.
--
-- No RLS policies on this table at all (RLS is still enabled, so the
-- default is deny-everything) -- unlike jobs/job_snapshots or
-- applications, there is no legitimate client-facing read or write path
-- for the outbox. Only the service-role backend (which bypasses RLS
-- entirely) ever touches it.
--
-- Proposal §20's own example shows a status change and its outbox event
-- committing in one transaction:
--   async with db.transaction():
--       application = await application_repo.change_stage(...)
--       await outbox_repo.append(...)
-- This backend only has Postgrest (HTTP) available, not a raw connection
-- to open a multi-statement transaction from Python. The equivalent here
-- is a Postgres function called via `.rpc(...)` -- the whole function
-- body runs as one transaction, which is exactly the guarantee this
-- section requires. `change_application_stage` below is that function:
-- it updates `applications.status`, inserts the paired
-- `application_events` row, and inserts the `event_outbox` row together,
-- or none of them (any error rolls back the lot). It's also the
-- idempotency boundary applications_store.create_application's own
-- docstring flagged as still open -- a retried call with the same
-- idempotency_key returns the current row without writing a second
-- event or outbox row.

create table public.event_outbox (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  aggregate_type text not null,
  aggregate_id uuid not null,
  event_type text not null,
  event_version integer not null,
  payload jsonb not null,
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  attempt_count integer not null default 0,
  last_error text
);

-- Powers the worker's poll: unpublished rows, oldest first.
create index event_outbox_unpublished_idx
  on public.event_outbox using btree (created_at)
  where published_at is null;

alter table public.event_outbox enable row level security;

create function public.change_application_stage(
  p_user_id uuid,
  p_application_id uuid,
  p_new_status text,
  p_idempotency_key text,
  p_actor_type text,
  p_actor_id text
)
returns public.applications
language plpgsql
security definer
set search_path = public
as $$
declare
  v_application public.applications;
  v_old_status text;
  v_already_ran uuid;
begin
  select id into v_already_ran
  from public.application_events
  where user_id = p_user_id and idempotency_key = p_idempotency_key;

  if v_already_ran is not null then
    select * into v_application
    from public.applications
    where id = p_application_id and user_id = p_user_id;

    if not found then
      raise exception 'application % not found for user %', p_application_id, p_user_id;
    end if;

    return v_application;
  end if;

  select status into v_old_status
  from public.applications
  where id = p_application_id and user_id = p_user_id
  for update;

  if not found then
    raise exception 'application % not found for user %', p_application_id, p_user_id;
  end if;

  update public.applications
  set status = p_new_status
  where id = p_application_id and user_id = p_user_id
  returning * into v_application;

  insert into public.application_events (
    application_id, user_id, event_type, payload, actor_type, actor_id, idempotency_key
  ) values (
    p_application_id, p_user_id, 'application.stage_changed',
    jsonb_build_object('old_stage', v_old_status, 'new_stage', p_new_status),
    p_actor_type, p_actor_id, p_idempotency_key
  );

  insert into public.event_outbox (
    user_id, aggregate_type, aggregate_id, event_type, event_version, payload, idempotency_key
  ) values (
    p_user_id, 'application', p_application_id, 'application.stage_changed.v1', 1,
    jsonb_build_object(
      'application_id', p_application_id,
      'old_stage', v_old_status,
      'new_stage', p_new_status
    ),
    'outbox:' || p_idempotency_key
  );

  return v_application;
end;
$$;
