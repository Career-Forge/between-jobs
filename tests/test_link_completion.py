"""Tests for finishing a /link after the merge commits (link_completion.py):
the storage move, the recount, and retiring the emptied source account.

The fake below is a small in-memory stand-in for the three things `finish_link`
touches -- the Storage API, the `artifact_versions` table and the three RPCs --
stateful enough that "moved" means the object is really gone from the source
prefix, and `user_owned_row_counts` computes its answer from what's left."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from storage3.exceptions import StorageApiError

from between_jobs.api.link_completion import LinkCompletion, finish_link

_SOURCE = "00000000-0000-0000-0000-00000000000a"
_TARGET = "00000000-0000-0000-0000-00000000000b"
_OTHER = "00000000-0000-0000-0000-00000000000c"
_SUBJECT = "987654321"

_OLD = datetime.now(UTC) - timedelta(hours=1)


class _Bucket:
    def __init__(self, objects: dict[str, tuple[bytes, datetime]]) -> None:
        self.objects = objects
        self.copy_errors: dict[str, Exception] = {}
        self.list_calls = 0
        self.after_copy: Callable[[], None] | None = None
        """Runs once, right after the first copy -- a stand-in for another
        request writing to the database while this one is mid-move."""

    async def list(self, prefix: str, options: dict[str, Any]) -> list[dict[str, Any]]:
        self.list_calls += 1
        seen: dict[str, dict[str, Any]] = {}
        for path, (_, created_at) in sorted(self.objects.items()):
            if not path.startswith(f"{prefix}/"):
                continue
            head, _, tail = path.removeprefix(f"{prefix}/").partition("/")
            if tail:
                seen.setdefault(head, {"name": head, "id": None})
            else:
                seen[head] = {
                    "name": head,
                    "id": f"id-{path}",
                    "created_at": created_at.isoformat(),
                }
        entries = [seen[name] for name in sorted(seen)]
        offset = options.get("offset", 0)
        return entries[offset : offset + options.get("limit", 100)]

    async def copy(self, from_path: str, to_path: str) -> dict[str, str]:
        if from_path in self.copy_errors:
            raise self.copy_errors[from_path]
        if to_path in self.objects:
            raise StorageApiError("The resource already exists", "Duplicate", 409)
        self.objects[to_path] = (self.objects[from_path][0], datetime.now(UTC))
        if self.after_copy is not None:
            hook, self.after_copy = self.after_copy, None
            hook()
        return {"path": to_path}

    async def download(self, path: str) -> bytes:
        if path not in self.objects:
            raise StorageApiError("Object not found", "not_found", 404)
        return self.objects[path][0]

    async def remove(self, paths: Sequence[str]) -> Sequence[dict[str, Any]]:
        removed = [{"name": p} for p in paths if p in self.objects]
        for p in paths:
            self.objects.pop(p, None)
        return removed


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self._filters: list[Any] = []
        self._update: dict[str, Any] | None = None

    def select(self, columns: str) -> _Query:
        return self

    def update(self, data: dict[str, Any]) -> _Query:
        self._update = data
        return self

    def eq(self, column: str, value: Any) -> _Query:
        self._filters.append(lambda r: r[column] == value)
        return self

    def in_(self, column: str, values: list[Any]) -> _Query:
        self._filters.append(lambda r: r[column] in values)
        return self

    def like(self, column: str, pattern: str) -> _Query:
        prefix = pattern.removesuffix("%")
        self._filters.append(lambda r: r[column].startswith(prefix))
        return self

    async def execute(self) -> SimpleNamespace:
        matching = [r for r in self._rows if all(f(r) for f in self._filters)]
        if self._update is not None:
            for row in matching:
                row.update(self._update)
        return SimpleNamespace(data=[dict(r) for r in matching])


class _Rpc:
    def __init__(self, result: Any) -> None:
        self._result = result

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._result)


class _FakeSupabase:
    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        rows: list[dict[str, Any]] | None = None,
        young: set[str] | None = None,
    ) -> None:
        stamped = {
            path: (data, datetime.now(UTC) if path in (young or set()) else _OLD)
            for path, data in (objects or {}).items()
        }
        self.bucket = _Bucket(stamped)
        self.rows = rows or []
        self.storage = SimpleNamespace(from_=lambda name: self.bucket)
        self.source_gone = False
        self.merge_calls = 0
        self.deleted = False
        # per recount: extra counts on top of what the files and rows imply
        self.extra_counts: list[dict[str, int]] = []
        self.delete_counts: dict[str, int] | None = None
        self.rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table(self, name: str) -> _Query:
        assert name == "artifact_versions"
        return _Query(self.rows)

    def _counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        objects = sum(1 for p in self.bucket.objects if p.startswith(f"{_SOURCE}/"))
        keys = sum(1 for r in self.rows if r["storage_key"].startswith(f"{_SOURCE}/"))
        if objects:
            counts["storage.objects"] = objects
        if keys:
            counts["artifact_versions.storage_key"] = keys
        return counts

    def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
        self.rpc_calls.append((name, params))
        if name == "finish_link_merge":
            self.merge_calls += 1
            return _Rpc({"ok": True, "source_gone": self.source_gone, "summary": {}})
        if name == "user_owned_row_counts":
            extra = self.extra_counts.pop(0) if self.extra_counts else {}
            return _Rpc({**self._counts(), **extra})
        if name == "finish_link_delete_source":
            counts = self.delete_counts if self.delete_counts is not None else self._counts()
            if counts:
                return _Rpc({"ok": True, "source_gone": False, "counts": counts})
            self.deleted = True
            return _Rpc({"ok": True, "source_gone": True})
        raise AssertionError(f"unexpected rpc {name}")


def _row(key: str, user_id: str = _TARGET, row_id: str | None = None) -> dict[str, Any]:
    return {"id": row_id or f"row-{key}", "user_id": user_id, "storage_key": key}


async def _finish(client: _FakeSupabase, **kwargs: Any) -> LinkCompletion:
    return await finish_link(
        client,  # type: ignore[arg-type]
        source_user_id=_SOURCE,
        target_user_id=_TARGET,
        subject=_SUBJECT,
        **kwargs,
    )


async def test_files_move_to_the_target_and_the_emptied_source_is_retired() -> None:
    key_a, key_b = f"{_SOURCE}/art-1/1", f"{_SOURCE}/art-1/2"
    client = _FakeSupabase(
        {key_a: b"one", key_b: b"two"}, rows=[_row(key_a, row_id="a"), _row(key_b, row_id="b")]
    )

    completion = await _finish(client)

    assert completion == LinkCompletion(already_complete=False, retired=True)
    assert client.bucket.objects.keys() == {f"{_TARGET}/art-1/1", f"{_TARGET}/art-1/2"}
    assert client.bucket.objects[f"{_TARGET}/art-1/1"][0] == b"one"
    assert sorted(r["storage_key"] for r in client.rows) == [
        f"{_TARGET}/art-1/1",
        f"{_TARGET}/art-1/2",
    ]
    assert client.deleted


@pytest.mark.parametrize(
    ("kwargs", "channel"),
    [({"channel": "discord"}, "discord"), ({"channel": "telegram"}, "telegram"), ({}, "telegram")],
    ids=["discord", "telegram", "default is telegram"],
)
async def test_the_channel_the_link_was_made_on_reaches_both_database_functions(
    kwargs: dict[str, str], channel: str
) -> None:
    """The functions find the link by its channel and check the source account against it: sent
    with the wrong one, a Discord link would never finish and the source would never retire."""
    client = _FakeSupabase()

    completion = await _finish(client, **kwargs)

    assert completion.retired
    finishing = [
        params
        for name, params in client.rpc_calls
        if name in ("finish_link_merge", "finish_link_delete_source")
    ]
    assert [name for name, _ in client.rpc_calls if name.startswith("finish_")] == [
        "finish_link_merge",
        "finish_link_delete_source",
    ]
    assert len(finishing) == 2
    for params in finishing:
        assert params["p_channel"] == channel
        assert params["p_subject"] == _SUBJECT


async def test_nothing_to_move_still_retires_the_source() -> None:
    client = _FakeSupabase()

    completion = await _finish(client)

    assert completion.retired
    assert client.deleted
    assert client.merge_calls == 1


async def test_an_object_with_no_row_goes_to_the_orphans_directory() -> None:
    key = f"{_SOURCE}/art-9/1"
    client = _FakeSupabase({key: b"orphan"})

    completion = await _finish(client)

    assert completion.retired
    assert client.bucket.objects.keys() == {f"{_TARGET}/_merged_orphans/{_SOURCE}/art-9/1"}


async def test_an_object_too_new_to_have_a_row_yet_is_left_alone() -> None:
    """create_version uploads first and inserts its row after; anything younger
    than five minutes may be in that gap."""
    key = f"{_SOURCE}/art-9/1"
    client = _FakeSupabase({key: b"just uploaded"}, young={key})

    completion = await _finish(client)

    assert not completion.retired
    assert completion.leftover == {"storage.objects": 1}
    assert client.bucket.objects.keys() == {key}
    assert not client.deleted


async def test_the_second_pass_picks_up_what_the_first_one_missed() -> None:
    """A row written between the merge and the first recount (by a request that
    resolved the source just before the link committed)."""
    client = _FakeSupabase()
    client.extra_counts = [{"public.saved_searches": 1}]

    completion = await _finish(client)

    assert client.merge_calls == 2
    assert completion.retired


async def test_rows_still_left_after_the_last_pass_keep_the_source_alive() -> None:
    client = _FakeSupabase()
    client.extra_counts = [{"public.saved_searches": 1}, {"public.saved_searches": 1}]

    completion = await _finish(client)

    assert client.merge_calls == 2
    assert completion == LinkCompletion(
        already_complete=False, retired=False, leftover={"public.saved_searches": 1}
    )
    assert not client.deleted


async def test_a_source_that_is_already_gone_is_reported_as_already_complete() -> None:
    client = _FakeSupabase({f"{_SOURCE}/art/1": b"x"})
    client.source_gone = True

    completion = await _finish(client)

    assert completion == LinkCompletion(already_complete=True, retired=True)
    assert f"{_SOURCE}/art/1" in client.bucket.objects  # nothing touched
    assert client.bucket.list_calls == 0


async def test_a_copy_that_already_landed_with_the_same_bytes_completes() -> None:
    """A pass that died between copying and removing: the retry finds the
    destination already there, checks it matches, and finishes the job."""
    key = f"{_SOURCE}/art-1/1"
    client = _FakeSupabase({key: b"same"}, rows=[_row(key)])
    client.bucket.objects[f"{_TARGET}/art-1/1"] = (b"same", _OLD)

    completion = await _finish(client)

    assert completion.retired
    assert client.bucket.objects.keys() == {f"{_TARGET}/art-1/1"}
    assert client.rows[0]["storage_key"] == f"{_TARGET}/art-1/1"


async def test_a_destination_holding_different_bytes_blocks_the_move() -> None:
    key = f"{_SOURCE}/art-1/1"
    client = _FakeSupabase({key: b"mine"}, rows=[_row(key)])
    client.bucket.objects[f"{_TARGET}/art-1/1"] = (b"someone else's", _OLD)

    completion = await _finish(client)

    assert not completion.retired
    assert key in client.bucket.objects
    assert client.bucket.objects[f"{_TARGET}/art-1/1"][0] == b"someone else's"
    assert client.rows[0]["storage_key"] == key
    assert not client.deleted


async def test_an_object_named_by_a_third_accounts_row_is_never_moved() -> None:
    key = f"{_SOURCE}/art-1/1"
    client = _FakeSupabase({key: b"x"}, rows=[_row(key, user_id=_OTHER)])

    completion = await _finish(client)

    assert not completion.retired
    assert key in client.bucket.objects
    assert client.rows[0]["storage_key"] == key


async def test_one_failing_object_does_not_strand_the_others() -> None:
    bad, good = f"{_SOURCE}/art-1/1", f"{_SOURCE}/art-1/2"
    client = _FakeSupabase({bad: b"a", good: b"b"}, rows=[_row(bad), _row(good)])
    client.bucket.copy_errors[bad] = StorageApiError("boom", "InternalError", 500)

    completion = await _finish(client)

    assert f"{_TARGET}/art-1/2" in client.bucket.objects
    assert bad in client.bucket.objects
    assert not completion.retired
    assert completion.leftover == {
        "storage.objects": 1,
        "artifact_versions.storage_key": 1,
    }


async def test_a_row_whose_object_no_longer_exists_stops_blocking_the_source() -> None:
    """The row already points at nothing; rewriting its prefix only lets the
    emptied account go."""
    dangling = f"{_SOURCE}/art-1/1"
    client = _FakeSupabase(rows=[_row(dangling)])

    completion = await _finish(client)

    assert completion.retired
    assert client.rows[0]["storage_key"] == f"{_TARGET}/art-1/1"


async def test_more_files_than_one_listing_page_and_nested_folders_all_move() -> None:
    objects = {f"{_SOURCE}/art-1/{n}": f"v{n}".encode() for n in range(150)}
    objects[f"{_SOURCE}/art-2/1"] = b"other"
    rows = [_row(path, row_id=f"r{i}") for i, path in enumerate(objects)]
    client = _FakeSupabase(objects, rows=rows)

    completion = await _finish(client)

    assert completion.retired
    assert len(client.bucket.objects) == 151
    assert all(p.startswith(f"{_TARGET}/") for p in client.bucket.objects)
    assert all(r["storage_key"].startswith(f"{_TARGET}/") for r in client.rows)


async def test_the_delete_function_finding_rows_again_keeps_the_source() -> None:
    """finish_link_delete_source repeats the count under a lock; if something
    landed since ours, it declines and says what."""
    client = _FakeSupabase()
    client.delete_counts = {"public.today_items": 1}

    completion = await _finish(client)

    assert completion == LinkCompletion(
        already_complete=False, retired=False, leftover={"public.today_items": 1}
    )


async def test_rpc_errors_propagate_to_the_caller() -> None:
    """The webhook contains them (and logs); finish_link doesn't swallow what
    it can't handle."""

    class Exploding(_FakeSupabase):
        def rpc(self, name: str, params: dict[str, Any]) -> _Rpc:
            raise RuntimeError("database down")

    with pytest.raises(RuntimeError):
        await _finish(Exploding())


