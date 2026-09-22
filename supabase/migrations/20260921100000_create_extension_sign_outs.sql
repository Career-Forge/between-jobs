-- E6 continuation -- the scoped "server-side revocation" the original spec
-- asked for, built exactly as narrow as Pranav's go-ahead: a liveness check
-- for extension routes ONLY (extension_auth.require_active_extension_user_id),
-- never a rewrite of require_user_id or the app's general auth path. The web
-- app and every other route keep working exactly as today; this table is
-- read by nothing else in the app.
--
-- One row per user, `signed_out_at` -- the simplest correct shape for
-- "reject anything issued before the last sign-out." No per-token
-- blocklist: a Supabase Auth access token already carries its own `iat`
-- (issued-at) claim, so comparing that single watermark against it is
-- enough, with no token ids to parse or store.
--
-- No RLS-facing policy at all (RLS enabled, deny-everything by default) --
-- read entirely by extension_auth.py's own dependency and written entirely
-- by `POST /extension/sign-out`, both through `app.state.supabase` (the
-- service-role client, which bypasses RLS). Same precedent as
-- `link_code_attempts`/`event_outbox`: nothing here has a legitimate
-- client-facing read or write path through PostgREST directly.

create table public.extension_sign_outs (
  user_id uuid primary key references auth.users (id) on delete cascade,
  signed_out_at timestamptz not null
);

alter table public.extension_sign_outs enable row level security;
