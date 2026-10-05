-- A claim must never return more rows than its limit.
--
-- claim_and_publish_outbox_batch took its batch with
--   update ... where id in (select id ... order by created_at limit n for update skip locked)
-- Postgres is free to plan that IN as a nested-loop semi join with the limited subquery on the
-- inner side, which re-runs the subquery for every outer row. Each re-run sees the rows the
-- update has already published disappear from "published_at is null", so it hands out the next
-- n, and the statement as a whole can update far more than n rows (40 for a limit of 20 was seen
-- on a local stack whose statistics had gone stale; a claim returning more than its limit also
-- lets one worker hold rows another could have taken). Which plan is chosen depends on the
-- table's statistics, so it came and went.
--
-- The batch is now selected in a MATERIALIZED common table expression, which Postgres evaluates
-- exactly once, and the update joins to that fixed set. Same signature, same return type, same
-- security definer and search_path as before.
--
-- Revert: create or replace the function with the body from
-- 20260811164121_create_outbox_claim_function.sql.

create or replace function public.claim_and_publish_outbox_batch(p_limit integer default 20)
returns setof public.event_outbox
language sql
security definer
set search_path = public
as $$
  with batch as materialized (
    select o.id
    from public.event_outbox o
    where o.published_at is null
    order by o.created_at
    limit p_limit
    for update skip locked
  )
  update public.event_outbox e
  set published_at = now(), attempt_count = e.attempt_count + 1
  from batch
  where e.id = batch.id
  returning e.*;
$$;

revoke execute on function public.claim_and_publish_outbox_batch(integer)
  from public, anon, authenticated;
grant execute on function public.claim_and_publish_outbox_batch(integer) to service_role;
