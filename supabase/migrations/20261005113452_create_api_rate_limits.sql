-- Per-user rate limits for the routes that spend money or heavy compute.
--
-- One fixed-window counter per (user, bucket). A bucket is a name the API gives to
-- a kind of expensive action ("prepare", "discover", ...); which buckets exist, and
-- how many requests per window each allows, is policy and lives in Python
-- (api/rate_limits.py), passed in as parameters below -- this function is generic
-- bookkeeping, so there is exactly one place that owns the numbers. The older
-- claim_extension_draft_answer_slot is the same shape for one hard-wired action and
-- stays as it is; this one replaces nothing.
--
-- Every attempt that has passed authentication is counted, including one that goes
-- on to fail: the point is to bound how often an expensive path can be started, not
-- to bill only the ones that succeed.
--
-- The check is atomic in the database, not read-then-write in Python, so two
-- concurrent requests from one user never both read a stale under-limit count. The
-- shape is the one that fixed the first-call race in the extension limiter:
-- `insert ... on conflict do nothing` arbitrates who creates the row (that caller's
-- slot is already claimed, so it returns at once), and everyone else, or any later
-- call, goes through the row lock.
--
-- The table is read and written only by the function below, called by the backend
-- as service_role: RLS is on with no policy at all, so a user token reads and writes
-- nothing. The row goes with the user (on delete cascade), which also keeps it out
-- of the account-deletion residue check.

create table public.api_rate_limits (
  user_id uuid not null references auth.users (id) on delete cascade,
  bucket text not null check (bucket <> '' and char_length(bucket) <= 100),
  window_started_at timestamptz not null,
  request_count integer not null check (request_count >= 0),
  primary key (user_id, bucket)
);

alter table public.api_rate_limits enable row level security;

-- Prod's default privileges grant new tables to anon, authenticated and
-- service_role (every privilege, TRUNCATE and REFERENCES included); a fresh stack
-- grants nothing. Revoke everything from all four, then grant back exactly what the
-- backend uses, so both end in the same state.
revoke all on table public.api_rate_limits from public, anon, authenticated, service_role;
grant select, insert, update, delete on table public.api_rate_limits to service_role;

-- Counts one request for (p_user_id, p_bucket) in the current window, unless the
-- bucket is already at p_max_requests. A refusal is a row with allowed = false, never
-- an error, so a caller can tell "over the limit" from "the limiter could not be
-- asked". retry_after_seconds is 0 when allowed; when refused, the whole seconds
-- (rounded up, never less than 1, never more than the window) until the window ends.
create or replace function public.claim_rate_limit_slot(
  p_user_id uuid,
  p_bucket text,
  p_window_seconds integer,
  p_max_requests integer
)
returns table (allowed boolean, retry_after_seconds integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row record;
  v_inserted integer;
  v_window interval;
  v_remaining numeric;
begin
  if p_user_id is null then
    raise exception 'p_user_id must not be null' using errcode = '22023';
  end if;
  if p_bucket is null or btrim(p_bucket) = '' or char_length(p_bucket) > 100 then
    raise exception 'p_bucket must be a non-empty name of at most 100 characters'
      using errcode = '22023';
  end if;
  if p_window_seconds is null or p_window_seconds < 1 then
    raise exception 'p_window_seconds must be a positive number of seconds' using errcode = '22023';
  end if;
  if p_max_requests is null or p_max_requests < 1 then
    raise exception 'p_max_requests must be at least 1' using errcode = '22023';
  end if;

  v_window := make_interval(secs => p_window_seconds);

  insert into public.api_rate_limits (user_id, bucket, window_started_at, request_count)
  values (p_user_id, p_bucket, now(), 1)
  on conflict (user_id, bucket) do nothing;
  get diagnostics v_inserted = row_count;
  if v_inserted > 0 then
    return query select true, 0;
    return;
  end if;

  select * into v_row from public.api_rate_limits r
  where r.user_id = p_user_id and r.bucket = p_bucket
  for update;

  if not found then
    -- The row vanished between the insert and the lock (the user was deleted and
    -- the cascade removed it): let the call through uncounted.
    return query select true, 0;
    return;
  end if;

  if v_row.window_started_at <= now() - v_window then
    update public.api_rate_limits r
    set window_started_at = now(), request_count = 1
    where r.user_id = p_user_id and r.bucket = p_bucket;
    return query select true, 0;
    return;
  end if;

  if v_row.request_count >= p_max_requests then
    v_remaining := extract(epoch from (v_row.window_started_at + v_window - now()));
    return query select false, least(p_window_seconds, greatest(1, ceil(v_remaining)::integer));
    return;
  end if;

  update public.api_rate_limits r
  set request_count = r.request_count + 1
  where r.user_id = p_user_id and r.bucket = p_bucket;
  return query select true, 0;
end;
$$;

revoke execute on function public.claim_rate_limit_slot(uuid, text, integer, integer)
  from public, anon, authenticated;
grant execute on function public.claim_rate_limit_slot(uuid, text, integer, integer)
  to service_role;
