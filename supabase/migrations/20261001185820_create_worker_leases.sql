-- One worker instance, enforced (launch plan P2.19, v1).
--
-- Railway's deploys overlap the old and new container, and an accidental second
-- replica is possible, so two copies of the API can run their background workers
-- at once. For most workers that is harmless: the outbox claims rows with FOR
-- UPDATE SKIP LOCKED behind idempotent consumers, and the cache purge converges.
-- Three have no claim at all -- the job registry poller, the saved-search matcher
-- and the Gmail reply checker -- and two of them spend the user's own LLM credit
-- per tick. Those three hold a lease: one row per worker, owned by whichever
-- process most recently claimed it and has kept renewing it.
--
-- Ownership is decided here, by the database clock, never by an app's. The
-- backend reaches this only through PostgREST's pooled connections, so a
-- session-level advisory lock would stick to whichever pooled connection took it
-- rather than to the process; a row with an expiry is the thing that works.
--
-- Table: not readable or writable through the API by any role. The one function
-- below is SECURITY DEFINER, so the backend needs no table privileges at all.
-- Operator view and break-glass, as the owner role:
--   select * from public.worker_leases;
--   update public.worker_leases set expires_at = now() where worker = '<name>';

create table public.worker_leases (
  worker     text primary key check (worker <> ''),
  holder     text not null check (holder <> ''),
  expires_at timestamptz not null
);

alter table public.worker_leases enable row level security;

-- Prod's default privileges grant new tables to anon, authenticated and
-- service_role; a fresh stack grants nothing. Revoke by name so both agree.
revoke all on table public.worker_leases from public, anon, authenticated, service_role;

-- Takes the lease for p_worker if it is free (no row, or the row has expired) or
-- already held by p_holder, extending it to now() + p_ttl_seconds; returns
-- whether p_holder holds it afterwards. A refusal is `false`, never an error:
-- the caller tells "another process holds it" apart from "I could not ask".
--
-- One statement, so concurrent first claims are arbitrated by the primary-key
-- index: the loser waits on the winner's uncommitted row, then re-evaluates the
-- WHERE against the committed one (READ COMMITTED) and changes nothing.
create or replace function public.claim_worker_lease(
  p_worker text, p_holder text, p_ttl_seconds integer
) returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_rows integer;
begin
  -- An absurd TTL would let one claim hold a lease for as long as it likes.
  if p_ttl_seconds is null or p_ttl_seconds < 1 or p_ttl_seconds > 3600 then
    raise exception 'p_ttl_seconds must be between 1 and 3600' using errcode = '22023';
  end if;

  insert into public.worker_leases as l (worker, holder, expires_at)
  values (p_worker, p_holder, now() + (p_ttl_seconds || ' seconds')::interval)
  on conflict (worker) do update
    set holder = excluded.holder, expires_at = excluded.expires_at
    where l.holder = excluded.holder or l.expires_at <= now();

  get diagnostics v_rows = row_count;
  return v_rows > 0;
end;
$$;

revoke execute on function public.claim_worker_lease(text, text, integer)
  from public, anon, authenticated;
grant execute on function public.claim_worker_lease(text, text, integer) to service_role;
