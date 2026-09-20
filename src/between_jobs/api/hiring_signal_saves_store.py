"""Hiring Signals P3 -- persistence for saved posts (`hiring_signal_saves`).

A save is a POINTER: the numeric activity id of a post, the id-only address it
lives at, the application it was saved against, and when. Nothing else about
the post is ever stored -- no author, no title, no text -- so a saved post is
only ever shown by re-embedding it live in the user's browser; if the post is
gone, the embed is empty and the UI says so, instead of a cached stand-in.

**The server builds the address.** `post_url` is always
`https://www.linkedin.com/feed/update/urn:li:activity:<id>`, rebuilt here from
the digits. The `/posts/` urls a search index returns embed a person's name
and topic words in their slug (`.../posts/jane-doe_hiring-...-activity-...`),
so they are never accepted from a client and never stored -- only the id is
kept, and even the address is derived from it on every read rather than
trusted from the column. The id is validated as ASCII digits only (`[0-9]`,
not `str.isdigit`, which accepts other scripts' digits), without a leading zero
(`0007...` is the same number as `7...`: one post, one save), before it reaches
a url a browser will load. A row that is not a well-formed pointer -- the
table's row-level-security insert policy was written for a client that no
longer exists, and `add_hiring_signal_saves_constraints` closes it -- is left
out of a listing instead of failing the whole list.

**No duplicates, arbitrated by the database.** The same post cannot be saved
twice for one application by one user: a partial unique index (see the
`add_hiring_signal_saves_unique_indexes` migration) decides, not a
check-then-insert race. `create_save` looks first only as a fast path; when
two requests race, the loser's insert hits the unique violation and the
existing row is returned exactly as if it had been there all along.

Every function takes a verified `user_id` and filters on it -- this backend
uses the service-role client, so these WHERE clauses are the enforcement
boundary, not RLS.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any, cast

from postgrest.exceptions import APIError

from supabase import AsyncClient

from .hiring_signal_query import clean_display_text
from .hiring_signals import embed_url

_TABLE = "hiring_signal_saves"
_UNIQUE_VIOLATION = "23505"
_ACTIVITY_ID_RX = re.compile(r"[1-9][0-9]{0,24}")
MAX_QUERY_LABEL_CHARS = 200

POST_URL_TEMPLATE = "https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}"


class HiringSignalSaveNotFound(Exception):
    """A save id does not exist, or belongs to another user -- deliberately
    indistinguishable from the caller's side."""


def is_valid_activity_id(value: object) -> bool:
    return isinstance(value, str) and _ACTIVITY_ID_RX.fullmatch(value) is not None


def canonical_post_url(activity_id: str) -> str:
    """The id-only address of a post. Pure string building: nothing here (or
    anywhere in this feature) ever requests it."""
    if not is_valid_activity_id(activity_id):
        raise ValueError("activity_id must be 1-25 ASCII digits, without a leading zero")
    return POST_URL_TEMPLATE.format(activity_id=activity_id)


def clean_query_label(label: object) -> str | None:
    """A label as it is stored: control/format/separator characters removed,
    whitespace collapsed, at most `MAX_QUERY_LABEL_CHARS` characters; `None`
    for a missing or empty one."""
    return clean_display_text(label, MAX_QUERY_LABEL_CHARS) or None


def to_saved_post(row: dict[str, Any]) -> dict[str, Any]:
    """The API shape of a stored row. `post_url` and `embed_url` are rebuilt
    from the activity id, never read from the `url` column."""
    activity_id = cast(str, row["activity_id"])
    return {
        "id": row["id"],
        "activity_id": activity_id,
        "post_url": canonical_post_url(activity_id),
        "embed_url": embed_url(activity_id),
        "created_at": row["created_at"],
    }


async def _find(
    supabase: AsyncClient, user_id: str, application_id: str, activity_id: str
) -> dict[str, Any] | None:
    result = (
        await supabase.table(_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .eq("activity_id", activity_id)
        .limit(1)
        .execute()
    )
    return cast(dict[str, Any], result.data[0]) if result.data else None


async def create_save(
    supabase: AsyncClient,
    user_id: str,
    application_id: str,
    *,
    activity_id: str,
    query_label: object = None,
) -> tuple[dict[str, Any], bool]:
    """Saves a post against an application. Returns `(saved_post, created)`:
    `created` is `False` when the post was already saved (a repeat request, or
    the losing side of a race) and the existing row is returned unchanged --
    its `discovered_via_query` is not overwritten."""
    url = canonical_post_url(activity_id)  # also validates the id
    existing = await _find(supabase, user_id, application_id, activity_id)
    if existing is not None:
        return to_saved_post(existing), False
    try:
        result = (
            await supabase.table(_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "application_id": application_id,
                    "url": url,
                    "activity_id": activity_id,
                    "discovered_via_query": clean_query_label(query_label),
                }
            )
            .execute()
        )
    except APIError as e:
        if e.code != _UNIQUE_VIOLATION:
            raise
        existing = await _find(supabase, user_id, application_id, activity_id)
        if existing is None:  # deleted between the violation and the re-read
            raise
        return to_saved_post(existing), False
    return to_saved_post(cast(dict[str, Any], result.data[0])), True


async def list_saves(
    supabase: AsyncClient, user_id: str, application_id: str
) -> list[dict[str, Any]]:
    """This user's saves for one application, newest first."""
    result = (
        await supabase.table(_TABLE)
        .select("*")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .order("created_at", desc=True)
        .execute()
    )
    posts: list[dict[str, Any]] = []
    for row in result.data:
        try:
            posts.append(to_saved_post(cast(dict[str, Any], row)))
        except (KeyError, ValueError):
            continue  # not a well-formed pointer: never listed, never a 500
    return posts


async def saved_activity_ids(
    supabase: AsyncClient,
    user_id: str,
    application_id: str,
    activity_ids: Collection[str],
) -> set[str]:
    """Which of `activity_ids` this user already saved for this application
    (one query) -- what marks a search result `saved`."""
    if not activity_ids:
        return set()
    result = (
        await supabase.table(_TABLE)
        .select("activity_id")
        .eq("user_id", user_id)
        .eq("application_id", application_id)
        .in_("activity_id", sorted(activity_ids))
        .execute()
    )
    return {cast(dict[str, Any], row)["activity_id"] for row in result.data}


async def delete_save(supabase: AsyncClient, user_id: str, save_id: str) -> None:
    result = (
        await supabase.table(_TABLE).delete().eq("id", save_id).eq("user_id", user_id).execute()
    )
    if not result.data:
        raise HiringSignalSaveNotFound(save_id)
