-- Bound what one user can store in saved_searches, and index what the matcher reads.
--
-- A saved search is the only thing the background matcher ever scores against: every
-- active row is read on every tick, and each one can trigger a full-text query and an LLM
-- scoring call on its owner's key. Nothing limited how many a user could have or how large
-- each could be (the columns were unbounded text and text[]), so one signed-in user could
-- store hundreds of rows of hundreds of kilobytes each and make the matcher, which runs
-- inside the API process, load all of it on every tick and every restart. The API now
-- bounds the request and refuses a row past the cap with a 409; this migration makes the
-- database hold the same lines, so a bug or a write that skips the API cannot store a row
-- in any other shape, and so two requests arriving together cannot both take the last slot.
--
-- 1. SIZE. A query is at most 200 characters and a location at most 100 (the same limits as a
--    standalone hiring-signal search), and the company filter at most 20 names of at most
--    100 characters each. The array check bounds the total (20 x 100 characters across all
--    names) rather than each name: a CHECK cannot look inside an array without a subquery,
--    and the total is what costs memory.
-- 2. COUNT. At most 20 per user, enforced by a BEFORE INSERT trigger. The count is only
--    correct if two concurrent inserts for one user cannot both read "19", so the trigger
--    takes a transaction-scoped advisory lock keyed on the user first: the second insert waits
--    for the first to commit, then counts again and sees it. Only an INSERT is checked:
--    merging a Telegram-only account into a web account (merge_user_data) re-parents rows
--    with an UPDATE, and refusing that would lose a user's searches mid-merge. A merged user
--    may therefore sit above the cap, and cannot add another until they delete down to it.
--    The refusal is SQLSTATE BJ009, which the API turns into a 409 CONFLICT.
-- 3. THE MATCHER'S READ. It now reads a fixed number of the least recently matched active
--    searches per tick; this partial index serves exactly that ordering.
--
-- Rows that already break a limit are trimmed first, so the constraints can be added
-- validated instead of NOT VALID (which would leave an old oversized row failing every later
-- update to it, the matcher's watermark update included). Trimming only ever shortens a value
-- to the limit (left()) or drops names past the twentieth; no row is deleted. Nothing in the
-- product creates a search that long, so on a healthy database this changes no row.

update public.saved_searches
set query = left(query, 200)
where char_length(query) > 200;

update public.saved_searches
set location = left(location, 100)
where char_length(location) > 100;

update public.saved_searches s
set companies = coalesce(
  (
    select array_agg(left(c.name, 100) order by c.ord)
    from unnest(s.companies) with ordinality as c (name, ord)
    where c.ord <= 20
  ),
  '{}'
)
where cardinality(s.companies) > 20
  or char_length(array_to_string(s.companies, '')) > 2000;

alter table public.saved_searches
  add constraint saved_searches_query_length check (char_length(query) <= 200),
  add constraint saved_searches_location_length
    check (location is null or char_length(location) <= 100),
  add constraint saved_searches_companies_size
    check (
      cardinality(companies) <= 20
      and char_length(array_to_string(companies, '')) <= 2000
    );

create function public.enforce_saved_search_limit()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('bj-saved-search-cap:' || new.user_id::text, 0));
  if (select count(*) from public.saved_searches s where s.user_id = new.user_id) >= 20 then
    raise exception 'saved search limit reached' using errcode = 'BJ009';
  end if;
  return new;
end;
$$;

-- A trigger function is never called by a role (it returns `trigger`, so it is not an RPC
-- either) and Postgres checks EXECUTE on it only when the trigger is created. Prod's default
-- privileges grant a new function to anon, authenticated and service_role and a fresh stack
-- grants nothing; revoke it from all of them so both end in the same state.
revoke execute on function public.enforce_saved_search_limit()
  from public, anon, authenticated, service_role;

create trigger saved_searches_enforce_limit
  before insert on public.saved_searches
  for each row
  execute function public.enforce_saved_search_limit();

create index saved_searches_active_watermark_idx
  on public.saved_searches (last_matched_at)
  where is_active;
