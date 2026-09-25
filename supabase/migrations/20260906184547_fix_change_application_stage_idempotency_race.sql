-- Fixes a real, adversarially-confirmed TOCTOU race in `change_application_
-- stage` (Sprint 2.6d): the original body checked `application_events` for
-- an existing `idempotency_key` row BEFORE acquiring the `applications`
-- row's `for update` lock. Two concurrent calls carrying the SAME
-- idempotency_key (Gmail reply/status parsing R4's new "Accept" button
-- makes a human double-click a genuinely plausible trigger, though any
-- caller could race this) could both pass the "already ran?" check before
-- either had inserted its own `application_events` row; the second call
-- then blocked on the row lock, woke up once the first committed, and its
-- own INSERT hit the real `unique (user_id, idempotency_key)` constraint --
-- a raw, uncaught Postgres 23505 propagating out of `change_stage` as an
-- unenveloped 500 instead of the graceful idempotent no-op the whole
-- design exists to guarantee.
--
-- Fix: acquire the row lock FIRST, then re-check `application_events`. A
-- second concurrent call now blocks on the lock until the first
-- transaction commits, sees the first call's own idempotency row once it
-- re-checks after acquiring the lock, and correctly takes the "already
-- ran" branch instead of attempting a duplicate insert. This is a
-- genuinely SQL-level concurrency fix -- no fake/mock can reproduce real
-- Postgres row-locking, so its correctness rests on live verification
-- against the real database, not a unit test.
create or replace function public.change_application_stage(
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
  if p_new_status not in
    ('saved', 'applied', 'screening', 'interviewing', 'offer', 'rejected', 'withdrawn')
  then
    raise exception 'invalid application status: %', p_new_status
      using errcode = '22023'; -- invalid_parameter_value, distinct from the
                                -- plain P0001 the "not found" raises below
                                -- use, so callers can tell the two apart.
  end if;

  -- Lock FIRST, idempotency check SECOND -- a concurrent call with the
  -- same idempotency_key now fully serializes behind this one instead of
  -- racing it (see this migration's own header comment).
  select status into v_old_status
  from public.applications
  where id = p_application_id and user_id = p_user_id
  for update;

  if not found then
    raise exception 'application % not found for user %', p_application_id, p_user_id;
  end if;

  select id into v_already_ran
  from public.application_events
  where user_id = p_user_id and idempotency_key = p_idempotency_key;

  if v_already_ran is not null then
    select * into v_application
    from public.applications
    where id = p_application_id and user_id = p_user_id;

    return v_application;
  end if;

  update public.applications
  set
    status = p_new_status,
    date_applied = case
      when p_new_status = 'applied' and date_applied is null then now()
      else date_applied
    end
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
