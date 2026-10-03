-- P0.9 -- the same Telegram update must not be processed twice.
--
-- Telegram redelivers an update it did not get a 2xx for, and a webhook that runs for a
-- minute (generating a resume) can make it give up waiting and send the same update again
-- while the first is still running. Each delivery then creates its own job, application and
-- (worse) paid generation. One row per update_id, claimed before processing:
--
--   claim     -> true:  this delivery owns the update, process it.
--                false: it is already running, or already done; answer 200 and stop.
--   complete  -> marks the update done; a later redelivery is dropped.
--   release   -> the handler failed or was cancelled; delete the claim so Telegram's retry
--                is processed instead of dropped (a 500 is how Telegram is asked to retry,
--                and the /link flow depends on that retry to finish a half-done link).
--
-- A claim that is neither completed nor released -- the process died -- expires after the
-- lease (default 10 minutes), after which a redelivery takes over. That is what keeps a
-- crash from silently swallowing the update.
--
-- The table is not readable or writable through the API by any role; the three functions
-- are SECURITY DEFINER and callable by the backend only. No personal data: an update_id is
-- a counter Telegram assigns.
--
-- Cleanup: every claim deletes up to 200 rows older than 7 days, so the table is bounded by
-- traffic and needs no worker (the only existing purge worker belongs to the hiring-signal
-- cache and can be switched off on its own). No traffic, no growth.

create table public.telegram_processed_updates (
  update_id    bigint primary key,
  claimed_at   timestamptz not null default now(),
  completed_at timestamptz
);

create index telegram_processed_updates_claimed_at_idx
  on public.telegram_processed_updates (claimed_at);

alter table public.telegram_processed_updates enable row level security;

-- Prod's default privileges grant new tables to anon, authenticated and service_role; a
-- fresh stack grants nothing. Revoke by name so both agree.
revoke all on table public.telegram_processed_updates
  from public, anon, authenticated, service_role;

create function public.claim_telegram_update(p_update_id bigint, p_lease_seconds integer default 600)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_rows integer;
begin
  if p_lease_seconds is null or p_lease_seconds < 1 or p_lease_seconds > 86400 then
    raise exception 'p_lease_seconds must be between 1 and 86400' using errcode = '22023';
  end if;

  delete from public.telegram_processed_updates
  where update_id in (
    select update_id from public.telegram_processed_updates
    where claimed_at < now() - interval '7 days'
    limit 200
  );

  -- One statement, so two deliveries of the same update arriving together are arbitrated
  -- by the primary-key index: the loser waits on the winner's row, re-evaluates the WHERE
  -- against the committed row (READ COMMITTED), and changes nothing.
  insert into public.telegram_processed_updates as t (update_id)
  values (p_update_id)
  on conflict (update_id) do update
    set claimed_at = now()
    where t.completed_at is null
      and t.claimed_at <= now() - make_interval(secs => p_lease_seconds);

  get diagnostics v_rows = row_count;
  return v_rows > 0;
end;
$$;

create function public.complete_telegram_update(p_update_id bigint)
returns void
language sql
security definer
set search_path = ''
as $$
  update public.telegram_processed_updates
  set completed_at = now()
  where update_id = p_update_id;
$$;

-- Only an unfinished claim can be released: a completed update stays recorded.
create function public.release_telegram_update(p_update_id bigint)
returns void
language sql
security definer
set search_path = ''
as $$
  delete from public.telegram_processed_updates
  where update_id = p_update_id and completed_at is null;
$$;

revoke execute on function public.claim_telegram_update(bigint, integer)
  from public, anon, authenticated;
grant execute on function public.claim_telegram_update(bigint, integer) to service_role;
revoke execute on function public.complete_telegram_update(bigint)
  from public, anon, authenticated;
grant execute on function public.complete_telegram_update(bigint) to service_role;
revoke execute on function public.release_telegram_update(bigint)
  from public, anon, authenticated;
grant execute on function public.release_telegram_update(bigint) to service_role;