async def test_the_original_of_a_move_already_repointed_is_finished_not_filed_away() -> None:
    """A pass repointed the row and copied the object, then died before it
    removed the original. What's left at the old key has no row naming it any
    more -- but it isn't an orphan, and must not be copied a second time into
    the orphans directory."""
    old, new = f"{_SOURCE}/art-1/1", f"{_TARGET}/art-1/1"
    client = _FakeSupabase({old: b"same"}, rows=[_row(new)])
    client.bucket.objects[new] = (b"same", _OLD)

    completion = await _finish(client)

    assert completion.retired
    assert client.bucket.objects.keys() == {new}
    assert client.rows[0]["storage_key"] == new


async def test_a_row_written_while_an_object_is_copied_aside_is_not_stranded() -> None:
    """create_version uploads, then inserts its row. An object that looked
    row-less when it was read can get its row while it's being copied to the
    orphans directory; removing it then would strand that row."""
    key = f"{_SOURCE}/art-9/1"
    client = _FakeSupabase({key: b"late row"})
    client.bucket.after_copy = lambda: client.rows.append(_row(key, user_id=_SOURCE))

    completion = await _finish(client)

    assert completion.retired
    assert client.bucket.objects.keys() == {f"{_TARGET}/art-9/1"}  # no orphan copy left behind
    assert [r["storage_key"] for r in client.rows] == [f"{_TARGET}/art-9/1"]


