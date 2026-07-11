"""BuilderHumans (G4 actor map) — Discord id → human-builder resolution (0002 §5.6/§11.4).

The response adapter (:mod:`gits.core.builder_response`) must never write a human
decision under an unverified identity. §11.4 is explicit: the actor is derived
from the authenticated Discord identity via an org binding, **fail closed** —
an unmapped id is refused, never falls back to the OS user (a lesson ghost's
butler identity path already paid for once).

For the MVP the binding lives in a **ghost-local** file,
``~/.gits/builder_humans.json``::

    { "<discord_user_id>": "<human_builder_id>" }

kept outside the org schema on purpose (PM ruling, task rkqwq6): the eventual
home is a ``discord_user_id`` field on the org node, but adding it now would
touch the org schema + lint for no MVP gain. This file is machine config,
created at activation; it is never seeded in the repo.

**Dormant + fail-closed by default.** No file ⇒ every lookup returns ``None`` ⇒
the adapter refuses with an "unmapped identity" card and writes nothing. A
corrupt file is treated as empty (logged) so a single bad edit can only ever
*deny*, never grant.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class BuilderHumans:
    """Read-only Discord-id → human-builder-id resolver (fail-closed)."""

    def __init__(self, humans_file: Path):
        self._file = humans_file

    def _read_raw(self) -> dict:
        """Parsed map, or ``{}`` if absent/corrupt (the fail-closed default)."""
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning(
                "Failed to read %s — treating as empty (fail-closed)",
                self._file, exc_info=True,
            )
            return {}
        if not isinstance(data, dict):
            logger.warning("%s is not a JSON object — treating as empty", self._file)
            return {}
        return data

    def resolve(self, discord_user_id: str | None) -> str | None:
        """Return the mapped human-builder id, or ``None`` if unmapped.

        ``None`` (unmapped, blank, or absent map) is the refusal signal — the
        caller must NOT proceed. The stored value is only trusted when it is a
        non-empty string; any other shape resolves to ``None``.
        """
        if not discord_user_id:
            return None
        value = self._read_raw().get(str(discord_user_id))
        if isinstance(value, str) and value.strip():
            return value
        return None
