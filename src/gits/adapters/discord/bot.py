"""Discord adapter — discord.py implementation of PlatformAdapter.

Handles slash commands, button interactions, message forwarding,
and thread lifecycle.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

# Messages authored by the bot itself are normally skipped to avoid self-loops.
# Vault-dispatched messages (sent via the bot's REST token from outside, e.g.
# the `butler` CLI) tag themselves with a recognizable prefix so they get
# forwarded to the bound CLI session.
#
# Default accepts: [butler], [butler:user], [管家], [管家:user], [vault], [vault:user]
# Override via GITS_DISPATCH_PREFIX_PATTERN env var (a Python regex).
_VAULT_DISPATCH_RE = re.compile(
    os.environ.get(
        "GITS_DISPATCH_PREFIX_PATTERN",
        r"^\[(butler|管家|vault)(:[^\]]*)?\]",
    )
)

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


def _build_cli_choices() -> list[app_commands.Choice]:
    """Build /bind cli choices from built-ins + ~/.gits/config.json aliases."""
    choices = [
        app_commands.Choice(name="Claude Code", value="claude"),
        app_commands.Choice(name="Codex CLI (OpenAI)", value="codex"),
        app_commands.Choice(name="OpenCode", value="opencode"),
    ]
    config_path = Path.home() / ".gits" / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            for alias in cfg.get("cli_aliases", {}):
                choices.append(app_commands.Choice(name=alias, value=alias))
        except Exception:
            pass
    return choices


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

            try:
                sent = await channel.send(**kwargs)
            except discord.HTTPException as e:
                if msg.select_options or msg.buttons:
                    n_opts = len(msg.select_options or [])
                    n_btns = sum(len(r) for r in (msg.buttons or []))
                    opt_chars = sum(
                        len(o.label or "") + len(o.value or "") + len(o.description or "")
                        for o in (msg.select_options or [])
                    )
                    btn_chars = sum(
                        len(b.label or "") + len(b.callback_data or "")
                        for r in (msg.buttons or []) for b in r
                    )
                    logger.error(
                        "Discord POST failed ch=%s status=%s text_len=%d "
                        "select_opts=%d opt_chars=%d buttons=%d btn_chars=%d "
                        "placeholder=%r text_preview=%r",
                        channel_id, getattr(e, "status", "?"), len(chunk),
                        n_opts, opt_chars, n_btns, btn_chars,
                        (msg.select_placeholder or "")[:80],
                        (msg.text or "")[:120],
                    )
                raise

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
            await thread.edit(archived=True, locked=True)

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
            if self.allowed_guilds:
                total = 0
                for gid in self.allowed_guilds:
                    guild = discord.Object(id=gid)
                    self.bot.tree.copy_global_to(guild=guild)
                    synced = await self.bot.tree.sync(guild=guild)
                    total += len(synced)
                logger.info(
                    "Synced %d slash commands across %d guild(s)",
                    total,
                    len(self.allowed_guilds),
                )
            else:
                synced = await self.bot.tree.sync()
                logger.info("Synced %d slash commands (global)", len(synced))
        except Exception:
            logger.exception("Failed to sync slash commands")

    async def _handle_message(self, message: discord.Message) -> None:
        """Handle incoming Discord messages (non-command text).

        Named ``_handle_message`` (not ``on_message``) to avoid shadowing
        ``PlatformAdapter.on_message()`` which registers callbacks.
        Registered with discord.py via a wrapper in ``__init__``.
        """
        # Ignore bot's own messages, except those tagged as vault-dispatched.
        # Pattern: leading "[butler]" or "[butler:<user>]" marks a message
        # sent from a vault session via the bot's REST token (see butler CLI).
        vault_match = None
        if message.author == self.bot.user:
            vault_match = _VAULT_DISPATCH_RE.match(message.content or "")
            if not vault_match:
                return
            logger.info("butler-dispatched: %s", (message.content or "")[:120])

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

        channel_id = str(message.channel.id)

        # Butler-dispatched slash commands (e.g. ``[butler:user] /bind <path>``)
        # are routed to engine handlers instead of forwarded to the CLI session.
        # Regular user messages can't reach this branch because line 293 already
        # returns early for any content starting with "/".
        if vault_match is not None:
            payload = (message.content or "")[vault_match.end():].lstrip()
            if payload.startswith("/"):
                if await self._handle_butler_command(message, channel_id, payload):
                    return

        # Only react and forward if this channel is bound
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

        # Download image attachments to /tmp
        image_paths = await self._download_attachments(message.attachments)

        # Convert to IncomingMessage
        incoming = IncomingMessage(
            platform="discord",
            channel_id=channel_id,
            user_id=str(message.author.id),
            text=message.content or None,
            image_paths=image_paths,
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

    async def _handle_butler_command(
        self, message: discord.Message, channel_id: str, payload: str
    ) -> bool:
        """Dispatch butler-prefixed slash commands (e.g. ``/bind``, ``/unbind``).

        Returns True when *payload* was handled here and the caller should
        skip the normal forward-to-CLI flow; False for unknown commands so
        they fall through (and ultimately get ignored — they start with "/").
        """
        if not self._engine:
            return False

        parts = payload.split()
        cmd = parts[0]
        args = parts[1:]

        if cmd == "/bind":
            # Parse positionals (path, cli) and flags (--fresh, --resume=<id>).
            positionals: list[str] = []
            explicit_fresh = False
            resume_id: str | None = None
            for tok in args:
                if tok == "--fresh":
                    explicit_fresh = True
                elif tok.startswith("--resume="):
                    resume_id = tok[len("--resume="):]
                else:
                    positionals.append(tok)

            if not positionals:
                try:
                    await message.reply(
                        "⚠️ `/bind` requires a path: "
                        "`/bind <path> [cli] [--fresh|--resume=<id>]`"
                    )
                except Exception:
                    logger.debug("Failed to reply to butler /bind")
                return True

            if explicit_fresh and resume_id:
                try:
                    await message.reply(
                        "⚠️ `--fresh` and `--resume` are mutually exclusive."
                    )
                except Exception:
                    logger.debug("Failed to reply to butler /bind")
                return True

            path = positionals[0]
            cli = positionals[1] if len(positionals) >= 2 else None

            # Picker is unusable from butler, so default (no flag) and --fresh
            # both go through the fresh path.
            fresh = resume_id is None

            # Discover sessions up-front so we can validate --resume=<id> and
            # include a "you could also resume X" hint when defaulting fresh.
            sessions: list = []
            discover_cli = cli or self._engine.settings.coding_cli_command
            try:
                resolved_path = Path(path).expanduser().resolve()
                if resolved_path.is_dir():
                    sessions = self._engine.launcher.discover_all_sessions(
                        str(resolved_path), target_cli=discover_cli
                    )
            except Exception:
                logger.debug("butler /bind: session discovery failed", exc_info=True)

            if resume_id:
                match = next(
                    (s for s in sessions if s.session_id == resume_id), None
                )
                if match is None:
                    try:
                        await message.reply(
                            f"⚠️ No session `{resume_id}` found in `{path}`. "
                            f"Run `/bind {path}` to see available sessions."
                        )
                    except Exception:
                        logger.debug("Failed to reply to butler /bind")
                    return True
                # v1: refuse cross-CLI resume from butler. The Discord UI's
                # button-resume path handles this via _handle_cross_cli_import;
                # butler can't drive that flow yet.
                try:
                    target_type = self._engine.launcher.resolve_cli(
                        discover_cli
                    ).base_type
                    source_type = (
                        self._engine.launcher.resolve_cli(match.source_cli).base_type
                        if match.source_cli
                        else target_type
                    )
                except Exception:
                    target_type = source_type = ""
                if target_type and source_type and target_type != source_type:
                    try:
                        await message.reply(
                            f"⚠️ Session `{resume_id}` originated from "
                            f"`{source_type}`; target CLI is `{target_type}`. "
                            f"Cross-CLI resume isn't supported via butler — "
                            f"use the Discord UI."
                        )
                    except Exception:
                        logger.debug("Failed to reply to butler /bind")
                    return True

            try:
                await message.add_reaction("🔗")
            except Exception:
                logger.debug("Failed to add 🔗 reaction")

            try:
                await self._engine.handle_bind(
                    channel_id,
                    path,
                    interaction=None,
                    mode="bypassPermissions",
                    cli=cli,
                    fresh=fresh,
                    session_id=resume_id,
                )
            except Exception:
                logger.exception("butler /bind failed")
                return True

            # When defaulting to fresh, surface any existing sessions so vault
            # can re-run with --resume=<id> if it actually wanted one of them.
            if fresh and sessions:
                lines = ["Existing sessions you could resume:"]
                for s in sessions[:10]:
                    summary = (s.summary or "").strip()[:60] or "(no summary)"
                    lines.append(f"• `{s.session_id}` — {summary}")
                if len(sessions) > 10:
                    lines.append(f"…and {len(sessions) - 10} more")
                lines.append(f"Use `/bind {path} --resume=<id>` to switch.")
                try:
                    await self.send_message(
                        channel_id, OutgoingMessage(text="\n".join(lines))
                    )
                except Exception:
                    logger.debug("Failed to send sessions hint")

            return True

        if cmd == "/unbind":
            try:
                await message.add_reaction("🔓")
            except Exception:
                logger.debug("Failed to add 🔓 reaction")
            try:
                await self._engine.handle_unbind(channel_id, interaction=None)
            except Exception:
                logger.exception("butler /unbind failed")
            return True

        return False

    async def _download_attachments(self, attachments: list[discord.Attachment]) -> list[str]:
        """Download image attachments to /tmp and return their local file paths."""
        paths: list[str] = []
        for att in attachments:
            if not att.content_type or not att.content_type.startswith("image/"):
                continue
            ext = Path(att.filename).suffix or ".png"
            tmp_path = f"/tmp/gits_dc_{uuid.uuid4().hex}{ext}"
            try:
                data = await att.read()
                Path(tmp_path).write_bytes(data)
                paths.append(tmp_path)
                logger.info("DiscordAdapter: saved attachment %s → %s (%d bytes)", att.filename, tmp_path, len(data))
            except Exception:
                logger.exception("DiscordAdapter: failed to download attachment %s", att.filename)
        return paths

    async def _handle_thread_create(self, thread: discord.Thread) -> None:
        """Handle a new thread being created in a bound channel.

        Auto-create a child session when the parent channel has an
        active binding and the thread originated from a user (or a
        butler-dispatched message — see exception below).

        Bot-created threads are normally ignored to avoid double-handling
        with ``/thread`` and ``/fork`` slash commands. Exception: when
        butler dispatches a task via the REST API it uses this bot's
        identity, so the thread's ``owner_id`` IS the bot — but the
        starter message carries the ``[butler|管家|vault[:user]]`` prefix
        that ``_VAULT_DISPATCH_RE`` recognizes. Those opt back in to the
        auto-bind path so the dispatched task lands in a live session.
        """
        if not self._engine:
            return

        parent_id = str(thread.parent_id) if thread.parent_id else None
        if not parent_id:
            return

        # Fetch starter message early so we can tell butler dispatches
        # apart from /thread, /fork, and any other future bot-created
        # threads. Empty string on miss — that's still "not butler".
        starter_message = ""
        try:
            if thread.starter_message:
                starter_message = thread.starter_message.content or ""
            else:
                starter = await thread.fetch_message(thread.id)
                starter_message = starter.content or ""
        except Exception:
            logger.debug("Could not fetch starter message for thread %s", thread.id)

        # Bot-created thread: only proceed when the starter message is a
        # butler-prefix dispatch. /thread and /fork don't carry the prefix,
        # so they still skip (their own flow handles session creation).
        if thread.owner_id == self.bot.user.id:
            if not _VAULT_DISPATCH_RE.match(starter_message):
                return

        # Access check (skip for bot-owned butler dispatches — the
        # author identity is the bot, which always has access to its
        # own channels).
        if thread.owner_id != self.bot.user.id:
            guild_id = thread.guild.id if thread.guild else None
            if thread.owner_id and not self._check_access(thread.owner_id, guild_id):
                return

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

        if not self._check_interaction_access(interaction):
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

        # Global app-command error handler: suppress expired-interaction errors
        # (10062 "Unknown interaction") that occur when the bot restarts while a
        # slash command is in-flight.  Discord shows "application did not respond"
        # to the user in these cases; there is nothing we can do server-side, but
        # we should log a warning rather than a full traceback.
        @tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            import discord as _discord
            original = getattr(error, "original", error)
            if (
                isinstance(original, _discord.errors.NotFound)
                and getattr(original, "code", None) == 10062
            ):
                cmd_name = interaction.command.name if interaction.command else "unknown"
                logger.warning(
                    "Interaction expired (10062) for /%s — "
                    "bot was likely restarting when the command arrived",
                    cmd_name,
                )
                return
            cmd_name = interaction.command.name if interaction.command else "unknown"
            logger.error(
                "App command error in /%s: %s",
                cmd_name,
                error,
                exc_info=error,
            )
            # If the interaction was deferred, send a followup so Discord
            # stops showing "thinking".
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"⚠️ `/{cmd_name}` failed: {original}",
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ `/{cmd_name}` failed: {original}",
                        ephemeral=True,
                    )
            except Exception:
                pass

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
            cli=_build_cli_choices(),
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
            # Check bot has send-message permission before attempting bind.
            channel = interaction.channel
            if channel and interaction.guild:
                perms = channel.permissions_for(interaction.guild.me)
                if not perms.send_messages:
                    await interaction.response.send_message(
                        "⚠️ Bot does not have **Send Messages** permission in this channel and cannot bind.\n"
                        "Go to the channel/category Settings → Permissions → enable **Send Messages** for the bot role.",
                        ephemeral=True,
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
            if not self._check_interaction_access(interaction):
                return []
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
                try:
                    await self._engine.handle_thread(
                        str(interaction.channel_id), message, interaction
                    )
                except Exception as exc:
                    logger.exception("cmd_thread failed")
                    try:
                        await interaction.followup.send(f"Failed to create thread: {exc}")
                    except Exception:
                        pass

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

        @tree.command(name="info", description="Show binding status and info")
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

        @tree.command(name="enter", description="Send Enter key")
        async def cmd_enter(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_enter(
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

        @tree.command(name="done", description="End the work session and close this thread")
        async def cmd_done(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message(
                    "Access denied.", ephemeral=True
                )
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_done(
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

        # ── E. Account management (Phase 0.12) ────────────────────────
        # Multi-account isolation via CLAUDE_CONFIG_DIR.
        # The deprecated ``/subscriptions`` and ``/sub-switch`` commands are
        # NOT registered — per the spec, only ``/accounts`` and
        # ``/account-switch`` are exposed via Discord. Credential add/remove
        # and session import remain CLI-only. The host-side ``gits subscription``
        # subcommands still exist (with deprecation hints) for V1 transition,
        # but no equivalent Discord surface is provided.

        @tree.command(name="accounts", description="List Claude accounts with live OAuth Usage data")
        async def cmd_accounts_list(interaction: discord.Interaction):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message("Access denied.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            if self._engine:
                await self._engine.handle_accounts_list(
                    str(interaction.channel_id), interaction
                )

        @tree.command(
            name="account-switch",
            description=(
                "Switch this channel's binding to a different Claude account "
                "(auto-imports current session)"
            ),
        )
        @app_commands.describe(name="Account name to switch to")
        async def cmd_account_switch(interaction: discord.Interaction, name: str):
            if not self._check_interaction_access(interaction):
                await interaction.response.send_message("Access denied.", ephemeral=True)
                return
            await interaction.response.defer()
            if self._engine:
                await self._engine.handle_account_switch(
                    str(interaction.channel_id), name, interaction
                )

        @cmd_account_switch.autocomplete("name")
        async def _account_switch_autocomplete(
            interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
            if not self._check_interaction_access(interaction):
                return []
            if not self._engine:
                return []
            try:
                manifest = self._engine.account_vault.load()
            except Exception:
                return []
            choices: list[app_commands.Choice[str]] = []
            for a in manifest.accounts:
                if current and current.lower() not in a.name.lower():
                    continue
                tag = " (default)" if a.name == manifest.default else ""
                choices.append(app_commands.Choice(name=a.name + tag, value=a.name))
                if len(choices) >= 25:
                    break
            return choices

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

        # Resolve the current input to an absolute path.
        # Relative paths should always be anchored to bind_root, not the
        # bot process cwd. Otherwise autocomplete behavior differs across
        # hosts depending on where the service was started from.
        if not current or current == ".":
            target = self.bind_root
        else:
            p = Path(current).expanduser()
            if not p.is_absolute():
                p = self.bind_root / p
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
