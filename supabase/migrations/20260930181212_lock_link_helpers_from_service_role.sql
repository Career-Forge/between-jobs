-- The account-merge migration (20260928000000) meant two of its helpers to be
-- internal: only functions the postgres role owns call them, so nothing that
-- reaches the database through the API -- the backend's service_role included
-- -- has any reason to. It revoked them from public, anon and authenticated
-- and left service_role to the project's default privileges, which differ:
-- prod grants EXECUTE on new functions to service_role, a fresh local stack
-- or branch doesn't. The result was the same function callable through the
-- API on prod and not anywhere else.
--
-- A separate migration rather than an edit, because 20260928000000 has
-- already been applied. Revoking what isn't granted is a no-op, so this is
-- safe wherever it runs; it changes nothing for the functions that call
-- these (they run as their owner).

revoke execute on function public.assert_unreferenced(regclass, uuid, regclass[])
  from service_role;
revoke execute on function public.is_auto_provisioned_telegram_user(uuid, text)
  from service_role;
