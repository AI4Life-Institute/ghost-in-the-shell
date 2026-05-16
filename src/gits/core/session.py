"""SessionManager — channel/thread <-> tmux window binding manager.

Persists state to ~/.gits/state.json using atomic writes.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..utils.atomic_write import atomic_write_json


@dataclass
class SessionBinding:
    """A binding between a chat channel/thread and a tmux window."""

    platform: str  # "discord" | "telegram"
    channel_id: str  # Discord channel or thread ID
    window_id: str  # tmux window ID (e.g., "@1")
    window_name: str  # tmux window name
    work_dir: str  # Working directory path
    coding_cli: str = "claude"  # "claude" | "codex" | "opencode" | custom
    cli_session_id: str | None = None  # Coding CLI session ID for resume
    parent_channel_id: str | None = None  # For threads: parent channel ID
    subdir: str | None = None  # For threads: optional subdirectory
    permission_mode: str | None = None  # e.g. "bypassPermissions", "auto"
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    )
    last_active_at: float = field(default_factory=time.time)
    suspended: bool = False
    # Per openspec change ``add-multi-account-hotswap`` (Phase 0.4):
    # ``None`` → legacy/back-compat path, claude reads ~/.claude/.
    # A name → the launcher injects ``CLAUDE_CONFIG_DIR=$HOME/.claude-{name}``.
    claude_account: str | None = None
    # Set when a per-binding switch_account abort/respawn failure leaves the
    # binding in an unrecoverable state. Surfaced in ``gits account list``.
    respawn_failed: bool = False
    # Timestamp (epoch seconds) of the first non-empty user forward to this
    # binding's tmux pane.  Transient (not persisted to state.json) — used by
    # JsonlMonitor to gate the "session not found" warning until the user has
    # actually interacted (claude does not flush its <sid>.jsonl until then,
    # so any earlier alarm is a false positive).
    first_interaction_at: float | None = None


def _binding_to_dict(b: SessionBinding) -> dict:
    """Serialize a SessionBinding, omitting fields that default to None/False.

    Keeps state.json diffs minimal for unmigrated installs and avoids
    populating the file with the new account-management fields when they
    are not in use.
    """
    data = asdict(b)
    if data.get("claude_account") is None:
        data.pop("claude_account", None)
    if data.get("respawn_failed") is False:
        data.pop("respawn_failed", None)
    # first_interaction_at is a transient in-memory signal — never persist it.
    data.pop("first_interaction_at", None)
    return data


def _binding_from_dict(data: dict) -> SessionBinding:
    """Construct a SessionBinding from a JSON dict, ignoring unknown keys.

    Forward-compat: future fields not yet known to this version of ghost
    are silently dropped rather than raising :class:`TypeError`.
    """
    known = SessionBinding.__dataclass_fields__.keys()
    filtered = {k: v for k, v in data.items() if k in known}
    return SessionBinding(**filtered)


class SessionManager:
    """Manage channel <-> tmux window bindings with JSON persistence."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self._bindings: dict[str, SessionBinding] = {}  # channel_id -> binding
        self._last_mtime: float = 0.0  # track file modifications
        self._load()

    def _load(self) -> None:
        """Load state from JSON file."""
        if not self.state_file.exists():
            return
        try:
            self._last_mtime = self.state_file.stat().st_mtime
            with open(self.state_file) as f:
                data = json.load(f)
            self._bindings.clear()
            for channel_id, binding_data in data.get("bindings", {}).items():
                self._bindings[channel_id] = _binding_from_dict(binding_data)
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _maybe_reload(self) -> None:
        """Reload state if the file was modified externally."""
        try:
            if self.state_file.exists():
                mtime = self.state_file.stat().st_mtime
                if mtime > self._last_mtime:
                    self._load()
        except OSError:
            pass

    async def _save(self) -> None:
        """Persist state to JSON file atomically."""
        data = {
            "bindings": {
                cid: _binding_to_dict(b) for cid, b in self._bindings.items()
            }
        }
        await atomic_write_json(self.state_file, data)

    async def bind(
        self,
        platform: str,
        channel_id: str,
        window_id: str,
        window_name: str,
        work_dir: str,
        coding_cli: str = "claude",
        cli_session_id: str | None = None,
        parent_channel_id: str | None = None,
        subdir: str | None = None,
        permission_mode: str | None = None,
        claude_account: str | None = None,
    ) -> SessionBinding:
        """Create or update a binding."""
        binding = SessionBinding(
            platform=platform,
            channel_id=channel_id,
            window_id=window_id,
            window_name=window_name,
            work_dir=work_dir,
            coding_cli=coding_cli,
            cli_session_id=cli_session_id,
            parent_channel_id=parent_channel_id,
            subdir=subdir,
            permission_mode=permission_mode,
            claude_account=claude_account,
        )
        self._bindings[channel_id] = binding
        await self._save()
        return binding

    async def unbind(self, channel_id: str) -> SessionBinding | None:
        """Remove a binding. Returns the removed binding or None."""
        binding = self._bindings.pop(channel_id, None)
        if binding:
            await self._save()
        return binding

    def get_binding(self, channel_id: str) -> SessionBinding | None:
        """Get binding for a channel/thread."""
        self._maybe_reload()
        return self._bindings.get(channel_id)

    def get_binding_by_window(self, window_id: str) -> SessionBinding | None:
        """Find binding by tmux window ID."""
        self._maybe_reload()
        for b in self._bindings.values():
            if b.window_id == window_id:
                return b
        return None

    def list_bindings(self) -> list[SessionBinding]:
        """List all active bindings."""
        self._maybe_reload()
        return list(self._bindings.values())

    def list_channel_threads(self, parent_channel_id: str) -> list[SessionBinding]:
        """List all thread bindings for a parent channel."""
        return [
            b
            for b in self._bindings.values()
            if b.parent_channel_id == parent_channel_id
        ]

    async def update_window_id(self, channel_id: str, new_window_id: str) -> None:
        """Update window ID (after tmux recovery, IDs change)."""
        binding = self._bindings.get(channel_id)
        if binding:
            binding.window_id = new_window_id
            await self._save()

    async def update_cli_session_id(self, channel_id: str, session_id: str) -> None:
        """Update CLI session ID (set by Hook on CLI startup)."""
        binding = self._bindings.get(channel_id)
        if binding:
            binding.cli_session_id = session_id
            await self._save()

    async def touch_active(self, channel_id: str) -> None:
        """Update last_active_at and clear suspended flag."""
        binding = self._bindings.get(channel_id)
        if binding:
            binding.last_active_at = time.time()
            binding.suspended = False
            await self._save()

    async def mark_first_interaction(self, channel_id: str) -> None:
        """Record the first non-empty user forward to this binding's pane.

        Idempotent: only writes when currently unset.  Transient (in-memory
        only) — never persisted to ``state.json``.  Read by JsonlMonitor to
        gate the "session not found" warning, since claude does not flush its
        ``<sid>.jsonl`` until the user actually interacts.
        """
        binding = self._bindings.get(channel_id)
        if binding is not None and binding.first_interaction_at is None:
            binding.first_interaction_at = time.time()

    async def mark_suspended(self, channel_id: str) -> None:
        """Mark a binding as suspended (claude process killed)."""
        binding = self._bindings.get(channel_id)
        if binding:
            binding.suspended = True
            await self._save()

    async def update_cli_session_id_by_window(
        self, window_name: str, session_id: str
    ) -> None:
        """Update CLI session ID by window name (called from Hook)."""
        for b in self._bindings.values():
            if b.window_name == window_name:
                b.cli_session_id = session_id
                await self._save()
                return

    async def update_claude_account(
        self, channel_id: str, claude_account: str | None
    ) -> SessionBinding | None:
        """Update a binding's ``claude_account`` field (used by switch_account)."""
        binding = self._bindings.get(channel_id)
        if binding is None:
            return None
        binding.claude_account = claude_account
        await self._save()
        return binding

    async def mark_respawn_failed(self, channel_id: str, failed: bool = True) -> None:
        """Set/clear ``respawn_failed`` on a binding (used by switch_account)."""
        binding = self._bindings.get(channel_id)
        if binding is not None:
            binding.respawn_failed = failed
            await self._save()

    async def migrate_legacy_bindings(self, target_account: str) -> int:
        """Set ``claude_account = target_account`` for every binding currently None.

        Per spec ``Capture-Current Auto-Migrates Bindings``: invoked at the
        end of ``gits account add <name> --capture-current`` to prevent the
        "dual-source" hazard where pre-existing bindings would keep writing
        to ``~/.claude/projects/`` while the new account directory holds a
        full copy.

        Bindings are NOT respawned — the new ``claude_account`` takes effect
        on the next natural respawn (HealthMonitor recovery, manual switch,
        binding restart). Returns the number of migrated bindings.
        """
        migrated = 0
        for binding in self._bindings.values():
            if binding.claude_account is None:
                binding.claude_account = target_account
                migrated += 1
        if migrated:
            await self._save()
        return migrated
