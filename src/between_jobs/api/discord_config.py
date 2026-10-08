"""The Discord app's settings, read once at boot and refused loudly when they are wrong.

Three variables, with the same all-or-nothing rule as the Telegram bridge:

- `DISCORD_APPLICATION_ID` and `DISCORD_PUBLIC_KEY` together turn the interactions endpoint on.
  The application id is the Discord application's snowflake (digits); the public key is the
  application's Ed25519 verification key, shown in the developer portal as 64 hexadecimal
  characters (32 bytes). Every request to the endpoint is checked against it before anything else
  happens, so a server with no key cannot answer one, and a wrong key would refuse every real
  interaction while looking healthy: that is why a malformed key stops the boot.
- `DISCORD_BOT_TOKEN` is optional and only adds: it lets the server open a direct message and send
  into it (pushes such as the high-fit alert, and the way a reply is still delivered when an
  interaction's own 15-minute token has expired). Without it the commands work in full and those
  two things do not happen.
- `DISCORD_INSTALL_URL` is cosmetic: the https address people open to add the app to their
  account or a server, which the web app's Integrations page links to. Left empty it is Discord's
  own install link for the application (`https://discord.com/oauth2/authorize?client_id=<id>`);
  set it only for a custom link. It may carry a query string, as every such link does. A value
  that is not an https address (or has credentials, a fragment or invisible characters) is
  ignored with a warning rather than stopping the boot, and the value is never put in the log.

One of the first two without the other, or a bot token without them, is a mistake and not "off":
the boot stops with a message that names the variables and never a value. None set means Discord
is off: the endpoint answers 404 FEATURE_DISABLED and a link code for Discord is not minted.

A public key that is a placeholder is refused too. The 14 encodings of the curve's small-order
points (all zeros is the common one) load as keys, and with one of them a signature that anybody
can write is valid for some requests, so the endpoint would turn real traffic away and let forged
requests in. Every request is checked against the key loaded here and nowhere else, which is why
this is the one place that refuses them.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .env import is_https_link, optional_env, refuse

logger = logging.getLogger(__name__)

_APPLICATION_ID = re.compile(r"[0-9]{15,25}")
_PUBLIC_KEY = re.compile(r"[0-9a-fA-F]{64}")
_BOT_TOKEN = re.compile(r"[A-Za-z0-9._\-]{20,200}")

# Ed25519 points of order 1, 2, 4 and 8, as the y coordinate in little-endian bytes with the sign
# bit cleared, plus the non-canonical spellings of y = 0 and y = 1 (y = p and y = p + 1, where
# p = 2**255 - 19). With any of them as the public key some signature (R = the identity, S = 0,
# for one) is valid for many messages. Clearing the sign bit of a key before the lookup covers
# the 14 encodings these seven stand for.
_SMALL_ORDER_KEYS = frozenset(
    bytes.fromhex(encoded)
    for encoded in (
        "00" * 32,
        "01" + "00" * 31,
        "ec" + "ff" * 30 + "7f",
        "ed" + "ff" * 30 + "7f",
        "ee" + "ff" * 30 + "7f",
        "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
        "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    )
)
_DEFAULT_INSTALL_URL = "https://discord.com/oauth2/authorize?client_id={application_id}"


@dataclass(frozen=True)
class DiscordConfig:
    application_id: str
    public_key: Ed25519PublicKey
    bot_token: str | None = None
    install_url: str | None = None

    def __repr__(self) -> str:
        # Never the token.
        return (
            f"DiscordConfig(application_id={self.application_id!r}, "
            f"bot={self.bot_token is not None})"
        )


def _clean(raw: str | None) -> str | None:
    """A setting's value without the whitespace a copy-paste leaves around it, or None when it is
    unset or blank."""
    value = raw.strip() if raw is not None else ""
    return value or None


def load_discord_config() -> DiscordConfig | None:
    """The settings, or None when Discord is off. Raises `ConfigurationError` (through `refuse`)
    for a half-set or malformed configuration."""
    application_id = _clean(optional_env("DISCORD_APPLICATION_ID"))
    public_key = _clean(optional_env("DISCORD_PUBLIC_KEY"))
    bot_token = _clean(optional_env("DISCORD_BOT_TOKEN"))

    if application_id is None and public_key is None:
        if bot_token is not None:
            refuse(
                "DISCORD_BOT_TOKEN is set but DISCORD_APPLICATION_ID and DISCORD_PUBLIC_KEY "
                "are not; set all three, or leave all three unset to run without Discord"
            )
        return None
    if application_id is None:
        refuse("DISCORD_PUBLIC_KEY is set but DISCORD_APPLICATION_ID is not; set both or neither")
    if public_key is None:
        refuse("DISCORD_APPLICATION_ID is set but DISCORD_PUBLIC_KEY is not; set both or neither")

    if _APPLICATION_ID.fullmatch(application_id) is None:
        refuse("DISCORD_APPLICATION_ID must be the application's id (digits only)")
    if _PUBLIC_KEY.fullmatch(public_key) is None:
        refuse(
            "DISCORD_PUBLIC_KEY must be 64 hexadecimal characters: the application's public key "
            "from the Discord developer portal"
        )
    raw_key = bytes.fromhex(public_key)
    if raw_key[:31] + bytes([raw_key[31] & 0x7F]) in _SMALL_ORDER_KEYS:
        refuse("DISCORD_PUBLIC_KEY is not a usable Ed25519 public key")
    try:
        key = Ed25519PublicKey.from_public_bytes(raw_key)
    except ValueError:
        refuse("DISCORD_PUBLIC_KEY is not a usable Ed25519 public key")
    if bot_token is not None and _BOT_TOKEN.fullmatch(bot_token) is None:
        refuse("DISCORD_BOT_TOKEN does not look like a bot token")

    install_url = _clean(optional_env("DISCORD_INSTALL_URL"))
    if install_url is None:
        # Discord's own link for the application; the id was checked to be digits just above.
        install_url = _DEFAULT_INSTALL_URL.format(application_id=application_id)
    elif not is_https_link(install_url):
        logger.warning(
            "DISCORD_INSTALL_URL is not an https address that can be linked; ignoring it"
        )
        install_url = None
    return DiscordConfig(
        application_id=application_id,
        public_key=key,
        bot_token=bot_token,
        install_url=install_url.rstrip("/") if install_url else None,
    )
