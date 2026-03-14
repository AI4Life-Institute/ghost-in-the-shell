"""TmuxController — tmux session/window management via libtmux.

Based on ccbot tmux_manager.py, adapted for Ghost in the Shell.
All blocking libtmux / subprocess calls are wrapped in ``asyncio.to_thread()``
so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from dataclasses import dataclass

import libtmux

from ..utils.security import SENSITIVE_ENV_VARS

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """Information about a tmux window."""

    window_id: str
    name: str
    cwd: str
    is_active: bool = False


class TmuxController:
    """tmux session controller — all operations async via libtmux.

    Every libtmux / subprocess call is wrapped in ``asyncio.to_thread()``
    so the event loop is never blocked.
    """

    def __init__(self, session_name: str = "gits"):
        self.session_name = session_name
        self._server: libtmux.Server | None = None
        self._session: libtmux.Session | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _get_server(self) -> libtmux.Server:
        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def _get_or_create_session(self) -> libtmux.Session:
        server = self._get_server()
        # Try to find existing session
        for s in server.sessions:
            if s.name == self.session_name:
                self._session = s
                return s
        # Create new session
        self._session = server.new_session(
            session_name=self.session_name,
            attach=False,
        )
        # Scrub sensitive env vars from the fresh session
        self._scrub_env(self._session)
        return self._session

    async def ensure_session(self) -> None:
        """Ensure the tmux session exists, create if needed."""
        await asyncio.to_thread(self._get_or_create_session)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def is_server_alive(self) -> bool:
        """Check if tmux server is running."""
        try:
            server = self._get_server()
            await asyncio.to_thread(lambda: len(server.sessions))
            return True
        except Exception:
            self._server = None
            self._session = None
            return False

    async def is_session_alive(self) -> bool:
        """Check if our session still exists."""
        try:
            server = await asyncio.to_thread(self._get_server)
            return any(s.name == self.session_name for s in server.sessions)
        except Exception:
            return False

    async def window_exists(self, window_id: str) -> bool:
        """Check if a specific window exists."""
        try:
            session = await asyncio.to_thread(self._get_or_create_session)
            return any(w.id == window_id for w in session.windows)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Window Management
    # ------------------------------------------------------------------

    async def list_windows(self) -> list[WindowInfo]:
        """List all windows in our session."""
        return await asyncio.to_thread(self._list_windows_sync)

    def _list_windows_sync(self) -> list[WindowInfo]:
        session = self._get_or_create_session()
        result: list[WindowInfo] = []
        for w in session.windows:
            pane = w.active_pane
            cwd = pane.current_path if pane else ""
            result.append(
                WindowInfo(
                    window_id=w.id or "",
                    name=w.name or "",
                    cwd=cwd or "",
                    is_active=w == session.active_window,
                )
            )
        return result

    async def create_window(
        self,
        name: str,
        cwd: str,
        command: str | None = None,
    ) -> WindowInfo:
        """Create a new window, optionally run a command."""
        return await asyncio.to_thread(self._create_window_sync, name, cwd, command)

    def _create_window_sync(
        self,
        name: str,
        cwd: str,
        command: str | None = None,
    ) -> WindowInfo:
        session = self._get_or_create_session()
        w = session.new_window(
            window_name=name,
            start_directory=cwd,
            attach=False,
        )
        # Unset CLAUDECODE so coding CLIs don't think they're nested
        pane = w.active_pane
        if pane:
            pane.send_keys("unset CLAUDECODE", enter=True)
            time.sleep(0.3)
            if command:
                pane.send_keys(command, enter=True)
        return WindowInfo(
            window_id=w.id or "",
            name=w.name or "",
            cwd=cwd,
        )

    async def kill_window(self, window_id: str) -> bool:
        """Kill a window by ID.  Returns True if found and killed."""
        return await asyncio.to_thread(self._kill_window_sync, window_id)

    def _kill_window_sync(self, window_id: str) -> bool:
        session = self._get_or_create_session()
        for w in session.windows:
            if w.id == window_id:
                w.kill()
                return True
        return False

    async def find_window_by_name(self, name: str) -> WindowInfo | None:
        """Find a window by name."""
        windows = await self.list_windows()
        for w in windows:
            if w.name == name:
                return w
        return None

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    async def send_text(self, window_id: str, text: str, enter: bool = True) -> None:
        """Send text to a window's active pane.

        Special handling (mirrors ccbot behaviour):

        * Regular text: ``literal=True`` + 500 ms delay + Enter
        * ``!command``: send ``!`` first, wait 1 s, then send the rest
          (triggers Claude Code bash mode).
        """
        await asyncio.to_thread(self._send_text_sync, window_id, text, enter)

    def _send_text_sync(self, window_id: str, text: str, enter: bool) -> None:
        pane = self._find_pane(window_id)
        if pane is None:
            raise ValueError(f"Window {window_id} not found")

        if text.startswith("!") and len(text) > 1:
            # Bang command: send ! first, wait, then rest
            pane.send_keys("!", literal=True, enter=False)
            time.sleep(1.0)
            pane.send_keys(text[1:], literal=True, enter=False)
            if enter:
                time.sleep(0.3)
                pane.send_keys("Enter", literal=False)
        else:
            pane.send_keys(text, literal=True, enter=False)
            if enter:
                time.sleep(0.5)
                pane.send_keys("Enter", literal=False)

    async def send_keys(self, window_id: str, keys: str) -> None:
        """Send special keys (Escape, C-c, Up, Down, Enter, etc.)."""
        await asyncio.to_thread(self._send_keys_sync, window_id, keys)

    def _send_keys_sync(self, window_id: str, keys: str) -> None:
        pane = self._find_pane(window_id)
        if pane is None:
            raise ValueError(f"Window {window_id} not found")
        pane.send_keys(keys, literal=False)

    # ------------------------------------------------------------------
    # Output Capture
    # ------------------------------------------------------------------

    async def capture_pane_text(self, window_id: str) -> str:
        """Capture pane content as plain text."""
        return await asyncio.to_thread(self._capture_text_sync, window_id)

    def _capture_text_sync(self, window_id: str) -> str:
        pane = self._find_pane(window_id)
        if pane is None:
            return ""
        lines = pane.capture_pane()
        return "\n".join(lines) if isinstance(lines, list) else str(lines)

    async def capture_pane_ansi(self, window_id: str) -> str:
        """Capture pane content with ANSI escape codes.

        Uses ``subprocess`` because libtmux's ``capture_pane`` does not
        expose the ``-e`` flag needed for ANSI colour output.
        """
        return await asyncio.to_thread(self._capture_ansi_sync, window_id)

    def _capture_ansi_sync(self, window_id: str) -> str:
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-e", "-p", "-t", window_id],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    # ------------------------------------------------------------------
    # Environment Variable Scrubbing
    # ------------------------------------------------------------------

    def _scrub_env(self, session: libtmux.Session) -> None:
        """Remove sensitive environment variables from a tmux session.

        This prevents coding CLIs running inside the session from
        accidentally reading bot tokens or API keys that were present in
        the parent process environment.
        """
        for var in SENSITIVE_ENV_VARS:
            try:
                session.set_environment(var, "")
                session.remove_environment(var)
            except libtmux.exc.LibTmuxException:
                pass
            except Exception:
                logger.debug("Failed to scrub env var %s", var, exc_info=True)

    async def scrub_env(self) -> None:
        """Async wrapper: scrub sensitive env vars from current session."""
        session = await asyncio.to_thread(self._get_or_create_session)
        await asyncio.to_thread(self._scrub_env, session)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pane(self, window_id: str) -> libtmux.Pane | None:
        """Find the active pane of a window by window ID."""
        session = self._get_or_create_session()
        for w in session.windows:
            if w.id == window_id:
                return w.active_pane
        return None
