"""Which hosts are on this machine or its network, and what a name resolves to: the checks that keep a fetch from
being pointed at a private address (SSRF), whatever notation the address is written in."""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_LOCAL_NAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home.arpa",
    ".intranet",
)


def _wire_host(url: httpx.URL) -> str:
    """The host as the connection will use it: ASCII, lower case, no trailing dot."""
    return url.raw_host.decode("ascii").lower().rstrip(".")


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """An IPv4 address written the way ``inet_aton`` reads it: 1 to 4 parts, each decimal,
    ``0x`` hex or ``0`` octal (``2130706433``, ``127.1``, ``0x7f.0.0.1``, ``0177.0.0.1``)."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        try:
            if part[:2].lower() == "0x":
                numbers.append(int(part[2:], 16))
            elif len(part) > 1 and part.startswith("0"):
                numbers.append(int(part, 8))
            else:
                numbers.append(int(part, 10))
        except ValueError:
            return None
        if part.startswith(("+", "-")) or "_" in part:
            return None
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (5 - len(parts)):
        return None
    value = last
    for position, n in enumerate(head):
        value += n << (8 * (3 - position))
    return ipaddress.IPv4Address(value)


def _parse_ip_host(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address a host string stands for, in any notation a resolver accepts, else None."""
    bare = host.strip("[]")
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    return _legacy_ipv4(bare)


def _is_public_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Global unicast only; an IPv4 address inside an IPv6 one is judged as the IPv4 address."""
    if isinstance(address, ipaddress.IPv6Address):
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded is None and address.teredo is not None:
            embedded = address.teredo[1]
        if embedded is not None:
            return _is_public_address(embedded)
    return address.is_global and not address.is_multicast


def _is_private_host(host: str) -> bool:
    """True for a host that is, or may be, on this machine or its local network."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(_LOCAL_NAME_SUFFIXES):
        return True
    address = _parse_ip_host(host.split("%")[0])
    return address is not None and not _is_public_address(address)


async def _resolve_addresses(host: str) -> list[str]:
    """The addresses a name resolves to, looked up off the event loop."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})
