-- E6 continuation -- per-user rate limiting for POST /extension/draft-answer,
-- the one extension route that spends real BYOK LLM credit per call. See
-- extension_rate_limit.py's own module docstring for the threshold
-- reasoning; the actual window/limit VALUES live in Python (passed as
-- parameters below), so there is exactly one place that owns them -- this
-- function is generic bookkeeping, not policy.
--
-- A fixed-window counter behind a `for update` row lock. SECURITY DEFINER +
-- revoked from public/anon/authenticated immediately, same precedent as
-- every other function in this migration history.
--
-- This file is the SQL prod actually ran for this version. The first-call
-- race it leaves open (two concurrent first-ever calls both find no row, and
-- the loser's plain insert hits a unique violation: a 500 for a call that was
-- under the limit)
-- is fixed in 20260925102732_fix_extension_draft_answer_slot_first_call_race.sql.
--
-- No RLS-facing policy (RLS enabled, deny-everything) -- same reasoning as
-- extension_sign_outs: read and written entirely by this function, called
-- only from extension_routes.draft_answer via the service-role client.

create table public.extension_draft_answer_rate_limits (
  user_id uuid primary key references auth.users (id) on delete cascade,
  window_started_at timestamptz not null,
  request_count integer not null default 0
);

alter table public.extension_draft_answer_rate_limits enable row level security;

create function public.claim_extension_draft_answer_slot(
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
begin
  select * into v_row from public.extension_draft_answer_rate_limits
  where user_id = p_user_id
  for update;

  if not found then
    insert into public.extension_draft_answer_rate_limits (user_id, window_started_at, request_count)
    values (p_user_id, now(), 1);
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
