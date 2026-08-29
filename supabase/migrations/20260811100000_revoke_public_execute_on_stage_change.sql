-- Follow-up to the previous migration -- that revoke targeted `anon` and
-- `authenticated` explicitly, but Postgres also grants EXECUTE to the
-- PUBLIC pseudo-role by default on function creation, and every role
-- (including anon/authenticated) implicitly inherits from PUBLIC.
-- Confirmed live via information_schema.role_routine_grants: after the
-- prior migration, `change_application_stage` still showed EXECUTE
-- granted to PUBLIC, so anon/authenticated could still reach it as
-- PUBLIC members even though they were no longer named directly. This
-- revoke closes that.

revoke execute on function public.change_application_stage(
  uuid, uuid, text, text, text, text
) from public;
