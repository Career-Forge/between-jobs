-- Real enforced stage vocabulary + date_applied (Applications Kanban K1) --
-- applications-kanban.md D1/D2/D6.
--
-- D1: all 7 values already live in the frontend's own STATUS_OPTIONS stay,
-- as real enforced values -- no shrinking to a smaller set.
--
-- D2: enforcement is membership-only, not a gated state machine. Any-to-any
-- moves among the 7 values stay legal (no real precedent anywhere -- the
-- private books, careerforge-command-center, or n8n's own applications
-- table -- asks for ordered/gated transitions, and real hiring pipelines
-- aren't strictly monotonic: a rejected candidate can get reopened, an
-- interview can bounce back to applied if rescheduled).
--
-- The CHECK constraint here is the durable, last-resort guarantee against
-- ANY write path (including a future one that bypasses
-- change_application_stage entirely). change_application_stage itself gets
-- its OWN copy of the same check below -- not redundant: the web route
-- already validates via a Pydantic Literal before ever calling this
-- function, but the Telegram bridge's stage-change callback
-- (telegram_webhook.py's `app:stage:` handler) parses `new_status` straight
-- out of a Telegram callback_data string and passes it to
-- applications_store.change_stage untyped -- callback_data is technically
-- forgeable, so that path has no validation at all today without this.
alter table public.applications
  add column date_applied timestamptz;

alter table public.applications
  add constraint applications_status_check
  check (status in ('saved', 'applied', 'screening', 'interviewing', 'offer', 'rejected', 'withdrawn'));

-- D6: date_applied stamps once, the first time status becomes 'applied',
-- and is never overwritten by a later bounce (applied -> interviewing ->
-- applied does not reset it) -- "when did this first go out," not "most
-- recent."
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
