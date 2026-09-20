"""Hiring Signals P4 -- persistence for the standalone tab's saved searches
(`hiring_signal_searches`).

A saved search is what the user TYPED and nothing else: a role and, optionally,
a metro. It does not run, schedule or watch anything -- there is no column that
would let a background job pick it up, on purpose -- and re-running it is the
user clicking, which sends the same two strings back to
`POST /hiring-signals/search`. That is why this feature has its own table rather
than a row in Job Finder's saved-search table: that table is scanned on a
schedule by a matcher that scores every row against job postings with a real
model call, and a hiring-signal row parked there would be scored against
listings it was never meant to match and could push a bogus notification to a
real user. Nothing in this feature reads, writes or names that table, and the
matcher never names this one; `tests/test_hiring_signal_isolation.py` enforces
both directions.

**Normalized once, stored normalized.** The role and the location are cleaned
(`hiring_signal_tab.normalize_query_text` / `normalize_location_text`: control
and format characters removed, whitespace collapsed to single spaces, at most
200 / 100 characters) and the cleaned text is what is stored, so what the list
shows is exactly what a re-run sends. Text that could not be searched (no letter
or digit) is refused up front: every saved search is one a click can run. The
`add_hiring_signal_searches_constraints` migration makes the database enforce
the same shape, so neither a bug here nor a direct write could store a search
in another form.

**"The same search" is decided by the database.** Two searches are the same when
their role and location are equal ignoring case (whitespace is already
collapsed): a unique index over `(user_id, lower(query), coalesce(lower(location),
''))` is the arbiter, not a check-then-insert race. `create_search` looks first
only as a fast path; when two requests race, the loser's insert hits the unique
violation and the winner's row is returned exactly as if it had been there all
along (the API answers 200 instead of 201). The look is done in Python over the
user's own rows -- there are never more than `MAX_SAVED_SEARCHES` of them --
rather than with a `LIKE`, because a `LIKE` would read a typed `%`, `_` or `*` as
a wildcard and answer "already saved" with somebody else's search. The Python key
lower-cases character by character (`_fold`), the way Postgres's `lower()` does,
so a pair of searches the index treats as equal is a pair this treats as equal.

**The cap.** At most `MAX_SAVED_SEARCHES` per user; the next is refused
(`HiringSignalSearchLimitReached`, which the route answers as `CONFLICT`, with a
message the UI shows), never silently dropped and never evicting an older one.
Counting and inserting are two statements, so two racing creates at 24 could both
pass the count. The insert is therefore VERIFIED: after it, the user's rows are
counted again, and a create that finds more than the cap deletes its own row and
is refused. Every create that saw the full set refuses itself, so once the
requests have finished there are never more than `MAX_SAVED_SEARCHES` rows --
the price is that creates that truly race can ALL be refused although the user is
under the cap (a randomized-interleaving simulation over the in-memory fakes, not
committed: 30 racing creates from 20 saved usually left all 30 refused and the table
at 20 rows). That is the safe direction and vanishingly rare for one person
clicking Save, but the refusal must not lie about it: the two refusals are two
exceptions. `HiringSignalSearchLimitReached` is the pre-count -- the user really has
`MAX_SAVED_SEARCHES` -- and says "delete one"; `HiringSignalSearchSaveRaced` (a
subclass, so a caller that only knows the first still refuses correctly) is the
verification finding a rival, and says "try again", because trying again is exactly
what works. (An advisory lock, or one function that counts and inserts in a single
statement, would remove the race; the feature's migrations deliberately create no
function -- see `test_hiring_signal_isolation.py` -- so it is disclosed instead.)

Every function takes a verified `user_id` and filters on it -- this backend uses
the service-role client, so these WHERE clauses are the enforcement boundary, not
RLS.
"""

from __future__ import annotations

from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .hiring_signal_tab import normalize_location_text, normalize_query_text

_TABLE = "hiring_signal_searches"
_UNIQUE_VIOLATION = "23505"
MAX_SAVED_SEARCHES = 25
_READ_BOUND = MAX_SAVED_SEARCHES * 4
"""A user never has more than the cap (plus a transient race overshoot), so
this only bounds a read that should not need bounding."""


class HiringSignalSearchNotFound(Exception):
    """A saved-search id does not exist, or belongs to another user --
    deliberately indistinguishable from the caller's side."""


class HiringSignalSearchLimitReached(Exception):
    """The user already has `MAX_SAVED_SEARCHES` saved searches."""


