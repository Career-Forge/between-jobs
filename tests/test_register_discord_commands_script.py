"""Tests for `scripts/register_discord_commands.py`: what gets sent to Discord (and that a dry run
sends nothing that changes it), what is refused before anything is sent, and that the bot token
never reaches the output or a URL. A pretend Discord with a recording transport; synthetic values
(see tests/discord_fakes.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from discord_fakes import APPLICATION_ID, BOT_TOKEN

from between_jobs.api import discord_commands as commands
from scripts import register_discord_commands as script
from scripts.register_discord_commands import (
    Refusal,
    check_application_id,
    check_definitions,
    check_token,
)

ENV = {"DISCORD_APPLICATION_ID": APPLICATION_ID, "DISCORD_BOT_TOKEN": BOT_TOKEN}
PATH = f"/api/v10/applications/{APPLICATION_ID}/commands"


class _Discord:
    """A pretend command registry: remembers what was asked and answers like Discord."""

    def __init__(self, *, existing: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str, Any, dict[str, str]]] = []
        self.commands: list[dict[str, Any]] = [{"name": n, "id": "1"} for n in existing or []]
        self.refusal: tuple[int, dict[str, Any]] | None = None
        self.unreachable = False
        self.not_json = False
        self.drop_on_put = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError(f"cannot reach {request.url}")
        assert request.url.host == "discord.com"
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            (
                request.method,
                request.url.path,
                body,
                {k.lower(): v for k, v in request.headers.items()},
            )
        )
        assert request.url.path == PATH
        if self.not_json:
            return httpx.Response(502, text="<html>bad gateway</html>")
        if request.method == "PUT" and self.refusal is not None:
            status, answer = self.refusal
            return httpx.Response(status, json=answer)
        if request.method == "PUT":
            assert body is not None
            self.commands = [{"name": d["name"], "id": "2"} for d in body]
            if self.drop_on_put:
                self.commands = self.commands[:-1]
            return httpx.Response(200, json=self.commands)
        return httpx.Response(200, json=self.commands)

    def methods(self) -> list[str]:
        return [method for method, *_ in self.calls]


async def _run(fake: _Discord, *argv: str, env: dict[str, str] | None = None) -> int:
    args = script._parser().parse_args(list(argv))
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as client:
        return await script.run(args, ENV if env is None else env, client)


async def test_the_default_is_a_dry_run_that_reads_and_changes_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord(existing=["list", "other"])

    assert await _run(fake) == 0

    assert fake.methods() == ["GET"]
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "to remove: other" in out and "to update: list" in out


async def test_apply_overwrites_the_global_commands_with_exactly_the_shipped_definitions() -> None:
    fake = _Discord()

    assert await _run(fake, "--apply") == 0

    assert fake.methods() == ["GET", "PUT", "GET"]
    _method, path, body, headers = fake.calls[1]
    assert path == PATH
    assert body == commands.command_definitions()
    assert headers["authorization"] == f"Bot {BOT_TOKEN}"
    assert {c["name"] for c in fake.commands} == commands.command_names()


async def test_the_commands_are_for_the_direct_message_and_both_installations_by_default() -> None:
    fake = _Discord()
    await _run(fake, "--apply")
    sent = fake.calls[1][2]
    assert {tuple(d["integration_types"]) for d in sent} == {(0, 1)}
    assert {tuple(d["contexts"]) for d in sent} == {(1,)}


@pytest.mark.parametrize(("flag", "types"), [("guild", (0,)), ("user", (1,)), ("both", (0, 1))])
async def test_the_installation_types_can_be_narrowed(flag: str, types: tuple[int, ...]) -> None:
    fake = _Discord()
    assert await _run(fake, "--apply", "--integration-types", flag) == 0
    assert {tuple(d["integration_types"]) for d in fake.calls[1][2]} == {types}


async def test_commands_this_repository_does_not_define_are_not_deleted_by_accident(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord(existing=["someone-elses-command"])

    with pytest.raises(Refusal, match="someone-elses-command"):
        await _run(fake, "--apply")
    assert fake.methods() == ["GET"]  # nothing was overwritten

    assert await _run(fake, "--apply", "--remove-others") == 0
    assert fake.methods()[-2:] == ["PUT", "GET"]
    assert "someone-elses-command" not in {c["name"] for c in fake.commands}


async def test_a_refusal_from_discord_exits_1_after_one_attempt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord()
    fake.refusal = (400, {"message": "Invalid Form Body", "code": 50035})

    assert await _run(fake, "--apply") == 1

    assert fake.methods() == ["GET", "PUT"]  # no retry
    assert "Invalid Form Body" in capsys.readouterr().err


async def test_commands_discord_does_not_report_back_exit_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord()
    fake.drop_on_put = True
    assert await _run(fake, "--apply") == 1
    assert "does not report the commands" in capsys.readouterr().err


async def test_the_token_never_reaches_the_output_or_a_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord(existing=["other"])
    fake.refusal = (401, {"message": f"bad token {BOT_TOKEN}", "code": 0})
    await _run(fake, "--apply", "--remove-others")
    fake.refusal = None
    await _run(fake, "--apply", "--remove-others")
    captured = capsys.readouterr()
    for text in (captured.out, captured.err):
        assert BOT_TOKEN not in text
        assert BOT_TOKEN.split(".")[2] not in text
    for _method, path, body, _headers in fake.calls:
        assert BOT_TOKEN not in path
        assert BOT_TOKEN not in json.dumps(body)


async def test_a_network_error_is_reported_by_class_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord()
    fake.unreachable = True
    assert await _run(fake, "--apply") == 1
    captured = capsys.readouterr()
    assert "ConnectError" in captured.err
    assert BOT_TOKEN not in captured.err + captured.out
    assert "discord.com" not in captured.err + captured.out


async def test_an_answer_that_is_not_json_fails_cleanly(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _Discord()
    fake.not_json = True
    assert await _run(fake) == 1
    assert "did not answer with JSON" in capsys.readouterr().err


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"DISCORD_APPLICATION_ID": APPLICATION_ID},
        {"DISCORD_BOT_TOKEN": BOT_TOKEN},
        {"DISCORD_APPLICATION_ID": "abc", "DISCORD_BOT_TOKEN": BOT_TOKEN},
        {"DISCORD_APPLICATION_ID": APPLICATION_ID, "DISCORD_BOT_TOKEN": "short"},
        {"DISCORD_APPLICATION_ID": APPLICATION_ID, "DISCORD_BOT_TOKEN": "has a space " * 4},
    ],
)
async def test_missing_or_malformed_credentials_refuse_before_any_call(env: dict[str, str]) -> None:
    fake = _Discord()
    with pytest.raises(Refusal):
        await _run(fake, "--apply", env=env)
    assert fake.calls == []


def test_the_credential_checks_never_echo_what_they_were_given() -> None:
    assert check_application_id(APPLICATION_ID) == APPLICATION_ID
    assert check_token(BOT_TOKEN) == BOT_TOKEN
    for bad in ("", "x", "12ab", "1" * 30):
        with pytest.raises(Refusal) as caught:
            check_application_id(bad)
        assert bad == "" or bad not in str(caught.value)
    for bad in ("", "short", "has space inside it 1234567890"):
        with pytest.raises(Refusal) as caught:
            check_token(bad)
        assert bad == "" or bad not in str(caught.value)


def test_the_shipped_definitions_pass_the_scripts_own_check_and_a_broken_one_does_not() -> None:
    definitions = commands.command_definitions()
    check_definitions(definitions)
    broken = [{**definitions[0], "name": "Bad Name"}]
    with pytest.raises(Refusal):
        check_definitions(broken)
    with pytest.raises(Refusal):
        check_definitions([])
    with pytest.raises(Refusal):
        check_definitions([definitions[0], definitions[0]])  # a repeated name
    swapped = {
        **definitions[1],
        "options": [
            {"name": "a", "description": "x", "type": 3, "required": False},
            {"name": "b", "description": "x", "type": 3, "required": True},
        ],
    }
    with pytest.raises(Refusal):
        check_definitions([swapped])


def test_a_run_refused_before_sending_exits_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("DISCORD_APPLICATION_ID", "DISCORD_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("sys.argv", ["register_discord_commands.py"])
    with pytest.raises(SystemExit) as exited:
        script.main()
    assert "refused: DISCORD_APPLICATION_ID" in str(exited.value)


def test_the_script_never_reads_a_dotenv_file() -> None:
    source = Path(script.__file__).read_text(encoding="utf-8")
    assert "dotenv" not in source
