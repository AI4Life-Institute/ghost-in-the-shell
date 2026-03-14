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
    created_at: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S")
    )


class SessionManager:
    """Manage channel <-> tmux window bindings with JSON persistence."""

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self._bindings: dict[str, SessionBinding] = {}  # channel_id -> binding
        self._load()

    def _load(self) -> None:
        """Load state from JSON file."""
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            for channel_id, binding_data in data.get("bindings", {}).items():
                self._bindings[channel_id] = SessionBinding(**binding_data)
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    async def _save(self) -> None:
        """Persist state to JSON file atomically."""
        data = {"bindings": {cid: asdict(b) for cid, b in self._bindings.items()}}
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
        return self._bindings.get(channel_id)

    def get_binding_by_window(self, window_id: str) -> SessionBinding | None:
        """Find binding by tmux window ID."""
        for b in self._bindings.values():
            if b.window_id == window_id:
                return b
        return None

    def list_bindings(self) -> list[SessionBinding]:
        """List all active bindings."""
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

    async def update_cli_session_id_by_window(
        self, window_name: str, session_id: str
    ) -> None:
        """Update CLI session ID by window name (called from Hook)."""
        for b in self._bindings.values():
            if b.window_name == window_name:
                b.cli_session_id = session_id
                await self._save()
                return
