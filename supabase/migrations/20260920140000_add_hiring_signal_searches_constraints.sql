-- Hiring Signals P4 -- make the database enforce what the API promises about a
-- saved standalone search: it is the user's own typed role and metro, already
-- cleaned, and the same search cannot be saved twice.
--
-- `hiring_signal_searches` (P1) is plain CRUD with nothing but its primary key,
-- so three things the API layer guarantees were only conventions:
--
--   1. SHAPE. `hiring_signal_searches_store` cleans what it stores (control,
--      format and separator characters removed, every whitespace run collapsed to
--      one space, at most 200 characters for the role and 100 for the place) and
--      refuses an empty role. The two CHECK constraints below make the database
--      hold the same line, so neither a bug in the API nor a direct write can
--      store a search in any other form. That includes the one that matters for
--      the next point: because a stored value is ALWAYS already trimmed and
--      whitespace-collapsed, comparing it case-insensitively is exactly "the same
--      words, ignoring case and spacing".
--   2. NO DUPLICATES. Nothing stopped the same search being saved twice, and a
--      check-then-insert in the API cannot arbitrate two racing requests. The
--      unique index is the arbiter: `create_search` handles its violation by
--      returning the row that won. `location` is nullable and Postgres treats
--      NULLs as distinct, so the key uses `coalesce(lower(location), '')` -- no
--      location is one value, and a NULL location and an empty one cannot both
--      exist (the CHECK forbids an empty location).
--   3. WHO WRITES. The P1 migration gave `authenticated` clients row-level-security
--      INSERT and UPDATE policies that check only `auth.uid() = user_id`, so
--      anyone holding the project's public anon key and their own session could
--      write a row straight through PostgREST in any shape, bypassing the API's
--      cleaning, its 25-search cap and its duplicate handling. Nothing in the web
--      app or the browser extension writes this table directly (every save goes
--      through `POST /hiring-signals/searches`, which uses the service-role key and
--      bypasses row-level security), and there is no update route at all (delete
--      and recreate), so both policies have no legitimate user and are dropped --
--      the same call `add_hiring_signal_saves_constraints` made for
--      `hiring_signal_saves`. SELECT and DELETE stay.
--
-- This table is deliberately NOT Job Finder's saved-search table and carries none
-- of its shape (no `is_active`, no watermark): nothing ever scans it in the
-- background, which is the entire reason it is its own table. Nothing here changes
-- that.
--
-- Verified before writing this, against the real project: `hiring_signal_searches`
-- held zero rows, so no existing row can violate a new constraint or the index.
--
-- The forbidden-character class is written with hex escapes only (Postgres reads
-- `\xhhhh` as one character however many digits follow), so this file holds no
-- invisible character. It is: the C0 and C1 controls (U+0001-U+001F and
-- U+007F-U+009F), the line and paragraph separators (U+2028, U+2029), and the
-- zero-width and bidirectional-control format characters (U+200B-U+200F,
-- U+202A-U+202E, U+2060-U+2064, U+2066-U+206F, U+FEFF). `clean_display_text`
-- removes the same set from what the API stores. Checked against the real
-- project, member by member and at each boundary (U+200A, U+2010, U+2065 and
-- U+2070 are outside it and pass).

alter table public.hiring_signal_searches
  add constraint hiring_signal_searches_query_shape
    check (
      char_length(query) between 1 and 200
      and query = btrim(regexp_replace(query, '\s+', ' ', 'g'))
      and query !~ '[\x01-\x1f\x7f-\x9f\x2028\x2029\x200b-\x200f\x202a-\x202e\x2060-\x2064\x2066-\x206f\xfeff]'
    ),
  add constraint hiring_signal_searches_location_shape
    check (
      location is null
      or (
        char_length(location) between 1 and 100
        and location = btrim(regexp_replace(location, '\s+', ' ', 'g'))
        and location !~ '[\x01-\x1f\x7f-\x9f\x2028\x2029\x200b-\x200f\x202a-\x202e\x2060-\x2064\x2066-\x206f\xfeff]'
      )
    );

create unique index hiring_signal_searches_user_query_location_key
  on public.hiring_signal_searches using btree (user_id, lower(query), coalesce(lower(location), ''));

drop policy hiring_signal_searches_insert_own on public.hiring_signal_searches;
drop policy hiring_signal_searches_update_own on public.hiring_signal_searches;
