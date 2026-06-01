"""Watchdog alert state — edge-trigger de-dupe + digest date-gate.

A tiny JSON file (``~/.gits/watchdog_state.json``) remembering, per
metric, the last alert *level* that was emitted, plus the last date the
daily balance digest fired. This is what makes alerting **edge-triggered**:
a sustained condition alerts once (on the rising edge) and clears once
(on recovery), instead of spamming every tick (task [[jeyuxq]] AC-5/AC-7).

Persisted (not in-memory) so the watchdog survives launchd respawns
without re-alerting an already-known condition or re-sending the day's
digest.

Read-only with respect to the host; the only thing it mutates is its own
state file (atomic write).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Alert levels, ordered. "ok" is the cleared/baseline state.
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_CRITICAL = "critical"


class WatchdogState:
    """Persisted last-fired level per metric + last digest date."""

    def __init__(self, path: Path):
        self._path = path
        self._levels: dict[str, str] = {}
        self._last_digest_date: str = ""
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return
        if isinstance(data, dict):
            levels = data.get("levels")
            if isinstance(levels, dict):
                self._levels = {
                    str(k): str(v) for k, v in levels.items()
                }
            self._last_digest_date = str(data.get("last_digest_date") or "")

    def _save(self) -> None:
        payload = {
            "levels": self._levels,
            "last_digest_date": self._last_digest_date,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".watchdog_state.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp, self._path)
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        except OSError:
            logger.debug("watchdog state save failed", exc_info=True)

    def level(self, metric: str) -> str:
        """Last emitted level for ``metric`` (``"ok"`` if never tripped)."""
        return self._levels.get(metric, LEVEL_OK)

    def set_level(self, metric: str, level: str) -> None:
        """Record ``metric`` is now at ``level`` and persist."""
        if self._levels.get(metric, LEVEL_OK) == level:
            return
        if level == LEVEL_OK:
            self._levels.pop(metric, None)
        else:
            self._levels[metric] = level
        self._save()

    def last_digest_date(self) -> str:
        """ISO ``YYYY-MM-DD`` of the last digest, or ``""`` if never."""
        return self._last_digest_date

    def mark_digest_sent(self, date_iso: str) -> None:
        """Record the daily digest fired on ``date_iso`` and persist."""
        if self._last_digest_date == date_iso:
            return
        self._last_digest_date = date_iso
        self._save()
