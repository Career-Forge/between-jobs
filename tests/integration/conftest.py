"""Fixtures for the tests that run against a LOCAL Supabase stack.

These exercise what a fake can't: the SQL functions behind /link (real
transactions, locks, foreign keys, triggers) and the Storage API. They run
only with `pytest -m local_supabase`, against `supabase start`'s stack -- never
a hosted project. `local_stack` refuses (fails, not skips) if the stack's URLs
aren't loopback, and every client used here is built from those URLs.

Bring the stack up and current first:

    supabase start
    supabase db reset --local
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

import asyncpg
import pytest

from between_jobs.api.artifact_versions_store import artifact_id_for
from between_jobs.api.link_codes_store import consume_link_code
from between_jobs.api.link_completion import LinkCompletion, finish_link
from supabase import AsyncClient, acreate_client

_REPO = Path(__file__).parent.parent.parent
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_BUCKET = "artifacts"


def _cannot_run(reason: str) -> NoReturn:
    """Skip on a developer's machine; fail in CI, where a silent skip is a green build that
    tested nothing (GitHub sets CI=true)."""
    if os.environ.get("CI"):
        pytest.fail(f"CI must run the local-stack tests, but: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def local_stack() -> dict[str, str]:
    if shutil.which("supabase") is None:
        _cannot_run("the supabase CLI isn't installed")
    proc = subprocess.run(
        ["supabase", "status", "-o", "json"],
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=60,
        check=False,
    )
    try:
        status = cast(dict[str, str], json.loads(proc.stdout))
    except ValueError:
        _cannot_run("no local Supabase stack is running (supabase start)")
    for key in ("API_URL", "DB_URL"):
        host = urlparse(status[key]).hostname
        assert host in _LOOPBACK, f"{key} is not a loopback address; refusing to run: {host}"
    return status


@pytest.fixture
async def sb(local_stack: dict[str, str]) -> AsyncClient:
    return await acreate_client(local_stack["API_URL"], local_stack["SERVICE_ROLE_KEY"])


@pytest.fixture
async def pg(local_stack: dict[str, str]) -> AsyncIterator[Any]:
    connection = await asyncpg.connect(local_stack["DB_URL"])
    try:
        function = await connection.fetchval(
            "select 1 from pg_proc where proname = 'finish_link_merge'"
        )
        assert function == 1, "the local database is behind: run `supabase db reset --local`"
        yield connection
    finally:
        await connection.close()


@dataclass(frozen=True)
class TelegramUser:
    id: str
    subject: str


class World:
    """Builds the users and rows a test needs, and removes them afterward.

    Every user, job and Telegram subject is unique per call, so a crashed
    earlier run's leftovers never collide with this one."""

    def __init__(self, sb: AsyncClient, pg: Any, stack: dict[str, str]) -> None:
        # Any, not AsyncClient: PostgREST's `data` is a recursive JSON union
        # that strict mypy won't let a test index into without a cast per read.
        self.sb: Any = sb
        self.pg = pg
        self.stack = stack
        self.users: list[str] = []
        self.subjects: list[str] = []
        self.job_ids: list[str] = []

    # -- users ---------------------------------------------------------

    async def telegram_user(self) -> TelegramUser:
        """What resolve_or_create_user_id makes on first contact."""
        subject = f"9{uuid.uuid4().int % 10**11:011d}"
        created = await self.sb.auth.admin.create_user(
            {
                "email": f"telegram-{uuid.uuid4().hex}@users.between-jobs.tech",
                "email_confirm": True,
                "app_metadata": {
                    "provider": "telegram",
                    "bj_provisioned_by": "telegram",
                    "bj_telegram_subject": subject,
                },
            }
        )
        user_id = str(created.user.id)
        self.users.append(user_id)
        self.subjects.append(subject)
        await self.identity(user_id, "telegram", subject)
        return TelegramUser(user_id, subject)

    async def web_user(self, *, password: str | None = None, email: str | None = None) -> str:
        created = await self.sb.auth.admin.create_user(
            {
                "email": email or f"web-{uuid.uuid4().hex}@example.com",
                "password": password or uuid.uuid4().hex,
                "email_confirm": True,
            }
        )
        user_id = str(created.user.id)
        self.users.append(user_id)
        return user_id

    async def identity(self, user_id: str, channel: str, subject: str) -> None:
        await (
            self.sb.table("channel_identities")
            .insert(
                {
                    "user_id": user_id,
                    "channel": channel,
                    "external_tenant": "",
                    "external_subject": subject,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            )
            .execute()
        )

    async def mint(self, user_id: str, *, expires_in: timedelta = timedelta(minutes=10)) -> str:
        code = uuid.uuid4().hex[:8].upper()
        await (
            self.sb.table("link_codes")
            .insert(
                {
                    "user_id": user_id,
                    "channel": "telegram",
                    "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                    "expires_at": (datetime.now(UTC) + expires_in).isoformat(),
                }
            )
            .execute()
        )
        return code

    # -- rows ----------------------------------------------------------

    async def job(self) -> tuple[str, str]:
        """(job_id, job_snapshot_id)."""
        tag = uuid.uuid4().hex[:10]
        job = (
            await self.sb.table("jobs")
            .insert({"company_name": f"Co-{tag}", "canonical_url": f"https://example.com/{tag}"})
            .execute()
        ).data[0]
        self.job_ids.append(cast(str, job["id"]))
        snap = (
            await self.sb.table("job_snapshots")
            .insert(
                {
                    "job_id": job["id"],
                    "source_url": f"https://example.com/{tag}",
                    "title": "Engineer",
                    "company_name": f"Co-{tag}",
                    "description_text": "x",
                    "structured_json": {},
                    "content_hash": f"snap-{tag}",
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "source_kind": "manual",
                }
            )
            .execute()
        ).data[0]
        return cast(str, job["id"]), cast(str, snap["id"])

    async def application(
        self, user_id: str, job: tuple[str, str], *, status: str = "saved", **extra: Any
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            (
                await self.sb.table("applications")
                .insert(
                    {
                        "user_id": user_id,
                        "job_id": job[0],
                        "active_job_snapshot_id": job[1],
                        "status": status,
                        "source_channel": "telegram",
                        **extra,
                    }
                )
                .execute()
            ).data[0],
        )

    async def profile_version(
        self,
        user_id: str,
        *,
        content_hash: str | None = None,
        canonical: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        tag = uuid.uuid4().hex[:10]
        return cast(
            dict[str, Any],
            (
                await self.sb.table("profile_versions")
                .insert(
                    {
                        "user_id": user_id,
                        "schema_version": "v1",
                        "source_kind": "json_paste",
                        "canonical_json": canonical if canonical is not None else {"tag": tag},
                        "content_hash": content_hash or f"hash-{tag}",
                        **extra,
                    }
                )
                .execute()
            ).data[0],
        )

    async def fact(
        self, user_id: str, profile_version_id: str, *, pointer: str, value: str = "v"
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            (
                await self.sb.table("career_facts")
                .insert(
                    {
                        "user_id": user_id,
                        "profile_version_id": profile_version_id,
                        "fact_type": "skill",
                        "entity_key": f"e-{uuid.uuid4().hex[:6]}",
                        "value_json": {"v": value},
                        "source_pointer": pointer,
                    }
                )
                .execute()
            ).data[0],
        )

    async def resume_document(
        self,
        user_id: str,
        profile_version_id: str,
        *,
        application_id: str | None = None,
        fact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            (
                await self.sb.table("resume_documents")
                .insert(
                    {
                        "user_id": user_id,
                        "application_id": application_id,
                        "profile_version_id": profile_version_id,
                        "section_order": [],
                        "section_visibility": {},
                        "header_layout": {},
                        "selected_evidence_fact_ids": fact_ids or [],
                        "accepted_patches": {},
                        "shape_overrides": {},
                        "assertions": {},
                    }
                )
                .execute()
            ).data[0],
        )

    async def artifact_version(
        self,
        user_id: str,
        application_id: str,
        profile_version_id: str,
        *,
        kind: str = "resume",
        version: int = 1,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        """A row, and -- unless `body` is None -- the object it names, the way
        artifact_versions_store.create_version writes them."""
        artifact_id = artifact_id_for(application_id, kind)
        key = f"{user_id}/{artifact_id}/{version}"
        if body is not None:
            await self.sb.storage.from_(_BUCKET).upload(
                key, body, {"content-type": "application/pdf"}
            )
        return cast(
            dict[str, Any],
            (
                await self.sb.table("artifact_versions")
                .insert(
                    {
                        "user_id": user_id,
                        "application_id": application_id,
                        "artifact_id": artifact_id,
                        "version": version,
                        "document_kind": kind,
                        "storage_key": key,
                        "profile_version_id": profile_version_id,
                        "evidence_fact_ids": [],
                        "media_type": "application/pdf",
                        "sha256": hashlib.sha256(body or b"").hexdigest(),
                        "generator": "test",
                        "generator_version": "1",
                    }
                )
                .execute()
            ).data[0],
        )

    # -- the link ------------------------------------------------------

    async def link(self, subject: str, code: str, source: str) -> dict[str, Any]:
        return await consume_link_code(
            self.sb,
            channel="telegram",
            external_subject=subject,
            code=code,
            source_user_id=source,
        )

    async def finish(self, source: str, target: str, subject: str) -> LinkCompletion:
        return await finish_link(
            self.sb, source_user_id=source, target_user_id=target, subject=subject
        )

    async def counts(self, user_id: str) -> dict[str, int]:
        result = await self.sb.rpc("user_owned_row_counts", {"p_user_id": user_id}).execute()
        return cast(dict[str, int], result.data)

    async def user_exists(self, user_id: str) -> bool:
        try:
            await self.sb.auth.admin.get_user_by_id(user_id)
        except Exception:
            return False
        return True

    async def storage_keys(self, prefix: str) -> list[str]:
        rows = await self.pg.fetch(
            "select name from storage.objects where bucket_id = $1 and name like $2 order by name",
            _BUCKET,
            f"{prefix}/%",
        )
        return [row["name"] for row in rows]

    async def cleanup(self) -> None:
        bucket = self.sb.storage.from_(_BUCKET)
        for user_id in self.users:
            with contextlib.suppress(Exception):
                names = await self.storage_keys(user_id)
                if names:
                    await bucket.remove(names)
        for user_id in self.users:
            with contextlib.suppress(Exception):
                await self.sb.auth.admin.delete_user(user_id)
        if self.job_ids:
            with contextlib.suppress(Exception):
                await self.sb.table("jobs").delete().in_("id", self.job_ids).execute()
        if self.subjects:
            with contextlib.suppress(Exception):
                await (
                    self.sb.table("link_code_attempts")
                    .delete()
                    .in_("external_subject", self.subjects)
                    .execute()
                )


@pytest.fixture
async def world(sb: AsyncClient, pg: Any, local_stack: dict[str, str]) -> AsyncIterator[World]:
    world = World(sb, pg, local_stack)
    try:
        yield world
    finally:
        await world.cleanup()


# The two-user fixtures the cross-tenant suites share. Imported last: they import `World` from here.
from .cross_tenant.fixtures import (  # noqa: E402, F401
    tenant_client,
    tenant_env,
    tenant_pair,
    tenant_world,
)
