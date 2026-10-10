"""An httpx transport that only connects to public addresses.

For downloads of files whose URL comes out of a provider's JSON (archival images, stock and
``ai_video`` clips): https alone does not stop a compromised or hostile source from pointing at
an https service on this machine or the local network, or at a cloud metadata address.

The check sits in the connection step, so it covers every hop the same way: the first URL and
each redirect target. The name is resolved once, every answer must be a global unicast address,
and the socket is then opened to that checked address (not to the name), so a DNS answer that
changes afterwards cannot move the connection. TLS still verifies the certificate against the
URL's host name. The address rules are the claim-ledger fetcher's (S1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpcore
import httpx

from hyperframes_mcp.util.hosts import _is_private_host, _resolve_addresses

Resolver = Callable[[str], Awaitable[list[str]]]


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolve, refuse non-public answers, connect to the checked address."""

    def __init__(
        self,
        inner: httpcore.AsyncNetworkBackend | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self._inner = inner or httpcore.AnyIOBackend()
        self._resolver = resolver or _resolve_addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await self._resolver(host)
        except OSError:
            addresses = []
        if not addresses:
            raise httpcore.ConnectError(f"cannot resolve host {host!r}")
        if any(_is_private_host(address) for address in addresses):
            raise httpcore.ConnectError(f"{host!r} resolves to a local or private address")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError(f"cannot connect to {host!r}")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def public_only_transport(
    *,
    resolver: Resolver | None = None,
    backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncHTTPTransport:
    """A transport whose connections go only to public addresses (see the module docstring).

    ``resolver`` and ``backend`` replace the DNS lookup and the socket layer, for tests.
    """
    transport = httpx.AsyncHTTPTransport()
    transport._pool._network_backend = _PublicOnlyBackend(backend, resolver)
    return transport
