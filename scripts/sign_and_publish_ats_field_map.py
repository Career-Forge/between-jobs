"""Browser extension E3c (browser-extension.md) -- generates the Ed25519
signing keypair (one-time), and signs + publishes a curated ATS field map
to the `ats_field_maps` table.

Unlike `import_geo_gazetteer.py`/`import_company_tiers.py`, this script
isn't porting a bulk reference dataset out of the frozen n8n repo -- it's
authoring and cryptographically signing between-jobs' own first-party
curated data. Per this project's own CLAUDE.md ("Hosted-service
internals... ATS selector maps... belong to the hosted services, not
this repo"), BOTH the curated source JSON (`--source`) and the Ed25519
private key (`--key-file`, or the `ATS_FIELD_MAP_SIGNING_KEY` env var)
live OUTSIDE this repo entirely -- wherever the maintainer keeps them
(a local file, a password manager export, `between-jobs-private`). This
script never hardcodes either path and never writes the private key
anywhere but the `--key-out` path the maintainer explicitly names.

The running FastAPI backend never reads the private key at all --
`src/between_jobs/api/ats_field_maps.py` only ever reads already-signed
rows this script wrote. That's deliberate: a compromise of the always-on
service can't forge a new valid map, matching the threat model D4 exists
for (a compromised or malicious intermediary between authoring and the
extension, not the legitimate backend itself).

Usage:

    # One-time: generate a keypair. Move the private-key file somewhere
    # secure (a password manager, an encrypted volume) immediately --
    # this script never touches it again after writing it once.
    python scripts/sign_and_publish_ats_field_map.py generate-key \\
        --key-out /somewhere/outside/this/repo/ats_field_map_signing_key.b64

    # Paste the printed public key into
    # extension/lib/ats-field-map.ts's KNOWN_PUBLIC_KEYS, under the same
    # signing_key_id passed to `publish` below.

    # Sign and publish a curated map (re-runnable; each run appends the
    # next version for that ats_type, never updates or deletes a row):
    python scripts/sign_and_publish_ats_field_map.py publish \\
        --source /somewhere/outside/this/repo/lever_field_map.json \\
        --ats-type lever \\
        --schema ats-field-map/v1 \\
        --signing-key-id prod-2026-09 \\
        --key-file /somewhere/outside/this/repo/ats_field_map_signing_key.b64 \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv

from between_jobs.api.supabase_client import create_supabase_client
from supabase import AsyncClient

_SIGNING_KEY_ENV_VAR = "ATS_FIELD_MAP_SIGNING_KEY"


def generate_keypair() -> tuple[str, str]:
    """Returns (private_key_b64, public_key_b64) -- both the 32-byte raw
    Ed25519 key halves, base64-encoded. The public half has no secrecy
    requirement (it's meant to be committed as a source constant in the
    extension); the private half must never be committed or logged
    anywhere this function's own caller doesn't explicitly write it."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_bytes).decode(), base64.b64encode(public_bytes).decode()


def load_private_key(*, key_file: Path | None) -> Ed25519PrivateKey:
    """Prefers the environment variable over `--key-file` when both are
    set -- an env var is easier to keep out of shell history/scrollback
    (e.g. sourced from a password manager's own CLI) than a bare file
    path typed on the command line, so it's the preferred path, not just
    a fallback."""
    raw_b64 = os.environ.get(_SIGNING_KEY_ENV_VAR)
    if raw_b64 is None:
        if key_file is None:
            raise SystemExit(
                f"No signing key: set {_SIGNING_KEY_ENV_VAR} or pass --key-file. "
                "Neither the key nor its source file may live inside this repo."
            )
        raw_b64 = key_file.read_text().strip()
    return ed25519.Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw_b64))


