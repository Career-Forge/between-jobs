"""Finishing a /link once `consume_link_code` has committed the merge.

`consume_link_code` moves every database row a Telegram-only account owned
to the web account, in one transaction. What SQL can't do is move the files
those rows point at: they're storage objects under a "<user_id>/..." key,
and Supabase refuses direct deletes on storage.objects, so the move goes
through the Storage API from here. Only when nothing is left -- every
table, every `artifact_versions.storage_key`, every storage object, as
`user_owned_row_counts` counts them -- is the emptied source account
retired, by `finish_link_delete_source`, which repeats that count itself
under a row lock before it deletes anything. Whatever can't be finished is
left in place, intact, and reported: an emptied-but-not-quite account
costs nothing, a deleted one takes its unmoved rows with it.

Everything here is safe to run again. A link that crashes anywhere after
its commit (before the storage move, halfway through it, before the
delete) is resumed by the same Telegram account resending the same code
within a day, which lands back here.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from storage3.exceptions import StorageApiError

from supabase import AsyncClient

from .link_codes_store import (
    finish_link_delete_source,
    finish_link_merge,
    user_owned_row_counts,
)
from .telegram_identity import CHANNEL

logger = logging.getLogger(__name__)

_BUCKET = "artifacts"
"""The only bucket that exists. Objects in any other would still show up in
`user_owned_row_counts` and keep the source from being retired."""

_MAX_PASSES = 2
"""A second pass catches a row or an object written between the merge and the
first recount, by a request that resolved the source account just before the
link committed."""

_PAGE_SIZE = 100
_MAX_LIST_DEPTH = 5

_TIME_BUDGET_SECONDS = 20.0
"""How long finishing one /link may spend moving files before it gives up and
leaves the rest for the same code sent again -- the webhook is waiting on this
to reply, and Telegram redelivers an update it gets no answer to."""

_MIN_ORPHAN_AGE = timedelta(minutes=5)
"""An object with no `artifact_versions` row yet may be mid-`create_version`,
which uploads first and inserts the row after. Anything younger is left
where it is; it keeps the source from being retired until a later pass."""

_ORPHAN_DIR = "_merged_orphans"
"""Where a genuinely row-less object goes, under the target's prefix. Its own
directory, so it can never sit at a key the target's next artifact upload
would use."""


@dataclass(frozen=True)
class LinkCompletion:
    """`already_complete`: the source account was already gone when this ran
    (a resumed link that had finished long ago). `retired`: it's gone now.
    `leftover`: what it still owned when this gave up, empty once retired."""

    already_complete: bool
    retired: bool
    leftover: dict[str, int] = field(default_factory=dict)


class _StorageBlocked(Exception):
    """Moving an object automatically isn't safe -- a third account
    references it, or its destination already holds different content. The
    message is one of this module's own fixed strings, safe to log."""


def _parse_created_at(entry: dict[str, Any]) -> datetime:
    raw = entry.get("created_at")
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        # Unknown age counts as brand new: leave the object alone.
        return datetime.now(UTC)


async def _list_objects(bucket: Any, prefix: str, depth: int = 0) -> list[dict[str, Any]]:
    """Every file under `prefix`, recursively, each with its full `path`. The
    Storage API lists one level at a time, 100 entries a page, and returns a
    folder as an entry with no id."""
    if depth > _MAX_LIST_DEPTH:
        raise _StorageBlocked("the storage listing is nested too deeply")
    found: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = cast(
            list[dict[str, Any]],
            await bucket.list(prefix, {"limit": _PAGE_SIZE, "offset": offset}),
        )
        for entry in page:
            path = f"{prefix}/{entry['name']}"
            if entry.get("id") is None:
                found.extend(await _list_objects(bucket, path, depth + 1))
            else:
                found.append({**entry, "path": path})
        if len(page) < _PAGE_SIZE:
            return found
        offset += _PAGE_SIZE


