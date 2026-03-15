"""Core Engine — wires all modules together and handles commands.

The engine is platform-agnostic: it receives commands from any adapter
and delegates to TmuxController, SessionManager, ScreenshotEngine, etc.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from ..adapters.base import Button, IncomingMessage, OutgoingMessage
from ..config import Settings
from .health import HealthMonitor
from .jsonl_monitor import JsonlMonitor
from .launcher import CLISession, CodingCLILauncher
from .monitor import PaneMonitor
from .screenshot import ScreenshotEngine
from .session import SessionManager
from .terminal_parser import PromptInfo
from .tmux import TmuxController

logger = logging.getLogger(__name__)


class Engine:
    """Core engine — orchestrates all GITS modules.

    Command handlers are called by the platform adapter (e.g. Discord
    slash commands). Each handler receives a ``channel_id`` and an
    optional platform-specific ``interaction`` object for responding.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        # Core modules
        self.tmux = TmuxController(session_name=settings.tmux_session_name)
        self.session_mgr = SessionManager(state_dir=settings.state_dir)
        self.screenshot = ScreenshotEngine(font_size=settings.screenshot_font_size)
        self.launcher = CodingCLILauncher(
            session_map_path=settings.session_map_file,
        )
        self.health = HealthMonitor(
            tmux=self.tmux,
            session_mgr=self.session_mgr,
            launcher=self.launcher,
            check_interval=settings.health_check_interval,
        )
        self.monitor = PaneMonitor(
            tmux=self.tmux,
            session_mgr=self.session_mgr,
            interval=settings.pane_poll_interval,
        )
        self.jsonl_monitor = JsonlMonitor(
            session_mgr=self.session_mgr,
            poll_interval=settings.jsonl_poll_interval,
        )

        # Platform adapter (set externally)
        self._adapter: Any = None

        # Pending session picker state: channel_id -> bind info
        self._pending_binds: dict[str, dict] = {}

    def set_adapter(self, adapter: Any) -> None:
        """Set the platform adapter."""
        self._adapter = adapter

    async def start(self) -> None:
        """Start the engine: ensure tmux session, start health monitor."""
        self.settings.state_dir.mkdir(parents=True, exist_ok=True)
        await self.tmux.ensure_session()
        await self.health.start()

        # Register health recovery callback
        self.health.on_recovery(self._on_recovery)

        # Register pane monitor callbacks
        self.monitor.on_prompt(self._on_pane_prompt)

        # Resume polling for existing bindings
        for binding in self.session_mgr.list_bindings():
            self.monitor.start_polling(binding.channel_id, binding.window_id)

        # Start JSONL output monitoring
        self.jsonl_monitor.on_message(self._on_jsonl_message)
        self.jsonl_monitor.start()

        logger.info("Engine started")

    async def stop(self) -> None:
        """Stop the engine."""
        self.monitor.stop_all()
        self.jsonl_monitor.stop()
        await self.health.stop()
        logger.info("Engine stopped")

    # ------------------------------------------------------------------
    # Message handler (plain text forwarding)
    # ------------------------------------------------------------------

    async def handle_message(self, msg: IncomingMessage) -> None:
        """Forward plain text messages to the bound tmux window."""
        binding = self.session_mgr.get_binding(msg.channel_id)
        if binding is None:
            logger.debug(
                "Ignoring message in unbound channel %s: %s",
                msg.channel_id,
                (msg.text or "")[:50],
            )
            return

        if msg.text:
            logger.info(
                "Forwarding message to tmux %s: %s",
                binding.window_id,
                msg.text[:80],
            )
            try:
                await self.tmux.send_text(binding.window_id, msg.text)
            except Exception:
                logger.exception("Failed to send text to tmux")

    # ------------------------------------------------------------------
    # A. Native Commands
    # ------------------------------------------------------------------

    async def handle_bind(
        self, channel_id: str, path: str | None, interaction: Any
    ) -> None:
        """Handle /bind — bind channel to a project directory.

        When existing CLI sessions are found in the directory, shows a
        session picker with buttons.  Otherwise starts a fresh session
        immediately.
        """
        if path:
            # Direct path specified — validate and bind
            p = Path(path).expanduser().resolve()
            if not p.exists():
                await self._reply(interaction, f"Path not found: `{path}`")
                return
            if not p.is_dir():
                await self._reply(interaction, f"Not a directory: `{path}`")
                return

            # Check allowed paths
            if self.settings.allowed_paths and not any(
                str(p).startswith(ap) for ap in self.settings.allowed_paths
            ):
                await self._reply(interaction, "Path not in allowed paths.")
                return

            channel = interaction.channel if interaction else None
            window_name = channel.name if channel else f"ch-{channel_id[:8]}"
            cli = self.settings.coding_cli_command

            # Discover existing sessions
            sessions = self.launcher.discover_sessions(str(p), cli=cli)

            if sessions:
                # Store pending bind info and show session picker
                self._pending_binds[channel_id] = {
                    "path": str(p),
                    "window_name": window_name,
                    "cli": cli,
                    "sessions": sessions,
                    "created_at": time.time(),
                }
                msg = self._build_session_picker_message(
                    sessions, str(p), channel_id
                )

                # Send picker via adapter, acknowledge the interaction
                if self._adapter:
                    await self._adapter.send_message(channel_id, msg)
                    await self._reply(
                        interaction,
                        f"Found {len(sessions)} existing session(s) in `{p}`. "
                        f"Pick one below or start fresh.",
                    )
                else:
                    await self._reply(
                        interaction,
                        f"Found {len(sessions)} session(s) but no adapter to "
                        f"show picker. Starting fresh.",
                    )
                    await self._create_bind(
                        channel_id, str(p), window_name, cli, interaction
                    )
                return
            else:
                # No sessions found — start fresh directly
                await self._create_bind(
                    channel_id, str(p), window_name, cli, interaction
                )
        else:
            # No path — launch interactive directory browser
            if self.settings.allowed_paths:
                start_dir = self.settings.allowed_paths[0]
            else:
                start_dir = str(Path.home())

            start_path = Path(start_dir).expanduser().resolve()
            if not start_path.is_dir():
                await self._reply(
                    interaction,
                    f"Starting directory not found: `{start_dir}`",
                )
                return

            channel = interaction.channel if interaction else None
            window_name = channel.name if channel else f"ch-{channel_id[:8]}"
            cli = self.settings.coding_cli_command

            self._pending_binds[channel_id] = {
                "type": "browse",
                "current_dir": str(start_path),
                "page": 0,
                "window_name": window_name,
                "cli": cli,
            }

            msg = self._build_dir_browser_message(
                str(start_path), channel_id, page=0
            )

            if self._adapter:
                await self._adapter.send_message(channel_id, msg)
                await self._reply(
                    interaction,
                    "Browse to select a project directory.",
                )
            else:
                await self._reply(
                    interaction,
                    "No adapter available for directory browser. "
                    "Use `/bind /path/to/project` instead.",
                )

    async def _create_bind(
        self,
        channel_id: str,
        work_dir: str,
        window_name: str,
        cli: str,
        interaction: Any,
        session_id: str | None = None,
    ) -> None:
        """Create a tmux window, binding, and reply with confirmation.

        If *session_id* is provided the CLI is launched in resume mode.
        """
        p = Path(work_dir)
        cmd = self.launcher.build_launch_command(cli=cli, session_id=session_id)

        win = await self.tmux.create_window(
            name=window_name, cwd=str(p), command=cmd
        )

        # Create binding
        await self.session_mgr.bind(
            platform="discord",
            channel_id=channel_id,
            window_id=win.window_id,
            window_name=window_name,
            work_dir=str(p),
            coding_cli=cli,
            cli_session_id=session_id,
        )

        # Start pane polling for the new binding
        self.monitor.start_polling(channel_id, win.window_id)

        # List directory contents for user orientation
        dir_info = self._format_dir_listing(p)

        if session_id:
            session_info = f"\nResuming session `{session_id[:16]}...`"
        else:
            session_info = "\nFresh session (hook will capture session ID)"

        await self._reply(
            interaction,
            f"Bound **#{window_name}** \u2192 `{p}`\n"
            f"tmux window: `{win.window_id}` | CLI: `{cli}`"
            f"{session_info}"
            f"{dir_info}",
        )

    # ------------------------------------------------------------------
    # Session Picker helpers
    # ------------------------------------------------------------------

    def _build_session_picker_message(
        self,
        sessions: list[CLISession],
        work_dir: str,
        channel_id: str,
    ) -> OutgoingMessage:
        """Build an OutgoingMessage with buttons for session selection.

        Shows at most 5 sessions (the most recent ones) plus a
        "New Session" button.
        """
        display_sessions = sessions[:5]

        lines = [f"**Resume Session?**\n"]
        lines.append(f"Existing sessions in `{work_dir}`:\n")
        for i, s in enumerate(display_sessions):
            age = _format_age(s.mtime)
            # Truncate summary for display
            summary = s.summary[:50] + "..." if len(s.summary) > 50 else s.summary
            lines.append(f"{i + 1}. **{summary}** \u2014 {s.message_count} msgs ({age})")

        text = "\n".join(lines)

        # Build buttons — one per session + New Session
        # callback_data must be < 100 chars, so use index not full ID
        session_buttons = []
        for i, s in enumerate(display_sessions):
            label = s.summary[:35] + "..." if len(s.summary) > 35 else s.summary
            session_buttons.append(
                Button(
                    label=f"\u25b6 {label}",
                    callback_data=f"bind_resume:{channel_id}:{i}",
                )
            )

        new_button = Button(
            label="\u2795 New Session",
            callback_data=f"bind_new:{channel_id}",
        )

        # Layout: session buttons in rows of 2, then New Session row
        button_rows: list[list[Button]] = []
        for i in range(0, len(session_buttons), 2):
            button_rows.append(session_buttons[i : i + 2])
        button_rows.append([new_button])

        return OutgoingMessage(text=text, buttons=button_rows)

    # ------------------------------------------------------------------
    # Directory Browser helpers
    # ------------------------------------------------------------------

    DIRS_PER_PAGE = 6

    def _build_dir_browser_message(
        self,
        current_dir: str,
        channel_id: str,
        page: int = 0,
    ) -> OutgoingMessage:
        """Build an OutgoingMessage with buttons for directory browsing.

        Shows subdirectories of *current_dir* as buttons, with navigation
        controls (parent, paging, select, cancel).
        """
        p = Path(current_dir)
        try:
            subdirs = sorted(
                [
                    e.name
                    for e in p.iterdir()
                    if e.is_dir() and not e.name.startswith(".")
                ]
            )
        except PermissionError:
            subdirs = []

        total_pages = max(1, (len(subdirs) + self.DIRS_PER_PAGE - 1) // self.DIRS_PER_PAGE)
        page = min(page, total_pages - 1)

        start = page * self.DIRS_PER_PAGE
        page_dirs = subdirs[start : start + self.DIRS_PER_PAGE]

        text = (
            f"Select Working Directory\n\n"
            f"Current: `{current_dir}`\n\n"
            f"Tap a folder to enter, or select current directory."
        )

        button_rows: list[list[Button]] = []

        # Directory buttons — 2 rows of 3
        for row_start in range(0, len(page_dirs), 3):
            row = []
            for i, dirname in enumerate(page_dirs[row_start : row_start + 3]):
                idx = start + row_start + i
                label = dirname[:20] + ".." if len(dirname) > 22 else dirname
                row.append(
                    Button(
                        label=f"{label}",
                        callback_data=f"browse:{channel_id}:{page}:{idx}",
                    )
                )
            button_rows.append(row)

        # Navigation row: parent + optional paging
        nav_row: list[Button] = []
        nav_row.append(
            Button(label=".. Parent", callback_data=f"browse_parent:{channel_id}")
        )
        if total_pages > 1:
            if page > 0:
                nav_row.append(
                    Button(
                        label="< Prev",
                        callback_data=f"browse_page:{channel_id}:{page - 1}",
                    )
                )
            nav_row.append(
                Button(label=f"{page + 1}/{total_pages}", callback_data="noop")
            )
            if page < total_pages - 1:
                nav_row.append(
                    Button(
                        label="Next >",
                        callback_data=f"browse_page:{channel_id}:{page + 1}",
                    )
                )
        button_rows.append(nav_row)

        # Action row: select + cancel
        action_row = [
            Button(label="Select", callback_data=f"browse_select:{channel_id}"),
            Button(label="Cancel", callback_data=f"browse_cancel:{channel_id}"),
        ]
        button_rows.append(action_row)

        return OutgoingMessage(text=text, buttons=button_rows)

    def _is_path_allowed(self, path: str) -> bool:
        """Check whether *path* is within the configured allowed_paths.

        Returns True if no allowed_paths restriction is configured.
        """
        if not self.settings.allowed_paths:
            return True
        return any(path.startswith(ap) for ap in self.settings.allowed_paths)

    def _get_sorted_subdirs(self, current_dir: str) -> list[str]:
        """Return sorted non-hidden subdirectory names of *current_dir*."""
        p = Path(current_dir)
        try:
            return sorted(
                e.name
                for e in p.iterdir()
                if e.is_dir() and not e.name.startswith(".")
            )
        except PermissionError:
            return []

    @staticmethod
    def _format_dir_listing(p: Path) -> str:
        """Format a directory listing for user orientation."""
        try:
            entries = sorted(p.iterdir())
            dirs = [e.name + "/" for e in entries if e.is_dir() and not e.name.startswith(".")]
            files = [e.name for e in entries if e.is_file() and not e.name.startswith(".")]
            listing_items = dirs + files
            if listing_items:
                listing = "\n".join(f"  {item}" for item in listing_items[:30])
                if len(listing_items) > 30:
                    listing += f"\n  ... and {len(listing_items) - 30} more"
                return f"\n```\n{listing}\n```"
            else:
                return "\n*(empty directory)*"
        except Exception:
            return ""

    async def handle_unbind(self, channel_id: str, interaction: Any) -> None:
        """Handle /unbind — unbind channel."""
        self.monitor.stop_polling(channel_id)
        binding = await self.session_mgr.unbind(channel_id)
        if binding:
            await self._reply(
                interaction,
                f"Unbound **{binding.window_name}** "
                f"(tmux window `{binding.window_id}` kept alive).",
            )
        else:
            await self._reply(interaction, "This channel is not bound.")

    async def handle_fork(
        self,
        channel_id: str,
        title: str,
        subdir: str | None,
        interaction: Any,
    ) -> None:
        """Handle /fork — create sub-task thread + tmux window."""
        # Get parent binding
        parent_binding = self.session_mgr.get_binding(channel_id)
        if parent_binding is None:
            await self._reply(
                interaction, "This channel is not bound. Use `/bind` first."
            )
            return

        # Determine work directory
        work_dir = parent_binding.work_dir
        if subdir:
            work_dir = str(Path(work_dir) / subdir)
            if not Path(work_dir).exists():
                await self._reply(
                    interaction, f"Subdirectory not found: `{subdir}`"
                )
                return

        # Create thread
        if self._adapter:
            thread_id = await self._adapter.create_thread(
                channel_id,
                title,
                auto_archive_minutes=self.settings.thread_auto_archive_minutes,
            )
        else:
            await self._reply(interaction, "No adapter available.")
            return

        # Create tmux window
        cli = parent_binding.coding_cli
        cmd = self.launcher.build_launch_command(cli=cli)

        win = await self.tmux.create_window(
            name=title, cwd=work_dir, command=cmd
        )

        # Create binding for thread
        await self.session_mgr.bind(
            platform="discord",
            channel_id=thread_id,
            window_id=win.window_id,
            window_name=title,
            work_dir=work_dir,
            coding_cli=cli,
            parent_channel_id=channel_id,
            subdir=subdir,
        )

        subdir_info = f" (subdir: `{subdir}`)" if subdir else ""
        await self._reply(
            interaction,
            f"Forked **{title}** → `{work_dir}`{subdir_info}\n"
            f"Thread: <#{thread_id}> | tmux: `{win.window_id}`",
        )

    async def handle_screenshot(self, channel_id: str, interaction: Any) -> None:
        """Handle /screenshot — take a terminal screenshot."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound. Use `/bind` first.")
            return

        try:
            ansi_text = await self.tmux.capture_pane_ansi(binding.window_id)
            png_bytes = await self.screenshot.capture(ansi_text)

            # Reply to the deferred interaction with the screenshot
            if interaction and hasattr(interaction, "followup"):
                import io

                import discord

                file = discord.File(io.BytesIO(png_bytes), filename="screenshot.png")
                await interaction.followup.send(file=file)
            elif self._adapter:
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(image=png_bytes),
                )
            else:
                await self._reply(interaction, "Screenshot captured (no adapter).")
        except Exception:
            logger.exception("Screenshot failed")
            await self._reply(interaction, "Screenshot failed.")

    async def handle_status(self, channel_id: str, interaction: Any) -> None:
        """Handle /status — show binding info."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Check window health
        alive = await self.tmux.window_exists(binding.window_id)
        status_icon = "🟢" if alive else "🔴"

        lines = [
            f"{status_icon} **{binding.window_name}**",
            f"Directory: `{binding.work_dir}`",
            f"CLI: `{binding.coding_cli}`",
            f"tmux window: `{binding.window_id}`",
        ]
        if binding.cli_session_id:
            lines.append(f"Session ID: `{binding.cli_session_id[:16]}...`")
        if binding.parent_channel_id:
            lines.append(f"Parent: <#{binding.parent_channel_id}>")
        if binding.subdir:
            lines.append(f"Subdir: `{binding.subdir}`")
        lines.append(f"Created: `{binding.created_at}`")

        await self._reply(interaction, "\n".join(lines))

    async def handle_stop(self, channel_id: str, interaction: Any) -> None:
        """Handle /stop — send Escape to interrupt."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        await self.tmux.send_keys(binding.window_id, "Escape")
        await asyncio.sleep(0.2)
        await self.tmux.send_keys(binding.window_id, "Escape")
        await self._reply(interaction, "Sent `Escape Escape` — operation interrupted.")

    async def handle_kill(self, channel_id: str, interaction: Any) -> None:
        """Handle /kill — kill tmux window and archive thread."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Stop polling and kill tmux window
        self.monitor.stop_polling(channel_id)
        killed = await self.tmux.kill_window(binding.window_id)

        # Remove binding
        await self.session_mgr.unbind(channel_id)

        # Archive thread if this is a thread
        if binding.parent_channel_id and self._adapter:
            try:
                await self._adapter.archive_thread(channel_id)
            except Exception:
                logger.debug("Could not archive thread %s", channel_id)

        status = "killed" if killed else "already gone"
        await self._reply(
            interaction,
            f"Window `{binding.window_name}` {status}. Binding removed.",
        )

    async def handle_new(self, channel_id: str, interaction: Any) -> None:
        """Handle /new — reset CLI session."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Send Ctrl-C to stop current process
        await self.tmux.send_keys(binding.window_id, "C-c")
        await asyncio.sleep(1)

        # Send exit to quit the CLI
        await self.tmux.send_text(binding.window_id, "exit")
        await asyncio.sleep(2)

        # Launch fresh CLI
        cmd = self.launcher.build_launch_command(cli=binding.coding_cli)
        await self.tmux.send_text(binding.window_id, cmd)

        # Clear session ID
        await self.session_mgr.update_cli_session_id(channel_id, "")

        await self._reply(interaction, "Session reset. Fresh CLI launched.")

    async def handle_bash(
        self, channel_id: str, command: str, interaction: Any
    ) -> None:
        """Handle /bash — run shell command in working directory."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=binding.work_dir,
            )
            output = result.stdout + result.stderr
            if len(output) > 1900:
                output = output[:1900] + "\n... (truncated)"
            await self._reply(
                interaction,
                f"```\n$ {command}\n{output}\n```\nExit code: {result.returncode}",
            )
        except subprocess.TimeoutExpired:
            await self._reply(interaction, f"Command timed out (30s): `{command}`")
        except Exception as e:
            await self._reply(interaction, f"Error: {e}")

    async def handle_model(
        self, channel_id: str, name: str | None, interaction: Any
    ) -> None:
        """Handle /model — switch the coding CLI model.

        If a model name is provided, sends ``/model <name>`` directly to
        the CLI (skipping its interactive picker which cannot be bridged
        to Discord).  If no name is given, shows the available choices.
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        if name:
            # Direct switch — bypass the interactive picker entirely
            await self.tmux.send_text(binding.window_id, f"/model {name}")
            await self._reply(
                interaction, f"Switching model to **{name}** …"
            )
        else:
            # No name — show help (don't send bare /model which opens
            # an interactive Ink picker that can't be operated from Discord)
            await self._reply(
                interaction,
                "Usage: `/model <name>`\n"
                "Available models:\n"
                "• `sonnet` — Sonnet 4.6 (daily coding)\n"
                "• `opus` — Opus 4.6 (complex reasoning)\n"
                "• `haiku` — Haiku 4.5 (fast, simple tasks)\n"
                "• `sonnet[1m]` — Sonnet 4.6 with 1M context\n"
                "• `opus[1m]` — Opus 4.6 with 1M context\n"
                "• `opusplan` — Opus planning + Sonnet execution",
            )

    # ------------------------------------------------------------------
    # B. CLI Forwarding
    # ------------------------------------------------------------------

    async def handle_cli_forward(
        self, channel_id: str, command: str, interaction: Any
    ) -> None:
        """Forward a command to the coding CLI via tmux."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Ensure command starts with /
        if not command.startswith("/"):
            command = f"/{command}"

        await self.tmux.send_text(binding.window_id, command)
        await self._reply(interaction, f"Forwarded: `{command}`")

    # ------------------------------------------------------------------
    # Button click handler (prompt bridge)
    # ------------------------------------------------------------------

    async def handle_button_click(
        self, channel_id: str, user_id: str, callback_data: str
    ) -> None:
        """Handle a button click from the platform adapter.

        Callback data formats:
        - ``prompt_opt:{window_id}:{option_number}`` — send number key
        - ``prompt_esc:{window_id}`` — send Escape
        - ``prompt_abort:{window_id}`` — send Ctrl-C
        """
        parts = callback_data.split(":")
        if len(parts) < 2:
            logger.warning("Invalid callback_data: %s", callback_data)
            return

        action = parts[0]

        if action == "prompt_opt" and len(parts) >= 3:
            window_id = parts[1]
            option_number = parts[2]
            logger.info(
                "Button click: option %s for window %s (user %s)",
                option_number,
                window_id,
                user_id,
            )
            await self.tmux.send_keys(window_id, option_number)

        elif action == "prompt_esc" and len(parts) >= 2:
            window_id = parts[1]
            logger.info(
                "Button click: Escape for window %s (user %s)",
                window_id,
                user_id,
            )
            await self.tmux.send_keys(window_id, "Escape")

        elif action == "prompt_abort" and len(parts) >= 2:
            window_id = parts[1]
            logger.info(
                "Button click: Ctrl-C for window %s (user %s)",
                window_id,
                user_id,
            )
            await self.tmux.send_keys(window_id, "C-c")

        elif action == "browse" and len(parts) >= 4:
            pending_channel = parts[1]
            browse_page = int(parts[2])
            dir_index = int(parts[3])
            await self._handle_browse_enter(pending_channel, dir_index, channel_id)

        elif action == "browse_parent" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_browse_parent(pending_channel, channel_id)

        elif action == "browse_page" and len(parts) >= 3:
            pending_channel = parts[1]
            new_page = int(parts[2])
            await self._handle_browse_page(pending_channel, new_page, channel_id)

        elif action == "browse_select" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_browse_select(pending_channel, channel_id)

        elif action == "browse_cancel" and len(parts) >= 2:
            pending_channel = parts[1]
            self._pending_binds.pop(pending_channel, None)
            if self._adapter:
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(text="Directory selection cancelled."),
                )

        elif action == "bind_resume" and len(parts) >= 3:
            pending_channel = parts[1]
            session_index = int(parts[2])
            await self._handle_bind_resume(pending_channel, session_index, channel_id)

        elif action == "bind_new" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_bind_new(pending_channel, channel_id)

        else:
            logger.warning("Unknown button action: %s", callback_data)

    # ------------------------------------------------------------------
    # Prompt message builder
    # ------------------------------------------------------------------

    @staticmethod
    def build_prompt_message(
        tool_context: str | None,
        options: list[tuple[int, str]],
        window_id: str,
    ) -> OutgoingMessage:
        """Build an OutgoingMessage with buttons for a Claude Code prompt.

        Parameters
        ----------
        tool_context:
            Descriptive text about what the tool is asking, e.g.
            ``"**Bash command**\\n```\\ntail -30 /tmp/log\\n```\\nProceed?"``
        options:
            List of ``(number, label)`` tuples for each prompt option.
        window_id:
            The tmux window ID the prompt belongs to.

        Returns
        -------
        OutgoingMessage with buttons for each option plus Cancel/Abort.
        """
        text = tool_context or "Claude Code is waiting for input."

        # Build option buttons — Discord allows max 5 buttons per row
        option_buttons = [
            Button(
                # Truncate long labels (Discord max 80 chars)
                label=label[:76] + "..." if len(label) > 80 else label,
                callback_data=f"prompt_opt:{window_id}:{number}",
            )
            for number, label in options
        ]

        # Control buttons
        cancel_button = Button(
            label="Cancel",
            callback_data=f"prompt_esc:{window_id}",
        )
        abort_button = Button(
            label="Abort",
            callback_data=f"prompt_abort:{window_id}",
        )

        # Layout: split option buttons into rows of max 3 (leave room),
        # then add cancel/abort row. Discord max is 5 per row, 5 rows total.
        button_rows: list[list[Button]] = []
        for i in range(0, len(option_buttons), 3):
            button_rows.append(option_buttons[i : i + 3])
        button_rows.append([cancel_button, abort_button])

        return OutgoingMessage(text=text, buttons=button_rows)

    # ------------------------------------------------------------------
    # JSONL monitor callback
    # ------------------------------------------------------------------

    async def _on_jsonl_message(self, channel_id: str, text: str) -> None:
        """Called by JsonlMonitor when new assistant output is detected."""
        if not self._adapter:
            return
        await self._adapter.send_message(
            channel_id,
            OutgoingMessage(text=text),
        )

    # ------------------------------------------------------------------
    # Pane monitor callbacks
    # ------------------------------------------------------------------

    async def _on_pane_prompt(
        self, channel_id: str, window_id: str, prompt_info: PromptInfo
    ) -> None:
        """Called by PaneMonitor when an interactive prompt is detected."""
        if not self._adapter:
            return

        options = [(o.number, o.label) for o in prompt_info.options]
        msg = self.build_prompt_message(
            tool_context=prompt_info.tool_context or None,
            options=options,
            window_id=window_id,
        )
        await self._adapter.send_message(channel_id, msg)

    # ------------------------------------------------------------------
    # Recovery callback
    # ------------------------------------------------------------------

    async def _on_recovery(self, result: Any) -> None:
        """Called by HealthMonitor after recovery attempt."""
        if not self._adapter:
            return

        details = "\n".join(result.details) if result.details else "No details"
        # Find a channel to report to (first binding)
        bindings = self.session_mgr.list_bindings()
        if bindings:
            channel_id = bindings[0].channel_id
            await self._adapter.send_message(
                channel_id,
                OutgoingMessage(
                    text=(
                        f"**tmux Recovery Report**\n"
                        f"Total: {result.total} | "
                        f"Recovered: {result.recovered} | "
                        f"Failed: {result.failed}\n"
                        f"```\n{details}\n```"
                    )
                ),
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Browse button handlers
    # ------------------------------------------------------------------

    async def _handle_browse_enter(
        self, pending_channel: str, dir_index: int, reply_channel: str
    ) -> None:
        """Handle clicking a directory button — enter that subdirectory."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None or pending.get("type") != "browse":
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Browser expired. Run `/bind` again."),
                )
            return

        subdirs = self._get_sorted_subdirs(pending["current_dir"])
        if dir_index < 0 or dir_index >= len(subdirs):
            logger.warning("Invalid browse dir_index %d", dir_index)
            return

        new_dir = str(Path(pending["current_dir"]) / subdirs[dir_index])

        if not self._is_path_allowed(new_dir):
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Cannot browse outside allowed paths."),
                )
            return

        pending["current_dir"] = new_dir
        pending["page"] = 0

        msg = self._build_dir_browser_message(new_dir, pending_channel, page=0)
        if self._adapter:
            await self._adapter.send_message(reply_channel, msg)

    async def _handle_browse_parent(
        self, pending_channel: str, reply_channel: str
    ) -> None:
        """Handle clicking '..' — go to parent directory."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None or pending.get("type") != "browse":
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Browser expired. Run `/bind` again."),
                )
            return

        parent = str(Path(pending["current_dir"]).parent)

        if not self._is_path_allowed(parent):
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Cannot browse outside allowed paths."),
                )
            return

        pending["current_dir"] = parent
        pending["page"] = 0

        msg = self._build_dir_browser_message(parent, pending_channel, page=0)
        if self._adapter:
            await self._adapter.send_message(reply_channel, msg)

    async def _handle_browse_page(
        self, pending_channel: str, new_page: int, reply_channel: str
    ) -> None:
        """Handle paging buttons."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None or pending.get("type") != "browse":
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Browser expired. Run `/bind` again."),
                )
            return

        pending["page"] = new_page

        msg = self._build_dir_browser_message(
            pending["current_dir"], pending_channel, page=new_page
        )
        if self._adapter:
            await self._adapter.send_message(reply_channel, msg)

    async def _handle_browse_select(
        self, pending_channel: str, reply_channel: str
    ) -> None:
        """Handle 'Select' — confirm current directory and proceed to bind."""
        pending = self._pending_binds.pop(pending_channel, None)
        if pending is None or pending.get("type") != "browse":
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Browser expired. Run `/bind` again."),
                )
            return

        selected_dir = pending["current_dir"]
        window_name = pending["window_name"]
        cli = pending["cli"]

        # Discover existing sessions in the selected directory
        sessions = self.launcher.discover_sessions(selected_dir, cli=cli)

        if sessions:
            # Show session picker
            self._pending_binds[pending_channel] = {
                "path": selected_dir,
                "window_name": window_name,
                "cli": cli,
                "sessions": sessions,
                "created_at": time.time(),
            }
            msg = self._build_session_picker_message(
                sessions, selected_dir, pending_channel
            )
            if self._adapter:
                await self._adapter.send_message(reply_channel, msg)
        else:
            # No sessions — create bind directly
            await self._create_bind(
                channel_id=pending_channel,
                work_dir=selected_dir,
                window_name=window_name,
                cli=cli,
                interaction=None,
            )
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(
                        text=(
                            f"Bound **#{window_name}** \u2192 "
                            f"`{selected_dir}`\n"
                            f"Fresh session started."
                        )
                    ),
                )

    async def _handle_bind_resume(
        self, pending_channel: str, session_index: int, reply_channel: str
    ) -> None:
        """Handle a bind_resume button click — resume an existing session."""
        pending = self._pending_binds.pop(pending_channel, None)
        if pending is None:
            logger.warning("No pending bind for channel %s", pending_channel)
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        sessions: list[CLISession] = pending["sessions"]
        if session_index < 0 or session_index >= len(sessions):
            logger.warning("Invalid session index %d", session_index)
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Invalid session selection."),
                )
            return

        session = sessions[session_index]
        logger.info(
            "Bind resume: channel=%s session=%s summary=%s",
            pending_channel,
            session.session_id,
            session.summary[:40],
        )

        # Create the window and binding with resume
        await self._create_bind(
            channel_id=pending_channel,
            work_dir=pending["path"],
            window_name=pending["window_name"],
            cli=pending["cli"],
            interaction=None,
            session_id=session.session_id,
        )

        # Send confirmation via adapter since we have no interaction object
        if self._adapter:
            age = _format_age(session.mtime)
            await self._adapter.send_message(
                reply_channel,
                OutgoingMessage(
                    text=(
                        f"Bound **#{pending['window_name']}** \u2192 "
                        f"`{pending['path']}`\n"
                        f"Resumed: **{session.summary}** ({age})"
                    )
                ),
            )

    async def _handle_bind_new(
        self, pending_channel: str, reply_channel: str
    ) -> None:
        """Handle a bind_new button click — start a fresh session."""
        pending = self._pending_binds.pop(pending_channel, None)
        if pending is None:
            logger.warning("No pending bind for channel %s", pending_channel)
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        logger.info(
            "Bind new: channel=%s path=%s",
            pending_channel,
            pending["path"],
        )

        # Create the window and binding without resume
        await self._create_bind(
            channel_id=pending_channel,
            work_dir=pending["path"],
            window_name=pending["window_name"],
            cli=pending["cli"],
            interaction=None,
        )

        # Send confirmation via adapter
        if self._adapter:
            await self._adapter.send_message(
                reply_channel,
                OutgoingMessage(
                    text=(
                        f"Bound **#{pending['window_name']}** \u2192 "
                        f"`{pending['path']}`\n"
                        f"Fresh session started."
                    )
                ),
            )

    async def _reply(self, interaction: Any, text: str) -> None:
        """Reply to an interaction or send to channel."""
        try:
            if interaction and hasattr(interaction, "followup"):
                await interaction.followup.send(text)
            elif interaction and hasattr(interaction, "channel"):
                await interaction.channel.send(text)
        except Exception:
            logger.exception("Failed to reply")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _format_age(mtime: float) -> str:
    """Format a timestamp as a human-readable age string.

    Examples: "just now", "3m ago", "2h ago", "1d ago", "3w ago".
    """
    delta = time.time() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        mins = int(delta / 60)
        return f"{mins}m ago"
    if delta < 86400:
        hours = int(delta / 3600)
        return f"{hours}h ago"
    if delta < 604800:
        days = int(delta / 86400)
        return f"{days}d ago"
    weeks = int(delta / 604800)
    return f"{weeks}w ago"
