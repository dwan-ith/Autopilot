"""SSRF guards for outbound connector calls with caller-supplied destinations.

Actions like ``webhook_callback`` accept a destination URL from mission payloads
(which may originate from LLM plans or unauthenticated-adjacent inputs). Without
a guard those calls can reach cloud metadata endpoints, loopback admin services,
or internal RFC1918 systems. Every address the hostname resolves to must be
globally routable before the request is allowed.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeDestinationError(ValueError):
    """Raised when an outbound destination is not a safe public HTTP(S) endpoint."""


def assert_public_http_url(url: str) -> str:
    """Validate *url* as an http(s) URL resolving only to global addresses.

    Returns the hostname on success. Raises :class:`UnsafeDestinationError` for
    non-http schemes, missing hosts, unresolvable names, and any resolved
    address that is loopback, private, link-local, reserved, or multicast.
    Resolving *every* address (not just the first) closes mixed-record tricks
    where a DNS name points at both a public and an internal IP. DNS-rebinding
    between this check and the connection remains possible in principle; the
    realistic vector (direct internal/metadata targets) is closed.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeDestinationError(
            f"scheme {parsed.scheme!r} not allowed (http/https only)"
        )
    host = parsed.hostname
    if not host:
        raise UnsafeDestinationError("URL has no hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeDestinationError(f"cannot resolve host {host!r}: {exc}") from exc
    if not infos:
        raise UnsafeDestinationError(f"host {host!r} resolved to no addresses")
    forbidden: set[str] = set()
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            forbidden.add(str(info[4][0]))
            continue
        if not addr.is_global:
            forbidden.add(str(addr))
    if forbidden:
        raise UnsafeDestinationError(
            f"host {host!r} resolves to protected address(es): {', '.join(sorted(forbidden))}"
        )
    return host