async def _copy_or_verify(bucket: Any, source_path: str, destination: str) -> None:
    """Copies the object, or -- when the destination already exists, because
    an earlier pass copied it and died before finishing -- checks that it
    holds the same bytes."""
    try:
        await bucket.copy(source_path, destination)
    except StorageApiError as e:
        if str(e.status) != "409":
            raise
        if await bucket.download(source_path) != await bucket.download(destination):
            raise _StorageBlocked("the destination already holds different content") from e


async def _holds_same_bytes(bucket: Any, original: str, other: str) -> bool:
    """Whether `other` exists and holds exactly the bytes of `original`. A
    missing object is a plain False; any other storage failure propagates."""
    try:
        candidate = await bucket.download(other)
    except StorageApiError as e:
        if str(e.status) == "404":
            return False
        raise
    return bool(candidate == await bucket.download(original))


async def _rows_naming(supabase: AsyncClient, key: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        (
            await supabase.table("artifact_versions")
            .select("id,user_id")
            .eq("storage_key", key)
            .execute()
        ).data,
    )


async def _move_object(
    supabase: AsyncClient,
    bucket: Any,
    *,
    source: str,
    target: str,
    entry: dict[str, Any],
    now: datetime,
) -> bool:
    """Moves one object under the source's prefix to the target's, repointing
    the `artifact_versions` rows that name it. False when it was left alone:
    too new to tell whether it's about to get a row, or a row appeared for it
    while it was being copied (the next pass moves it the ordinary way)."""
    path = cast(str, entry["path"])
    rest = path.removeprefix(f"{source}/")
    onward = f"{target}/{rest}"
    rows = await _rows_naming(supabase, path)
    if any(row["user_id"] not in (source, target) for row in rows):
        raise _StorageBlocked("an artifact version of a third account names this object")

    orphan = False
    if rows:
        destination = onward
    else:
        moved_rows = await _rows_naming(supabase, onward)
        if any(row["user_id"] not in (source, target) for row in moved_rows):
            raise _StorageBlocked("an artifact version of a third account names this object")
        if moved_rows and await _holds_same_bytes(bucket, path, onward):
            # An earlier pass already copied the object and repointed the row,
            # then died before it removed the original: this is what's left
            # of that move, not a row-less object, so it must not be filed
            # away as one. Bytes have to match -- a row at the target's key
            # can equally be the target's own newer upload, which a genuine
            # orphan at the same slot must not be mistaken for a copy of.
            destination = onward
        elif now - _parse_created_at(entry) < _MIN_ORPHAN_AGE:
            return False
        else:
            destination = f"{target}/{_ORPHAN_DIR}/{source}/{rest}"
            orphan = True

    await _copy_or_verify(bucket, path, destination)

    if rows:
        await (
            supabase.table("artifact_versions")
            .update({"storage_key": destination})
            .eq("storage_key", path)
            .in_("user_id", [source, target])
            .execute()
        )
    # Whatever the object was, a row for the old key may have been written
    # since it was read above (create_version uploading, then inserting), or
    # belong to neither account. Removing the object now would leave that row
    # pointing at nothing.
    if await _rows_naming(supabase, path):
        if orphan:
            await bucket.remove([destination])
            return False
        raise _StorageBlocked("an artifact version still names the old key")

    await bucket.remove([path])
    return True


async def _rewrite_dangling_keys(
    supabase: AsyncClient, bucket: Any, *, source: str, target: str
) -> None:
    """An `artifact_versions` row whose object no longer exists (removed by
    hand, or never finished uploading) would keep naming the source's prefix
    forever and block retiring the account. It already points at nothing, so
    rewriting the prefix only lets the account go."""
    rows = cast(
        list[dict[str, Any]],
        (
            await supabase.table("artifact_versions")
            .select("id,user_id,storage_key")
            .like("storage_key", f"{source}/%")
            .execute()
        ).data,
    )
    if not rows:
        return
    existing = {entry["path"] for entry in await _list_objects(bucket, source)}
    for row in rows:
        key = cast(str, row["storage_key"])
        if key in existing or row["user_id"] not in (source, target):
            continue
        await (
            supabase.table("artifact_versions")
            .update({"storage_key": f"{target}/{key.removeprefix(f'{source}/')}"})
            .eq("id", row["id"])
            .execute()
        )


