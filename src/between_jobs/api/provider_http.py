"""Outbound calls to a model provider that are not themselves a completion: checking a key,
listing models, exchanging an OAuth code.

Every one of them goes through `fetch_json`, which holds four guarantees the callers lean on:

- THE DESTINATION IS CHECKED FIRST (`assert_safe_destination`). The address is built from the
  registry (provider_registry.py), never taken from a request, so this is the second lock, not
  the first: https only, the standard port, no userinfo, a real DNS name rather than an IP or
  `localhost`, and every address it resolves to must be a public one. A name that resolves into
  a private, loopback or link-local range is refused.
- NO REDIRECT IS FOLLOWED. A 3xx is an answer, and it is reported as a failure of the provider.
  A provider has no business sending a request that carries a key somewhere else.
- THE ANSWER IS BOUNDED, in bytes (a model list can be megabytes) and in time (one limit for
  the whole call, resolving the name included, not per read). It is read as a stream and
  abandoned the moment it passes the limit; the limit applies after decompression, so a
  compressed bomb is stopped too. A completion, which the OpenAI SDK reads, gets the same byte
  bound (after decompression) from `bound_response`, an httpx response hook.
- NOTHING OF THE PROVIDER'S ANSWER IS ECHOED. What comes back is a status, a parsed body for
  the caller to read fields out of, and a few headers; an error body can quote the key, so
  callers compare what they read to fixed strings and never print it.

Failures are raised as `UpstreamFailure` with a short `reason` token from the same vocabulary
`llm_client` classifies a completion's failure with.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from .errors import ApiError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
"""The most of an answer that is read. Real model lists are well under 1 MB; this is the
bound, not the expectation."""

CALL_TIMEOUT_SECONDS = 20.0
"""The most one call may take in all, connecting and reading included."""

CONNECT_TIMEOUT_SECONDS = 6.0

_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
_NUMERIC_LABEL = re.compile(r"[0-9]+|0[xX][0-9a-fA-F]*")
"""A host whose last label looks like this is an IPv4 address in one of the short or hex forms
(`127.1`, `0x7f.1`, `2130706433`) that URL parsers and `getaddrinfo` accept and `ipaddress`
does not. No real top-level domain is numeric, so a name that ends this way is refused here
rather than left for the resolver to turn into an address."""


class ResponseTooLarge(Exception):
    """An answer that passed `MAX_RESPONSE_BYTES`. Raised from inside the HTTP client;
    `llm_client.generate` finds it on the chain of the SDK's connection error and reports
    `response_too_large`."""


class UpstreamFailure(Exception):
    """A call that did not produce an answer. `reason` is a `llm_client.ProviderReason`."""

    def __init__(self, reason: Literal["timeout", "unreachable", "response_too_large"]) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class UpstreamAnswer:
    status: int
    body: Any
    """The parsed JSON, or None when the body was empty or not JSON."""
    headers: Mapping[str, str]


# -- the destination -------------------------------------------------------------------------


def check_destination_shape(url: str) -> str:
    """The host of `url` if it is a destination this server may call, or ApiError. The shape
    only; `assert_safe_destination` adds the DNS check."""
    refusal = ApiError(
        "INVALID_INPUT",
        "That address is not one this server will call.",
        details={"reason": "base_url_refused"},
    )
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise refusal from e
    host = parts.hostname
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or not host.isascii()
        or host.endswith(".")
        or "." not in host
        or host == "localhost"
        or host.endswith(_LOCAL_SUFFIXES)
        or _NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1])
    ):
        raise refusal
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host.lower()
    raise refusal  # an IP literal, whatever range it is in


async def bound_response(response: Any) -> None:
    """An httpx `response` event hook for a client the OpenAI SDK reads through. It refuses a
    response whose declared length is over `MAX_RESPONSE_BYTES`, and makes the read of any other
    stop with `ResponseTooLarge` once the DECODED body passes it. It runs after the headers and
    before the body is read, so nothing past the limit is buffered. (Typed `Any`: the SDK may sit
    on a different httpx package than this module's, and only `headers` and `aiter_bytes` are
    used.)

    The SDK reads a non-streaming answer with `response.aread()`, which iterates
    `response.aiter_bytes()`; shadowing that one method on this response is how the read is
    bounded without replacing the client's transport (and its proxy settings). A test reads a
    compressed body through a real client so a change in httpx that stops this from working
    fails there."""
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge
    unbounded = response.aiter_bytes

    async def bounded(chunk_size: int | None = None) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in unbounded(chunk_size):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ResponseTooLarge
            yield chunk

    response.__dict__["aiter_bytes"] = bounded


async def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. A seam: tests replace it, and nothing else calls DNS."""
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return [str(info[4][0]) for info in infos]


async def assert_safe_destination(url: str) -> None:
    """Raises ApiError unless `url` is https to a DNS name that resolves only to public
    addresses. Not a guard against a DNS answer that changes between this check and the
    connection; the addresses it is used with are the registry's, whose owners are the
    providers themselves."""
    host = check_destination_shape(url)
    try:
        addresses = await _resolve(host)
    except OSError as e:
        raise UpstreamFailure("unreachable") from e
    if not addresses:
        raise UpstreamFailure("unreachable")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as e:
            raise UpstreamFailure("unreachable") from e
        # `is_global` is false for private, loopback, link-local, carrier-grade NAT, reserved
        # and documentation ranges (multicast is judged global, so it is refused on its own);
        # an IPv4-mapped IPv6 address is judged by the IPv4 inside.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise ApiError(
                "INVALID_INPUT",
                "That address is not one this server will call.",
                details={"reason": "base_url_refused"},
            )


# -- the call ----------------------------------------------------------------------------------


_KEPT_HEADERS = ("retry-after",)


async def fetch_json(
    http: httpx.AsyncClient,
    method: Literal["GET", "POST"],
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None = None,
    json_body: Any = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    timeout_seconds: float = CALL_TIMEOUT_SECONDS,
) -> UpstreamAnswer:
    """One bounded call. Raises ApiError for a refused destination and UpstreamFailure for a
    call that got no usable answer; any other status is returned for the caller to judge."""
    timeout = httpx.Timeout(timeout_seconds, connect=CONNECT_TIMEOUT_SECONDS)
    try:
        async with asyncio.timeout(timeout_seconds):
            # Inside the limit: a stalled resolver must not outlast it.
            await assert_safe_destination(url)
            async with http.stream(
                method,
                url,
                headers=dict(headers),
                params=dict(params) if params else None,
                json=json_body,
                timeout=timeout,
                follow_redirects=False,
            ) as response:
                data = await _read_bounded(response, max_bytes)
                status = response.status_code
                kept = {
                    name: response.headers[name]
                    for name in _KEPT_HEADERS
                    if name in response.headers
                }
    except TimeoutError as e:
        raise UpstreamFailure("timeout") from e
    except httpx.TimeoutException as e:
        raise UpstreamFailure("timeout") from e
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise UpstreamFailure("unreachable") from e
    return UpstreamAnswer(status, _parse(data), kept)


async def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise UpstreamFailure("response_too_large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise UpstreamFailure("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(data: bytes) -> Any:
    if not data:
        return None
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        return None
