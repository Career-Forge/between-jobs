-- Outbox worker support (Sprint 2.6d) -- Proposal §20: "Outbox workers use
-- FOR UPDATE SKIP LOCKED, publish idempotently, and mark rows only after
-- delivery."
--
-- A single Postgrest RPC call runs as one transaction, and any row lock
-- taken inside it is released the instant that transaction commits --
-- there is no way to hold a `FOR UPDATE SKIP LOCKED` lock across two
-- separate calls (claim, then a later mark-published) without a raw
-- connection this backend doesn't have. So claim and publish happen as
-- one atomic statement instead: an UPDATE ... FROM (SELECT ... FOR UPDATE
-- SKIP LOCKED) RETURNING *, which is genuinely safe for multiple
-- concurrent worker instances polling at once. There are no subscribers
-- yet (Sprint 2.6d's whole point is proving the bus exists before they
-- do), so "publish" here just means "mark it published" -- there's
-- nothing downstream to fail mid-delivery and roll this claim back for.
--
-- SECURITY DEFINER + a hard revoke from public/anon/authenticated from
-- the start this time -- Sprint 2.6d's own change_application_stage
-- shipped without the revoke first and had to be patched live after the
-- security advisor caught it (see the two migrations right before this
-- one). This function is even more sensitive than that one: it has no
-- user_id filter at all -- it processes the entire cross-user outbox --
-- so it must never be reachable through PostgREST by any client role.

create function public.claim_and_publish_outbox_batch(p_limit integer default 20)
returns setof public.event_outbox
language sql
security definer
set search_path = public
as $$
  update public.event_outbox
  set published_at = now(), attempt_count = attempt_count + 1
  where id in (
    select id from public.event_outbox
    where published_at is null
    order by created_at
    limit p_limit
    for update skip locked
  )
  returning *;
$$;

revoke execute on function public.claim_and_publish_outbox_batch(integer)
  from public, anon, authenticated;