async def _move_storage(
    supabase: AsyncClient, *, source: str, target: str, deadline: float
) -> bool:
    """One pass over everything under the source's storage prefix. False when
    something blocked it -- the caller then leaves the source in place. Stops
    quietly at `deadline` (a `time.monotonic()` value); what's left is
    counted by the caller's recount and finished by the same code sent again."""
    bucket = supabase.storage.from_(_BUCKET)
    now = datetime.now(UTC)
    ids = {"source_user_id": source, "target_user_id": target}
    moved = left_alone = failed = 0
    timed_out = False
    for entry in await _list_objects(bucket, source):
        if time.monotonic() >= deadline:
            timed_out = True
            break
        try:
            if await _move_object(
                supabase, bucket, source=source, target=target, entry=entry, now=now
            ):
                moved += 1
            else:
                left_alone += 1
        except _StorageBlocked as e:
            logger.error(
                "a linked account's file can't be moved",
                extra={"ctx": {**ids, "reason": str(e)}},
            )
            return False
        except Exception:
            # One object's failure (a network error, a storage 5xx) must not
            # strand the rest; the recount after this pass sees what's left.
            logger.warning(
                "moving a linked account's file failed", extra={"ctx": ids}, exc_info=True
            )
            failed += 1
    if not timed_out:
        await _rewrite_dangling_keys(supabase, bucket, source=source, target=target)
    logger.info(
        "moved a linked account's files",
        extra={
            "ctx": {
                **ids,
                "moved": moved,
                "left_alone": left_alone,
                "failed": failed,
                "timed_out": timed_out,
            }
        },
    )
    return True


def _describe(counts: dict[str, int]) -> str:
    return ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))


async def finish_link(
    supabase: AsyncClient,
    *,
    source_user_id: str,
    target_user_id: str,
    subject: str,
    budget_seconds: float = _TIME_BUDGET_SECONDS,
) -> LinkCompletion:
    """Moves what the merge left (storage) and retires the source account
    once it owns nothing. Gives up moving files after `budget_seconds`, which
    reads as not retired -- the same code sent again carries on. Raises on an
    unexpected failure (an RPC or storage error the passes below don't
    contain); the caller logs it and carries on."""
    deadline = time.monotonic() + budget_seconds
    ids = {"source_user_id": source_user_id, "target_user_id": target_user_id}
    leftover: dict[str, int] = {}
    for attempt in range(_MAX_PASSES):
        merged = await finish_link_merge(
            supabase,
            channel=CHANNEL,
            subject=subject,
            source_user_id=source_user_id,
            target_user_id=target_user_id,
        )
        if merged.get("source_gone"):
            return LinkCompletion(already_complete=attempt == 0, retired=True)
        blocked = not await _move_storage(
            supabase, source=source_user_id, target=target_user_id, deadline=deadline
        )
        leftover = await user_owned_row_counts(supabase, source_user_id)
        if blocked or not leftover or time.monotonic() >= deadline:
            break

    if leftover:
        logger.error(
            "a linked account still owns rows after finishing its link",
            extra={"ctx": {**ids, "leftover": _describe(leftover)}},
        )
        return LinkCompletion(already_complete=False, retired=False, leftover=leftover)

    deleted = await finish_link_delete_source(
        supabase,
        channel=CHANNEL,
        subject=subject,
        source_user_id=source_user_id,
        target_user_id=target_user_id,
    )
    if deleted.get("source_gone"):
        return LinkCompletion(already_complete=False, retired=True)
    leftover = cast(dict[str, int], deleted.get("counts") or {})
    logger.error(
        "a linked account owned rows again by the time it was to be retired",
        extra={"ctx": {**ids, "leftover": _describe(leftover)}},
    )
    return LinkCompletion(already_complete=False, retired=False, leftover=leftover)
