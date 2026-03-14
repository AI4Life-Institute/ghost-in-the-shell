"""Core Engine — wires all modules together and handles commands.

The engine is platform-agnostic: it receives commands from any adapter
and delegates to TmuxController, SessionManager, ScreenshotEngine, etc.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from ..adapters.base import Button, IncomingMessage, OutgoingMessage
from ..config import Settings
from .health import HealthMonitor
from .launcher import CodingCLILauncher
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

        # Platform adapter (set externally)
        self._adapter: Any = None

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
        self.monitor.on_output(self._on_pane_output)
        self.monitor.on_prompt(self._on_pane_prompt)

        # Resume polling for existing bindings
        for binding in self.session_mgr.list_bindings():
            self.monitor.start_polling(binding.channel_id, binding.window_id)

        logger.info("Engine started")

    async def stop(self) -> None:
        """Stop the engine."""
        self.monitor.stop_all()
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
        """Handle /bind — bind channel to a project directory."""
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

            # Discover existing sessions
            sessions = self.launcher.discover_sessions(str(p))

            # Create tmux window
            channel = interaction.channel if interaction else None
            window_name = channel.name if channel else f"ch-{channel_id[:8]}"
            cli = self.settings.coding_cli_command

            # Pick session to resume (most recent, if any)
            session_id = sessions[0].session_id if sessions else None
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

            resume_info = ""
            if session_id:
                resume_info = f"\nResuming session `{session_id[:8]}...`"

            # List directory contents for user orientation
            try:
                entries = sorted(p.iterdir())
                dirs = [e.name + "/" for e in entries if e.is_dir() and not e.name.startswith(".")]
                files = [e.name for e in entries if e.is_file() and not e.name.startswith(".")]
                listing_items = dirs + files
                if listing_items:
                    listing = "\n".join(f"  {item}" for item in listing_items[:30])
                    if len(listing_items) > 30:
                        listing += f"\n  ... and {len(listing_items) - 30} more"
                    dir_info = f"\n```\n{listing}\n```"
                else:
                    dir_info = "\n*(empty directory)*"
            except Exception:
                dir_info = ""

            await self._reply(
                interaction,
                f"Bound **#{window_name}** → `{p}`\n"
                f"tmux window: `{win.window_id}` | CLI: `{cli}`"
                f"{resume_info}{dir_info}",
            )
        else:
            # No path — for now, tell user to provide one
            # TODO: implement interactive directory browser
            await self._reply(
                interaction,
                "Usage: `/bind /path/to/project`\n"
                "Interactive directory browser coming soon.",
            )

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
    # Pane monitor callbacks
    # ------------------------------------------------------------------

    async def _on_pane_output(self, channel_id: str, new_lines: str) -> None:
        """Called by PaneMonitor when new terminal output is detected."""
        if not self._adapter:
            return

        text = new_lines.strip()
        if not text:
            return

        # Truncate to ~1800 chars to leave room for code block formatting
        if len(text) > 1800:
            text = text[:1800] + "\n... (truncated)"

        await self._adapter.send_message(
            channel_id,
            OutgoingMessage(text=f"```\n{text}\n```"),
        )

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

    async def _reply(self, interaction: Any, text: str) -> None:
        """Reply to an interaction or send to channel."""
        try:
            if interaction and hasattr(interaction, "followup"):
                await interaction.followup.send(text)
            elif interaction and hasattr(interaction, "channel"):
                await interaction.channel.send(text)
        except Exception:
            logger.exception("Failed to reply")
