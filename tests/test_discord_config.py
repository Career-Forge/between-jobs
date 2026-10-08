"""The Discord settings (api/discord_config.py): all-or-nothing like the Telegram bridge, checked
at boot, and an error that names the variable and never what was in it."""

from __future__ import annotations

import logging

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from discord_fakes import APPLICATION_ID, BOT_TOKEN, PRIVATE_KEY, PUBLIC_KEY_HEX, sign

from between_jobs.api.discord_config import load_discord_config
from between_jobs.api.env import ConfigurationError

_VARIABLES = (
    "DISCORD_APPLICATION_ID",
    "DISCORD_PUBLIC_KEY",
    "DISCORD_BOT_TOKEN",
    "DISCORD_INSTALL_URL",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _set(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_with_nothing_set_discord_is_off() -> None:
    assert load_discord_config() is None


def test_blank_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, DISCORD_APPLICATION_ID="  ", DISCORD_PUBLIC_KEY="", DISCORD_BOT_TOKEN=" \n")
    assert load_discord_config() is None


def test_the_application_id_and_public_key_turn_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, DISCORD_APPLICATION_ID=APPLICATION_ID, DISCORD_PUBLIC_KEY=PUBLIC_KEY_HEX)

    config = load_discord_config()

    assert config is not None
    assert config.application_id == APPLICATION_ID
    assert config.bot_token is None
    assert config.install_url == f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"
    # The key is the real thing: it verifies a signature made with the matching private key.
    config.public_key.verify(bytes.fromhex(sign(b"x", "1")), b"1x")


def test_a_bot_token_adds_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(
        monkeypatch,
        DISCORD_APPLICATION_ID=APPLICATION_ID,
        DISCORD_PUBLIC_KEY=PUBLIC_KEY_HEX.upper(),
        DISCORD_BOT_TOKEN=f"  {BOT_TOKEN}\n",
    )
    config = load_discord_config()
    assert config is not None and config.bot_token == BOT_TOKEN
    assert BOT_TOKEN not in repr(config)


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"DISCORD_PUBLIC_KEY": PUBLIC_KEY_HEX}, "DISCORD_APPLICATION_ID"),
        ({"DISCORD_APPLICATION_ID": APPLICATION_ID}, "DISCORD_PUBLIC_KEY"),
        ({"DISCORD_BOT_TOKEN": BOT_TOKEN}, "DISCORD_BOT_TOKEN"),
        (
            {"DISCORD_BOT_TOKEN": BOT_TOKEN, "DISCORD_PUBLIC_KEY": PUBLIC_KEY_HEX},
            "DISCORD_APPLICATION_ID",
        ),
    ],
)
def test_a_half_set_configuration_stops_the_boot_naming_the_variable_and_no_value(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], named: str
) -> None:
    _set(monkeypatch, **values)

    with pytest.raises(ConfigurationError) as caught:
        load_discord_config()

    assert named in str(caught.value)
    for value in values.values():
        assert value not in str(caught.value)


# The y coordinates (little-endian, sign bit cleared) of the points of order 1, 2, 4 and 8, as
# published for Ed25519, and the non-canonical spellings of y = 0 and y = 1.
_SMALL_ORDER_BASES = (
    "00" * 32,
    "01" + "00" * 31,
    "ec" + "ff" * 30 + "7f",
    "ed" + "ff" * 30 + "7f",
    "ee" + "ff" * 30 + "7f",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
)


def _with_sign_bit(encoded: str) -> str:
    return encoded[:-2] + format(int(encoded[-2:], 16) | 0x80, "02x")


_SMALL_ORDER_SPELLINGS = [*_SMALL_ORDER_BASES, *[_with_sign_bit(h) for h in _SMALL_ORDER_BASES]]


