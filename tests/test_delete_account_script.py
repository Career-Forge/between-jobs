"""Tests for `scripts/delete_account.py` (launch plan P4.5): picking the right account on the
right project, the dry run that changes nothing, and that the real run is the same code the
"Delete my account" button runs."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import delete_account as script
from scripts.delete_account import Refusal, check_target, project_ref, resolve_user

_PROD = "https://abcdefghijklmnopqrst.supabase.co"


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        (_PROD, "abcdefghijklmnopqrst"),
        ("https://ABCDEFGHIJKLMNOPQRST.supabase.co/", "abcdefghijklmnopqrst"),
        ("http://127.0.0.1:54321", "local"),
    ],
)
def test_a_project_is_named_by_its_url(url: str, ref: str) -> None:
    assert project_ref(url) == ref


@pytest.mark.parametrize(
    "url",
    [
        "https://abcdefghijklmnopqrst.evil.test",
        "http://abcdefghijklmnopqrst.supabase.co",
        "https://\uff41bcdefghijklmnopqrst.supabase.co",  # a fullwidth 'a'
        "https://example.com",
        "",
    ],
)
def test_a_url_that_does_not_name_a_project_is_refused(url: str) -> None:
    with pytest.raises(Refusal):
        project_ref(url)


def test_the_typed_ref_must_match_the_project_in_the_environment() -> None:
    check_target(_PROD, "abcdefghijklmnopqrst")
    with pytest.raises(Refusal, match="does not match"):
        check_target(_PROD, "someotherproject")


class _Admin:
    def __init__(self, users: list[SimpleNamespace]) -> None:
        self.users = users
        self.pages_asked: list[int] = []

    async def get_user_by_id(self, user_id: str) -> Any:
        for user in self.users:
            if str(user.id) == user_id:
                return SimpleNamespace(user=user)
        raise RuntimeError("User not found")

    async def list_users(self, page: int, per_page: int) -> list[SimpleNamespace]:
        self.pages_asked.append(page)
        start = (page - 1) * per_page
        return self.users[start : start + per_page]


def _supabase(users: list[SimpleNamespace]) -> Any:
    return SimpleNamespace(auth=SimpleNamespace(admin=_Admin(users)))


def _user(n: int, email: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"00000000-0000-0000-0000-{n:012d}", email=email)


async def test_an_account_is_found_by_email_ignoring_case_and_spaces() -> None:
    supabase = _supabase([_user(1, "a@example.com"), _user(2, "Person@Example.com")])

    found = await resolve_user(supabase, user_id=None, email="  person@example.COM ")

    assert found == ("00000000-0000-0000-0000-000000000002", "Person@Example.com")


async def test_an_email_that_matches_nobody_or_several_is_refused() -> None:
    supabase = _supabase([_user(1, "a@example.com"), _user(2, "a@example.com")])

    with pytest.raises(Refusal, match="2 accounts"):
        await resolve_user(supabase, user_id=None, email="a@example.com")
    with pytest.raises(Refusal, match="0 accounts"):
        await resolve_user(supabase, user_id=None, email="nobody@example.com")


async def test_an_email_is_found_past_the_first_page() -> None:
    users = [_user(n, f"user{n}@example.com") for n in range(1, 451)]
    supabase = _supabase(users)

    found = await resolve_user(supabase, user_id=None, email="user431@example.com")

    assert found[0].endswith("431")
    assert supabase.auth.admin.pages_asked == [1, 2, 3]


async def test_an_account_is_found_by_id() -> None:
    supabase = _supabase([_user(7, "seven@example.com")])

    assert await resolve_user(
        supabase, user_id="00000000-0000-0000-0000-000000000007", email=None
    ) == (
        "00000000-0000-0000-0000-000000000007",
        "seven@example.com",
    )


def _args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "user_id": "00000000-0000-0000-0000-000000000007",
        "email": None,
        "confirm_ref": "abcdefghijklmnopqrst",
        "dry_run": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    user = _user(7, "seven@example.com")

    async def fake_client(url: str, key: str) -> Any:
        calls.append("client")
        return _supabase([user])

    async def fake_describe(supabase: Any, user_id: str) -> dict[str, Any]:
        calls.append("describe")
        return {
            "rows_per_table": {"public.applications": 2},
            "stored_files": 3,
            "gmail_connected": True,
        }

    async def fake_delete(supabase: Any, http: Any, user_id: str, **kwargs: Any) -> Any:
        calls.append("delete")
        return SimpleNamespace(leftover_tables={})

    monkeypatch.setattr(script, "acreate_client", fake_client)
    monkeypatch.setattr(script, "describe", fake_describe)
    monkeypatch.setattr(script, "delete_account", fake_delete)
    return calls


async def test_a_dry_run_reports_and_deletes_nothing(
    stub: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert await script.run(_args(dry_run=True), _PROD, "key") == 0

    assert "delete" not in stub
    out = capsys.readouterr().out
    assert "seven@example.com" in out and "stored files: 3" in out and "[DRY RUN]" in out


async def test_the_real_run_is_the_same_code_as_the_button(stub: list[str]) -> None:
    assert await script.run(_args(dry_run=False), _PROD, "key") == 0

    assert stub == ["client", "describe", "delete"]


async def test_a_wrong_ref_stops_before_anything_is_read(stub: list[str]) -> None:
    with pytest.raises(Refusal):
        await script.run(_args(confirm_ref="wrongproject"), _PROD, "key")

    assert stub == []
