"""Stale-on-error DNS resolver for the Discord adapter's aiohttp connector.

Why this exists
---------------
This host resolves DNS through Tailscale MagicDNS (``100.100.100.100``).
That proxy intermittently stalls for a few seconds-to-minutes (netmap
updates, sleep/wake, key refresh), during which *every* ``getaddrinfo``
on the box returns ``EAI_NONAME`` ("nodename nor servname provided").
When that happens while discord.py is (re)connecting, the gateway/REST
lookup for ``gateway-*.discord.gg`` fails with ``ClientConnectorDNSError``
and the bot drops off Discord until the proxy recovers — silently losing
any messages sent during the gap.

Connectivity itself is fine during these stalls (the failure is DNS, not
TCP), and Discord's gateway/CDN live on stable Cloudflare anycast IPs.
So we wrap aiohttp's default resolver with a cache that serves the
last-known-good answer whenever a live lookup fails. The bot then connects
straight through a DNS blip instead of disconnecting.

This is intentionally app-level only — it does not touch the host's
Tailscale/DNS configuration.
"""

from __future__ import annotations

import logging
import socket
import time
from typing import TYPE_CHECKING

from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

if TYPE_CHECKING:
    from aiohttp.abc import ResolveResult

logger = logging.getLogger(__name__)

# How long a successful answer is considered fresh. Within this window the
# cached result is returned without a live lookup; past it we attempt a live
# lookup but still fall back to the (stale) cached answer if that lookup fails.
_FRESH_TTL = 60.0


class StaleOnErrorResolver(AbstractResolver):
    """Delegate to the default resolver, caching the last good answer per host.

    On a live-resolution failure, returns the cached answer if one exists
    (serve-stale), otherwise re-raises. This keeps the Discord connection
    alive through transient DNS-proxy stalls without masking a genuinely
    unresolvable host (which never populates the cache).
    """

    def __init__(self, inner: AbstractResolver | None = None) -> None:
        self._inner = inner or DefaultResolver()
        # key -> (monotonic timestamp, results)
        self._cache: dict[tuple[str, int, int], tuple[float, list[ResolveResult]]] = {}

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        key = (host, port, family)
        cached = self._cache.get(key)

        # Serve a fresh cached answer without hitting the resolver at all.
        if cached is not None and (time.monotonic() - cached[0]) < _FRESH_TTL:
            return cached[1]

        try:
            results = await self._inner.resolve(host, port, family)
        except OSError as exc:
            if cached is not None:
                age = time.monotonic() - cached[0]
                logger.warning(
                    "DNS resolve failed for %s (%s); serving cached answer "
                    "(%.0fs old) to ride out the stall",
                    host,
                    exc,
                    age,
                )
                return cached[1]
            # Nothing cached — propagate so callers see the real failure.
            raise

        self._cache[key] = (time.monotonic(), results)
        return results

    async def close(self) -> None:
        await self._inner.close()
