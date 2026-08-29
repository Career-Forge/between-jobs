-- Fixes a real vulnerability found live via the security advisor right
-- after the previous migration: Postgres/PostgREST auto-grants EXECUTE
-- on newly created public-schema functions to `anon` and `authenticated`.
-- `change_application_stage` is SECURITY DEFINER and trusts its
-- `p_user_id` argument rather than deriving it from the caller's JWT, so
-- as shipped, ANY signed-in user could call
-- `/rest/v1/rpc/change_application_stage` with someone else's user id and
-- silently rewrite their application's status -- a real IDOR / privilege
-- escalation, not a theoretical one.
--
-- This backend only ever calls this function via the service-role
-- client, which already bypasses RLS entirely and doesn't need PostgREST
-- RPC access at all -- it connects directly. So the fix is a straight
-- revoke, not a rewrite to derive p_user_id from auth.uid(): no client
-- role should be able to reach this function through PostgREST, ever.

revoke execute on function public.change_application_stage(
  uuid, uuid, text, text, text, text
) from anon, authenticated;