async def test_moving_files_stops_at_the_time_budget_and_leaves_the_rest_for_a_resend() -> None:
    key = f"{_SOURCE}/art-1/1"
    client = _FakeSupabase({key: b"x"}, rows=[_row(key)])

    completion = await _finish(client, budget_seconds=0)

    assert not completion.retired
    assert key in client.bucket.objects
    assert completion.leftover == {"storage.objects": 1, "artifact_versions.storage_key": 1}
    assert client.merge_calls == 1  # no second pass once out of time
    assert not client.deleted


async def test_an_orphan_at_a_slot_the_target_since_filled_is_filed_away_not_wedged() -> None:
    """A row-less object at the artifact's next-version slot, and the target
    then regenerated that artifact into the same slot with different bytes. A
    row at the target's key is only proof of an earlier move when the bytes
    match; here it's the target's own upload, so the orphan goes aside."""
    kept, orphan = f"{_SOURCE}/art-1/1", f"{_SOURCE}/art-1/2"
    clash = f"{_TARGET}/art-1/2"
    client = _FakeSupabase({kept: b"v1", orphan: b"stray"}, rows=[_row(kept), _row(clash)])
    client.bucket.objects[clash] = (b"the target's new version", _OLD)

    completion = await _finish(client)

    assert completion.retired
    assert client.bucket.objects[clash][0] == b"the target's new version"
    assert client.bucket.objects[f"{_TARGET}/_merged_orphans/{_SOURCE}/art-1/2"][0] == b"stray"
    assert client.bucket.objects.keys() == {
        f"{_TARGET}/art-1/1",
        clash,
        f"{_TARGET}/_merged_orphans/{_SOURCE}/art-1/2",
    }


async def test_an_orphan_whose_look_alike_row_points_at_nothing_is_filed_away() -> None:
    orphan, clash = f"{_SOURCE}/art-1/2", f"{_TARGET}/art-1/2"
    client = _FakeSupabase({orphan: b"stray"}, rows=[_row(clash)])  # no object at `clash`

    completion = await _finish(client)

    assert completion.retired
    assert f"{_TARGET}/_merged_orphans/{_SOURCE}/art-1/2" in client.bucket.objects
    assert clash not in client.bucket.objects