@pytest.mark.parametrize(
    ("application_id", "public_key", "token", "named"),
    [
        ("12ab", PUBLIC_KEY_HEX, None, "DISCORD_APPLICATION_ID"),
        ("123456789012345678x", PUBLIC_KEY_HEX, None, "DISCORD_APPLICATION_ID"),
        ("1234", PUBLIC_KEY_HEX, None, "DISCORD_APPLICATION_ID"),
        ("1" * 14, PUBLIC_KEY_HEX, None, "DISCORD_APPLICATION_ID"),
        ("1" * 26, PUBLIC_KEY_HEX, None, "DISCORD_APPLICATION_ID"),
        (APPLICATION_ID, PUBLIC_KEY_HEX[:-2], None, "DISCORD_PUBLIC_KEY"),
        (APPLICATION_ID, PUBLIC_KEY_HEX + "00", None, "DISCORD_PUBLIC_KEY"),
        (APPLICATION_ID, "zz" + PUBLIC_KEY_HEX[2:], None, "DISCORD_PUBLIC_KEY"),
        (
            APPLICATION_ID,
            " ".join([PUBLIC_KEY_HEX[:32], PUBLIC_KEY_HEX[32:]]),
            None,
            "DISCORD_PUBLIC_KEY",
        ),
        *[(APPLICATION_ID, key, None, "DISCORD_PUBLIC_KEY") for key in _SMALL_ORDER_SPELLINGS],
        (APPLICATION_ID, PUBLIC_KEY_HEX, "short", "DISCORD_BOT_TOKEN"),
        (APPLICATION_ID, PUBLIC_KEY_HEX, "has a space inside it 1234567890", "DISCORD_BOT_TOKEN"),
        (APPLICATION_ID, PUBLIC_KEY_HEX, '"' + BOT_TOKEN + '"', "DISCORD_BOT_TOKEN"),
    ],
)
def test_a_malformed_value_stops_the_boot_naming_only_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    application_id: str,
    public_key: str,
    token: str | None,
    named: str,
) -> None:
    _set(monkeypatch, DISCORD_APPLICATION_ID=application_id, DISCORD_PUBLIC_KEY=public_key)
    if token is not None:
        _set(monkeypatch, DISCORD_BOT_TOKEN=token)

    with pytest.raises(ConfigurationError) as caught:
        load_discord_config()

    message = str(caught.value)
    assert named in message
    for secret in (application_id, public_key, token):
        if secret:
            assert secret not in message


def _boot(monkeypatch: pytest.MonkeyPatch, install_url: str | None = None) -> None:
    _set(monkeypatch, DISCORD_APPLICATION_ID=APPLICATION_ID, DISCORD_PUBLIC_KEY=PUBLIC_KEY_HEX)
    if install_url is not None:
        monkeypatch.setenv("DISCORD_INSTALL_URL", install_url)


_DISCORD_PROVIDED = f"https://discord.com/oauth2/authorize?client_id={APPLICATION_ID}"


def test_unset_the_install_address_is_discords_own_link_for_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _boot(monkeypatch)
    config = load_discord_config()
    assert config is not None and config.install_url == _DISCORD_PROVIDED
    _boot(monkeypatch, "  \n")  # blank is unset
    config = load_discord_config()
    assert config is not None and config.install_url == _DISCORD_PROVIDED


@pytest.mark.parametrize(
    "link",
    [
        "https://discord.com/oauth2/authorize?client_id=1&scope=bot+applications.commands"
        "&integration_type=0",
        _DISCORD_PROVIDED,
        "https://example.com/add-the-app",
    ],
)
def test_an_install_address_is_taken_as_written_query_string_and_all(
    monkeypatch: pytest.MonkeyPatch, link: str
) -> None:
    _boot(monkeypatch, f"  {link}\n")
    config = load_discord_config()
    assert config is not None and config.install_url == link


def test_a_trailing_slash_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _boot(monkeypatch, "https://example.com/add-the-app/")
    config = load_discord_config()
    assert config is not None and config.install_url == "https://example.com/add-the-app"


