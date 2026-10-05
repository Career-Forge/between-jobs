-- Deleting a user cascades through every table that references them, but Supabase Auth also keeps
-- an audit log (auth.audit_log_entries) whose payload holds the user's id and email: logins,
-- logouts, and the "user deleted" entry that deleting the user writes itself. Nothing cascades
-- into it, so an account deleted at the user's request would still be named, with its email, in
-- that log.
--
-- Only the postgres role may delete from that table, so this is SECURITY DEFINER, callable only
-- by the service role (the API calls it right after it deletes the user, with the email it read
-- beforehand: the entry written by the deletion itself carries it). It matches the log's own
-- keys exactly and never by a substring, so another user's entries are never touched.
--
-- The payload predicates cannot use an index (and an index on a table Supabase Auth owns is not
-- ours to add), so this reads the whole log: milliseconds for the log of a young service, tens of
-- seconds at around ten million entries, hence the generous timeout. If it ever fails the API logs
-- it, and sending the deletion request again repeats it by id.

create or replace function public.purge_user_auth_traces(p_user_id uuid, p_email text)
returns integer
language plpgsql
security definer
set search_path = ''
set statement_timeout = '120s'
as $$
declare
  v_deleted integer;
begin
  delete from auth.audit_log_entries
  where payload ->> 'actor_id' = p_user_id::text
     or payload -> 'traits' ->> 'user_id' = p_user_id::text
     or (
       coalesce(p_email, '') <> ''
       and (
         payload ->> 'actor_username' = p_email
         or payload -> 'traits' ->> 'user_email' = p_email
       )
     );
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

revoke execute on function public.purge_user_auth_traces(uuid, text)
  from public, anon, authenticated;
grant execute on function public.purge_user_auth_traces(uuid, text) to service_role;
