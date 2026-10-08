"""The Discord interactions endpoint (api/discord_webhook.py): that nothing happens until a request
has proved it is Discord's, that the three-second answer is the right one for each kind of
interaction, and the rules around the work it starts.

The signed requests are SYNTHETIC: interactions written by hand in the shape Discord documents,
signed with a throwaway key made in tests/discord_fakes.py. There is no reference implementation
of the Discord adapter to capture from, so these tests pin its own contract (see that module).

Every case that must be refused uses a database that fails the test if it is touched, and a
network that records every request: "nothing else happens" is checked, not assumed."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any
from unittest import mock

import pytest
from discord_app import DiscordSupabase, Harness, NoDatabase
from discord_fakes import (
    DM_CHANNEL,
    OTHER_KEY,
    PRIVATE_KEY,
    SUBJECT,
    FakeDiscord,
    body_of,
    button,
    command,
    fresh_token,
    ping,
    sign,
    signed_headers,
)

from between_jobs.api import discord_webhook
from between_jobs.api.deferred_reply import DeferredReplies
from between_jobs.api.discord_webhook import (
    MAX_TIMESTAMP_SKEW_SECONDS,
    SignatureRejected,
    verify_request,
)

# -- the signature, unit by unit ------------------------------------------------------------------

_PUBLIC = PRIVATE_KEY.public_key()
_BODY = b'{"type":1}'
_NOW = 1_790_000_000.0
_TS = str(int(_NOW))


def _reason(
    signature: str | None, timestamp: str | None, body: bytes = _BODY, now: float = _NOW
) -> str:
    with pytest.raises(SignatureRejected) as caught:
        verify_request(_PUBLIC, signature, timestamp, body, now=now)
    return caught.value.reason


def test_a_correct_signature_over_the_timestamp_and_raw_body_is_accepted() -> None:
    verify_request(_PUBLIC, sign(_BODY, _TS), _TS, _BODY, now=_NOW)


def test_hex_may_be_upper_case() -> None:
    verify_request(_PUBLIC, sign(_BODY, _TS).upper(), _TS, _BODY, now=_NOW)


@pytest.mark.parametrize("missing", [None, ""])
def test_a_missing_header_is_refused(missing: str | None) -> None:
    good = sign(_BODY, _TS)
    assert _reason(missing, _TS) == "missing"
    assert _reason(good, missing) == "missing"
    assert _reason(missing, missing) == "missing"


@pytest.mark.parametrize(
    "signature",
    [
        "00" * 63,  # one byte short
        "00" * 65,  # one byte long
        "zz" * 64,  # not hex
        ("ab" * 32) + " " + ("ab" * 31) + "a",  # a space (bytes.fromhex would skip it)
        "0x" + "ab" * 63,
        "١٢" * 64,  # non-ASCII digits
        sign(_BODY, _TS) + "\n",
    ],
)
def test_a_signature_that_is_not_128_hex_characters_is_refused_before_it_is_verified(
    signature: str,
) -> None:
    assert _reason(signature, _TS) == "malformed"


@pytest.mark.parametrize(
    "timestamp", ["abc", "-5", "1e9", "12 34", "\uff11\uff12", "1" * 14, "1.5", "+7"]
)
def test_a_timestamp_that_is_not_plain_digits_is_refused(timestamp: str) -> None:
    assert _reason(sign(_BODY, _TS), timestamp) == "malformed"


def test_a_signature_by_another_key_is_refused() -> None:
    assert _reason(sign(_BODY, _TS, OTHER_KEY), _TS) == "bad_signature"


def test_a_changed_body_is_refused() -> None:
    assert _reason(sign(_BODY, _TS), _TS, b'{"type":2}') == "bad_signature"
    assert _reason(sign(_BODY, _TS), _TS, _BODY + b" ") == "bad_signature"


def test_a_changed_timestamp_is_refused() -> None:
    assert _reason(sign(_BODY, _TS), str(int(_NOW) + 1)) == "bad_signature"


def test_the_signature_covers_the_raw_bytes_not_what_they_parse_to() -> None:
    document = {"type": 1, "id": "1", "application_id": "2"}
    signed = json.dumps(document, separators=(",", ":")).encode()
    reserialized = json.dumps(document, indent=2).encode()  # the same document
    reordered = json.dumps(dict(reversed(list(document.items()))), separators=(",", ":")).encode()
    signature = sign(signed, _TS)
    verify_request(_PUBLIC, signature, _TS, signed, now=_NOW)
    for body in (reserialized, reordered):
        assert json.loads(body) == document
        assert _reason(signature, _TS, body) == "bad_signature"


def test_the_timestamp_must_be_within_the_window_either_way() -> None:
    edge = MAX_TIMESTAMP_SKEW_SECONDS
    assert edge == 300
    for offset in (-edge, -1, 0, 1, edge):
        stamp = str(int(_NOW) + offset)
        verify_request(_PUBLIC, sign(_BODY, stamp), stamp, _BODY, now=_NOW)
    stale = str(int(_NOW) - edge - 1)
    future = str(int(_NOW) + edge + 1)
    assert _reason(sign(_BODY, stale), stale) == "stale"
    assert _reason(sign(_BODY, future), future) == "future"


def test_a_timestamp_in_milliseconds_is_read_as_such() -> None:
    """Discord sends whole seconds. A value too large to be a date in seconds is milliseconds, so
    a change of unit would not turn every real request into a "future" one."""
    millis = str(int(_NOW) * 1000 + 250)
    verify_request(_PUBLIC, sign(_BODY, millis), millis, _BODY, now=_NOW)
    stale = str((int(_NOW) - 301) * 1000)
    assert _reason(sign(_BODY, stale), stale) == "stale"
    too_long = str(int(_NOW) * 10000)  # 14 digits
    assert _reason(sign(_BODY, too_long), too_long) == "malformed"


def test_a_stale_request_is_refused_even_though_its_signature_is_valid() -> None:
    stale = str(int(_NOW) - 86400)
    _PUBLIC.verify(bytes.fromhex(sign(_BODY, stale)), stale.encode() + _BODY)  # valid
    assert _reason(sign(_BODY, stale), stale) == "stale"


# -- the endpoint ---------------------------------------------------------------------------------


def _quiet() -> tuple[NoDatabase, FakeDiscord]:
    return NoDatabase(), FakeDiscord()


def test_a_ping_is_answered_with_a_pong() -> None:
    database, world = _quiet()
    with Harness(database, world) as h:
        response = h.post(ping())
    assert response.status_code == 200 and response.json() == {"type": 1}
    assert database.touched == [] and world.requests == []


_REFUSED = [
    "no headers",
    "no signature",
    "no timestamp",
    "short signature",
    "non-hex signature",
    "empty signature",
    "other key",
    "tampered body",
    "tampered timestamp",
    "stale",
    "future",
    "garbage timestamp",
    "reserialized body",
]


# Far enough outside the window that the seconds a slow start of the app takes between building a
# request and the server reading the clock cannot move it back in: one second past the edge made
# this case pass for real (a 200) whenever a second boundary was crossed in between. The exact edge
# is pinned by the fixed-clock tests above.
_WELL_OUTSIDE = MAX_TIMESTAMP_SKEW_SECONDS + 100


def _refused_case(name: str) -> tuple[dict[str, str], bytes]:
    """The headers and body of one kind of request that must be refused, built when the test
    runs (a timestamp made at collection time would age past the window)."""
    body = body_of(ping())
    now = int(time.time())
    good = signed_headers(body, timestamp=now)
    cases: dict[str, tuple[dict[str, str], bytes]] = {
        "no headers": ({}, body),
        "no signature": ({"X-Signature-Timestamp": str(now)}, body),
        "no timestamp": ({"X-Signature-Ed25519": good["X-Signature-Ed25519"]}, body),
        "short signature": (
            {**good, "X-Signature-Ed25519": good["X-Signature-Ed25519"][:-2]},
            body,
        ),
        "non-hex signature": ({**good, "X-Signature-Ed25519": "zz" * 64}, body),
        "empty signature": ({**good, "X-Signature-Ed25519": ""}, body),
        "other key": (signed_headers(body, timestamp=now, key=OTHER_KEY), body),
        "tampered body": (good, body.replace(b"1", b"2", 1)),
        "tampered timestamp": ({**good, "X-Signature-Timestamp": str(now + 1)}, body),
        "stale": (signed_headers(body, timestamp=now - _WELL_OUTSIDE), body),
        "future": (signed_headers(body, timestamp=now + _WELL_OUTSIDE), body),
        "garbage timestamp": ({**good, "X-Signature-Timestamp": "yesterday"}, body),
        "reserialized body": (good, json.dumps(json.loads(body), indent=1).encode()),
    }
    return cases[name]


@pytest.mark.parametrize("name", _REFUSED)
def test_an_unverified_request_is_a_401_and_nothing_else_happens(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    headers, body = _refused_case(name)
    database, world = _quiet()
    with caplog.at_level(logging.DEBUG), Harness(database, world) as h:
        response = h.post_raw(body, headers)

    assert response.status_code == 401
    assert database.touched == [] and world.requests == []
    assert discord_webhook.interaction_registry.running == 0
    # Nothing of the request is echoed or logged.
    assert "application_id" not in response.text
    for record in caplog.records:
        assert "application_id" not in record.getMessage()


def test_a_refused_request_logs_a_reason_and_never_a_header_or_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = body_of(command("link", {"code": "SECRETCODE"}))
    headers = {"X-Signature-Ed25519": "ab" * 64, "X-Signature-Timestamp": str(int(time.time()))}
    with caplog.at_level(logging.DEBUG), Harness(NoDatabase()) as h:
        assert h.post_raw(body, headers).status_code == 401
    refused = [r for r in caplog.records if "refused" in r.getMessage()]
    assert [r.ctx for r in refused] == [{"reason": "bad_signature"}]  # type: ignore[attr-defined]
    for record in caplog.records:
        assert "SECRETCODE" not in record.getMessage() and "ab" * 20 not in record.getMessage()


def test_unauthenticated_requests_cannot_reach_the_handler_with_a_big_body() -> None:
    """The cap is enforced before the signature is even looked at: a 413, not a 401, and the
    handler never ran."""
    database = NoDatabase()
    big = b"x" * (64 * 1024 + 1)
    with Harness(database) as h:
        unsigned = h.post_raw(big, {})
        signed = h.post_raw(big, signed_headers(big, timestamp=int(time.time())))
        at_cap = h.post_raw(
            b"x" * (64 * 1024), signed_headers(b"x" * (64 * 1024), timestamp=int(time.time()))
        )
    assert unsigned.status_code == 413 and signed.status_code == 413
    assert unsigned.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert at_cap.status_code == 400  # signed, within the cap, and not JSON
    assert database.touched == []


def test_a_signed_body_that_is_not_an_interaction_is_a_400() -> None:
    database = NoDatabase()
    with Harness(database) as h:
        for body in (b"not json", b"[1, 2]", b'"text"', b"42", b"\xff\xfe"):
            response = h.post_raw(body, signed_headers(body, timestamp=int(time.time())))
            assert response.status_code == 400, body
    assert database.touched == []


def test_a_signed_command_missing_what_every_command_has_is_a_400_and_nothing_happens() -> None:
    database, world = _quiet()
    payload = command("list")
    del payload["user"]
    with Harness(database, world) as h:
        assert h.post(payload).status_code == 400
    assert database.touched == [] and world.requests == []


def test_with_discord_off_the_endpoint_does_not_exist() -> None:
    database, world = _quiet()
    with Harness(database, world, discord_on=False) as h:
        body = body_of(ping())
        response = h.post_raw(body, signed_headers(body, timestamp=int(time.time())))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FEATURE_DISABLED"
    assert database.touched == [] and world.requests == []


# -- what each kind of interaction is answered with ----------------------------------------------


def test_a_command_is_answered_with_a_deferral_and_the_work_replaces_the_placeholder() -> None:
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        response = h.post(command("privacy"))
    assert response.status_code == 200 and response.json() == {"type": 5}
    (reply,) = world.sent
    assert reply.kind == "edit_original"
    assert reply.json_body is not None and "Privacy, in short" in reply.json_body["content"]


def test_a_button_tap_is_answered_with_a_silent_deferral_and_the_reply_is_a_new_message() -> None:
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        response = h.post(button(f"profile:cancel:{'1' * 36}"))
    assert response.status_code == 200 and response.json() == {"type": 6}
    assert [r.kind for r in world.requests] == []  # cancelling something already gone says nothing


def test_the_deferral_is_the_whole_http_answer_and_carries_no_token_or_content() -> None:
    token = fresh_token()
    supabase = DiscordSupabase()
    with Harness(supabase) as h:
        response = h.post(command("privacy", token=token))
    assert token not in response.text
    assert set(response.json()) == {"type"}


@pytest.mark.parametrize("kind", [4, 5, 99])
def test_an_interaction_the_app_does_not_use_gets_a_short_private_refusal(kind: int) -> None:
    database, world = _quiet()
    payload = command("list")
    payload["type"] = kind
    with Harness(database, world) as h:
        response = h.post(payload)
    body = response.json()
    assert response.status_code == 200
    assert body["type"] == 4 and body["data"]["flags"] == 64
    assert body["data"]["allowed_mentions"] == {"parse": []}
    assert database.touched == [] and world.requests == []


def test_a_command_the_app_does_not_define_gets_the_same_refusal() -> None:
    database, world = _quiet()
    with Harness(database, world) as h:
        response = h.post(command("shutdown"))
    assert response.json()["type"] == 4
    assert database.touched == [] and world.requests == []


@pytest.mark.parametrize(
    "payload",
    [
        command("list", dm=False),
        command("link", {"code": "ABCD2345"}, dm=False),
        command("list", context=2),  # a group chat, or someone else's DM
        button("app:prepare:1", dm=False),
    ],
    ids=["server command", "server /link", "other DM", "server button"],
)
def test_anything_outside_the_direct_message_with_the_app_is_refused_privately_with_no_work(
    payload: dict[str, Any],
) -> None:
    database, world = _quiet()
    with Harness(database, world) as h:
        response = h.post(payload)

    body = response.json()
    assert response.status_code == 200
    assert body["type"] == 4 and body["data"]["flags"] == 64
    assert "direct message" in body["data"]["content"]
    assert body["data"]["allowed_mentions"] == {"parse": []}
    assert database.touched == []  # not even the claim
    assert world.requests == []


# -- bounds -------------------------------------------------------------------------------------


def test_when_every_place_is_taken_the_answer_is_busy_and_nothing_is_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = DeferredReplies(max_concurrent=1)
    monkeypatch.setattr(discord_webhook, "interaction_registry", full)
    supabase = DiscordSupabase()
    world = FakeDiscord()

    async def hold() -> None:
        slot = await full.try_reserve()
        assert slot is not None  # taken, and never released during the request

    asyncio.run(hold())
    with Harness(supabase, world) as h:
        response = h.post(command("list"))

    body = response.json()
    assert body["type"] == 4 and body["data"]["flags"] == 64
    assert "busy" in body["data"]["content"] or "a lot" in body["data"]["content"]
    assert supabase.ledger.claims == [] and world.requests == []


def test_the_registry_is_sized_for_quick_handlers_and_is_not_the_generation_registry() -> None:
    from between_jobs.api import deferred_reply

    assert discord_webhook.interaction_registry is not deferred_reply.registry
    assert discord_webhook.MAX_IN_FLIGHT > deferred_reply.DEFAULT_MAX_CONCURRENT


def test_a_slot_is_given_back_when_the_work_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = DeferredReplies(max_concurrent=1)
    monkeypatch.setattr(discord_webhook, "interaction_registry", registry)
    supabase = DiscordSupabase()
    with Harness(supabase) as h:
        first = h.post(command("privacy"))
        h.settle()
        second = h.post(command("privacy"))
    assert first.json() == second.json() == {"type": 5}  # neither was told it was busy


def _a_second_one_is_not_told_it_is_busy(
    monkeypatch: pytest.MonkeyPatch, supabase: DiscordSupabase, *, first_is_already_known: bool
) -> None:
    registry = DeferredReplies(max_concurrent=2)
    monkeypatch.setattr(discord_webhook, "interaction_registry", registry)
    payload = command("privacy", interaction_id="1100000000000000777")
    if first_is_already_known:
        asyncio.run(supabase.ledger.claim("1100000000000000777"))  # an unfinished earlier delivery
    with Harness(supabase) as h:
        if not first_is_already_known:
            assert h.post(payload).json() == {"type": 5}
            h.settle()
        replays = [h.post(payload) for _ in range(5)]  # more repeats than there are places
        fresh = h.post(command("privacy", interaction_id="1100000000000000778"))
    assert all(r.json() == {"type": 5} for r in replays)  # dropped, not told "busy" (type 4)
    assert fresh.json() == {"type": 5}  # and none of them kept its place


def test_repeats_of_a_finished_interaction_do_not_use_up_the_places(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped repeat gives its place back. Otherwise replaying one captured request (valid for
    five minutes) a few dozen times would leave the app answering "busy" to everyone."""
    _a_second_one_is_not_told_it_is_busy(
        monkeypatch, DiscordSupabase(), first_is_already_known=False
    )


