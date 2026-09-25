-- Fix the first-call race in claim_extension_draft_answer_slot.
--
-- The version prod runs (20260921154631) looks the user's row up with
-- `select ... for update` and inserts it when nothing is found. A brand-new
-- user has no row to lock, so two concurrent first-ever calls both fall
-- through to the plain insert, and the loser hits a unique violation: a 500
-- for a call that was under the limit and should have gone through. This fix
-- was written into the original migration file after that migration had
-- already been applied, so prod never received it; it ships here as its own
-- migration instead.
--
-- Row creation is now arbitrated by `insert ... on conflict do nothing`
-- first, so exactly one concurrent caller creates the row. That caller's
-- slot is already claimed (request_count starts at 1), so it returns at
-- once. Every other caller -- a losing race, or an existing user on any
-- later call -- falls through to the row lock. If the row vanished in
-- between (the user was deleted and the cascade removed it), the call is let
-- through uncounted.
--
-- Checked on a local stack against real concurrent sessions: two first-ever
-- calls both succeed (count 2), and five first-ever calls against a limit of
-- three return exactly three true and two false, with no errors. The old
-- version fails both cases with duplicate-key errors.

create or replace function public.claim_extension_draft_answer_slot(
  p_user_id uuid,
  p_window_seconds integer,
  p_max_requests integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row record;
  v_inserted integer;
begin
  insert into public.extension_draft_answer_rate_limits (user_id, window_started_at, request_count)
  values (p_user_id, now(), 1)
  on conflict (user_id) do nothing;
  get diagnostics v_inserted = row_count;
  if v_inserted > 0 then
    return true;
  end if;

  select * into v_row from public.extension_draft_answer_rate_limits
  where user_id = p_user_id
  for update;

  if not found then
    return true;
  end if;

  if v_row.window_started_at <= now() - (p_window_seconds || ' seconds')::interval then
    update public.extension_draft_answer_rate_limits
    set window_started_at = now(), request_count = 1
    where user_id = p_user_id;
    return true;
  end if;

  if v_row.request_count >= p_max_requests then
    return false;
  end if;

  update public.extension_draft_answer_rate_limits
  set request_count = request_count + 1
  where user_id = p_user_id;
  return true;
end;
$$;

revoke execute on function public.claim_extension_draft_answer_slot(uuid, integer, integer)
  from public, anon, authenticated;
