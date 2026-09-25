-- Hiring Signals P3 -- the same post can be saved only once.
--
-- `hiring_signal_saves` (P1) has no uniqueness at all, so two requests racing
-- to save the same post (a double click, a retry after a timeout, two tabs)
-- would each insert a row. The application layer looks first, but only the
-- database can arbitrate a race: these two PARTIAL unique indexes are the
-- arbiter, and `hiring_signal_saves_store.create_save` handles the unique
-- violation by returning the row that won. Two indexes, not one, because
-- `application_id` is nullable and Postgres treats NULLs as distinct in a
-- plain unique index -- a save with no application (the standalone tab, P4)
-- would otherwise never conflict with itself.
--
--   with an application:  one row per (user, application, post)
--   without one (P4):     one row per (user, post)
--
-- Verified before writing this: `hiring_signal_saves` held zero rows in the
-- real project, so there were no duplicates to resolve first.
create unique index hiring_signal_saves_user_app_activity_key
  on public.hiring_signal_saves using btree (user_id, application_id, activity_id)
  where application_id is not null;

create unique index hiring_signal_saves_user_activity_no_app_key
  on public.hiring_signal_saves using btree (user_id, activity_id)
  where application_id is null;

-- `hiring_signal_query_cache` (P1) is purged of expired rows a bounded batch
-- at a time, oldest first, on every cache write (no scheduler): that query is
-- `created_at < cutoff order by created_at limit N`, which this index turns
-- from a scan of the whole table into a walk from its oldest end. The table
-- holds at most about a day of rows, so this is cheap insurance, not a hot
-- path.
create index hiring_signal_query_cache_created_at_idx
  on public.hiring_signal_query_cache using btree (created_at);
