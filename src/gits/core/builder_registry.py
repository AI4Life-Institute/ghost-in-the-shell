"""BuilderRegistry (G1) — ghost-owned builder ticket registry (0002 §5.1).

The authoritative consumer registry for builder-os tickets, ``~/.gits/
builder_tickets.json``, UID-keyed:

    {
      "builder-os:17": {
        "runtime_dir": "<abs>",
        "event_log": "<abs>",
        "channel_id": "...",
        "driver_session_id": "drv-...",
        "capability_token": "...",
        "assistant_channel_id": "..."
      }
    }

This — not ``SessionBinding`` — is what :class:`BuilderEventMonitor` iterates,
which sidesteps the forward-compat hazard entirely (F3: ``_binding_from_dict``
silently drops unknown fields, so nothing load-bearing may live in extended
binding fields).

**Paths are stored absolute, resolved once at registration** against the
configured ``BUILDER_OS_ROOT`` (``settings.builder_os_root``) — a global process
must not depend on cwd-relative paths (M2). Repo-relative remains the rule
*inside* builder-os records; the ghost-side registry is the resolution boundary.

Writing (``register``/``unregister``) is exercised by G6/T8 (``/bos start``);
for T6 it is covered by unit tests. The registry is read every poll by the
monitor, so reads are cheap and tolerant of a missing/corrupt file (→ empty).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..utils.atomic_write import atomic_write_json
from ..utils.lock import credential_lock

logger = logging.getLogger(__name__)

# How long a register/unregister waits for the cross-process registry mutex
# before giving up (fail-closed — a stuck lock must not hang a Discord handler).
_REGISTRY_LOCK_TIMEOUT_S = 10.0

# Fields ghost knows about. Unknown keys in a stored record are preserved on
# read (forward-compat) but not required.
_KNOWN_FIELDS = (
    "runtime_dir",
    "event_log",
    "channel_id",
    "driver_session_id",
    "capability_token",
    "assistant_channel_id",
)


@dataclass(frozen=True)
class BuilderTicket:
    """One registry entry (0002 §5.1). ``uid`` is the map key, not stored in-value."""

    uid: str
    runtime_dir: str
    event_log: str
    channel_id: str | None = None
    driver_session_id: str | None = None
    capability_token: str | None = None
    assistant_channel_id: str | None = None

    @classmethod
    def from_dict(cls, uid: str, data: dict) -> BuilderTicket:
        """Build from a stored record, ignoring unknown keys (forward-compat)."""
        return cls(
            uid=uid,
            runtime_dir=data.get("runtime_dir", ""),
            event_log=data.get("event_log", ""),
            channel_id=data.get("channel_id"),
            driver_session_id=data.get("driver_session_id"),
            capability_token=data.get("capability_token"),
            assistant_channel_id=data.get("assistant_channel_id"),
        )


class BuilderRegistry:
    """Read/write access to ``~/.gits/builder_tickets.json`` (0002 §5.1)."""

    def __init__(self, registry_file: Path, builder_os_root: Path | None = None):
        self._file = registry_file
        # Resolution boundary for repo-relative builder-os paths (M2).
        self._root = builder_os_root.expanduser() if builder_os_root else None
        # Cross-process mutex for the register/unregister read-modify-write (B3):
        # the daemon and any concurrent `gits` CLI serialize on this, matching the
        # credential-vault idiom. A sibling lockfile, never the registry itself.
        self._lock_file = registry_file.with_name(registry_file.name + ".lock")
        # Last-observed integrity fault (minor): distinguishes a *corrupt* file
        # (surfaced) from a *missing* one (dormant, silent). Updated on every read.
        self._corrupt: str | None = None

    # -- reads --------------------------------------------------------------

    def _read_raw(self) -> dict:
        """Return the parsed registry dict, or ``{}`` if absent/corrupt.

        A missing file is the dormant default (zero builder tickets ⇒ the
        monitor is a no-op). A corrupt file is still treated as empty so a single
        bad write can never crash the poll loop — but the corruption is recorded
        in ``self._corrupt`` so the monitor can surface an explicit builder-global
        fault rather than silently pretending zero tickets (minor).
        """
        if not self._file.exists():
            self._corrupt = None
            return {}
        try:
            data = json.loads(self._file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read builder registry %s: %s", self._file, exc)
            self._corrupt = f"{self._file} is unreadable/corrupt: {exc}"
            return {}
        if not isinstance(data, dict):
            logger.error("%s is not a JSON object — corrupt", self._file)
            self._corrupt = f"{self._file} is not a JSON object"
            return {}
        self._corrupt = None
        return data

    def integrity_fault(self) -> str | None:
        """Return a fault message if the registry file exists but is corrupt,
        else ``None`` (absent = dormant, valid = healthy). One cheap read; the
        monitor calls this each poll to surface corruption explicitly (minor)."""
        self._read_raw()
        return self._corrupt

    def list_tickets(self) -> list[BuilderTicket]:
        """All registered tickets (empty list if the registry is absent)."""
        return [BuilderTicket.from_dict(uid, rec) for uid, rec in self._read_raw().items()
                if isinstance(rec, dict)]

    def get(self, uid: str) -> BuilderTicket | None:
        rec = self._read_raw().get(uid)
        if not isinstance(rec, dict):
            return None
        return BuilderTicket.from_dict(uid, rec)

    def exists(self) -> bool:
        """True if the registry file is present (used by the dormancy fast-path)."""
        return self._file.exists()

    # -- writes (G6/T8) -----------------------------------------------------

    def _resolve(self, path: str) -> str:
        """Resolve a possibly repo-relative builder-os path to an absolute string.

        Absolute inputs pass through (still normalized). Relative inputs resolve
        against ``BUILDER_OS_ROOT``; without a configured root a relative path is
        resolved against cwd as a last resort and a warning is logged — callers
        (T8) should always configure the root.
        """
        p = Path(path).expanduser()
        if not p.is_absolute():
            if self._root is not None:
                p = self._root / p
            else:
                logger.warning(
                    "builder_os_root unset; resolving relative registry path %r "
                    "against cwd — configure BUILDER_OS_ROOT", path,
                )
                p = p.resolve()
        return str(p)

    async def register(
        self,
        uid: str,
        *,
        runtime_dir: str,
        event_log: str,
        channel_id: str | None = None,
        driver_session_id: str | None = None,
        capability_token: str | None = None,
        assistant_channel_id: str | None = None,
    ) -> BuilderTicket:
        """Register (or replace) a ticket. Paths are resolved absolute here (M2).

        The read-modify-write is serialized under a cross-process file mutex (B3)
        so a concurrent register/unregister (e.g. the monitor's disposition
        unregister racing a ``/bos start``) can't lose a write.
        """
        record = {
            "runtime_dir": self._resolve(runtime_dir),
            "event_log": self._resolve(event_log),
            "channel_id": channel_id,
            "driver_session_id": driver_session_id,
            "capability_token": capability_token,
            "assistant_channel_id": assistant_channel_id,
        }
        # Drop None-valued optional fields to keep the file minimal (paths stay).
        _keep = ("runtime_dir", "event_log")
        record = {k: v for k, v in record.items() if v is not None or k in _keep}
        async with credential_lock(self._lock_file, timeout=_REGISTRY_LOCK_TIMEOUT_S):
            data = self._read_raw()
            data[uid] = record
            await atomic_write_json(self._file, data)
        logger.info("Registered builder ticket %s (event_log=%s)", uid, record["event_log"])
        return BuilderTicket.from_dict(uid, record)

    async def unregister(self, uid: str) -> BuilderTicket | None:
        """Remove a ticket. Returns the removed entry or None. RMW under the same
        cross-process mutex as :meth:`register` (B3)."""
        async with credential_lock(self._lock_file, timeout=_REGISTRY_LOCK_TIMEOUT_S):
            data = self._read_raw()
            rec = data.pop(uid, None)
            if rec is None:
                return None
            await atomic_write_json(self._file, data)
        logger.info("Unregistered builder ticket %s", uid)
        return BuilderTicket.from_dict(uid, rec) if isinstance(rec, dict) else None
