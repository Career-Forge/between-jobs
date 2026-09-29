"""Finishing a /link against a local Supabase stack: real storage objects, real
`artifact_versions` rows, the real RPCs -- what tests/test_link_completion.py
checks against an in-memory fake, checked against the real Storage API.

Run with `pytest -m local_supabase` after `supabase start` and
`supabase db reset --local`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from between_jobs.api.artifact_versions_store import artifact_id_for, create_version

from .conftest import World

pytestmark = pytest.mark.local_supabase


async def _age(pg: Any, name: str) -> None:
    """Storage stamps objects with the time they were uploaded; the tests
    that need one to look old say so directly."""
    await pg.execute(
        "update storage.objects set created_at = now() - interval '1 hour' "
        "where bucket_id = 'artifacts' and name = $1",
        name,
    )


async def _download(world: World, key: str) -> bytes:
    return bytes(await world.sb.storage.from_("artifacts").download(key))


async def test_files_move_to_the_target_and_the_emptied_source_is_retired(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    first = await world.artifact_version(src.id, app["id"], pv["id"], version=1, body=b"resume-v1")
    second = await world.artifact_version(src.id, app["id"], pv["id"], version=2, body=b"resume-v2")
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert completion.retired and not completion.already_complete
    assert completion.leftover == {}
    assert await world.storage_keys(src.id) == []
    assert len(await world.storage_keys(target)) == 2
    rows = (
        await world.sb.table("artifact_versions")
        .select("storage_key,user_id")
        .in_("id", [first["id"], second["id"]])
        .execute()
    ).data
    assert all(r["user_id"] == target and r["storage_key"].startswith(f"{target}/") for r in rows)
    assert sorted([await _download(world, r["storage_key"]) for r in rows]) == [
        b"resume-v1",
        b"resume-v2",
    ]
    assert not await world.user_exists(src.id)
    assert await world.user_exists(target)


async def test_a_shared_job_renumbers_versions_and_every_key_still_names_a_real_object(
    world: World,
) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    src_app = await world.application(src.id, job)
    tgt_app = await world.application(target, job)
    src_pv = await world.profile_version(src.id)
    tgt_pv = await world.profile_version(target)
    await world.artifact_version(src.id, src_app["id"], src_pv["id"], version=1, body=b"src-1")
    await world.artifact_version(src.id, src_app["id"], src_pv["id"], version=2, body=b"src-2")
    await world.artifact_version(target, tgt_app["id"], tgt_pv["id"], version=1, body=b"tgt-1")
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert completion.retired
    rows = (
        await world.sb.table("artifact_versions")
        .select("version,storage_key")
        .eq("artifact_id", artifact_id_for(tgt_app["id"], "resume"))
        .order("version")
        .execute()
    ).data
    assert [r["version"] for r in rows] == [1, 2, 3]
    existing = set(await world.storage_keys(target))
    assert all(r["storage_key"] in existing for r in rows)
    assert sorted([await _download(world, r["storage_key"]) for r in rows]) == [
        b"src-1",
        b"src-2",
        b"tgt-1",
    ]
    assert await world.storage_keys(src.id) == []


async def test_the_targets_next_upload_after_a_collapse_overwrites_nothing(world: World) -> None:
    """Renumbering changes which version number a moved object has, not the
    key it sits under -- so the next create_version can't land on any of them."""
    src = await world.telegram_user()
    target = await world.web_user()
    job = await world.job()
    src_app = await world.application(src.id, job)
    tgt_app = await world.application(target, job)
    src_pv = await world.profile_version(src.id)
    tgt_pv = await world.profile_version(target)
    await world.artifact_version(src.id, src_app["id"], src_pv["id"], version=1, body=b"src-1")
    await world.artifact_version(target, tgt_app["id"], tgt_pv["id"], version=1, body=b"tgt-1")
    await world.artifact_version(target, tgt_app["id"], tgt_pv["id"], version=2, body=b"tgt-2")
    await world.link(src.subject, await world.mint(target), src.id)
    await world.finish(src.id, target, src.subject)
    before = set(await world.storage_keys(target))

    created = await create_version(
        world.sb,
        target,
        application_id=tgt_app["id"],
        document_kind="resume",
        content=b"brand new",
        media_type="application/pdf",
        generator="test",
        generator_version="1",
        profile_version_id=tgt_pv["id"],
        job_snapshot_id=None,
        evidence_fact_ids=[],
        warnings=[],
    )

    assert created["version"] == 4
    assert created["storage_key"] not in before
    assert await _download(world, created["storage_key"]) == b"brand new"
    rows = (
        await world.sb.table("artifact_versions")
        .select("storage_key")
        .eq("artifact_id", artifact_id_for(tgt_app["id"], "resume"))
        .execute()
    ).data
    assert sorted([await _download(world, r["storage_key"]) for r in rows]) == [
        b"brand new",
        b"src-1",
        b"tgt-1",
        b"tgt-2",
    ]


