-- E6 continuation -- per-user rate limiting for POST /extension/draft-answer,
-- the one extension route that spends real BYOK LLM credit per call. See
-- extension_rate_limit.py's own module docstring for the threshold
-- reasoning; the actual window/limit VALUES live in Python (passed as
-- parameters below), so there is exactly one place that owns them -- this
-- function is generic bookkeeping, not policy.
--
-- A fixed-window counter, atomically claimed via an `insert ... on conflict`
-- (for the brand-new-user case, where there is nothing yet to lock) followed
-- by a `for update` row lock (for every call after that) -- the same two-part
-- shape create_merge_and_consume_link_code.sql's own consume_link_code
-- already established for link_code_attempts' rate limiting, applied here
-- for the first time to a genuinely per-request (not per-failure) limit. Two
-- concurrent draft-answer calls from the same user, first-ever call or not,
-- can never both read a stale under-limit count and both be let through, and
-- can never race each other into a duplicate-key error either -- the
-- ON CONFLICT arbitrates row creation, the row lock serializes everything
-- after it. SECURITY DEFINER + revoked from public/anon/authenticated
-- immediately, same precedent as every other function in this migration
-- history.
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
  v_inserted integer;
begin
  -- A brand-new user's row can't be arbitrated by `select ... for update`
  -- below -- there is nothing yet to lock, so two concurrent first-ever
  -- calls both fall through to the old plain `insert`, and the loser hits
  -- a unique-violation instead of a clean 429. Arbitrate it the same way
  -- consume_link_code's own insert into link_code_attempts already does:
  -- `insert ... on conflict do nothing` first, so exactly one concurrent
  -- caller creates the row; that caller's own slot is already claimed
  -- (request_count starts at 1), so it returns immediately without
  -- touching the row again. Every other caller (a losing race, or simply
  -- an existing user on any later call) falls through to the row lock
  -- below, which is safe once the row is guaranteed to exist.
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