class HiringSignalSearchSaveRaced(HiringSignalSearchLimitReached):
    """A save of this user's was in progress at the same time and the two together
    would have passed the cap, so this one was taken back. The user may well be under
    the cap; asking again is the right response."""


def _fold(text: str) -> str:
    """Lower-case, one character at a time. Postgres's `lower()` maps each
    character on its own, while `str.lower()` also applies a context rule (a
    capital sigma at the end of a word becomes a different letter than one in the
    middle), which would make two searches the index calls equal look different
    here. Checked against this project's own Postgres, whose `lower()` agrees with
    the per-character form: a capital sigma is the same small sigma wherever it
    sits, and a capital I with a dot above becomes `i` plus a combining dot, as it
    does here."""
    return "".join(ch.lower() for ch in text)


def _key(query: str, location: str | None) -> tuple[str, str]:
    """The unique index's key, computed the way the index computes it."""
    return _fold(query), _fold(location) if location is not None else ""


def to_saved_search(row: dict[str, Any]) -> dict[str, Any]:
    """The API shape of a stored row. Raises `KeyError`/`TypeError` for a row that
    is not a well-formed saved search (the caller leaves it out of a list)."""
    query, location = row["query"], row.get("location")
    if not isinstance(query, str) or not (location is None or isinstance(location, str)):
        raise TypeError("not a saved search")
    return {
        "id": row["id"],
        "query": query,
        "location": location,
        "created_at": row["created_at"],
    }


async def _rows(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    result = (
        await supabase.table(_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(_READ_BOUND)
        .execute()
    )
    return [cast(dict[str, Any], row) for row in result.data]


def _same_search(
    rows: list[dict[str, Any]], query: str, location: str | None
) -> dict[str, Any] | None:
    wanted = _key(query, location)
    for row in rows:
        stored_location = row.get("location")
        if (
            isinstance(row.get("query"), str)
            and _key(row["query"], stored_location if isinstance(stored_location, str) else None)
            == wanted
        ):
            return row
    return None


async def create_search(
    supabase: AsyncClient, user_id: str, *, query: object, location: object = None
) -> tuple[dict[str, Any], bool]:
    """Saves a search. Returns `(saved_search, created)`: `created` is `False`
    when an equivalent search was already saved (a repeat request, a different
    capitalization, or the losing side of a race) and the existing row is
    returned unchanged. Raises `hiring_signal_tab.InvalidSearchInput` for text
    that cannot be searched, `HiringSignalSearchLimitReached` at the cap and
    `HiringSignalSearchSaveRaced` when a concurrent save took the last slot."""
    query_text = normalize_query_text(query)
    location_text = normalize_location_text(location)

    rows = await _rows(supabase, user_id)
    existing = _same_search(rows, query_text, location_text)
    if existing is not None:
        return to_saved_search(existing), False
    if len(rows) >= MAX_SAVED_SEARCHES:
        raise HiringSignalSearchLimitReached

    try:
        result = (
            await supabase.table(_TABLE)
            .insert({"user_id": user_id, "query": query_text, "location": location_text})
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise
        existing = _same_search(await _rows(supabase, user_id), query_text, location_text)
        if existing is None:  # deleted between the violation and the re-read
            raise
        return to_saved_search(existing), False

    created = cast(dict[str, Any], result.data[0])
    if len(await _rows(supabase, user_id)) > MAX_SAVED_SEARCHES:
        # lost a race for the last slot (see the module docstring, "The cap")
        await (
            supabase.table(_TABLE).delete().eq("id", created["id"]).eq("user_id", user_id).execute()
        )
        raise HiringSignalSearchSaveRaced
    return to_saved_search(created), True


async def list_searches(supabase: AsyncClient, user_id: str) -> list[dict[str, Any]]:
    """This user's saved searches, newest first."""
    searches: list[dict[str, Any]] = []
    for row in await _rows(supabase, user_id):
        try:
            searches.append(to_saved_search(row))
        except (KeyError, TypeError):
            continue  # not a well-formed saved search: never listed, never a 500
    return searches


async def delete_search(supabase: AsyncClient, user_id: str, search_id: str) -> None:
    result = (
        await supabase.table(_TABLE).delete().eq("id", search_id).eq("user_id", user_id).execute()
    )
    if not result.data:
        raise HiringSignalSearchNotFound(search_id)
