"""Discord adapter — discord.py implementation of PlatformAdapter.

Handles slash commands, button interactions, message forwarding,
and thread lifecycle.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..base import (
    Button,
    ButtonCallback,
    IncomingMessage,
    MessageCallback,
    OutgoingMessage,
    PlatformAdapter,
    SelectOption,
)

logger = logging.getLogger(__name__)


class DiscordAdapter(PlatformAdapter):
    """Discord implementation of PlatformAdapter using discord.py."""

    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        allowed_guilds: list[int] | None = None,
        bind_root: "Path | None" = None,
    ):
        from pathlib import Path

        self.token = token
        self.allowed_users = set(allowed_users or [])
        self.allowed_guilds = set(allowed_guilds or [])
        self.bind_root: Path = (bind_root or Path.cwd()).expanduser().resolve()

        # Message / button callbacks (set by Core Engine)
        self._message_callbacks: list[MessageCallback] = []
        self._button_callbacks: list[ButtonCallback] = []

        # Bot setup
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        self.bot = commands.Bot(
            command_prefix="!",
            intents=intents,
        )

        # Register event handlers.
        # _handle_message is our handler but discord.py requires the name
        # "on_message" — we use a thin wrapper to avoid colliding with the
        # PlatformAdapter.on_message() callback-registration method.
        self.bot.event(self.on_ready)

        @self.bot.event
        async def on_message(message: discord.Message) -> None:
            await self._handle_message(message)

        self.bot.event(self.on_interaction)

        @self.bot.event
        async def on_thread_create(thread: discord.Thread) -> None:
            await self._handle_thread_create(thread)

        @self.bot.event
        async def on_thread_update(before: discord.Thread, after: discord.Thread) -> None:
            await self._handle_thread_update(before, after)

        @self.bot.event
        async def on_thread_delete(thread: discord.Thread) -> None:
            await self._handle_thread_delete(thread)

        # We'll register slash commands in _setup_commands()
        self._engine: Any = None  # Set by Core Engine

    def set_engine(self, engine: Any) -> None:
        """Set reference to the Core Engine (for command handlers)."""
        self._engine = engine

    # ------------------------------------------------------------------
    # PlatformAdapter interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the Discord bot."""
        self._setup_commands()
        await self.bot.start(self.token)

    async def stop(self) -> None:
        """Shut down the Discord bot."""
        await self.bot.close()

    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        """Send a message to a Discord channel/thread.

        Long text is automatically split into multiple messages to avoid
        Discord's 2000-character limit.
        """
        from gits.core.jsonl_monitor import split_message

        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))

        text_chunks = split_message(msg.text, 2000) if msg.text else [""]

        # Send all chunks; attach image/buttons only to the last one.
        sent: Any = None
        for i, chunk in enumerate(text_chunks):
            kwargs: dict[str, Any] = {}
            if chunk:
                kwargs["content"] = chunk

            is_last = i == len(text_chunks) - 1
            if is_last:
                if msg.image:
                    kwargs["file"] = discord.File(
                        io.BytesIO(msg.image), filename="screenshot.png"
                    )
                if msg.select_options or msg.buttons:
                    kwargs["view"] = self._build_view(
                        button_rows=msg.buttons,
                        select_options=msg.select_options,
                        select_placeholder=msg.select_placeholder,
                    )

            sent = await channel.send(**kwargs)

        logger.info(
            "Discord POST ch=%s msg_id=%s content=%s",
            channel_id, sent.id, (msg.text or "")[:60],
        )
        return str(sent.id)

    async def edit_message(
        self, channel_id: str, message_id: str, msg: OutgoingMessage
    ) -> None:
        """Edit an existing Discord message."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))

        message = await channel.fetch_message(int(message_id))

        kwargs: dict[str, Any] = {}
        if msg.text is not None:
            kwargs["content"] = msg.text[:2000]
        if msg.select_options or msg.buttons:
            kwargs["view"] = self._build_view(
                button_rows=msg.buttons,
                select_options=msg.select_options,
                select_placeholder=msg.select_placeholder,
            )

        await message.edit(**kwargs)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a Discord message."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))

        message = await channel.fetch_message(int(message_id))
        await message.delete()

    def on_message(self, callback: MessageCallback) -> None:
        self._message_callbacks.append(callback)

    def on_button_click(self, callback: ButtonCallback) -> None:
        self._button_callbacks.append(callback)

    async def create_thread(
        self,
        channel_id: str,
        title: str,
        auto_archive_minutes: int = 10080,
    ) -> str:
        """Create a Discord thread."""
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))

        thread = await channel.create_thread(
            name=title,
            auto_archive_duration=auto_archive_minutes,
        )
        return str(thread.id)

    async def archive_thread(self, thread_id: str) -> None:
        """Archive a Discord thread."""
        thread = self.bot.get_channel(int(thread_id))
        if thread is None:
            thread = await self.bot.fetch_channel(int(thread_id))
        if isinstance(thread, discord.Thread):
            await thread.edit(archived=True)

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def _check_access(self, user_id: int, guild_id: int | None = None) -> bool:
        """Check if a user / guild is allowed."""
        if not self.allowed_users and not self.allowed_guilds:
            return True  # no restrictions
        if self.allowed_users and user_id in self.allowed_users:
            return True
        return bool(
            self.allowed_guilds and guild_id and guild_id in self.allowed_guilds
        )

    # ------------------------------------------------------------------
    # Discord event handlers
    # ------------------------------------------------------------------

    async def on_ready(self) -> None:
        """Called when the bot is ready."""
        logger.info("Discord bot ready as %s", self.bot.user)
        try:
            synced = await self.bot.tree.sync()
            logger.info("Synced %d slash commands", len(synced))
        except Exception:
            logger.exception("Failed to sync slash commands")

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages (non-command text).

        Named ``_handle_message`` (not ``on_message``) to avoid shadowing
        ``PlatformAdapter.on_message()`` which registers callbacks.
        Registered with discord.py via a wrapper in ``__init__``.
        """
        # Ignore bot's own messages
        if message.author == self.bot.user:
            return

        # Ignore Discord system messages (e.g. "started a thread", pins, etc.)
        if message.type != discord.MessageType.default and message.type != discord.MessageType.reply:
            return

        # Ignore slash command interactions (handled separately)
        if message.content.startswith("/"):
            return

        # Access control
        guild_id = message.guild.id if message.guild else None
        if not self._check_access(message.author.id, guild_id):
            return

        logger.debug(
            "on_message from %s in %s: %s",
            message.author,
            message.channel.id,
            (message.content or "")[:80],
        )

        # Only react and forward if this channel is bound
        channel_id = str(message.channel.id)
        is_bound = bool(
            self._engine and self._engine.session_mgr.get_binding(channel_id)
        )
        if not is_bound:
            await self.bot.process_commands(message)
            return

        # React 👀 to acknowledge we received the message
        try:
            await message.add_reaction("👀")
        except Exception:
            logger.debug("Failed to add 👀 reaction")

        # Convert to IncomingMessage
        incoming = IncomingMessage(
            platform="discord",
            channel_id=channel_id,
            user_id=str(message.author.id),
            text=message.content or None,
            image_paths=[],  # TODO: handle attachments
            reply_to=str(message.reference.message_id) if message.reference else None,
            raw=message,
        )

        for cb in self._message_callbacks:
            try:
                await cb(incoming)
            except Exception:
                logger.exception("Message callback error")

        # React ⚙️ after forwarding to tmux
        try:
            await message.add_reaction("⚙️")
        except Exception:
            logger.debug("Failed to add ⚙️ reaction")

        # Let commands.Bot process prefix commands (e.g. !bash)
        await self.bot.process_commands(message)

    async def _handle_thread_create(self, thread: discord.Thread) -> None:
        """Handle a new thread being created in a bound channel.

        If the parent channel has an active binding and this thread
        was NOT created by the bot itself, auto-create a child session.
        """
        if not self._engine:
            return

        # Ignore threads created by the bot (e.g. from /thread or /fork)
        if thread.owner_id == self.bot.user.id:
            return

        # Check access
        guild_id = thread.guild.id if thread.guild else None
        if thread.owner_id and not self._check_access(thread.owner_id, guild_id):
            return

        parent_id = str(thread.parent_id) if thread.parent_id else None
        if not parent_id:
            return

        # Get starter message
        starter_message = ""
        try:
            if thread.starter_message:
                starter_message = thread.starter_message.content or ""
            else:
                # Fetch it
                starter = await thread.fetch_message(thread.id)
                starter_message = starter.content or ""
        except Exception:
            logger.debug("Could not fetch starter message for thread %s", thread.id)

        try:
            await self._engine.handle_thread_auto(
                thread_id=str(thread.id),
                parent_channel_id=parent_id,
                starter_message=starter_message,
            )
        except Exception:
            logger.exception("Failed to auto-create session for thread %s", thread.id)

    async def _handle_thread_update(
        self, before: discord.Thread, after: discord.Thread
    ) -> None:
        """Kill session when a thread is archived."""
        if not self._engine:
            return
        if not before.archived and after.archived:
            thread_id = str(after.id)
            try:
                await self._engine._kill_single(thread_id)
            except Exception:
                logger.debug("Failed to kill session for archived thread %s", thread_id)

    async def _handle_thread_delete(self, thread: discord.Thread) -> None:
        """Kill session when a thread is deleted."""
        if not self._engine:
            return
        thread_id = str(thread.id)
        try:
            await self._engine._kill_single(thread_id)
        except Exception:
            logger.debug("Failed to kill session for deleted thread %s", thread_id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle button and select menu interactions."""
        if interaction.type != discord.InteractionType.component:
            return

        if not interaction.data:
            return

        # component_type: 2 = button, 3 = select menu
        component_type = interaction.data.get("component_type", 2)

        if component_type == 3:
            # Select menu — use the selected value as callback_data
            values = interaction.data.get("values", [])
            if not values:
                return
            callback_data = values[0]
        else:
            # Button
            callback_data = interaction.data.get("custom_id", "")
            if not callback_data:
                return

        # Acknowledge the interaction
        await interaction.response.defer()

        channel_id = str(interaction.channel_id)
        user_id = str(interaction.user.id)

        for cb in self._button_callbacks:
            try:
                await cb(channel_id, user_id, callback_data)
            except Exception:
                logger.exception("Button callback error")

    # ------------------------------------------------------------------
    # Slash command setup
    # ------------------------------------------------------------------

    def _setup_commands(self) -> None:
        """Register all slash commands with the bot's command tree."""
        tree = self.bot.tree

        # ── A. Native Commands ────────────────────────────────────────

        @tree.command(name="bind", description="Bind this channel to a project directory")
        @app_commands.describe(
            path="Project directory path",
            mode="Permission mode for the coding CLI",
            cli="Coding CLI to use (default: claude)",
        )
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="Normal (confirm)", value="default"),
                app_commands.Choice(name="YOLO (auto)", value="bypassPermissions"),
            ],
            cli=[
                app_commands.Choice(name="Claude Code", value="claude"),
                app_commands.Choice(name="Codex CLI (OpenAI)", value="codex"),
                app_commands.Choice(name="OpenCode", value="opencode"),
            ],
        )
        async def cmd_bind(
            interaction: discord.Interaction,
            path: str,
            mode: str | None = "bypassPermissions",
            cli: str | None = None,
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_bind(
                    str(interaction.channel_id), path, interaction,
                    mode=mode, cli=cli,
                )

        @cmd_bind.autocomplete("path")
        async def _bind_path_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            """Autocomplete directory paths as the user types."""
            return self._autocomplete_paths(current)

        @tree.command(name="unbind", description="Unbind this channel")
        async def cmd_unbind(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_unbind(
                    str(interaction.channel_id), interaction
                )

        @tree.command(name="thread", description="Create a thread with a new session (shared directory)")
        @app_commands.describe(
            message="Initial message to send to the new session",
        )
        async def cmd_thread(
            interaction: discord.Interaction,
            message: str,
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_thread(
                    str(interaction.channel_id), message, interaction
                )

        @tree.command(name="fork", description="Create a thread with isolated git worktree")
        @app_commands.describe(
            title="Thread title",
        )
        async def cmd_fork(
            interaction: discord.Interaction,
            title: str,
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_fork(
                    str(interaction.channel_id), title, interaction
                )

        @tree.command(name="mode", description="Switch permission mode (resumes session with new flag)")
        @app_commands.describe(mode="Permission mode to switch to")
        @app_commands.choices(
            mode=[
                app_commands.Choice(name="Normal (confirm)", value="default"),
                app_commands.Choice(name="YOLO (auto)", value="bypassPermissions"),
                app_commands.Choice(name="Auto (run tools)", value="auto"),
                app_commands.Choice(name="AcceptEdits (auto-edit)", value="acceptEdits"),
            ]
        )
        async def cmd_mode(
            interaction: discord.Interaction,
            mode: str,
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_mode(
                    str(interaction.channel_id), mode, interaction
                )

        @tree.command(name="screenshot", description="Take a terminal screenshot")
        async def cmd_screenshot(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_screenshot(
                    str(interaction.channel_id), interaction
                )

        @tree.command(name="status", description="Show binding status and info")
        async def cmd_status(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_status(
                    str(interaction.channel_id), interaction
                )

        @tree.command(name="esc", description="Send Escape key")
        async def cmd_esc(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_esc(
                    str(interaction.channel_id), interaction
                )

        @tree.command(name="kill", description="Kill the tmux window and archive thread")
        async def cmd_kill(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_kill(
                    str(interaction.channel_id), interaction
                )

        @tree.command(name="new", description="Reset the coding CLI session")
        @app_commands.describe(message="Optional initial message to send to the new session")
        async def cmd_new(interaction: discord.Interaction, message: str | None = None):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_new(
                    str(interaction.channel_id), interaction, message=message
                )

        @tree.command(name="bash", description="Run a shell command in the working directory")
        @app_commands.describe(command="Shell command to execute")
        async def cmd_bash(interaction: discord.Interaction, command: str):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_bash(
                    str(interaction.channel_id), command, interaction
                )

        # ── Send keys command ──────────────────────────────────────────

        @tree.command(name="keys", description="Send key sequence to tmux (↑↓⏎⎋)")
        @app_commands.describe(keys="Key sequence to send")
        @app_commands.choices(
            keys=[
                app_commands.Choice(name="↓ Down (move selection down)", value="Down"),
                app_commands.Choice(name="↑ Up (move selection up)", value="Up"),
                app_commands.Choice(name="⏎ Enter (confirm selection)", value="Enter"),
                app_commands.Choice(name="⎋ Escape (cancel)", value="Escape"),
                app_commands.Choice(name="↓⏎ Down + Enter (select option 2)", value="Down Enter"),
                app_commands.Choice(name="↓↓⏎ Down Down + Enter (select option 3)", value="Down Down Enter"),
                app_commands.Choice(name="^C Ctrl-C (abort)", value="C-c"),
                app_commands.Choice(name="Tab", value="Tab"),
                app_commands.Choice(name="Space", value="Space"),
            ]
        )
        async def cmd_keys(interaction: discord.Interaction, keys: str):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_keys(
                    str(interaction.channel_id), keys, interaction
                )

        # ── Model command (native with Discord choices) ────────────────

        @tree.command(name="model", description="Switch the AI model (claude: sonnet/opus/haiku, codex: o3/gpt-4o, etc.)")
        @app_commands.describe(name="Model name to switch to (e.g. sonnet, opus, o3, gpt-4o)")
        async def cmd_model(
            interaction: discord.Interaction, name: str | None = None
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_model(
                    str(interaction.channel_id), name, interaction
                )

        # ── B. Browser Agent ──────────────────────────────────────────

        @tree.command(name="browse", description="Run a browser agent task")
        @app_commands.describe(
            goal="Natural language goal for the browser agent",
            profile="Chrome profile to use (default: Personal Chrome)",
        )
        async def cmd_browse(
            interaction: discord.Interaction,
            goal: str,
            profile: str = "Personal Chrome",
        ):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_browse(
                    goal=goal,
                    profile=profile,
                    channel_id=str(interaction.channel_id),
                    interaction=interaction,
                )

        # ── C. CLI Forwarding Commands ────────────────────────────────

        for cmd_name, description in [
            ("compact", "Compact the context window"),
            ("clear", "Clear conversation history"),
            ("cost", "Show token usage and cost"),
            ("memory", "Edit project memory file"),
            ("context", "Show context window usage"),
            ("diff", "Show code changes"),
            ("usage", "Show rate limit and usage info"),
        ]:
            self._register_forward_command(tree, cmd_name, description)

        # ── D. Universal Forwarder ────────────────────────────────────

        @tree.command(name="cc", description="Forward any command to the coding CLI")
        @app_commands.describe(command="CLI command to forward (without leading /)")
        async def cmd_cc(interaction: discord.Interaction, command: str):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_cli_forward(
                    str(interaction.channel_id), command, interaction
                )

    def _register_forward_command(
        self, tree: app_commands.CommandTree, name: str, description: str
    ) -> None:
        """Register a CLI-forwarding slash command."""

        @tree.command(name=name, description=description)
        async def _forward(interaction: discord.Interaction, args: str | None = None):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            cmd = f"/{name}" + (f" {args}" if args else "")
            if self._engine:
                await self._engine.handle_cli_forward(
                    str(interaction.channel_id), cmd, interaction
                )

    def _check_interaction_access(self, interaction: discord.Interaction) -> bool:
        guild_id = interaction.guild_id
        return self._check_access(interaction.user.id, guild_id)

    # ------------------------------------------------------------------
    # Path autocomplete
    # ------------------------------------------------------------------

    def _autocomplete_paths(self, current: str) -> list[app_commands.Choice[str]]:
        """Build autocomplete choices for directory paths.

        Starts from the bot's working directory (cwd). As the user types,
        lists subdirectories matching the input. Returns up to 25 choices.

        IMPORTANT: Discord fills the input field with the choice's ``value``
        on selection. We use absolute paths as values so that subsequent
        autocomplete calls can resolve them correctly.  The ``name``
        (display label) is kept short: ``path_tail/`` with max 100 chars.
        """
        from pathlib import Path

        choices: list[app_commands.Choice[str]] = []

        def _label(p: Path, prefix: str = "") -> str:
            """Short display label (Discord max 100 chars for name)."""
            short = f"{prefix}{p.name}/"
            if len(short) > 95:
                short = short[:92] + "..."
            return short

        def _add(label: str, value: str) -> None:
            # Discord: name max 100 chars, value max 100 chars
            if len(value) > 100:
                value = value[:100]
            choices.append(app_commands.Choice(name=label[:100], value=value))

        # Resolve the current input to an absolute path
        if not current or current == ".":
            target = self.bind_root
        else:
            p = Path(current).expanduser()
            if not p.is_absolute():
                p = Path.cwd() / p
            target = p.resolve() if p.exists() else p

        if target.is_dir():
            # Show: select current, parent, then subdirectories
            _add(f"✅ Select: {target.name or str(target)}/", str(target))

            if target.parent != target:
                _add(f"⬆ .. ({target.parent.name or '/'}/) ", str(target.parent))

            try:
                for entry in sorted(target.iterdir()):
                    if entry.is_dir() and not entry.name.startswith("."):
                        _add(f"📁 {entry.name}/", str(entry))
                        if len(choices) >= 25:
                            break
            except OSError:
                pass

        elif target.parent.is_dir():
            # Partial name typed — filter parent's children
            parent = target.parent
            prefix = target.name.lower()

            _add(f"⬆ .. ({parent.name or '/'}/) ", str(parent))

            try:
                for entry in sorted(parent.iterdir()):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    if prefix and not entry.name.lower().startswith(prefix):
                        continue
                    _add(f"📁 {entry.name}/", str(entry))
                    if len(choices) >= 25:
                        break
            except OSError:
                pass

        return choices[:25]

    # ------------------------------------------------------------------
    # Button builder
    # ------------------------------------------------------------------

    def _build_view(
        self,
        button_rows: list[list[Button]] | None = None,
        select_options: list[SelectOption] | None = None,
        select_placeholder: str | None = None,
    ) -> discord.ui.View:
        """Convert our Button/SelectOption models to a discord.py View."""
        view = discord.ui.View(timeout=None)

        # Add Select Menu first (appears above buttons in Discord)
        if select_options:
            opts = [
                discord.SelectOption(
                    label=opt.label[:100],
                    value=opt.value[:100],
                    description=(opt.description[:100] if opt.description else None),
                )
                for opt in select_options[:25]  # Discord max 25
            ]
            select = discord.ui.Select(
                placeholder=(select_placeholder or "Select an option…")[:150],
                options=opts,
                custom_id="session_picker",
            )
            view.add_item(select)

        # Add buttons below
        if button_rows:
            for row in button_rows:
                for btn in row:
                    view.add_item(
                        discord.ui.Button(
                            label=btn.label,
                            custom_id=btn.callback_data,
                            style=discord.ButtonStyle.secondary,
                        )
                    )

        return view
