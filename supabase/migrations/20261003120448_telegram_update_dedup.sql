-- P0.9 -- the same Telegram update must not be processed twice.
--
-- Telegram redelivers an update it did not get a 2xx for, and a webhook that runs for a
-- minute (generating a resume) can make it give up waiting and send the same update again
-- while the first is still running. Each delivery then creates its own job, application and
-- (worse) paid generation. One row per update_id, claimed before processing.
--
-- claim_telegram_update answers one of three things, because the webhook must answer Telegram
-- differently for each:
--   'claimed'     this delivery owns the update: process it.
--   'done'        an earlier delivery finished it: answer 200 and stop; Telegram stops too.
--   'in_progress' an earlier delivery claimed it and has not finished, and its lease has not
--                 run out: answer NON-2xx. Never 200: if that earlier delivery's process
--                 died (a deploy, an OOM kill) nothing will ever finish the update, and a 200
--                 would tell Telegram to stop redelivering it -- losing the update, and with
--                 it the /link resume that depends on a redelivery. A non-2xx makes Telegram
--                 keep trying until the first delivery completes ('done') or its lease runs
--                 out and a redelivery takes over ('claimed').
-- complete_telegram_update marks the update done; release_telegram_update deletes an
-- unfinished claim when the handler failed or was cancelled, so the retry a 500 asks for is
-- processed at once instead of waiting out the lease.
--
-- The lease is chosen by the caller: long enough that a live handler is never taken over
-- (the slowest, a resume generation, is bounded at about 4-5 minutes by its own timeouts),
-- short enough that a crashed one is picked up while Telegram is still retrying. complete and
-- release do not carry an owner token: they could only misfire if a handler outlived its
-- lease, which the callers' lease sizes exclude.
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
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_rows integer;
  v_done boolean;
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
  if v_rows > 0 then
    return 'claimed';
  end if;

  select completed_at is not null into v_done
  from public.telegram_processed_updates
  where update_id = p_update_id;
  if v_done then
    return 'done';
  end if;
  -- An unfinished claim inside its lease -- or the row was released between the two
  -- statements, in which case Telegram's next retry claims it. Either way: not done.
  return 'in_progress';
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
