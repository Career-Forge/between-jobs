-- Counts one use of a remembered answer in a single statement.
--
-- The extension reports each fill that used a remembered answer exactly as stored, and the
-- backend adds one to `times_used` and stamps `last_used_at`. Doing that as a read followed by a
-- write loses an increment when two reports for one answer arrive together (both read N, both
-- write N + 1), so the increment lives here, where one `update` takes the row lock and adds to
-- the value that is actually stored.
--
-- Returns true when the answer exists and belongs to p_user_id, false otherwise -- the same answer
-- for an id that is missing and an id that is someone else's, so a caller learns nothing about which
-- ids exist. The `updated_at` trigger fires on this update like any other, so an answer that keeps
-- being used is also the most recently updated one under its intent.
create or replace function public.record_approved_answer_use(
  p_user_id uuid,
  p_answer_id uuid
)
returns boolean
language sql
security definer
set search_path = ''
as $$
  with used as (
    update public.approved_answers
       set times_used = times_used + 1,
           last_used_at = now()
     where id = p_answer_id
       and user_id = p_user_id
    returning 1
  )
  select exists (select 1 from used);
$$;

revoke execute on function public.record_approved_answer_use(uuid, uuid)
  from public, anon, authenticated;
grant execute on function public.record_approved_answer_use(uuid, uuid)
  to service_role;