async def test_more_files_than_one_listing_page_all_move(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    for version in range(1, 106):
        await world.artifact_version(
            src.id, app["id"], pv["id"], version=version, body=f"v{version}".encode()
        )
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert completion.retired
    assert len(await world.storage_keys(target)) == 105
    assert await world.storage_keys(src.id) == []


async def test_a_file_with_no_row_waits_until_it_is_old_enough_then_moves_aside(
    world: World, pg: Any
) -> None:
    """create_version uploads, then inserts its row; a young row-less object
    may be in that gap and is left alone."""
    src = await world.telegram_user()
    target = await world.web_user()
    loose = f"{src.id}/loose-artifact/1"
    await world.sb.storage.from_("artifacts").upload(
        loose, b"loose", {"content-type": "text/plain"}
    )
    await world.link(src.subject, await world.mint(target), src.id)

    young = await world.finish(src.id, target, src.subject)

    assert not young.retired
    assert young.leftover == {"storage.objects": 1}
    assert await world.user_exists(src.id)

    await _age(pg, loose)
    old = await world.finish(src.id, target, src.subject)

    assert old.retired
    assert await world.storage_keys(target) == [
        f"{target}/_merged_orphans/{src.id}/loose-artifact/1"
    ]


async def test_a_link_that_crashed_after_committing_is_resumed_by_resending_the_code(
    world: World,
) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    await world.artifact_version(src.id, app["id"], pv["id"], body=b"kept")
    code = await world.mint(target)
    await world.link(src.subject, code, src.id)
    # ...the process dies here, before finish_link. The Telegram account now
    # resolves to the target, so the resend arrives with the target as "source".

    resumed = await world.link(src.subject, code, target)

    assert resumed["resumed"] is True and resumed["source_user_id"] == src.id
    completion = await world.finish(
        resumed["source_user_id"], resumed["target_user_id"], src.subject
    )
    assert completion.retired and not completion.already_complete
    assert len(await world.storage_keys(target)) == 1
    assert await world.storage_keys(src.id) == []

    again = await world.link(src.subject, code, target)
    finished = await world.finish(again["source_user_id"], again["target_user_id"], src.subject)
    assert finished.already_complete and finished.retired


async def test_a_destination_holding_different_bytes_blocks_the_move(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    version = await world.artifact_version(src.id, app["id"], pv["id"], body=b"mine")
    squatted = f"{target}/{version['storage_key'].removeprefix(f'{src.id}/')}"
    await world.sb.storage.from_("artifacts").upload(
        squatted, b"squatter", {"content-type": "application/pdf"}
    )
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert not completion.retired
    assert await world.user_exists(src.id)
    assert await _download(world, version["storage_key"]) == b"mine"
    assert await _download(world, squatted) == b"squatter"


async def test_the_copy_of_an_earlier_pass_that_died_is_recognized_and_finished(
    world: World,
) -> None:
    """A pass copied the object, then died before updating the row or removing
    the original: the destination already exists with the same bytes."""
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    version = await world.artifact_version(src.id, app["id"], pv["id"], body=b"same")
    destination = f"{target}/{version['storage_key'].removeprefix(f'{src.id}/')}"
    await world.sb.storage.from_("artifacts").upload(
        destination, b"same", {"content-type": "application/pdf"}
    )
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert completion.retired
    assert await world.storage_keys(target) == [destination]
    row = (
        await world.sb.table("artifact_versions")
        .select("storage_key")
        .eq("id", version["id"])
        .execute()
    ).data
    assert row == [{"storage_key": destination}]


async def test_a_version_row_whose_object_is_gone_no_longer_blocks_the_source(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    app = await world.application(src.id, await world.job())
    pv = await world.profile_version(src.id)
    dangling = await world.artifact_version(src.id, app["id"], pv["id"], body=None)
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert completion.retired
    row = (
        await world.sb.table("artifact_versions")
        .select("storage_key")
        .eq("id", dangling["id"])
        .execute()
    ).data
    assert row[0]["storage_key"].startswith(f"{target}/")


async def test_an_object_a_third_accounts_row_names_is_never_touched(world: World) -> None:
    src = await world.telegram_user()
    target = await world.web_user()
    stranger = await world.web_user()
    stranger_app = await world.application(stranger, await world.job())
    stranger_pv = await world.profile_version(stranger)
    key = f"{src.id}/odd/1"
    await world.sb.storage.from_("artifacts").upload(key, b"x", {"content-type": "text/plain"})
    await (
        world.sb.table("artifact_versions")
        .insert(
            {
                "user_id": stranger,
                "application_id": stranger_app["id"],
                "artifact_id": artifact_id_for(stranger_app["id"], "resume"),
                "version": 1,
                "document_kind": "resume",
                "storage_key": key,
                "profile_version_id": stranger_pv["id"],
                "evidence_fact_ids": [],
                "media_type": "text/plain",
                "sha256": "0" * 64,
                "generator": "test",
                "generator_version": "1",
            }
        )
        .execute()
    )
    await world.link(src.subject, await world.mint(target), src.id)

    completion = await world.finish(src.id, target, src.subject)

    assert not completion.retired
    assert await world.storage_keys(src.id) == [key]
    assert datetime.now(UTC)  # nothing here depends on wall-clock time
