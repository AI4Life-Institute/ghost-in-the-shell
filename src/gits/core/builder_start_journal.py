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
common recovery case — reuses the same request key and therefore the same token.

**Cross-form retries** (``issue:10`` then ``issue:10 repo:builder-os``) key
differently, so the request key alone is not enough: a naive retry would mint a
NEW token, and because admit persists the FIRST token's hash and never overwrites
it, the human would be permanently unauthorized. This is closed by
:meth:`reconcile` — called immediately after admit resolves the canonical uid, it
binds every request entry to the **earliest** token already recorded for that uid
(insertion order = admit order), so a cross-form retry reuses the original token.
The registry write happens with the reconciled token. (Irreducible residual: a
crash in the single ``await`` between admit *returning* and :meth:`reconcile`
persisting the uid binding — orders of magnitude smaller than the admit→register
window this closes, and impossible to eliminate without a transactional admit.)

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

    async def reconcile(self, key: str, ticket_uid: str, current_token: str) -> str:
        """Bind *key* to the canonical *ticket_uid* and return the **authoritative**
        token for it — the earliest one already recorded for this uid across any
        request form (insertion order = admit order), else *current_token*.

        Called immediately after admit resolves the uid. This is what makes a
        cross-form retry safe: attempt 1 (``issue:10``) admits token A and stamps
        its uid before crashing at the registry write; attempt 2
        (``issue:10 repo:builder-os``) mints token B, admits (builder-os keeps
        A's hash), then reconciles → finds attempt 1's A bound to this uid →
        returns A, which is what gets registered. The human stays authorized.
        """
        async with credential_lock(self._lock_file, timeout=_LOCK_TIMEOUT_S):
            data = self._read()
            authoritative = None
            for entry in data.values():  # dict preserves insertion (=admit) order
                if (isinstance(entry, dict) and entry.get("ticket_uid") == ticket_uid
                        and entry.get("token")):
                    authoritative = entry["token"]
                    break
            if authoritative is None:
                authoritative = current_token
            cur = data.get(key)
            if not isinstance(cur, dict):
                cur = {}
            cur["token"] = authoritative
            cur["ticket_uid"] = ticket_uid
            data[key] = cur
            await atomic_write_json(self._file, data)
            if authoritative != current_token:
                logger.info(
                    "start journal: reconciled %s to the original token for %s "
                    "(cross-form retry — crash-safe)", key, ticket_uid)
            return authoritative

    async def clear(self, key: str, *, ticket_uid: str | None = None) -> None:
        """Drop the entry once the registry write has committed (window closed).

        Also drops every other entry bound to *ticket_uid* (a cross-form retry
        leaves a stale sibling entry that must not leak a token), and is safe to
        call on a definitive admit failure to avoid a stale-token leak."""
        async with credential_lock(self._lock_file, timeout=_LOCK_TIMEOUT_S):
            data = self._read()
            changed = data.pop(key, None) is not None
            if ticket_uid is not None:
                for k in [k for k, e in data.items()
                          if isinstance(e, dict) and e.get("ticket_uid") == ticket_uid]:
                    data.pop(k, None)
                    changed = True
            if changed:
                await atomic_write_json(self._file, data)
                logger.debug("start journal: cleared %s (uid=%s)", key, ticket_uid)

    def token_for(self, key: str) -> str | None:
        """Non-locking peek (for tests / diagnostics)."""
        entry = self._read().get(key)
        return entry.get("token") if isinstance(entry, dict) else None
