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

from ..adapters.base import Button, IncomingMessage, OutgoingMessage, SelectOption
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

        # Auto-install Claude Code SessionStart hook if not present
        self._ensure_hooks_installed()

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
        # Cancel all message drainer tasks
        logger.info("Engine stopped")

    # ------------------------------------------------------------------
    # Hook auto-install
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_hooks_installed() -> None:
        """Auto-install CLI hooks for all supported CLIs."""
        from ..__main__ import _install_hook, _install_opencode_plugin

        for name, installer in [
            ("Claude", _install_hook),
            ("OpenCode", _install_opencode_plugin),
        ]:
            try:
                installer()
            except Exception:
                logger.warning("Failed to auto-install %s hook", name, exc_info=True)

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
                submit = _submit_keys_for_cli(binding.coding_cli)
                await self.tmux.send_text(
                    binding.window_id, msg.text, submit_keys=submit
                )
            except Exception:
                logger.exception("Failed to send text to tmux")

    # ------------------------------------------------------------------
    # A. Native Commands
    # ------------------------------------------------------------------

    async def handle_bind(
        self,
        channel_id: str,
        path: str | None,
        interaction: Any,
        mode: str | None = None,
        cli: str | None = None,
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
            cli = cli or self.settings.coding_cli_command

            # Discover existing sessions
            sessions = self.launcher.discover_sessions(str(p), cli=cli)

            if sessions:
                # Store pending bind info and show session picker
                self._pending_binds[channel_id] = {
                    "path": str(p),
                    "window_name": window_name,
                    "cli": cli,
                    "sessions": sessions,
                    "mode": mode,
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
                        channel_id, str(p), window_name, cli, interaction,
                        mode=mode,
                    )
                return
            else:
                # No sessions found — start fresh directly
                await self._create_bind(
                    channel_id, str(p), window_name, cli, interaction,
                    mode=mode,
                )
        else:
            await self._reply(
                interaction,
                "Please provide a path: `/bind /path/to/project`\n"
                "Start typing and use the dropdown to navigate.",
            )

    async def _create_bind(
        self,
        channel_id: str,
        work_dir: str,
        window_name: str,
        cli: str,
        interaction: Any,
        session_id: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Create a tmux window, binding, and reply with confirmation.

        If *session_id* is provided the CLI is launched in resume mode.
        If *mode* is provided, adds the corresponding flag to the CLI command:
        - ``"auto"`` → ``--allowedTools Edit,Write,... ``
        - ``"yolo"`` → ``--dangerously-skip-permissions``
        """
        p = Path(work_dir)
        cmd = self.launcher.build_launch_command(cli=cli, session_id=session_id)

        # Append permission mode flag (CLI-specific)
        if mode and mode != "default":
            cmd = _append_permission_flag(cmd, cli, mode)

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
            permission_mode=mode if mode and mode != "default" else None,
        )

        # Start pane polling for the new binding
        self.monitor.start_polling(channel_id, win.window_id)

        # List directory contents for user orientation
        dir_info = self._format_dir_listing(p)

        if session_id:
            session_info = f"\nResuming session `{session_id[:16]}...`"
        else:
            session_info = "\nFresh session"

        # Build confirmation with quick-action buttons
        wid = win.window_id
        nav_buttons = [
            [
                Button(label="↑ Up", callback_data=f"prompt_opt:{wid}:Up"),
                Button(label="↓ Down", callback_data=f"prompt_opt:{wid}:Down"),
                Button(label="⏎ Enter", callback_data=f"prompt_opt:{wid}:Enter"),
                Button(label="⎋ Esc", callback_data=f"prompt_esc:{wid}"),
            ],
            [
                Button(label="Accept (↓⏎)", callback_data=f"nav:{wid}:Down Enter"),
                Button(label="Screenshot", callback_data=f"nav:{wid}:screenshot"),
                Button(label="Ctrl-C", callback_data=f"prompt_abort:{wid}"),
            ],
        ]

        confirm_text = (
            f"Bound **#{window_name}** \u2192 `{p}`\n"
            f"tmux: `{wid}` | CLI: `{cli}`"
            f"{session_info}{dir_info}"
        )

        if self._adapter:
            await self._adapter.send_message(
                channel_id,
                OutgoingMessage(text=confirm_text, buttons=nav_buttons),
            )
        if interaction:
            await self._reply(interaction, "Bound successfully.")

        # Auto-screenshot after CLI starts up
        binding = self.session_mgr.get_binding(channel_id)
        if binding:
            await self._auto_screenshot(channel_id, binding, interaction, delay=2.0)

    # ------------------------------------------------------------------
    # Session Picker helpers
    # ------------------------------------------------------------------

    _SESSION_PAGE_SIZE = 24  # max Select options per page (leave 1 for "New Session")

    def _build_session_picker_message(
        self,
        sessions: list[CLISession],
        work_dir: str,
        channel_id: str,
        page: int = 0,
    ) -> OutgoingMessage:
        """Build an OutgoingMessage with a Select Menu for session selection.

        Sessions are sorted most-recently-active first (by mtime).
        Shows up to _SESSION_PAGE_SIZE sessions per page via Discord Select Menu.
        Adds a "Next Page" button when more sessions exist beyond this page.
        """
        page_size = self._SESSION_PAGE_SIZE
        start = page * page_size
        end = start + page_size
        page_sessions = sessions[start:end]
        total = len(sessions)
        has_more = end < total

        lines = ["**Resume Session?**\n"]
        lines.append(f"Found **{total}** session(s) in `{work_dir}` — sorted by most recently active.")
        if total > page_size:
            page_total = (total + page_size - 1) // page_size
            lines.append(f"Page {page + 1}/{page_total}")
        lines.append("\nPick a session from the dropdown below, or start a new one.")
        text = "\n".join(lines)

        # Build Select Menu options — one per session on this page
        # Use absolute index so callback_data maps correctly even across pages
        select_opts: list[SelectOption] = []
        for abs_i, s in enumerate(page_sessions, start=start):
            age = _format_age(s.mtime)
            label = s.summary[:100]
            # description: last message (truncated) + msg count + age
            meta = f" · {s.message_count} msgs · {age}"
            last = s.last_message
            max_last = 100 - len(meta)
            if last and max_last > 6:
                last_truncated = (last[:max_last - 1] + "…") if len(last) > max_last else last
                desc = last_truncated + meta
            else:
                desc = f"{s.message_count} msgs · {age}"
            select_opts.append(
                SelectOption(
                    label=label or f"Session {abs_i + 1}",
                    value=f"bind_resume:{channel_id}:{abs_i}",
                    description=desc,
                )
            )

        # "New Session" as the first Select option
        select_opts.insert(
            0,
            SelectOption(
                label="＋ New Session",
                value=f"bind_new:{channel_id}",
                description="Start a fresh session in this directory",
            ),
        )

        # "Next Page" button when there are more sessions
        button_rows: list[list[Button]] = []
        if has_more:
            button_rows.append([
                Button(
                    label=f"Next Page → ({end + 1}–{min(end + page_size, total)} of {total})",
                    callback_data=f"bind_page:{channel_id}:{page + 1}",
                )
            ])

        return OutgoingMessage(
            text=text,
            select_options=select_opts,
            select_placeholder="Select a session to resume…",
            buttons=button_rows or None,
        )

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

    async def handle_thread(
        self,
        channel_id: str,
        message: str,
        interaction: Any,
    ) -> None:
        """Handle /thread — create a Discord thread + child session.

        The child session shares the parent's work_dir and coding_cli.
        The *message* is sent as the first prompt to the new session.
        """
        parent_binding = self.session_mgr.get_binding(channel_id)
        if parent_binding is None:
            await self._reply(
                interaction, "This channel is not bound. Use `/bind` first."
            )
            return

        # Use first ~40 chars of message as thread title
        title = message[:40].strip()
        if len(message) > 40:
            title += "…"

        if not self._adapter:
            await self._reply(interaction, "No adapter available.")
            return

        thread_id = await self._adapter.create_thread(
            channel_id,
            title,
            auto_archive_minutes=self.settings.thread_auto_archive_minutes,
        )

        cli = parent_binding.coding_cli
        mode = parent_binding.permission_mode
        cmd = self.launcher.build_launch_command(cli=cli)
        if mode:
            cmd = _append_permission_flag(cmd, cli, mode)
        work_dir = parent_binding.work_dir

        win = await self.tmux.create_window(
            name=title, cwd=work_dir, command=cmd
        )

        await self.session_mgr.bind(
            platform="discord",
            channel_id=thread_id,
            window_id=win.window_id,
            window_name=title,
            work_dir=work_dir,
            coding_cli=cli,
            parent_channel_id=channel_id,
            permission_mode=mode,
        )

        # Start monitoring
        self.monitor.start_polling(thread_id, win.window_id)

        # Send initial prompt after CLI starts up
        async def _send_initial_prompt() -> None:
            await asyncio.sleep(2.0)  # wait for CLI to be ready
            submit = _submit_keys_for_cli(cli)
            await self.tmux.send_text(win.window_id, message, submit_keys=submit)

        asyncio.create_task(_send_initial_prompt())

        # Confirm in thread
        if self._adapter:
            await self._adapter.send_message(
                thread_id,
                OutgoingMessage(
                    text=f"Session started → `{work_dir}`\ntmux: `{win.window_id}` | CLI: `{cli}`"
                ),
            )

        await self._reply(
            interaction,
            f"Thread **{title}** created → <#{thread_id}>",
        )

    async def handle_thread_auto(
        self,
        thread_id: str,
        parent_channel_id: str,
        starter_message: str,
    ) -> None:
        """Auto-create session for a Discord thread (no interaction).

        Called when a user creates a thread directly in Discord
        under a bound channel.
        """
        parent_binding = self.session_mgr.get_binding(parent_channel_id)
        if parent_binding is None:
            return  # parent not bound, ignore

        title = starter_message[:40].strip() if starter_message else "thread"
        if len(starter_message) > 40:
            title += "…"

        cli = parent_binding.coding_cli
        mode = parent_binding.permission_mode
        cmd = self.launcher.build_launch_command(cli=cli)
        if mode:
            cmd = _append_permission_flag(cmd, cli, mode)
        work_dir = parent_binding.work_dir

        win = await self.tmux.create_window(
            name=title, cwd=work_dir, command=cmd
        )

        await self.session_mgr.bind(
            platform="discord",
            channel_id=thread_id,
            window_id=win.window_id,
            window_name=title,
            work_dir=work_dir,
            coding_cli=cli,
            parent_channel_id=parent_channel_id,
            permission_mode=mode,
        )

        self.monitor.start_polling(thread_id, win.window_id)

        # Send initial prompt
        if starter_message:
            async def _send_initial_prompt() -> None:
                await asyncio.sleep(2.0)
                submit = _submit_keys_for_cli(cli)
                await self.tmux.send_text(
                    win.window_id, starter_message, submit_keys=submit
                )

            asyncio.create_task(_send_initial_prompt())

        if self._adapter:
            await self._adapter.send_message(
                thread_id,
                OutgoingMessage(
                    text=f"Auto-session started → `{work_dir}`\ntmux: `{win.window_id}` | CLI: `{cli}`"
                ),
            )

    async def handle_fork(
        self,
        channel_id: str,
        title: str,
        interaction: Any,
    ) -> None:
        """Handle /fork — create a git worktree + thread + child session.

        Unlike /thread (shared directory), /fork creates an isolated
        git worktree so the child session doesn't interfere with the parent.
        """
        parent_binding = self.session_mgr.get_binding(channel_id)
        if parent_binding is None:
            await self._reply(
                interaction, "This channel is not bound. Use `/bind` first."
            )
            return

        repo_dir = parent_binding.work_dir

        # Check if it's a git repo
        if not _is_git_repo(repo_dir):
            await self._reply(
                interaction,
                "`/fork` requires a git repository. "
                "Use `/thread` for non-git directories.",
            )
            return

        # Create worktree
        worktree_path = await asyncio.to_thread(
            _create_worktree, repo_dir, title
        )
        if worktree_path is None:
            await self._reply(interaction, "Failed to create git worktree.")
            return

        # Create thread
        if not self._adapter:
            await self._reply(interaction, "No adapter available.")
            return

        thread_id = await self._adapter.create_thread(
            channel_id,
            title,
            auto_archive_minutes=self.settings.thread_auto_archive_minutes,
        )

        cli = parent_binding.coding_cli
        mode = parent_binding.permission_mode
        cmd = self.launcher.build_launch_command(cli=cli)
        if mode:
            cmd = _append_permission_flag(cmd, cli, mode)

        win = await self.tmux.create_window(
            name=title, cwd=worktree_path, command=cmd
        )

        await self.session_mgr.bind(
            platform="discord",
            channel_id=thread_id,
            window_id=win.window_id,
            window_name=title,
            work_dir=worktree_path,
            coding_cli=cli,
            parent_channel_id=channel_id,
            permission_mode=mode,
        )

        self.monitor.start_polling(thread_id, win.window_id)

        await self._reply(
            interaction,
            f"Forked **{title}** → `{worktree_path}` (worktree)\n"
            f"Thread: <#{thread_id}> | tmux: `{win.window_id}`",
        )

    async def _auto_screenshot(
        self, channel_id: str, binding: Any, interaction: Any, delay: float = 0.5
    ) -> None:
        """Automatically capture and send a screenshot after a command.

        Waits *delay* seconds for the terminal to settle, then captures
        and sends the screenshot as a follow-up message.
        """
        await asyncio.sleep(delay)
        try:
            ansi_text = await self.tmux.capture_pane_ansi(binding.window_id)
            png_bytes = await self.screenshot.capture(ansi_text)

            if self._adapter:
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(image=png_bytes),
                )
            elif interaction and hasattr(interaction, "channel"):
                import io

                import discord

                file = discord.File(io.BytesIO(png_bytes), filename="screenshot.png")
                await interaction.channel.send(file=file)
        except Exception:
            logger.debug("Auto-screenshot failed for %s", channel_id)

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
        if binding.parent_channel_id:
            lines.append(f"Parent: <#{binding.parent_channel_id}>")
        if binding.subdir:
            lines.append(f"Subdir: `{binding.subdir}`")
        lines.append(f"Created: `{binding.created_at}`")

        # Resume command
        if binding.cli_session_id:
            from .launcher import RESUME_TEMPLATES
            cli = binding.coding_cli or "claude"
            templates = RESUME_TEMPLATES.get(cli)
            if templates:
                resume_cmd = templates["by_id"].format(id=binding.cli_session_id)
            else:
                resume_cmd = f"{cli} --resume {binding.cli_session_id}"
            lines.append(f"\n**Resume:**\n```\ncd {binding.work_dir}\n{resume_cmd}\n```")

        await self._reply(interaction, "\n".join(lines))

    async def handle_keys(
        self, channel_id: str, keys: str, interaction: Any
    ) -> None:
        """Handle /keys — send a key sequence to the tmux pane.

        Supports space-separated key names: ``Down Enter``, ``Down Down Enter``,
        ``Escape``, ``C-c``, ``Tab``, ``Space``, etc.
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        key_list = keys.split()
        for key in key_list:
            await self.tmux.send_keys(binding.window_id, key)
            await asyncio.sleep(0.15)

        await self._reply(interaction, f"Sent: `{keys}`")
        await self._auto_screenshot(channel_id, binding, interaction)

    async def handle_esc(self, channel_id: str, interaction: Any) -> None:
        """Handle /esc — send a single Escape key."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        await self.tmux.send_keys(binding.window_id, "Escape")
        await self._reply(interaction, "Sent `Escape`.")
        await self._auto_screenshot(channel_id, binding, interaction)


    async def handle_kill(
        self,
        channel_id: str,
        interaction: Any,
        force_worktree: bool = False,
    ) -> None:
        """Handle /kill — kill tmux window, archive thread, clean worktree.

        If the session uses a git worktree and it has uncommitted changes,
        a confirmation prompt is shown (unless *force_worktree* is True).
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Check if work_dir is a worktree with dirty state
        is_wt = await asyncio.to_thread(_is_worktree, binding.work_dir)
        if is_wt and not force_worktree:
            dirty_files = await asyncio.to_thread(
                _worktree_dirty_files, binding.work_dir
            )
            if dirty_files:
                # Send confirmation buttons
                preview = "\n".join(dirty_files[:10])
                if len(dirty_files) > 10:
                    preview += f"\n... and {len(dirty_files) - 10} more"
                confirm_msg = OutgoingMessage(
                    text=(
                        f"**Worktree has uncommitted changes:**\n"
                        f"```\n{preview}\n```\n"
                        f"Delete worktree anyway?"
                    ),
                    buttons=[[
                        Button(
                            label="Yes, delete worktree",
                            callback_data=f"kill_wt_yes:{channel_id}",
                        ),
                        Button(
                            label="No, keep worktree",
                            callback_data=f"kill_wt_no:{channel_id}",
                        ),
                    ]],
                )
                if self._adapter:
                    await self._adapter.send_message(channel_id, confirm_msg)
                if interaction:
                    await self._reply(
                        interaction,
                        "Worktree has changes — check the confirmation above.",
                    )
                return

        # Kill child sessions first (threads and forks)
        children = self.session_mgr.list_channel_threads(channel_id)
        for child in children:
            await self._kill_single(child.channel_id, archive_thread=True)

        # Kill this session
        await self._kill_single(channel_id, archive_thread=True, remove_worktree=is_wt)

        status_parts = [f"Window `{binding.window_name}` killed. Binding removed."]
        if children:
            status_parts.append(f"Also killed {len(children)} child session(s).")
        if is_wt:
            status_parts.append("Worktree removed.")
        await self._reply(interaction, " ".join(status_parts))

    async def _kill_single(
        self,
        channel_id: str,
        archive_thread: bool = False,
        remove_worktree: bool = False,
    ) -> None:
        """Kill a single session: stop polling, kill window, unbind."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            return

        self.monitor.stop_polling(channel_id)
        await self.tmux.kill_window(binding.window_id)
        await self.session_mgr.unbind(channel_id)

        # Remove worktree if requested
        if remove_worktree or await asyncio.to_thread(
            _is_worktree, binding.work_dir
        ):
            await asyncio.to_thread(_remove_worktree, binding.work_dir)

        # Archive thread
        if archive_thread and binding.parent_channel_id and self._adapter:
            try:
                await self._adapter.archive_thread(channel_id)
            except Exception:
                logger.debug("Could not archive thread %s", channel_id)

    async def handle_new(
        self, channel_id: str, interaction: Any, message: str | None = None
    ) -> None:
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

        if message:
            async def _send_initial_prompt() -> None:
                await asyncio.sleep(2.0)  # wait for CLI to be ready
                submit = _submit_keys_for_cli(binding.coding_cli)
                await self.tmux.send_text(binding.window_id, message, submit_keys=submit)

            asyncio.create_task(_send_initial_prompt())
            await self._reply(interaction, f"Session reset. Fresh CLI launched.\nSent: {message[:80]}")
        else:
            await self._reply(interaction, "Session reset. Fresh CLI launched.")

    async def handle_mode(
        self, channel_id: str, mode: str, interaction: Any
    ) -> None:
        """Handle /mode — switch permission mode by resuming the session with a new flag.

        Kills the current CLI process and relaunches it with the same session ID
        (resume) so conversation history is preserved, but with the new permission
        mode flag applied.
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        cli = binding.coding_cli
        session_id = binding.cli_session_id

        # Kill the running CLI
        await self.tmux.send_keys(binding.window_id, "C-c")
        await asyncio.sleep(0.5)
        await self.tmux.send_text(binding.window_id, "exit")
        await asyncio.sleep(1.5)

        # Resume with new mode
        cmd = self.launcher.build_launch_command(cli=cli, session_id=session_id)
        if mode and mode != "default":
            cmd = _append_permission_flag(cmd, cli, mode)
        await self.tmux.send_text(binding.window_id, cmd)

        # Persist new mode
        stored_mode = mode if mode != "default" else None
        binding.permission_mode = stored_mode
        await self.session_mgr._save()

        mode_label = {
            "bypassPermissions": "YOLO (全自動)",
            "auto": "Auto",
            "acceptEdits": "AcceptEdits",
            "default": "普通 (需要確認)",
        }.get(mode, mode)
        resume_note = f" (resuming `{session_id[:16]}…`)" if session_id else " (fresh)"
        await self._reply(
            interaction,
            f"Mode switched to **{mode_label}**{resume_note}",
        )

    async def handle_bash(
        self, channel_id: str, command: str, interaction: Any
    ) -> None:
        """Handle /bash — send a !command to the coding CLI via tmux.

        Sends the command with a ``!`` prefix which triggers the CLI's
        bash execution mode (Claude Code runs it directly).
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        # Send as !command — triggers Claude Code's bash mode
        bash_cmd = f"!{command}"
        submit = _submit_keys_for_cli(binding.coding_cli)
        await self.tmux.send_text(binding.window_id, bash_cmd, submit_keys=submit)
        await self._reply(interaction, f"Sent to tmux: `{bash_cmd}`")
        await self._auto_screenshot(channel_id, binding, interaction, delay=1.0)

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
                "**Claude Code** — `sonnet`, `opus`, `haiku`, `sonnet[1m]`, `opus[1m]`, `opusplan`\n"
                "**Codex** — `o3`, `o4-mini`, `gpt-4o`, or any model name supported by your CLI",
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

        # Ensure command starts with exactly one /
        command = command.lstrip("/")
        command = f"/{command}"

        submit = _submit_keys_for_cli(binding.coding_cli)
        await self.tmux.send_text(binding.window_id, command, submit_keys=submit)
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
        - ``bind_resume:{channel_id}:{index}`` — resume existing session
        - ``bind_new:{channel_id}`` — start fresh session
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

        elif action == "nav" and len(parts) >= 3:
            window_id = parts[1]
            nav_action = ":".join(parts[2:])  # rejoin in case of colons

            if nav_action == "screenshot":
                # Take screenshot and send
                try:
                    ansi_text = await self.tmux.capture_pane_ansi(window_id)
                    png_bytes = await self.screenshot.capture(ansi_text)
                    if self._adapter:
                        await self._adapter.send_message(
                            channel_id,
                            OutgoingMessage(image=png_bytes),
                        )
                except Exception:
                    logger.exception("Nav screenshot failed")
            else:
                # Send key sequence (space-separated keys)
                for key in nav_action.split():
                    await self.tmux.send_keys(window_id, key)
                    await asyncio.sleep(0.15)

        elif action == "kill_wt_yes" and len(parts) >= 2:
            target_channel = parts[1]
            await self.handle_kill(target_channel, interaction=None, force_worktree=True)
            if self._adapter:
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(text="Session killed and worktree removed."),
                )

        elif action == "kill_wt_no" and len(parts) >= 2:
            target_channel = parts[1]
            binding = self.session_mgr.get_binding(target_channel)
            if binding:
                # Kill tmux + unbind but keep worktree
                self.monitor.stop_polling(target_channel)
                self._cancel_drainer(target_channel)
                await self.tmux.kill_window(binding.window_id)
                await self.session_mgr.unbind(target_channel)
                if binding.parent_channel_id and self._adapter:
                    try:
                        await self._adapter.archive_thread(target_channel)
                    except Exception:
                        pass
            if self._adapter:
                wt_path = binding.work_dir if binding else "unknown"
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(
                        text=f"Session killed. Worktree preserved at `{wt_path}`."
                    ),
                )

        elif action == "bind_resume" and len(parts) >= 3:
            pending_channel = parts[1]
            session_index = int(parts[2])
            await self._handle_bind_resume(pending_channel, session_index, channel_id)

        elif action == "bind_new" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_bind_new(pending_channel, channel_id)

        elif action == "bind_page" and len(parts) >= 3:
            pending_channel = parts[1]
            page = int(parts[2])
            await self._handle_bind_page(pending_channel, page, channel_id)

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
    # Session picker button handlers
    # ------------------------------------------------------------------

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
            mode=pending.get("mode"),
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
            mode=pending.get("mode"),
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

    async def _handle_bind_page(
        self, pending_channel: str, page: int, reply_channel: str
    ) -> None:
        """Handle a bind_page button click — show next page of sessions."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None:
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        sessions = pending["sessions"]
        msg = self._build_session_picker_message(sessions, pending["path"], pending_channel, page=page)
        if self._adapter:
            await self._adapter.send_message(reply_channel, msg)

    # ------------------------------------------------------------------
    # Browser agent command
    # ------------------------------------------------------------------

    async def handle_browse(
        self,
        goal: str,
        profile: str,
        channel_id: str,
        interaction: Any,
    ) -> None:
        """Create and run a browser agent task.

        Immediately acknowledges the interaction, then fires off the
        BrowserAgent in a background asyncio task so the slash command
        returns quickly while the agent runs asynchronously.
        """
        from ..adapters.browser.agent import BrowserAgent
        from ..storage.sqlite import GitsDB, TaskRepo

        await self._reply(interaction, f"Starting browser task: {goal}")

        # Create task record.
        async with GitsDB() as db:
            task_id = await TaskRepo(db.conn).create(goal=goal, profile=profile)

        # Notification callback posts Discord updates during execution.
        async def notify(tid: str, event: str, data: dict) -> None:
            msg = ""
            if event == "step":
                msg = f"Step {data['seq']}: {data['action']} — {data.get('reasoning', '')}"
            elif event == "done":
                msg = f"Done: {data.get('summary', '')}"
            elif event == "ask_user":
                msg = (
                    f"Agent needs input: {data.get('message', '')}\n"
                    "Reply in this channel to continue."
                )
            elif event == "failed":
                msg = f"Failed: {data.get('error', '')}"
            if msg and self._adapter:
                await self._adapter.send_message(
                    channel_id, OutgoingMessage(text=msg)
                )

        async def _run() -> None:
            async with GitsDB() as db:
                agent = BrowserAgent(db=db, profile=profile, notify_cb=notify)
                await agent.run(task_id=task_id, goal=goal)

        asyncio.create_task(_run())

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


# CLIs that need Escape+Enter to submit (multi-line editor mode)
_ESCAPE_ENTER_CLIS = frozenset({"codex", "copilot"})


def _submit_keys_for_cli(cli: str) -> str:
    """Return the tmux submit key sequence for a given CLI type."""
    if cli in _ESCAPE_ENTER_CLIS:
        return "Escape Enter"
    return "Enter"


def _append_permission_flag(cmd: str, cli: str, mode: str) -> str:
    """Append the correct permission flag based on CLI type and mode.

    Mapping (our mode → CLI flag):
      claude:
        default           → (nothing)
        acceptEdits       → --permission-mode acceptEdits
        auto              → --permission-mode auto
        bypassPermissions → --permission-mode bypassPermissions
      codex:
        default           → (nothing)
        acceptEdits       → (not supported, skip)
        auto              → --full-auto
        bypassPermissions → --dangerously-bypass-approvals-and-sandbox
      copilot:
        default           → (nothing)
        acceptEdits       → --allow-tool=write --allow-tool=edit
        auto              → --allow-all-tools
        bypassPermissions → --yolo
      opencode:           → (no permission flags supported)
    """
    if mode == "default":
        return cmd

    if cli == "codex":
        if mode == "bypassPermissions":
            cmd += " --dangerously-bypass-approvals-and-sandbox"
        elif mode == "auto":
            cmd += " --full-auto"
        # acceptEdits not supported by codex — skip
        # Enable hooks feature for session tracking
        cmd += " --enable codex_hooks"
    elif cli == "copilot":
        if mode == "bypassPermissions":
            cmd += " --yolo"
        elif mode == "auto":
            cmd += " --allow-all-tools"
        elif mode == "acceptEdits":
            cmd += " --allow-tool=write --allow-tool=edit"
    elif cli == "opencode":
        pass  # no permission flags supported
    else:
        # claude and others
        cmd += f" --permission-mode {mode}"
    return cmd


def _is_git_repo(path: str) -> bool:
    """Check if *path* is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--git-dir"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _create_worktree(repo_dir: str, label: str) -> str | None:
    """Create a git worktree and return its path, or None on failure.

    Worktree is created at ``<repo>/.worktrees/gits-<label-slug>/``.
    A new branch ``gits/<label-slug>`` is created from HEAD.
    """
    import re

    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", label)[:30].strip("-")
    if not slug:
        slug = "session"

    wt_dir = str(Path(repo_dir) / ".worktrees" / f"gits-{slug}")
    branch = f"gits/{slug}"

    # Ensure parent dir exists
    Path(wt_dir).parent.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", "-b", branch, wt_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return wt_dir
        # Branch may already exist — try without -b
        result = subprocess.run(
            ["git", "-C", repo_dir, "worktree", "add", wt_dir],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return wt_dir
        logger.error("git worktree add failed: %s", result.stderr)
        return None
    except Exception:
        logger.exception("Failed to create worktree")
        return None


def _remove_worktree(worktree_path: str) -> bool:
    """Remove a git worktree. Returns True on success."""
    try:
        # Find the main repo dir from the worktree's .git file
        git_file = Path(worktree_path) / ".git"
        repo_dir = worktree_path
        if git_file.is_file():
            # .git file contains: "gitdir: /path/to/repo/.git/worktrees/..."
            content = git_file.read_text().strip()
            if content.startswith("gitdir:"):
                gitdir = content.split(":", 1)[1].strip()
                # Go up from .git/worktrees/<name> to repo root
                repo_dir = str(Path(gitdir).resolve().parent.parent.parent)

        result = subprocess.run(
            ["git", "-C", repo_dir, "worktree", "remove", "--force", worktree_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        logger.exception("Failed to remove worktree %s", worktree_path)
        return False


def _is_worktree(path: str) -> bool:
    """Check if *path* is a git worktree (not the main working tree)."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False
        # Check if it's a worktree by looking for .git file (not dir)
        git_path = Path(path) / ".git"
        return git_path.is_file()  # worktrees have a .git *file*, not dir
    except Exception:
        return False


def _worktree_dirty_files(worktree_path: str) -> list[str]:
    """Return list of dirty files in a worktree (empty if clean)."""
    try:
        result = subprocess.run(
            ["git", "-C", worktree_path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.strip().split("\n") if line.strip()]
    except Exception:
        return []


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
