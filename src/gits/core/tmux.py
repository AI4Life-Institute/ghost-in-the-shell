"""TmuxController — tmux session/window management via libtmux.

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
        def _sync() -> bool:
            session = self._get_or_create_session()
            return any(w.id == window_id for w in self._safe_windows(session))
        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            return False

    async def pane_pid(self, window_id: str) -> int | None:
        """Return the PID of the shell process running in a window's active pane.

        Returns None if the window does not exist or the PID cannot be read.
        """
        def _sync() -> int | None:
            pane = self._find_pane(window_id)
            if pane is None:
                return None
            try:
                result = pane.cmd("display-message", "-p", "#{pane_pid}")
                pid_str = (result.stdout[0] if result.stdout else "").strip()
                return int(pid_str) if pid_str.isdigit() else None
            except Exception:
                return None
        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            return None

    async def pane_current_command(self, window_id: str) -> str:
        """Return the foreground command running in a window's active pane.

        Returns an empty string if the window does not exist.
        Uses tmux's #{pane_current_command} format variable.
        """
        def _sync() -> str:
            pane = self._find_pane(window_id)
            if pane is None:
                return ""
            try:
                result = pane.cmd("display-message", "-p", "#{pane_current_command}")
                return (result.stdout[0] if result.stdout else "").strip()
            except Exception:
                return ""
        try:
            return await asyncio.to_thread(_sync)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Window Management
    # ------------------------------------------------------------------

    async def list_windows(self) -> list[WindowInfo]:
        """List all windows in our session."""
        return await asyncio.to_thread(self._list_windows_sync)

    def _list_windows_sync(self) -> list[WindowInfo]:
        session = self._get_or_create_session()
        result: list[WindowInfo] = []
        for w in self._safe_windows(session):
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
        try:
            w = session.new_window(
                window_name=name,
                start_directory=cwd,
                attach=False,
            )
        except ValueError as e:
            # libtmux parses `tmux list-windows -F ...` line-by-line; an
            # existing window whose name contains \n / \t / \r / \0 spans
            # multiple stdout lines and trips `zip(..., strict=True)`.
            # Scrub names of existing windows and retry once.
            if "zip()" not in str(e) and "shorter than argument" not in str(e):
                raise
            scrubbed = self._scrub_window_names()
            logger.warning(
                "tmux.new_window hit libtmux parse error; scrubbed %d window name(s) and retrying",
                scrubbed,
            )
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
        for w in self._safe_windows(session):
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

    async def send_text(
        self,
        window_id: str,
        text: str,
        enter: bool = True,
        submit_keys: str = "Enter",
    ) -> None:
        """Send text to a window's active pane.

        Special handling:

        * Regular text: ``literal=True`` + 500 ms delay + submit
        * ``!command``: send ``!`` first, wait 1 s, then send the rest
          (triggers Claude Code bash mode).

        ``submit_keys`` controls how input is submitted:
        * ``"Enter"`` — bare Enter (Claude Code)
        * ``"Escape Enter"`` — Escape then Enter (Codex CLI, Copilot CLI)
        """
        await asyncio.to_thread(
            self._send_text_sync, window_id, text, enter, submit_keys
        )

    def _send_text_sync(
        self, window_id: str, text: str, enter: bool, submit_keys: str = "Enter"
    ) -> None:
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
                self._send_submit_keys(pane, submit_keys)
        else:
            pane.send_keys(text, literal=True, enter=False)
            if enter:
                time.sleep(0.5)
                self._send_submit_keys(pane, submit_keys)

    @staticmethod
    def _send_submit_keys(pane: libtmux.Pane, submit_keys: str) -> None:
        """Send the submit key sequence to a pane.

        Supports multi-key sequences like "Escape Enter" (space-separated).
        """
        for key in submit_keys.split():
            pane.send_keys(key, literal=False, enter=False)
            time.sleep(0.2)

    async def send_keys(self, window_id: str, keys: str) -> None:
        """Send special keys (Escape, C-c, Up, Down, Enter, etc.)."""
        await asyncio.to_thread(self._send_keys_sync, window_id, keys)

    def _send_keys_sync(self, window_id: str, keys: str) -> None:
        pane = self._find_pane(window_id)
        if pane is None:
            raise ValueError(f"Window {window_id} not found")
        pane.send_keys(keys, literal=False, enter=False)

    # ------------------------------------------------------------------
    # Output Capture
    # ------------------------------------------------------------------

    async def capture_pane_text(self, window_id: str) -> str:
        """Capture pane content as plain text.

        Uses a direct subprocess call to avoid libtmux enumerating all
        session windows (which runs a heavy ``tmux list-panes`` with every
        format variable for each window).
        """
        def _sync() -> str:
            try:
                result = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-t", window_id],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return result.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return ""
        return await asyncio.to_thread(_sync)

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
    # Window Name Scrubbing
    # ------------------------------------------------------------------

    _BAD_NAME_CHARS = "\n\t\r\0"

    def _scrub_window_names(self) -> int:
        """Rename any windows whose names contain chars that break libtmux.

        libtmux's ``fetch_objs`` parses ``tmux list-windows -F ...`` output
        line-by-line, so a window name containing ``\\n``/``\\t``/``\\r``/``\\0``
        splits one record across multiple lines and trips ``zip(..., strict=True)``.
        We bypass libtmux entirely here: read window IDs via subprocess
        (``#{window_id}`` is always single-line safe), query each window's
        name with ``display-message``, and rename any offenders.

        Returns the number of windows renamed.
        """
        renamed = 0
        try:
            list_proc = subprocess.run(
                ["tmux", "list-windows", "-t", self.session_name, "-F", "#{window_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if list_proc.returncode != 0:
                return 0
            ids = [line for line in list_proc.stdout.splitlines() if line.startswith("@")]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0

        for wid in ids:
            try:
                name_proc = subprocess.run(
                    ["tmux", "display-message", "-t", wid, "-p", "#{window_name}"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            if name_proc.returncode != 0:
                continue
            # display-message always appends one trailing \n; strip just that one.
            raw = name_proc.stdout
            if raw.endswith("\n"):
                raw = raw[:-1]
            if not any(ch in raw for ch in self._BAD_NAME_CHARS):
                continue
            safe = raw.translate(
                str.maketrans({c: " " for c in self._BAD_NAME_CHARS})
            ).strip()[:80] or "window"
            try:
                subprocess.run(
                    ["tmux", "rename-window", "-t", wid, safe],
                    check=False,
                    timeout=3,
                )
                renamed += 1
                logger.warning(
                    "tmux: window %s had control chars in name; renamed to %r",
                    wid, safe,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        return renamed

    async def scrub_window_names(self) -> int:
        """Async wrapper; safe to call on startup."""
        return await asyncio.to_thread(self._scrub_window_names)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_pane(self, window_id: str) -> libtmux.Pane | None:
        """Find the active pane of a window by window ID."""
        session = self._get_or_create_session()
        for w in self._safe_windows(session):
            if w.id == window_id:
                return w.active_pane
        return None

    def _safe_windows(self, session: libtmux.Session) -> list[libtmux.Window]:
        """Return ``session.windows`` with one retry if libtmux's parser dies.

        ``session.windows`` runs ``tmux list-windows -F ...`` and parses
        stdout line-by-line; a window name with ``\\n``/``\\t``/``\\r``/``\\0``
        splits one record across multiple lines and crashes the parse
        with ``ValueError: zip() argument 2 is shorter than argument 1``.
        Scrub offending names and retry.
        """
        try:
            return list(session.windows)
        except ValueError as e:
            if "zip()" not in str(e) and "shorter than argument" not in str(e):
                raise
            scrubbed = self._scrub_window_names()
            logger.warning(
                "tmux list-windows hit libtmux parse error; scrubbed %d window name(s) and retrying",
                scrubbed,
            )
            return list(session.windows)
