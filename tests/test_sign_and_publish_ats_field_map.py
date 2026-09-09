"""Tests for browser-extension.md E3c's offline signing/publish script --
canonicalization, the reserved-key collision guard (an adversarially-
confirmed real bug: a curated source file defining its own top-level
`version`/`ats_type`/`schema` used to silently win the payload merge and
get signed instead of the actual CLI-computed values), the real Ed25519
sign/verify round trip, key-loading precedence, and version numbering."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from scripts.sign_and_publish_ats_field_map import (
    canonicalize,
    generate_keypair,
    load_private_key,
    next_version,
    sign_field_map,
)


def test_canonicalize_sorts_keys_deterministically() -> None:
    assert canonicalize({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonicalize_has_no_extraneous_whitespace() -> None:
    assert canonicalize({"a": [1, 2]}) == '{"a":[1,2]}'


def test_canonicalize_is_deterministic_across_calls() -> None:
    payload = {"z": 1, "a": {"y": 2, "b": 3}}
    assert canonicalize(payload) == canonicalize(payload)


def test_sign_field_map_produces_a_signature_that_really_verifies() -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    canonical, _signature_b64 = sign_field_map(
        ats_type="lever",
        version=1,
        schema="ats-field-map/v1",
        fields={"custom_question_prefix": "cards["},
        private_key=private_key,
    )
    _canonical2, signature_b64 = sign_field_map(
        ats_type="lever",
        version=1,
        schema="ats-field-map/v1",
        fields={"custom_question_prefix": "cards["},
        private_key=private_key,
    )
    public_key = private_key.public_key()
    # Real round-trip: the public key genuinely verifies the signature
    # over the exact canonical bytes -- not just "a function returned
    # without raising."
    public_key.verify(base64.b64decode(signature_b64), canonical.encode("utf-8"))


def test_sign_field_map_embeds_ats_type_version_schema_in_the_payload() -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    canonical, _ = sign_field_map(
        ats_type="lever",
        version=3,
        schema="ats-field-map/v1",
        fields={"custom_question_prefix": "cards["},
        private_key=private_key,
    )
    assert '"ats_type":"lever"' in canonical
    assert '"version":3' in canonical
    assert '"schema":"ats-field-map/v1"' in canonical


def test_sign_field_map_rejects_a_source_file_that_redefines_version() -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="version"):
        sign_field_map(
            ats_type="lever",
            version=3,
            schema="ats-field-map/v1",
            fields={"version": 99, "custom_question_prefix": "cards["},
            private_key=private_key,
        )


def test_sign_field_map_rejects_a_source_file_that_redefines_ats_type() -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(ValueError, match="ats_type"):
        sign_field_map(
            ats_type="lever",
            version=1,
            schema="ats-field-map/v1",
            fields={"ats_type": "greenhouse"},
            private_key=private_key,
        )


def test_load_private_key_prefers_the_env_var_over_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    real_key = ed25519.Ed25519PrivateKey.generate()
    real_b64 = base64.b64encode(
        real_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode()
    wrong_key_file = tmp_path / "wrong.b64"
    wrong_key_file.write_text(
        base64.b64encode(
            ed25519.Ed25519PrivateKey.generate().private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
        ).decode()
    )
    monkeypatch.setenv("ATS_FIELD_MAP_SIGNING_KEY", real_b64)

    loaded = load_private_key(key_file=wrong_key_file)

    loaded_bytes = loaded.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    real_bytes = real_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    assert loaded_bytes == real_bytes


def test_load_private_key_raises_a_clear_error_with_neither_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATS_FIELD_MAP_SIGNING_KEY", raising=False)
    with pytest.raises(SystemExit):
        load_private_key(key_file=None)


def test_generate_keypair_returns_a_real_usable_ed25519_pair() -> None:
    private_b64, public_b64 = generate_keypair()
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    public_key = ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    signature = private_key.sign(b"probe")
    public_key.verify(signature, b"probe")


class _FakeQueryBuilder:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def select(self, *_: Any, **__: Any) -> _FakeQueryBuilder:
        return self

    def eq(self, column: str, value: Any) -> _FakeQueryBuilder:
        return _FakeQueryBuilder([r for r in self._rows if r.get(column) == value])

    def order(self, _field: str, *, desc: bool = False) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(sorted(self._rows, key=lambda r: r["version"], reverse=desc))

    def limit(self, n: int) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._rows[:n])

    async def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._rows)


class _FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def table(self, _name: str) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._rows)


async def test_next_version_starts_at_one_for_a_new_ats_type() -> None:
    supabase = _FakeSupabase([])
    assert await next_version(supabase, "lever") == 1  # type: ignore[arg-type]


async def test_next_version_increments_past_the_highest_existing_version() -> None:
    supabase = _FakeSupabase(
        [{"ats_type": "lever", "version": 1}, {"ats_type": "lever", "version": 2}]
    )
    assert await next_version(supabase, "lever") == 3  # type: ignore[arg-type]


async def test_next_version_is_scoped_per_ats_type() -> None:
    supabase = _FakeSupabase([{"ats_type": "greenhouse", "version": 5}])
    assert await next_version(supabase, "lever") == 1  # type: ignore[arg-type]
