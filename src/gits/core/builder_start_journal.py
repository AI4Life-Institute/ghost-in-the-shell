"""BuilderStartJournal (B2) — crash-safe capability-token durability for ``/bos start``.

The failure this closes (codex B2)
-----------------------------------
``/bos start`` mints a per-ticket capability token, calls ``builder-os ticket
admit`` (which persists ``sha256(token)`` — **only if absent**, admit.py's
self-heal guard), then writes the token into the ghost registry. A crash *after*
admit but *before* the registry write leaves the hash persisted builder-os-side
with the registry never written. A naive retry mints a **new** token; admit is
idempotent and will **not** overwrite the already-present hash, so the registry
ends up holding a token whose hash builder-os never stored → every later human
``driver respond`` is rejected as unauthorized, permanently.

The fix
-------
Record the minted token in a durable ghost-owned journal **before** calling
``admit``. A retry of the same start reuses the journalled token, so admit's
persisted hash and the registry's token always agree. The entry is cleared once
the registry write commits (the crash window is closed).

Keying
------
Keyed by the **start request** (``<repo-or-blank>#<issue>``), not the canonical
ticket uid: ghost cannot resolve builder-os's default repo alias pre-admit
without reading contract material (§5.8 forbids contract knowledge in ghost), and
the canonical uid is only known *after* admit returns. An identical retry — the
real recovery case — reuses the same request key and therefore the same token.
The canonical uid is stored on the entry once known, for observability and to let
a caller reconcile. (Residual: two *different* command forms for the same ticket
— e.g. ``issue:5`` vs ``issue:5 repo:x`` — key differently; the post-admit
registry idempotency check catches an already-registered ticket, so the only
uncovered sliver is a cross-form retry inside the admit→register crash window.)

Durability
----------
Same idiom as the registry: an atomic write under the cross-process
``credential_lock`` mutex, so a daemon and a concurrent CLI can't corrupt it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from ..utils.atomic_write import atomic_write_json
from ..utils.lock import credential_lock

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT_S = 10.0


def request_key(repo: str | None, issue: int) -> str:
    """The journal key for a ``/bos start`` request (stable across retries)."""
    return f"{repo or ''}#{issue}"


class BuilderStartJournal:
    """Durable ``request_key → {token, ticket_uid?}`` record for in-flight starts."""

    def __init__(self, journal_file: Path):
        self._file = journal_file
        self._lock_file = journal_file.with_name(journal_file.name + ".lock")

    def _read(self) -> dict:
        if not self._file.exists():
            return {}
        try:
            import json
            data = json.loads(self._file.read_text())
        except (ValueError, OSError):
            logger.warning("start journal %s unreadable — treating as empty",
                           self._file, exc_info=True)
            return {}
        return data if isinstance(data, dict) else {}

    async def get_or_create_token(
        self, key: str, mint: Callable[[], str],
    ) -> str:
        """Return the token journalled for *key*, minting + persisting one (via
        *mint*) if none exists yet. The read-mint-write is atomic under the mutex
        so two concurrent starts of the same request can't mint two tokens."""
        async with credential_lock(self._lock_file, timeout=_LOCK_TIMEOUT_S):
            data = self._read()
            entry = data.get(key)
            if isinstance(entry, dict) and entry.get("token"):
                return entry["token"]
            token = mint()
            data[key] = {"token": token}
            await atomic_write_json(self._file, data)
            logger.info("start journal: minted token for %s (crash-safe)", key)
            return token

    async def mark_admitted(self, key: str, ticket_uid: str) -> None:
        """Annotate the entry with the canonical uid once admit resolves it."""
        async with credential_lock(self._lock_file, timeout=_LOCK_TIMEOUT_S):
            data = self._read()
            entry = data.get(key)
            if isinstance(entry, dict):
                entry["ticket_uid"] = ticket_uid
                await atomic_write_json(self._file, data)

    async def clear(self, key: str) -> None:
        """Drop the entry once the registry write has committed (window closed)."""
        async with credential_lock(self._lock_file, timeout=_LOCK_TIMEOUT_S):
            data = self._read()
            if data.pop(key, None) is not None:
                await atomic_write_json(self._file, data)
                logger.debug("start journal: cleared %s (committed)", key)

    def token_for(self, key: str) -> str | None:
        """Non-locking peek (for tests / diagnostics)."""
        entry = self._read().get(key)
        return entry.get("token") if isinstance(entry, dict) else None
