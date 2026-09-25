-- Hiring Signals P3 -- make the database enforce what the API promises about
-- a saved post: it is a POINTER (a numeric post id and the address derived from
-- it) and nothing else.
--
-- The API layer already guarantees that: `hiring_signal_saves_store` validates
-- the id, rebuilds the address itself and never accepts a client-supplied url,
-- author, title or text. But the P1 migration also gave `authenticated`
-- clients a row-level-security INSERT policy that checks only
-- `auth.uid() = user_id`, with no constraint on any column. Anyone holding the
-- project's public anon key and their own session could therefore write a row
-- straight through PostgREST with any url, any text, and an `activity_id` that
-- is not a number -- and the API's list of that user's saves would then fail
-- for every request (a malformed row cannot be turned into an address).
--
-- Nothing in the web app or the browser extension writes this table directly:
-- every save goes through `POST /applications/{id}/hiring-signals/saves`, which
-- uses the service-role key (it bypasses row-level security). So the client
-- INSERT policy has no legitimate user, and it is dropped. The CHECK
-- constraints below are the second lock (they also bind the service role, so a
-- bug in the API could not store a url or free text here either).
--
-- Verified before writing this: `hiring_signal_saves` held zero rows in the real
-- project, so no existing row can violate a new constraint.

alter table public.hiring_signal_saves
  add constraint hiring_signal_saves_activity_id_shape
    check (activity_id ~ '^[1-9][0-9]{0,24}$'),
  add constraint hiring_signal_saves_url_is_the_canonical_address
    check (url = 'https://www.linkedin.com/feed/update/urn:li:activity:' || activity_id),
  add constraint hiring_signal_saves_label_length
    check (discovered_via_query is null or char_length(discovered_via_query) <= 200);

drop policy hiring_signal_saves_insert_own on public.hiring_signal_saves;