def test_repeats_of_an_unfinished_interaction_do_not_use_up_the_places(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _a_second_one_is_not_told_it_is_busy(
        monkeypatch, DiscordSupabase(), first_is_already_known=True
    )


def test_a_running_handler_keeps_its_place_until_it_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bound is on handlers that are running: a place is not given back when the work is
    handed over, only when it ends."""
    registry = DeferredReplies(max_concurrent=1)
    monkeypatch.setattr(discord_webhook, "interaction_registry", registry)
    supabase = DiscordSupabase()
    gate = threading.Event()

    async def held(*_args: Any, **_kwargs: Any) -> None:
        while not gate.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(discord_webhook, "handle_inbound", held)
    with Harness(supabase) as h:
        try:
            first = h.post(command("privacy", interaction_id="1100000000000000801"))
            second = h.post(command("privacy", interaction_id="1100000000000000802"))
            assert first.json() == {"type": 5}
            assert second.json()["type"] == 4 and second.json()["data"]["flags"] == 64  # busy
            assert supabase.ledger.claims == ["1100000000000000801"]  # the busy one claimed nothing
        finally:
            gate.set()  # so a failed assertion cannot leave the harness waiting for the handler
        h.settle()
        third = h.post(command("privacy", interaction_id="1100000000000000803"))
        assert third.json() == {"type": 5}  # the place came back when the handler ended


# -- timing the shipped defaults must keep --------------------------------------------------------


def test_the_shipped_timings_keep_the_answer_inside_discords_three_seconds() -> None:
    """The other tests shorten these; this one reads what a real server runs with. The claim is
    taken before the acknowledgement, which Discord wants within three seconds."""
    assert 0 < discord_webhook._CLAIM_TIMEOUT_SECONDS < 3.0
    assert 0 < discord_webhook._RESPONSE_WAIT_SECONDS <= 10
    assert 0 < discord_webhook._SETTLE_TIMEOUT_SECONDS <= 30
    assert 0 < discord_webhook._AFTER_RESPONSE_PAUSE_SECONDS < 1.0
    # The claim outlives the 15 minutes an interaction's token lasts, so a handler still working
    # is never taken over.
    assert discord_webhook._LEASE_SECONDS >= 15 * 60


def test_completing_the_claim_is_bounded_so_a_hung_database_cannot_hold_a_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completion that never answers would hold the handler's task, and its place among the
    interactions being handled, for as long as it hung."""

    async def hang(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(discord_webhook, "complete_interaction", hang)
    monkeypatch.setattr(discord_webhook, "_SETTLE_TIMEOUT_SECONDS", 0.2)

    async def settle() -> None:
        await asyncio.wait_for(discord_webhook._settle_claim(object(), "1"), timeout=5)  # type: ignore[arg-type]

    asyncio.run(settle())  # returns after about 0.2 seconds; it would raise TimeoutError otherwise


# -- the claim is a safety net, not a gate ---------------------------------------------------------


class _ClaimFailsOnce(DiscordSupabase):
    def __init__(self) -> None:
        super().__init__()
        self.failures_left = 1

    def rpc(self, fn: str, params: dict[str, Any]) -> Any:
        if fn == "claim_discord_interaction" and self.failures_left:
            self.failures_left -= 1

            class _Down:
                async def execute(self) -> Any:
                    raise ConnectionError("the database is unreachable")

            return _Down()
        return super().rpc(fn, params)


def test_a_replay_is_processed_again_when_the_claim_could_not_be_made() -> None:
    """The documented limit of the dedup (see the README): it is a safety net, not a gate. When the
    claim cannot be made the interaction is processed without it and nothing is recorded, so a
    replay of the same signed request inside the five-minute window runs a second time. Dropping
    a person's command because the bookkeeping failed would be worse; once a claim does succeed,
    later repeats are dropped as ever."""
    supabase = _ClaimFailsOnce()
    world = FakeDiscord()
    payload = command("privacy", interaction_id="1100000000000000911")
    with Harness(supabase, world) as h:
        first = h.post(payload)
        h.settle()
        second = h.post(payload)
        h.settle()
        third = h.post(payload)
    assert first.json() == second.json() == third.json() == {"type": 5}
    assert len(world.sent) == 2  # the first (no claim) and the second (claimed); the third dropped
    assert supabase.ledger.claims == ["1100000000000000911"] * 2


# -- the order: the work waits for the answer ----------------------------------------------------


def test_the_work_does_not_start_until_the_answer_has_been_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interaction's webhook accepts nothing before Discord has the first answer, so the
    handler must not reach Discord before its own acknowledgement has gone out."""
    events: list[str] = []
    real = discord_webhook._mark_sent

    async def marked(event: asyncio.Event) -> None:
        events.append("answer sent")
        await real(event)

    monkeypatch.setattr(discord_webhook, "_mark_sent", marked)
    world = FakeDiscord()
    original = world.handler

    def recording(request: Any) -> Any:
        events.append("discord request")
        return original(request)

    world.handler = recording  # type: ignore[method-assign]
    with Harness(DiscordSupabase(), world) as h:
        h.post(command("privacy"))

    assert events[0] == "answer sent"
    assert "discord request" in events


def test_the_work_pauses_a_moment_after_the_answer_before_it_calls_discord() -> None:
    """The answer having left is not Discord having recorded it, so the first call waits a beat."""
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(seconds: float, *args: Any) -> None:
        pauses.append(seconds)
        await real_sleep(0, *args)

    world = FakeDiscord()
    original = world.handler

    def recording(request: Any) -> Any:
        pauses.append(-1.0)  # marks the first call to Discord in the list of pauses
        return original(request)

    world.handler = recording  # type: ignore[method-assign]
    with (
        Harness(DiscordSupabase(), world) as h,
        mock.patch.object(discord_webhook, "_AFTER_RESPONSE_PAUSE_SECONDS", 0.25),
        mock.patch("asyncio.sleep", recording_sleep),
    ):
        h.post(command("privacy"))

    assert 0.25 in pauses and -1.0 in pauses
    assert pauses.index(0.25) < pauses.index(-1.0)


def test_a_work_never_told_the_answer_went_out_starts_after_a_bounded_wait(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def never(_event: asyncio.Event) -> None:
        return None

    monkeypatch.setattr(discord_webhook, "_mark_sent", never)
    monkeypatch.setattr(discord_webhook, "_RESPONSE_WAIT_SECONDS", 0.2)
    world = FakeDiscord()
    with caplog.at_level(logging.WARNING), Harness(DiscordSupabase(), world) as h:
        h.post(command("privacy"))

    assert len(world.sent) == 1  # it still ran
    assert any("not confirmed sent" in r.getMessage() for r in caplog.records)


def test_the_claim_is_taken_before_the_answer_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A claim that cannot be made in time must not hold the three-second answer hostage."""
    supabase = DiscordSupabase()

    async def hang(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(discord_webhook, "claim_interaction", hang)
    monkeypatch.setattr(discord_webhook, "_CLAIM_TIMEOUT_SECONDS", 0.1)
    world = FakeDiscord()
    started = time.monotonic()
    with Harness(supabase, world) as h:
        response = h.post(command("privacy"))
    assert response.json() == {"type": 5}
    assert time.monotonic() - started < 10
    assert len(world.sent) == 1  # processed without dedup


def test_the_ordinary_id_is_what_is_claimed_with_the_lease_of_the_interactions_token() -> None:
    supabase = DiscordSupabase()
    payload = command("privacy", interaction_id="1100000000000000555")
    with Harness(supabase) as h:
        h.post(payload)
    assert supabase.ledger.claims == ["1100000000000000555"]
    assert supabase.ledger.leases == [900]
    assert supabase.ledger.rows["1100000000000000555"]["completed"] is True


def test_the_endpoint_never_reads_the_senders_name_or_anything_but_the_signed_user() -> None:
    payload = command("privacy", user_id=SUBJECT)
    payload["user"]["username"] = "<@999> @everyone"
    supabase = DiscordSupabase()
    world = FakeDiscord()
    with Harness(supabase, world) as h:
        h.post(payload)
    assert "@everyone" not in world.all_text()
    assert DM_CHANNEL not in str(supabase.rpc_calls)  # the chat id is not who the sender is