@pytest.mark.parametrize(
    "link",
    [
        "https://discord.com/oauth2/authorize?client_id=1#section",
        "https://discord.com/oauth2/authorize#client_id=1",
        "https://user:pw@discord.com/oauth2/authorize?client_id=1",
        "https://discord.com/oauth2/authorize?client_id=1 &scope=bot",
        "https://discord.com/oauth2/authorize?client_id=1\u200b",
        "https://discord.com/oauth2/authorize?client_id=1\u202e",
        "https://discord.com/oauth2\\authorize?client_id=1",
        "http://discord.com/oauth2/authorize?client_id=1",
        "javascript:alert(1)",
        "https://?client_id=1",
        "https://discord.com:99999/oauth2/authorize?client_id=1",
    ],
)
def test_an_install_address_that_could_mislead_is_ignored_with_a_warning_that_does_not_echo_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, link: str
) -> None:
    _boot(monkeypatch, link)
    with caplog.at_level(logging.WARNING):
        config = load_discord_config()
    assert config is not None and config.install_url is None
    assert any("DISCORD_INSTALL_URL" in r.getMessage() for r in caplog.records)
    for fragment in ("client_id", "pw@", "javascript", "discord.com"):
        assert fragment not in caplog.text


def _small_order_encodings() -> set[str]:
    """Every 32-byte encoding of a point whose order divides 8, found by arithmetic on the curve
    rather than read from a list: multiplying random curve points by the prime group order lands
    in the 8-element subgroup of small-order points, and the subgroup is closed under addition."""
    p = 2**255 - 19
    d = -121665 * pow(121666, -1, p) % p
    order = 2**252 + 27742317777372353535851937790883648493

    def add(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
        t = d * a[0] * b[0] * a[1] * b[1] % p
        x = (a[0] * b[1] + b[0] * a[1]) * pow(1 + t, -1, p) % p
        y = (a[1] * b[1] + a[0] * b[0]) * pow(1 - t, -1, p) % p
        return x, y

    def multiply(point: tuple[int, int], n: int) -> tuple[int, int]:
        result, addend = (0, 1), point
        while n:
            if n & 1:
                result = add(result, addend)
            addend = add(addend, addend)
            n >>= 1
        return result

    def point_with_y(y: int) -> tuple[int, int] | None:
        x2 = (y * y - 1) * pow(d * y * y + 1, -1, p) % p
        x = pow(x2, (p + 3) // 8, p)
        if (x * x - x2) % p:
            x = x * pow(2, (p - 1) // 4, p) % p
        return (x, y) if (x * x - x2) % p == 0 else None

    torsion = {(0, 1)}
    for y in range(2, 60):
        point = point_with_y(y)
        if point is not None:
            torsion.add(multiply(point, order))
    for _ in range(3):  # close under addition
        torsion |= {add(a, b) for a in torsion for b in torsion}
    assert len(torsion) == 8

    encodings = set()
    for x, y in torsion:
        for spelled_y in (y, y + p):  # canonical, and non-canonical where it still fits in 255 bits
            if spelled_y < 2**255:
                for sign in {x & 1, 1 - (x & 1)} if x == 0 else {x & 1}:
                    raw = (spelled_y | (sign << 255)).to_bytes(32, "little")
                    encodings.add(raw.hex())
    return encodings


def test_the_list_of_refused_keys_is_every_small_order_point_there_is() -> None:
    derived = _small_order_encodings()
    assert derived <= set(_SMALL_ORDER_SPELLINGS)  # nothing derived is missing from the cases...
    assert len(derived) >= 8  # ...and the derivation did find the points (identity, 2, 4, 8)


def test_a_freshly_generated_real_key_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in range(50):
        real = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        _set(monkeypatch, DISCORD_APPLICATION_ID=APPLICATION_ID, DISCORD_PUBLIC_KEY=real)
        assert load_discord_config() is not None


def test_the_private_half_of_the_test_key_is_not_what_the_config_holds() -> None:
    assert PRIVATE_KEY.public_key().public_bytes_raw().hex() == PUBLIC_KEY_HEX