def canonicalize(payload: dict[str, Any]) -> str:
    """Sorted keys, no extraneous whitespace -- deterministic byte output
    so re-signing the identical logical content always produces the
    identical signed bytes, and so this is unambiguous about exactly
    which bytes get signed. Stored and served verbatim afterward; never
    re-derived from the parsed object again, on either side."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


_RESERVED_PAYLOAD_KEYS = frozenset({"ats_type", "version", "schema"})


def sign_field_map(
    *,
    ats_type: str,
    version: int,
    schema: str,
    fields: dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> tuple[str, str]:
    """Returns (payload_canonical, signature_b64). `ats_type`/`version`/
    `schema` are embedded INSIDE the signed payload itself, not left as
    unsigned DB metadata alongside it -- otherwise a compromised
    intermediary could serve an old, valid (payload, signature) pair
    under a different `version`/`ats_type` label than the one that was
    actually signed, since the DB columns wouldn't be cryptographically
    bound to the content. The extension re-checks this binding itself
    after verifying (`verifyAndParseFieldMap`), the same defense-in-depth
    precedent `fillCustomTextAnswer` already established for this
    codebase.

    Adversarially-confirmed gap: merging `fields` last used to let a
    curated source file that happened to define its own top-level
    `ats_type`/`version`/`schema` key (e.g. a "version" field describing
    Lever's own markup revision, not this map's) silently win the dict
    merge and get signed instead of the actual CLI-computed values --
    the DB row would then claim a version the signed payload doesn't
    actually contain, which is exactly the mismatch the extension's own
    binding check exists to catch, just now firing on an honest publish
    instead of an attack. Rejected explicitly instead."""
    collision = _RESERVED_PAYLOAD_KEYS & fields.keys()
    if collision:
        raise ValueError(
            f"--source JSON must not define {sorted(collision)} -- these come from "
            "--ats-type/the next version number/--schema and would silently override them."
        )
    payload = {"ats_type": ats_type, "version": version, "schema": schema, **fields}
    canonical = canonicalize(payload)
    signature = private_key.sign(canonical.encode("utf-8"))
    return canonical, base64.b64encode(signature).decode()


async def next_version(supabase: AsyncClient, ats_type: str) -> int:
    result = (
        await supabase.table("ats_field_maps")
        .select("version")
        .eq("ats_type", ats_type)
        .order("version", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return 1
    row = cast("dict[str, Any]", result.data[0])
    return int(row["version"]) + 1


async def publish(
    supabase: AsyncClient,
    *,
    source_path: Path,
    ats_type: str,
    schema: str,
    signing_key_id: str,
    private_key: Ed25519PrivateKey,
    dry_run: bool,
) -> dict[str, Any]:
    with source_path.open() as f:
        fields = json.load(f)

    version = await next_version(supabase, ats_type)
    payload_canonical, signature_b64 = sign_field_map(
        ats_type=ats_type,
        version=version,
        schema=schema,
        fields=fields,
        private_key=private_key,
    )
    row: dict[str, Any] = {
        "ats_type": ats_type,
        "version": version,
        "schema": schema,
        "payload_canonical": payload_canonical,
        "signature_b64": signature_b64,
        "signing_key_id": signing_key_id,
    }
    if not dry_run:
        await supabase.table("ats_field_maps").insert(row).execute()
    return row


async def _main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate-key", help="Generate a new Ed25519 signing keypair (one-time)."
    )
    generate_parser.add_argument(
        "--key-out",
        type=Path,
        required=True,
        help="Where to write the base64 private key. Must be OUTSIDE this repo.",
    )

    publish_parser = subparsers.add_parser(
        "publish", help="Sign and publish a curated field map as the next version."
    )
    publish_parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the curated field-map JSON (the fields to sign). Never this repo's own tree.",
    )
    publish_parser.add_argument("--ats-type", required=True)
    publish_parser.add_argument("--schema", required=True, help='e.g. "ats-field-map/v1"')
    publish_parser.add_argument(
        "--signing-key-id",
        required=True,
        help="Must match a key registered in extension/lib/ats-field-map.ts's KNOWN_PUBLIC_KEYS.",
    )
    publish_parser.add_argument(
        "--key-file",
        type=Path,
        default=None,
        help=f"Path to the base64 private key. Ignored if {_SIGNING_KEY_ENV_VAR} is set.",
    )
    publish_parser.add_argument(
        "--dry-run", action="store_true", help="Sign and print, but don't write to Supabase."
    )

    args = parser.parse_args()

    if args.command == "generate-key":
        private_key_b64, public_key_b64 = generate_keypair()
        # Adversarially-confirmed gap: `Path.write_text` creates the file
        # at the OS default mode (subject only to umask, typically 644 --
        # world/group readable) with no window in which to chmod it
        # afterward, since ANY window at all is real exposure for the
        # one secret D4's whole signing model depends on. Opening with
        # an explicit 0o600 mode means the file is never readable by
        # anyone but the current user, from the instant it's created.
        fd = os.open(args.key_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, private_key_b64.encode())
        finally:
            os.close(fd)
        print(f"Private key written to {args.key_out} (mode 0600) -- move it somewhere secure now.")
        print(f"Public key (paste into extension/lib/ats-field-map.ts):\n  {public_key_b64}")
        return

    private_key = load_private_key(key_file=args.key_file)
    supabase, _url = await create_supabase_client()
    row = await publish(
        supabase,
        source_path=args.source,
        ats_type=args.ats_type,
        schema=args.schema,
        signing_key_id=args.signing_key_id,
        private_key=private_key,
        dry_run=args.dry_run,
    )
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(
        f"{prefix}published {row['ats_type']} version {row['version']} (schema {row['schema']!r})"
    )


if __name__ == "__main__":
    asyncio.run(_main())
