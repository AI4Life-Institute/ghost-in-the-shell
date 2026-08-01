"""Core Engine — wires all modules together and handles commands.

The engine is platform-agnostic: it receives commands from any adapter
and delegates to TmuxController, SessionManager, ScreenshotEngine, etc.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adapters.base import Button, IncomingMessage, OutgoingMessage, SelectOption
from ..config import Settings
from ..telemetry import platform_for, track
from .account import AccountLayout, SwitchResult, effective_account
from .account_vault import AccountVault
from .guard import GuardHandler
from .health import HealthMonitor
from .jsonl_monitor import JsonlMonitor
from .launcher import CLISession, CodingCLILauncher, prefix_account_env
from .monitor import PaneMonitor
from .quota import QuotaPatternMatcher, QuotaSignalDebouncer
from .quota_notifier import QuotaNotifier
from .screenshot import ScreenshotEngine
from .session import SessionManager
from .subscription import SubscriptionVault, SwitchPrimitive
from .terminal_parser import PromptInfo, parse_status_line
from .tmux import TmuxController
from .usage_panel import format_usage_panel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /model help copy — single source of truth
# ---------------------------------------------------------------------------
# Both the engine's no-name help branch and the Discord slash-command
# description draw their model copy from here so they can't drift apart as
# new models ship. Deliberately lists stable *aliases* only (no version
# numbers) plus a catch-all for any model name the CLI accepts. See task
# ctn2c4. ``MODEL_CMD_DESCRIPTION`` must stay within Discord's 100-char
# slash-command description limit.

MODEL_HELP = (
    "Usage: `/model <name>` — switch the coding CLI model.\n\n"
    "**Claude Code aliases** (track the latest model per provider):\n"
    "`default` (your account's recommended) · `best` (most capable) · "
    "`opus` · `sonnet` · `haiku` · `opus[1m]` · `sonnet[1m]` (1M context) · "
    "`opusplan` (Opus to plan, Sonnet to execute)\n\n"
    "**Codex / other CLIs** — or any model name your CLI accepts "
    "(e.g. `o3`, `gpt-4o`).\n\n"
    "Aliases always point at the latest model; pass a full model ID to "
    "pin a specific version."
)

MODEL_CMD_DESCRIPTION = (
    "Switch the coding CLI model — run with no name to list aliases "
    "(default, best, opus, sonnet…)"
)


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
        from .subscription import active_env_file_path

        # Multi-account isolation (Phase 0.2/0.3/0.5/0.7).
        self.account_layout = AccountLayout()
        self.account_vault = AccountVault(
            state_dir=settings.state_dir, layout=self.account_layout
        )
        # Per-binding switch_account locks; lazily populated.
        self._switch_locks: dict[str, asyncio.Lock] = {}

        # Per ``add-default-account-native-and-refresh``: when default
        # account routes to native ~/.claude/ but the isolated dir has
        # newer credentials, the user may see one re-login. Surface it.
        self._warn_if_default_credentials_diverge()

        self.launcher = CodingCLILauncher(
            session_map_path=settings.session_map_file,
            config_path=settings.config_file,
            active_env_file=active_env_file_path(settings.state_dir),
            account_layout=self.account_layout,
            account_vault=self.account_vault,
        )
        self.health = HealthMonitor(
            tmux=self.tmux,
            session_mgr=self.session_mgr,
            launcher=self.launcher,
            check_interval=settings.health_check_interval,
            credential_lock_path=settings.credential_lock_file,
        )
        self.monitor = PaneMonitor(
            tmux=self.tmux,
            session_mgr=self.session_mgr,
            interval=settings.pane_poll_interval,
        )
        self.jsonl_monitor = JsonlMonitor(
            session_mgr=self.session_mgr,
            poll_interval=settings.jsonl_poll_interval,
            launcher=self.launcher,
            tmux=self.tmux,
        )

        # Subscription credential vault (multi-account hot-swap).
        # Lazy: only meaningful once user runs `gits subscription add`.
        self.subscription_vault = SubscriptionVault(settings.subscriptions_dir)
        self.switch_primitive = SwitchPrimitive(
            tmux=self.tmux,
            session_mgr=self.session_mgr,
            launcher=self.launcher,
            vault=self.subscription_vault,
            lock_path=settings.credential_lock_file,
        )

        # Quota detection chain: pattern matcher → debouncer → notifier.
        # Wired even when no subscriptions exist so the cost is just regex
        # classification per JSONL/pane line, but events are dropped by the
        # notifier (which checks for an active subscription).
        self.quota_matcher = QuotaPatternMatcher(settings.quota_patterns_file)
        self.quota_matcher.load()
        self.quota_debouncer = QuotaSignalDebouncer(self.quota_matcher)
        self.quota_notifier = QuotaNotifier(
            vault=self.subscription_vault,
            notify=self._broadcast_to_bindings,
        )
        self._wire_quota_pipeline()

        # OAuth token refresh — in-process daily scheduler so non-default
        # accounts' refresh tokens stay alive without host-level launchd /
        # cron setup. Per ``add-default-account-native-and-refresh``.
        from .token_refresh import TokenRefreshScheduler
        self.token_refresh = TokenRefreshScheduler(
            vault=self.account_vault,
            layout=self.account_layout,
            state_dir=settings.state_dir,
        )

        # Deployment drift watch — in-process periodic scan (Ghost task
        # drftnt / ghost#37). No new daemon, and deliberately unreachable
        # from the guard: the guard runs on every tool call and must never
        # gain a network call or a notification path.
        from .drift_watch import DriftWatcher
        self.drift_watch = DriftWatcher(
            state_dir=settings.state_dir,
            notify=self._notify_ops_channel,
            interval_s=settings.ghost_drift_watch_interval_s,
        )

        # Guard handler (initialized in start())
        self.guard: GuardHandler | None = None

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

        # Detect legacy artifacts from earlier design drafts (see openspec
        # change add-multi-account-hotswap). These do not block startup but
        # surface guidance to the user.
        self._warn_legacy_artifacts()

        # Auto-install Claude Code SessionStart hook if not present
        self._ensure_hooks_installed()

        await self.tmux.ensure_session()

        # Defensive: rename any windows whose names contain \n/\t/\r/\0.
        # libtmux's list-windows parser crashes on those, which would
        # cascade into bind/health failures.
        try:
            await self.tmux.scrub_window_names()
        except Exception:
            logger.debug("startup: window-name scrub failed", exc_info=True)

        # Initialize guard handler for ops session
        from .skill_loader import SkillLoader
        loader = SkillLoader()
        ops_session = loader.load_ops_session()
        self.guard = GuardHandler(tmux=self.tmux, ops_session=ops_session)
        await self.guard.ensure_ops_session()

        self.health.set_engine(self)
        await self.health.start()

        # Register health recovery callback
        self.health.on_recovery(self._on_recovery)

        # Register pane monitor callbacks
        self.monitor.on_prompt(self._on_pane_prompt)

        # Resume polling for active (non-suspended) bindings only
        for binding in self.session_mgr.list_bindings():
            if not binding.suspended:
                self.monitor.start_polling(binding.channel_id, binding.window_id)

        # Start JSONL output monitoring
        self.jsonl_monitor.on_message(self._on_jsonl_message)
        self.jsonl_monitor.start()

        # Quota notifier (no-op until subscriptions are registered)
        await self.quota_notifier.start()

        # OAuth token refresh scheduler (daily, in-process).
        self.token_refresh.start()

        # Deployment drift watch (hourly, in-process).
        if self.settings.ghost_drift_watch_enabled:
            self.drift_watch.start()

        logger.info("Engine started")

    async def stop(self) -> None:
        """Stop the engine."""
        self.monitor.stop_all()
        self.jsonl_monitor.stop()
        await self.quota_notifier.stop()
        await self.token_refresh.stop()
        await self.drift_watch.stop()
        await self.health.stop()
        # Cancel all message drainer tasks
        logger.info("Engine stopped")

    def _wire_quota_pipeline(self) -> None:
        """Hook QuotaSignalDebouncer into JsonlMonitor and PaneMonitor."""

        def feed(channel_id: str, text: str) -> None:
            try:
                event = self.quota_debouncer.feed(channel_id, text)
                if event is not None:
                    self.quota_notifier.submit(event)
            except Exception:
                logger.exception("quota feed failed for %s", channel_id)

        self.jsonl_monitor.on_jsonl_line(feed)
        self.monitor.on_pane_text(feed)

    async def _broadcast_to_bindings(self, text: str) -> None:
        """Send *text* to every channel that currently has a binding.

        Used by QuotaNotifier for user-visible quota-exhaustion notifications.
        Falls through silently if no adapter is wired.
        """
        if self._adapter is None:
            return
        try:
            from ..adapters.base import OutgoingMessage
        except Exception:
            return
        bindings = self.session_mgr.list_bindings()
        for b in bindings:
            try:
                await self._adapter.send_message(
                    b.channel_id, OutgoingMessage(text=text)
                )
            except Exception:
                logger.debug(
                    "broadcast to %s failed", b.channel_id, exc_info=True
                )

    def _ops_channel_id(self) -> str | None:
        """The butler home channel of the checkout this code runs from.

        Reusing the binding that already exists rather than minting another
        channel setting: ghost#18 introduces ``GITS_WATCHDOG_ALERT_CHANNEL``
        for the same purpose and has not landed, and two half-owned routing
        keys is worse than either one.
        """
        try:
            from ..butler.identity import load_binding

            root = Path(__file__).resolve().parents[3]
            return load_binding(cwd=str(root)).get("channel_id") or None
        except Exception:
            logger.debug("ops channel lookup failed", exc_info=True)
            return None

    async def _notify_ops_channel(self, text: str) -> None:
        """Send an operator alert to the single ops channel.

        Deliberately **not** ``_broadcast_to_bindings``: a machine-level alert
        fanned out into every working session is the noise that gets the whole
        mechanism muted (operator answer Q1, 2026-06-01).

        Raises on any failure — the caller records the failure and keeps the
        incident outstanding, so an undelivered notice is never mistaken for a
        delivered one.
        """
        if self._adapter is None:
            raise RuntimeError("no platform adapter wired")
        cid = self._ops_channel_id()
        if cid is None:
            raise RuntimeError(
                "no butler home channel bound — run `ghost butler bind <channel_id>`"
            )
        await self._adapter.send_message(cid, OutgoingMessage(text=text))

    async def inject_message(self, session_name: str, text: str) -> None:
        """Inject text into a named tmux session (for Guard and other uses)."""
        from .guard import _get_session_window
        window_id = await asyncio.to_thread(
            lambda: _get_session_window(session_name)
        )
        if window_id:
            await self.tmux.send_text(window_id, text, submit_keys="\n")
        else:
            logger.warning("inject_message: no window found for session %s", session_name)

    # ------------------------------------------------------------------
    # Legacy artifact detection
    # ------------------------------------------------------------------

    def _warn_legacy_artifacts(self) -> None:
        """Surface legacy paths from older design drafts.

        See openspec change ``add-multi-account-hotswap``. These do not block
        startup; they hint at residual state from earlier (now-deprecated)
        design iterations so the user can clean up or migrate.
        """
        from pathlib import Path

        candidates = [
            (
                Path.home() / ".gits" / "subscriptions",
                "deprecated SubscriptionVault path; replaced by ~/.gits/accounts/",
            ),
            (
                Path.home() / ".gits" / "active-env.sh",
                "deprecated env-source-file mechanism; replaced by CLAUDE_CONFIG_DIR injection",
            ),
            (
                Path.home() / ".claude-shared",
                "residue from an earlier shared-symlink design draft; the current strict-isolation "
                "design does not use a shared directory",
            ),
            (
                Path.home() / ".gits" / "quota_patterns.yaml",
                "deprecated passive quota matcher input; replaced by OAuth Usage API",
            ),
        ]
        for path, hint in candidates:
            if path.exists():
                logger.warning("legacy artifact found at %s — %s", path, hint)

    # ------------------------------------------------------------------
    # Hook auto-install
    # ------------------------------------------------------------------

    def _ensure_hooks_installed(self) -> None:
        """Auto-install CLI hooks for all supported CLIs and aliases."""
        from ..__main__ import _install_hook, _install_opencode_plugin

        for name, installer in [
            ("Claude", lambda: _install_hook()),
            ("OpenCode", _install_opencode_plugin),
        ]:
            try:
                installer()
            except Exception:
                logger.warning("Failed to auto-install %s hook", name, exc_info=True)

        # Install hook into each alias config_dir that differs from the default
        for alias, cfg in self.launcher._aliases.items():
            config_dir = cfg.get("config_dir")
            if not config_dir or cfg.get("type", "claude") != "claude":
                continue
            try:
                _install_hook(config_dir=config_dir)
            except Exception:
                logger.warning("Failed to auto-install hook for alias %s", alias, exc_info=True)

        # Self-heal every managed Claude account (task 3ead61). Accounts are
        # isolated CLAUDE_CONFIG_DIRs registered in the account vault — they
        # are NOT covered by the alias loop above, which is how `sharon-team`
        # ended up hookless and silently broke its dispatch mirror. _install_hook
        # is idempotent, so this is a cheap no-op for already-hooked accounts.
        # Per-account failures are loud-logged but never crash boot.
        try:
            if self.account_vault.is_initialized():
                for entry in self.account_vault.list():
                    try:
                        # quiet=True: a per-account "already installed" line on
                        # every daemon restart would just spam pm2 out.log.
                        # Failures are still surfaced via logger.warning below.
                        rc = _install_hook(config_dir=entry.config_dir, quiet=True)
                        if rc != 0:
                            logger.warning(
                                "account-hook self-heal: install for account %s "
                                "returned rc=%s — run `gits account fix-hooks %s`",
                                entry.name, rc, entry.name,
                            )
                    except Exception:
                        logger.warning(
                            "account-hook self-heal failed for account %s — "
                            "run `gits account fix-hooks %s`",
                            entry.name, entry.name, exc_info=True,
                        )
        except Exception:
            logger.warning(
                "account-hook self-heal: could not enumerate accounts", exc_info=True
            )

    # ------------------------------------------------------------------
    # Message handler (plain text forwarding)
    # ------------------------------------------------------------------

    async def handle_message(self, msg: IncomingMessage) -> None:
        """Forward plain text messages to the bound tmux window."""
        # Intercept search queries for session picker
        pending = self._pending_binds.get(msg.channel_id)
        if pending and pending.get("search_pending") and msg.text:
            pending["search_pending"] = False
            pending["search_query"] = msg.text.strip()
            sessions = pending["sessions"]
            picker_msg = self._build_session_picker_message(
                sessions, pending["path"], msg.channel_id,
                target_cli=pending.get("cli", ""),
                search_query=pending["search_query"],
            )
            if self._adapter:
                await self._adapter.send_message(msg.channel_id, picker_msg)
            return

        binding = self.session_mgr.get_binding(msg.channel_id)
        if binding is None:
            logger.debug(
                "Ignoring message in unbound channel %s: %s",
                msg.channel_id,
                (msg.text or "")[:50],
            )
            return

        if msg.text or msg.image_paths:
            # Ensure the tmux window still exists; recreate it if killed externally
            await self._ensure_window_alive(binding)

            # Auto-resume if explicitly suspended OR if the CLI has exited
            # (pane is now running a shell like zsh/bash instead of the CLI)
            if binding.suspended:
                await self._resume_suspended(binding)
            else:
                current_cmd = await self.tmux.pane_current_command(binding.window_id)
                shell_names = {"zsh", "bash", "sh", "fish", "dash"}
                if current_cmd and current_cmd.lower() in shell_names:
                    logger.info(
                        "CLI exited in window %s (current cmd: %s), auto-resuming",
                        binding.window_id, current_cmd,
                    )
                    await self._resume_suspended(binding)

            await self.session_mgr.touch_active(msg.channel_id)
            # Mark before forwarding so JsonlMonitor's missing-session warning
            # gate opens for this binding (claude doesn't flush its jsonl until
            # the user actually interacts — any earlier alarm is a race).
            await self.session_mgr.mark_first_interaction(msg.channel_id)

            submit = _submit_keys_for_cli(binding.coding_cli)

            if msg.image_paths:
                # Forward images as @path references so Claude CLI can read them
                for img_path in msg.image_paths:
                    logger.info("Forwarding image to tmux %s: %s", binding.window_id, img_path)
                    try:
                        await self.tmux.send_text(binding.window_id, f"@{img_path}", submit_keys=submit)
                    except Exception:
                        logger.exception("Failed to send image path to tmux")

            if msg.text:
                logger.info(
                    "Forwarding message to tmux %s: %s",
                    binding.window_id,
                    msg.text[:80],
                )
                track("cmd_message", platform=msg.platform)
                # Ride the reference in on the same send: a literal newline
                # through tmux send-keys submits the prompt, which would split
                # one utterance into two turns. The operator's Discord view is
                # untouched either way — the ref only exists inside the pane.
                ref = _format_utterance_ref(msg)
                payload = f"{msg.text} {ref}" if ref else msg.text
                try:
                    await self.tmux.send_text(
                        binding.window_id, payload, submit_keys=submit
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
        fresh: bool = False,
        session_id: str | None = None,
        account: str | None = None,
        model: str | None = None,
    ) -> None:
        """Handle /bind — bind channel to a project directory.

        When existing CLI sessions are found in the directory, shows a
        session picker with buttons.  Otherwise starts a fresh session
        immediately.  Pass ``fresh=True`` to skip session discovery entirely.
        Pass ``session_id`` to resume a specific session directly.

        ``account`` (per task [[gbraq8]]) pins the claude account name
        used for this binding, overriding ``manifest.default``. ``None``
        keeps the legacy "use default" behavior.

        ``model`` (per openspec ``add-dispatch-model-pin``) pins the CLI
        model for a fresh claude launch via ``--model <name>``. Ignored on
        resume (the resumed session keeps its own model) and for
        non-claude bases.
        """
        track("cmd_bind", platform=platform_for(channel_id), cli=cli or "default")
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

            # Discover existing sessions (all CLIs, target first).
            # Treat an explicit ``session_id`` the same as ``fresh``: caller
            # has already picked, so skip the picker and let ``_create_bind``
            # resume it directly. Slash-UI button picks go through
            # ``_handle_bind_resume`` → ``_create_bind`` and don't enter here.
            sessions = [] if (fresh or session_id) else self.launcher.discover_all_sessions(str(p), target_cli=cli)

            if sessions:
                # Store pending bind info and show session picker
                self._pending_binds[channel_id] = {
                    "path": str(p),
                    "window_name": window_name,
                    "cli": cli,
                    "sessions": sessions,
                    "mode": mode,
                    "account": account,
                    "created_at": time.time(),
                }
                msg = self._build_session_picker_message(
                    sessions, str(p), channel_id, target_cli=cli
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
                        mode=mode, account=account, model=model,
                    )
                return
            else:
                # No sessions found — start fresh (or resume if session_id provided)
                await self._create_bind(
                    channel_id, str(p), window_name, cli, interaction,
                    mode=mode, session_id=session_id, account=account,
                    model=model,
                )
        else:
            await self._reply(
                interaction,
                "Please provide a path: `/bind /path/to/project`\n"
                "Start typing and use the dropdown to navigate.",
            )

    def _warn_if_default_credentials_diverge(self) -> None:
        """Log a one-line WARN if native vs isolated default-account creds disagree.

        Per ``add-default-account-native-and-refresh``: the default account
        is now routed through ``~/.claude/`` natively. If the user previously
        had an isolated ``~/.claude-{default}/.credentials.json`` that's
        newer than the native file, the next claude launch may see stale
        creds and prompt re-login. Surface this at startup so the user can
        run ``gits account migrate-default-native --apply`` proactively.
        """
        try:
            manifest = self.account_vault.load()
        except Exception:
            return
        default = manifest.default
        if not default:
            return
        try:
            native = self.account_layout.legacy_claude_dir() / ".credentials.json"
            isolated = self.account_layout.account_dir(default) / ".credentials.json"
            if not native.exists() or not isolated.exists():
                return
            native_mt = native.stat().st_mtime
            isolated_mt = isolated.stat().st_mtime
        except OSError:
            return
        if native_mt >= isolated_mt:
            return
        logger.warning(
            "default-account credentials drift: ~/.claude-%s/.credentials.json "
            "is newer than ~/.claude/.credentials.json — next claude launch may "
            "prompt re-login. Run `gits account migrate-default-native --apply` "
            "to reconcile.",
            default,
        )

    def _default_claude_account(self) -> str | None:
        """Look up ``manifest.default`` for new bindings (Phase 0.4 D4).

        Returns ``None`` when the multi-account vault isn't initialized so
        legacy single-account installs continue using ``~/.claude/`` with
        no ``CLAUDE_CONFIG_DIR`` injection.
        """
        try:
            return self.account_vault.load().default
        except Exception:
            return None

    async def _create_bind(
        self,
        channel_id: str,
        work_dir: str,
        window_name: str,
        cli: str,
        interaction: Any,
        session_id: str | None = None,
        mode: str | None = None,
        account: str | None = None,
        model: str | None = None,
    ) -> None:
        """Create a tmux window, binding, and reply with confirmation.

        If *session_id* is provided the CLI is launched in resume mode.
        If *mode* is provided, adds the corresponding flag to the CLI command:
        - ``"auto"`` → ``--allowedTools Edit,Write,... ``
        - ``"yolo"`` → ``--dangerously-skip-permissions``

        New bindings inherit ``manifest.default`` (per spec D4); the account
        is persisted on the binding and ``CLAUDE_CONFIG_DIR`` is injected
        into the launch command so claude reads/writes
        ``~/.claude-{name}/`` instead of ``~/.claude/``.

        ``account`` (per task [[gbraq8]]) overrides
        ``manifest.default`` for this binding. The dispatch
        load-balancer resolves the concrete name at dispatch time and
        passes it via ``--account=<name>`` on ``/bind``.

        ``model`` (per openspec ``add-dispatch-model-pin``) appends
        ``--model <name>`` to fresh claude launches only — resumes keep
        the session's own model, non-claude bases ignore it.
        """
        p = Path(work_dir)
        claude_account = account or self._default_claude_account()
        cmd = self.launcher.build_launch_command(
            cli=cli, session_id=session_id, claude_account=claude_account,
        )

        # Append permission mode flag (CLI-specific)
        if mode and mode != "default":
            cmd = _append_permission_flag(cmd, cli, mode)

        if model and not session_id:
            try:
                base_type = self.launcher.resolve_cli(
                    cli, claude_account=claude_account
                ).base_type
            except Exception:
                base_type = ""
            if base_type == "claude":
                cmd += f" --model {model}"

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
            claude_account=claude_account,
        )

        # Start pane polling for the new binding
        self.monitor.start_polling(channel_id, win.window_id)

        if interaction:
            await self._reply(interaction, "Bound successfully.")

        # Shared post-bind UX: confirmation + dir listing + nav buttons + screenshot
        await self._send_bind_report(
            channel_id=channel_id,
            work_dir=p,
            window_id=win.window_id,
            window_name=window_name,
            cli=cli,
            session_id=session_id,
            interaction=interaction,
        )

    async def _send_bind_report(
        self,
        channel_id: str,
        work_dir: str | Path,
        window_id: str,
        window_name: str,
        cli: str,
        session_id: str | None = None,
        interaction: Any = None,
    ) -> None:
        """Post the standard post-bind UX block to *channel_id*.

        Shared by ``_create_bind`` (manual ``/bind``) and
        ``handle_thread_auto`` (auto-bind) so the two paths can't drift.
        Sends a confirmation line + directory listing of ``work_dir`` +
        quick-action buttons, then fires ``_auto_screenshot`` against the
        binding for this channel. ``interaction`` is only used as a
        fallback channel for the screenshot when no adapter is registered.
        """
        p = Path(work_dir)
        dir_info = self._format_dir_listing(p)

        if session_id:
            session_info = f"\nResuming session `{session_id[:16]}...`"
        else:
            session_info = "\nFresh session"

        wid = window_id
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

        binding = self.session_mgr.get_binding(channel_id)
        if binding:
            await self._auto_screenshot(channel_id, binding, interaction, delay=2.0)

    # ------------------------------------------------------------------
    # Session Picker helpers
    # ------------------------------------------------------------------

    _SESSION_PAGE_SIZE = 10  # max Select options per page (leave 1 for "New Session")

    def _build_session_picker_message(
        self,
        sessions: list[CLISession],
        work_dir: str,
        channel_id: str,
        page: int = 0,
        target_cli: str = "",
        search_query: str = "",
    ) -> OutgoingMessage:
        """Build an OutgoingMessage with a Select Menu for session selection.

        Sessions are sorted most-recently-active first (by mtime).
        Shows up to _SESSION_PAGE_SIZE sessions per page via Discord Select Menu.
        Adds a "Next Page" button when more sessions exist beyond this page.
        When *search_query* is given, sessions are filtered first.
        """
        # Filter sessions by search query if provided
        if search_query:
            q = search_query.lower()
            sessions = [
                s for s in sessions
                if q in s.summary.lower()
                or q in s.last_message.lower()
                or q in s.first_message.lower()
                or q in s.session_id.lower()
            ]

        page_size = self._SESSION_PAGE_SIZE
        start = page * page_size
        end = start + page_size
        page_sessions = sessions[start:end]
        total = len(sessions)
        has_more = end < total

        # Resolve target CLI base type for cross-CLI badge detection
        target_type = self.launcher.resolve_cli(target_cli).base_type if target_cli else ""

        # Count cross-CLI import candidates
        import_count = sum(
            1 for s in sessions if s.source_cli and s.source_cli != target_type
        )
        lines = ["**Resume Session?**\n"]
        if search_query:
            lines.append(f"Search: `{search_query}` — **{total}** result(s)")
        else:
            lines.append(f"Found **{total}** session(s) in `{work_dir}` — sorted by most recently active.")
        if import_count:
            lines.append(f"Includes **{import_count}** importable session(s) from other CLIs (marked ↗).")
        if total > page_size:
            page_total = (total + page_size - 1) // page_size
            lines.append(f"Page {page + 1}/{page_total}")
        lines.append("\nPick a session from the dropdown below, or start a new one.")
        text = "\n".join(lines)

        # Build Select Menu options — one per session on this page
        # Use absolute index so callback_data maps correctly even across pages
        # NOTE: when search is active, we store filtered indices in _pending_binds["filtered_sessions"]
        select_opts: list[SelectOption] = []
        for page_i, s in enumerate(page_sessions):
            age = _format_age(s.mtime)
            s_base = self.launcher.resolve_cli(s.source_cli).base_type if s.source_cli else target_type
            is_import = s_base != target_type
            badge = f"↗[{s.source_cli}] " if is_import else ""
            # Use first user message as label; fall back to slug
            first = s.first_message or s.summary
            label = (badge + first)[:100]
            # description: session ID (short) + last message (truncated) + msg count + age
            import_note = " · import" if is_import else ""
            sid_short = s.session_id[:8]
            meta = f" · {sid_short} · {s.message_count} msgs · {age}{import_note}"
            last = s.last_message
            max_last = 100 - len(meta)
            if last and max_last > 6:
                last_truncated = (last[:max_last - 1] + "…") if len(last) > max_last else last
                desc = last_truncated + meta
            else:
                desc = f"{sid_short} · {s.message_count} msgs · {age}{import_note}"
            # Use session_id as the callback value for unambiguous lookup
            select_opts.append(
                SelectOption(
                    label=label or f"Session {start + page_i + 1}",
                    value=f"bind_resume_id:{channel_id}:{s.session_id}",
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

        # Buttons: Search + Next Page
        button_rows: list[list[Button]] = []
        search_row: list[Button] = [
            Button(
                label="🔍 Search sessions",
                callback_data=f"bind_search:{channel_id}",
            )
        ]
        if search_query:
            search_row.append(
                Button(
                    label="✕ Clear search",
                    callback_data=f"bind_search_clear:{channel_id}",
                )
            )
        button_rows.append(search_row)
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
        track("cmd_unbind", platform=platform_for(channel_id))
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
        # Threads and forks inherit the parent's claude_account so the child
        # claude process reads/writes the same per-account config dir as
        # the parent (per spec: New binding inherits per-binding account).
        claude_account = getattr(parent_binding, "claude_account", None)
        cmd = self.launcher.build_launch_command(cli=cli, claude_account=claude_account)
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
            claude_account=claude_account,
        )

        # Start monitoring
        self.monitor.start_polling(thread_id, win.window_id)

        # Send initial prompt after CLI starts up
        async def _send_initial_prompt() -> None:
            await _wait_for_cli_idle(self.tmux, win.window_id)
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
        # Threads and forks inherit the parent's claude_account so the child
        # claude process reads/writes the same per-account config dir as
        # the parent (per spec: New binding inherits per-binding account).
        claude_account = getattr(parent_binding, "claude_account", None)
        cmd = self.launcher.build_launch_command(cli=cli, claude_account=claude_account)
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
            claude_account=claude_account,
        )

        self.monitor.start_polling(thread_id, win.window_id)

        # Send initial prompt
        if starter_message:
            async def _send_initial_prompt() -> None:
                await _wait_for_cli_idle(self.tmux, win.window_id)
                submit = _submit_keys_for_cli(cli)
                await self.tmux.send_text(
                    win.window_id, starter_message, submit_keys=submit
                )

            asyncio.create_task(_send_initial_prompt())

        # Shared post-bind UX — same block as manual /bind so users get the
        # familiar directory listing + screenshot signal that the session
        # actually came up. No interaction (auto-bind has no slash command).
        await self._send_bind_report(
            channel_id=thread_id,
            work_dir=work_dir,
            window_id=win.window_id,
            window_name=title,
            cli=cli,
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
        # Threads and forks inherit the parent's claude_account so the child
        # claude process reads/writes the same per-account config dir as
        # the parent (per spec: New binding inherits per-binding account).
        claude_account = getattr(parent_binding, "claude_account", None)
        cmd = self.launcher.build_launch_command(cli=cli, claude_account=claude_account)
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
            claude_account=claude_account,
            owned_worktree=True,
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
        track("cmd_screenshot", platform=platform_for(channel_id))
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound. Use `/bind` first.")
            return

        try:
            ansi_text = await self.tmux.capture_pane_ansi(binding.window_id)
            png_bytes = await self.screenshot.capture(ansi_text)

            # Reply to the deferred interaction with the screenshot
            import discord as _discord
            if interaction and hasattr(interaction, "followup"):
                import io

                file = _discord.File(io.BytesIO(png_bytes), filename="screenshot.png")
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
        """Handle /info — show binding info."""
        track("cmd_status", platform=platform_for(channel_id))
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
        if binding.permission_mode:
            lines.append(f"Permission: `{binding.permission_mode}`")
        # Claude account isolation (per add-multi-account-hotswap, Phase 0.4):
        # show which CLAUDE_CONFIG_DIR this binding launches into.
        acct = getattr(binding, "claude_account", None)
        if isinstance(acct, str):
            lines.append(f"Account: `{acct}` (~/.claude-{acct}/)")
        else:
            lines.append("Account: `<default>` (~/.claude/)")
        if getattr(binding, "respawn_failed", False):
            lines.append("⚠ Respawn failed — try `/account-switch <name>` to retry")
        lines.append(f"Created: `{binding.created_at}`")

        # Session file path + live stats
        if binding.cli_session_id:
            lines.append(f"Session ID: `{binding.cli_session_id}`")
            # Scope discovery + file lookup to this binding's claude_account so
            # /info reads the same path runtime _safe_session_id reads. Without
            # this, sessions written under ~/.claude-{account}/projects/ show
            # "Session file: ❌ not found" even though resume actually works.
            acct_for_lookup = getattr(binding, "claude_account", None)
            if not isinstance(acct_for_lookup, str):
                acct_for_lookup = None
            # Show the human-readable summary so it matches the /bind dropdown label
            try:
                cli = binding.coding_cli or "claude"
                target_type = self.launcher.resolve_cli(cli).base_type
                matched = next(
                    (
                        s for s in self.launcher.discover_all_sessions(
                            binding.work_dir,
                            target_cli=cli,
                            claude_account=acct_for_lookup,
                        )
                        if s.session_id == binding.cli_session_id
                    ),
                    None,
                )
                if matched:
                    s_base = self.launcher.resolve_cli(matched.source_cli).base_type if matched.source_cli else target_type
                    badge = f"↗[{matched.source_cli}] " if s_base != target_type else ""
                    lines.append(f"Session summary: \"{badge}{matched.summary}\"")
            except Exception:
                pass
            sess_file = self.launcher.get_session_file(
                binding.work_dir,
                binding.coding_cli or "claude",
                binding.cli_session_id,
                claude_account=acct_for_lookup,
            )
            if sess_file:
                lines.append(f"Session file: `{sess_file}`")
                try:
                    st = Path(sess_file).stat()
                    import time as _time
                    age = int(_time.time() - st.st_mtime)
                    size_kb = st.st_size // 1024
                    active_icon = "🟢" if age < 300 else "🟡"
                    lines.append(
                        f"Session file status: {active_icon} size={size_kb}KB, "
                        f"last write {age}s ago"
                    )
                except OSError:
                    lines.append("Session file status: ❓ (stat failed)")
            else:
                lines.append("Session file: ❌ not found")

        # session_map.json entry for this window (detects session drift)
        try:
            import json as _json
            smap_path = self.settings.session_map_file
            if smap_path.exists():
                smap = _json.loads(smap_path.read_text())
                win_entry = next(
                    (v for k, v in smap.items() if k.endswith(f":{binding.window_id}")),
                    None,
                )
                if win_entry:
                    map_sid = win_entry.get("session_id", "")
                    drift = map_sid != binding.cli_session_id
                    drift_icon = "⚠️ DRIFT" if drift else "✅ match"
                    lines.append(
                        f"session_map[@{binding.window_id}]: `{map_sid[:16]}…` {drift_icon}"
                    )
                else:
                    lines.append(f"session_map[@{binding.window_id}]: ❓ no entry")
        except Exception:
            pass

        # Imported context file (from cross-CLI import)
        import_file = Path(binding.work_dir) / ".gits-import.md"
        if import_file.exists():
            lines.append(f"Imported context: `{import_file}`")

        # Resume command
        if binding.cli_session_id:
            from .launcher import RESUME_TEMPLATES
            cli = binding.coding_cli or "claude"
            templates = RESUME_TEMPLATES.get(cli)
            if templates:
                resume_cmd = templates["by_id"].format(id=binding.cli_session_id)
            else:
                resume_cmd = f"{cli} --resume {binding.cli_session_id}"
            if binding.permission_mode and binding.permission_mode != "default":
                resume_cmd = _append_permission_flag(resume_cmd, cli, binding.permission_mode)
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

    async def handle_enter(self, channel_id: str, interaction: Any) -> None:
        """Handle /enter — send a single Enter key."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        await self.tmux.send_keys(binding.window_id, "Enter")
        await self._reply(interaction, "Sent `Enter`.")
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

    async def handle_raw(self, channel_id: str, text: str, interaction: Any) -> None:
        """Handle /raw <text> — send text verbatim to the CLI pane.

        Useful for things Discord intercepts (slash commands like ``/status``)
        or that ghost itself would otherwise interpret. The text is typed
        literally into the pane and submitted with the CLI's correct submit
        keys (Enter for claude, Escape+Enter for codex/copilot).
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        submit = _submit_keys_for_cli(binding.coding_cli)
        await self.tmux.send_text(binding.window_id, text, submit_keys=submit)
        # Show what was sent so the user can confirm; truncate for safety
        preview = text if len(text) <= 100 else text[:97] + "..."
        await self._reply(interaction, f"Sent: `{preview}`")
        await self._auto_screenshot(channel_id, binding, interaction)


    async def handle_done(
        self,
        channel_id: str,
        interaction: Any,
        force_worktree: bool = False,
    ) -> None:
        """Handle /done — end work session, archive and lock thread, clean worktree.

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

        # Reply before archiving — followup cannot post to a locked thread
        status_parts = [f"Window `{binding.window_name}` done. Binding removed."]
        children = self.session_mgr.list_channel_threads(channel_id)
        if children:
            status_parts.append(f"Also closed {len(children)} child session(s).")
        if is_wt:
            status_parts.append("Worktree removed.")
        await self._reply(interaction, " ".join(status_parts))

        # Close child sessions first (threads and forks). Engine-created
        # worktrees (children from /fork) get cleaned up; bindings whose
        # work_dir is a user-owned worktree we just happen to point at are
        # preserved (the owned_worktree flag is the discriminator — see
        # [[23do0p]]).
        for child in children:
            await self._kill_single(
                child.channel_id,
                archive_thread=True,
                remove_worktree=child.owned_worktree,
            )

        # Close this session
        await self._kill_single(channel_id, archive_thread=True, remove_worktree=is_wt)

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

        # Remove worktree only when the caller explicitly asks. Do NOT auto-remove
        # just because work_dir happens to be a worktree — that silently destroyed
        # user-owned worktrees on thread archive (see task [[23do0p]]).
        if remove_worktree:
            await asyncio.to_thread(_remove_worktree, binding.work_dir)

        # Archive thread
        if archive_thread and self._adapter:
            try:
                await self._adapter.archive_thread(channel_id)
            except Exception:
                logger.warning("Could not archive thread %s", channel_id, exc_info=True)

    async def _suspend_binding(self, channel_id: str) -> None:
        """Kill the claude process in a tmux window but keep the window alive."""
        from ..utils.process import find_claude_children, kill_claude_process

        binding = self.session_mgr.get_binding(channel_id)
        if binding is None or binding.suspended:
            return
        logger.info(
            "Suspending idle binding %s (window %s, last active %.0f min ago)",
            channel_id,
            binding.window_id,
            (time.time() - binding.last_active_at) / 60,
        )
        # Send C-c to interrupt any running tool, then find and kill the
        # Claude child process directly so it actually exits.
        try:
            await self.tmux.send_keys(binding.window_id, "C-c")
            await asyncio.sleep(0.5)
        except Exception:
            logger.debug("Could not send C-c to window %s", binding.window_id)

        pane_pid = await self.tmux.pane_pid(binding.window_id)
        if pane_pid:
            children = await find_claude_children(pane_pid)
            if children:
                await kill_claude_process(children, grace_seconds=2.0)
        self.monitor.stop_polling(channel_id)
        await self.session_mgr.mark_suspended(channel_id)

    async def _ensure_window_alive(self, binding: Any) -> bool:
        """Ensure the tmux window in *binding* exists; recreate it if dead.

        Returns True if the window was recreated, False if it was already alive.
        The tmux session is created automatically by TmuxController when missing.
        """
        if await self.tmux.window_exists(binding.window_id):
            return False

        logger.warning(
            "tmux window %s is dead for channel %s — recreating",
            binding.window_id,
            binding.channel_id,
        )
        try:
            win = await self.tmux.create_window(
                name=binding.window_name,
                cwd=binding.work_dir,
            )
        except Exception:
            logger.exception(
                "Failed to recreate tmux window for channel %s", binding.channel_id
            )
            return False

        await self.session_mgr.update_window_id(binding.channel_id, win.window_id)
        binding.window_id = win.window_id  # update local reference immediately
        logger.info(
            "Recreated tmux window %s for channel %s",
            win.window_id,
            binding.channel_id,
        )
        return True

    async def _send_relaunch_in_pane(self, binding: Any, cmd: str) -> None:
        """Send a CLI launch/resume command into an existing pane, CWD-safe.

        The pane's shell may hold a stale CWD inode (e.g. the work_dir was
        rm'd and recreated under the same path while the shell was alive).
        In that state zsh refuses to fork/exec anything with
        "current working directory was deleted, so that command didn't work",
        so a bare ``send_text(cmd)`` silently fails. Prefixing
        ``cd <work_dir> && `` rescues the stale-inode case (absolute-path
        ``cd`` does not need the current inode), is a no-op when CWD is
        already healthy, and degrades to a single clean ``cd:`` error when
        the work_dir genuinely no longer exists.
        """
        guarded = f"cd {shlex.quote(binding.work_dir)} && {cmd}"
        await self.tmux.send_text(binding.window_id, guarded)

    async def _resume_suspended(self, binding: Any) -> None:
        """Resume a suspended binding by relaunching the CLI."""
        if binding.cli_session_id:
            logger.info("Auto-resuming suspended binding %s (session %s)",
                        binding.channel_id, binding.cli_session_id[:8])
        else:
            logger.info("Auto-restarting CLI for binding %s (fresh)", binding.channel_id)
        await self._ensure_window_alive(binding)
        # Verify the session JSONL actually exists before passing --resume; if
        # the binding has a stale session_id (e.g., the SessionStart hook
        # registered an id but claude exited before writing any conversation),
        # fall back to a fresh launch instead of looping on
        # "No conversation found with session ID: ...".
        safe_sid = await self._safe_session_id(binding)
        cmd = self.launcher.build_launch_command(
            cli=binding.coding_cli,
            session_id=safe_sid,
            claude_account=getattr(binding, "claude_account", None),
        )
        if binding.permission_mode:
            cmd = _append_permission_flag(cmd, binding.coding_cli, binding.permission_mode)
        try:
            await self._send_relaunch_in_pane(binding, cmd)
            await asyncio.sleep(3.0)  # wait for CLI to be ready
        except Exception:
            logger.exception("Failed to resume binding %s", binding.channel_id)
        await self.session_mgr.touch_active(binding.channel_id)
        self.monitor.start_polling(binding.channel_id, binding.window_id)

    # ------------------------------------------------------------------
    # Multi-account switch primitive (Phase 0.7)
    # ------------------------------------------------------------------

    def _binding_lock(self, channel_id: str) -> asyncio.Lock:
        """Return (and lazily create) a per-binding asyncio.Lock."""
        lock = self._switch_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self._switch_locks[channel_id] = lock
        return lock

    async def switch_account(
        self,
        channel_id: str,
        target: str,
        *,
        auto_import: bool = False,
        reason: str = "manual",
    ) -> SwitchResult:
        """Atomically switch a binding's claude account.

        Per openspec change ``add-multi-account-hotswap`` (D5/D16):

        1. Hold a per-binding asyncio.Lock for the entire operation.
        2. Validate the target exists in :class:`AccountVault`.
        3. Send ``C-c`` to the pane and wait briefly.
        4. Find the claude child processes and SIGTERM → SIGKILL → reap.
        5. (Optional) When ``auto_import=True`` and the binding has a
           non-None ``claude_account`` and ``cli_session_id``, copy the
           current source JSONL into the target account's projects dir IF
           the target does not already have it. This step is the core of
           the Discord ``/account-switch`` UX (D16).
        6. Update the binding's ``claude_account`` field (atomic state.json
           write) and the manifest's ``lastSwitch`` / ``default``.
        7. Respawn claude in the same pane with
           ``CLAUDE_CONFIG_DIR=$HOME/.claude-{target}`` injected.
        8. Release the lock and return a :class:`SwitchResult`.

        Concurrent ``switch_account`` calls on **different** bindings run
        in parallel — there is no global mutex.
        """

        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            return SwitchResult(
                success=False, binding_id=channel_id, target=target,
                previous=None, error=f"no binding for channel {channel_id}",
            )

        previous = getattr(binding, "claude_account", None)

        # Same-account fast path (no lock needed)
        if previous == target:
            return SwitchResult(
                success=True, binding_id=channel_id, target=target,
                previous=previous, import_status="same_account",
            )

        # Validate target before doing anything destructive.
        if self.account_vault.get(target) is None:
            return SwitchResult(
                success=False, binding_id=channel_id, target=target,
                previous=previous, error=f"unknown account '{target}'",
            )

        async with self._binding_lock(channel_id):
            return await self._do_switch(
                binding, target, previous,
                auto_import=auto_import, reason=reason,
            )

    async def _do_switch(
        self,
        binding: Any,
        target: str,
        previous: str | None,
        *,
        auto_import: bool,
        reason: str,
    ) -> SwitchResult:
        """Perform the locked switch sequence. Caller must hold the binding lock."""
        from ..utils.process import find_claude_children, kill_claude_process

        result = SwitchResult(
            success=False, binding_id=binding.channel_id, target=target,
            previous=previous,
        )

        # 1. Send C-c, wait 300ms
        try:
            await self.tmux.send_keys(binding.window_id, "C-c")
            await asyncio.sleep(0.3)
        except Exception:
            logger.debug("send_keys C-c failed for %s", binding.window_id)

        # 2. Kill claude process(es). 5s SIGTERM grace, 1s reap after SIGKILL.
        pane_pid = await self.tmux.pane_pid(binding.window_id)
        if pane_pid:
            children = await find_claude_children(pane_pid)
            if children:
                kill_results = await kill_claude_process(
                    children, grace_seconds=5.0, reap_after_kill=1.0,
                )
                still_alive = [pid for pid, ok in kill_results.items() if not ok]
                if still_alive:
                    result.error = (
                        f"failed to kill claude pids {still_alive}; switch aborted"
                    )
                    logger.warning(result.error)
                    return result

        # 3. Auto-import (D16) — runs after kill, before field update.
        if auto_import:
            result.import_status = self._auto_import_session(binding, target, result)
        # else: leave import_status="skipped_no_import"

        # 4. Update binding.claude_account and persist state.json.
        await self.session_mgr.update_claude_account(binding.channel_id, target)
        await self.session_mgr.mark_respawn_failed(binding.channel_id, False)
        # Refresh local reference because the in-memory dataclass was mutated.
        binding = self.session_mgr.get_binding(binding.channel_id)

        # 5. Update manifest (lastSwitch + default + lastUsed).
        try:
            self.account_vault.record_switch(
                binding_id=binding.channel_id,
                from_=previous,
                to=target,
                reason=reason,
            )
        except Exception as e:
            logger.warning("record_switch failed: %s", e)

        # 6. Respawn with CLAUDE_CONFIG_DIR=<target>.
        try:
            await self._ensure_window_alive(binding)
            # Verify the target-side session JSONL actually exists before
            # passing --resume. After auto-import "imported" or "target_existed"
            # the file is there; for "no_source" / "no_session" / CLI path the
            # file may be missing — fall back to fresh launch to avoid
            # `claude --resume <id>` failing with "No conversation found".
            safe_sid = await self._safe_session_id(binding)
            cmd = self.launcher.build_launch_command(
                cli=binding.coding_cli,
                session_id=safe_sid,
                claude_account=target,
            )
            if binding.permission_mode:
                cmd = _append_permission_flag(
                    cmd, binding.coding_cli, binding.permission_mode,
                )
            await self._send_relaunch_in_pane(binding, cmd)
            await asyncio.sleep(0.5)  # let claude start; respawn confirmation is async
        except Exception as e:
            logger.exception("respawn failed for %s", binding.channel_id)
            result.respawn_failed = True
            result.error = f"respawn failed: {e}"
            await self.session_mgr.mark_respawn_failed(binding.channel_id, True)
            try:
                self.account_vault.record_switch(
                    binding_id=binding.channel_id, from_=previous, to=target,
                    reason=reason, partial=True,
                )
            except Exception:
                pass
            return result

        result.success = True
        return result

    async def _safe_session_id(self, binding: Any) -> str | None:
        """Return ``binding.cli_session_id`` only if its JSONL file exists.

        Used before passing the id to ``claude --resume <id>``. Avoids the
        ``"No conversation found with session ID: ..."`` failure mode where
        the SessionStart hook registered a session_id (writing it to
        session_map.json which ghost picks up) but claude exited before
        writing any conversation JSONL — leaving a permanently-stale id on
        the binding that breaks every subsequent respawn.

        When the file is missing, the stale id is cleared from the binding
        (atomic state.json write) so the next launch is fresh AND
        :class:`JsonlMonitor` stops emitting "session not found" warnings
        every poll cycle. The hook will write the new session_id once
        claude actually starts a conversation.
        """
        sid = binding.cli_session_id
        if not sid:
            return None
        account = getattr(binding, "claude_account", None)
        if not isinstance(account, str):
            account = None
        try:
            path_str = self.launcher.get_session_file(
                binding.work_dir,
                binding.coding_cli or "claude",
                sid,
                claude_account=account,
            )
        except Exception:
            path_str = None
        if path_str and Path(path_str).exists():
            return sid
        logger.info(
            "binding %s: session %s not found at expected path "
            "(account=%s, work_dir=%s) — clearing stale id, launching fresh",
            binding.channel_id, sid, account, binding.work_dir,
        )
        try:
            await self.session_mgr.update_cli_session_id(binding.channel_id, "")
        except Exception:
            logger.exception("could not clear stale cli_session_id")
        return None

    def _auto_import_session(
        self, binding: Any, target: str, result: SwitchResult
    ) -> str:
        """Inline auto-import step for ``switch_account`` (D16).

        Returns one of:

        * ``"no_session"`` — binding has no ``cli_session_id``.
        * ``"no_source"`` — source-side JSONL doesn't exist anywhere.
        * ``"imported"`` — target was missing the file; copied source → target.
        * ``"imported_overwrote"`` — target had an OLDER copy; replaced with
          source (source mtime > target mtime). The previous target file is
          preserved as ``<target>.gits-bak`` until the copy succeeds, then
          removed.
        * ``"target_existed"`` — target had a copy at least as new as source;
          preserved unchanged. Caller can still ``--force`` via host CLI to
          override.

        The mtime-based decision rule replaces the original "always preserve
        target" semantics so a switch sequence like A→B→A→B uses the most
        recently-active copy by default (typically the source side, since
        that's where the user has been talking right before the switch).
        """
        import shutil

        sid = binding.cli_session_id
        if not sid:
            return "no_session"

        previous = getattr(binding, "claude_account", None)
        # Locate source — use launcher.get_session_file so dir-hash variants
        # are handled identically to runtime resolution.
        source_str = self.launcher.get_session_file(
            binding.work_dir, binding.coding_cli or "claude", sid,
            claude_account=previous,
        )
        if source_str is None:
            return "no_source"
        source_path = Path(source_str)

        # Mirror source's parent dir name into target so claude finds it
        # via the same dir-hash strategy.
        target_projects = self.account_layout.projects_dir(target)
        target_dir = target_projects / source_path.parent.name
        target_path = target_dir / source_path.name

        result.source_path = str(source_path)
        result.target_path = str(target_path)

        # Decide based on existence + mtime comparison.
        will_overwrite = False
        if target_path.exists():
            try:
                source_mtime = source_path.stat().st_mtime
                target_mtime = target_path.stat().st_mtime
            except OSError as e:
                logger.warning(
                    "auto-import mtime stat failed; preserving target: %s", e,
                )
                return "target_existed"
            if source_mtime <= target_mtime:
                logger.info(
                    "auto-import: target %s has newer-or-equal copy of session %s "
                    "(source mtime=%.0f, target mtime=%.0f); preserving",
                    target, sid, source_mtime, target_mtime,
                )
                return "target_existed"
            # Source is strictly newer — overwrite with backup.
            will_overwrite = True

        backup_path: Path | None = None
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if will_overwrite:
                backup_path = target_path.with_suffix(target_path.suffix + ".gits-bak")
                # If a stale backup exists from a prior failed run, replace it.
                if backup_path.exists():
                    backup_path.unlink()
                target_path.replace(backup_path)
            shutil.copy2(source_path, target_path)
            if backup_path is not None:
                backup_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("auto-import copy failed: %s", e)
            # Best-effort restore: if we moved the original to .gits-bak but
            # the copy failed, put it back so target has its previous content.
            if backup_path is not None and backup_path.exists() and not target_path.exists():
                try:
                    backup_path.replace(target_path)
                except OSError:
                    pass  # leave .gits-bak for manual recovery
            return "no_source"  # treat as best-effort

        try:
            self.account_vault.record_import(
                session_id=sid, from_=previous, to=target,
            )
        except Exception:
            pass

        if will_overwrite:
            logger.info(
                "auto-import: overwrote older target session %s on account %s "
                "with newer source from %s",
                sid, target, previous,
            )
            return "imported_overwrote"
        logger.info(
            "auto-import: copied session %s from %s to %s",
            sid, previous, target,
        )
        return "imported"

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

        # Launch fresh CLI — preserve binding's account so claude reads/writes
        # the per-account config dir (per spec: existing binding respawn keeps
        # its claude_account).
        cmd = self.launcher.build_launch_command(
            cli=binding.coding_cli,
            claude_account=getattr(binding, "claude_account", None),
        )
        await self._send_relaunch_in_pane(binding, cmd)

        # Clear session ID
        await self.session_mgr.update_cli_session_id(channel_id, "")

        if message:
            async def _send_initial_prompt() -> None:
                await _wait_for_cli_idle(self.tmux, binding.window_id)
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

        session_id = binding.cli_session_id

        # Graceful quit + resume with the new mode flag. Preserves the
        # binding's account so claude reads/writes the per-account config dir.
        await self._graceful_resume(
            binding, session_id=session_id, permission_mode=mode
        )

        # Persist new mode
        stored_mode = mode if mode != "default" else None
        binding.permission_mode = stored_mode
        await self.session_mgr._save()

        mode_label = {
            "bypassPermissions": "YOLO (fully automatic)",
            "auto": "Auto",
            "acceptEdits": "AcceptEdits",
            "default": "Normal (requires confirmation)",
        }.get(mode, mode)
        resume_note = f" (resuming `{session_id[:16]}…`)" if session_id else " (fresh)"
        await self._reply(
            interaction,
            f"Mode switched to **{mode_label}**{resume_note}",
        )

    async def _graceful_resume(
        self,
        binding: Any,
        *,
        session_id: str | None,
        permission_mode: str | None,
    ) -> str:
        """Graceful in-pane quit + resume: ``C-c`` → ``exit`` → relaunch.

        Sends a graceful interrupt then a clean ``exit`` so the CLI process
        tears down on its own (never ``kill -9`` / ``tmux kill-*``), then
        rebuilds the launch command — preserving the binding's
        ``claude_account`` so the freshly spawned process reads that account's
        on-disk credentials — and relaunches it in the **same** pane via
        ``_send_relaunch_in_pane``. Returns the command string sent.

        Single source for the quit/resume dance shared by ``handle_mode`` and
        ``handle_restart``. When *permission_mode* is set (and not
        ``"default"``) its CLI flag is re-applied to the resume command so the
        mode survives the relaunch.
        """
        await self.tmux.send_keys(binding.window_id, "C-c")        # graceful interrupt
        await asyncio.sleep(0.5)
        await self.tmux.send_text(binding.window_id, "exit")       # graceful quit (no kill -9)
        await asyncio.sleep(1.5)
        cmd = self.launcher.build_launch_command(
            cli=binding.coding_cli,
            session_id=session_id or None,
            claude_account=getattr(binding, "claude_account", None),
        )
        if permission_mode and permission_mode != "default":
            cmd = _append_permission_flag(cmd, binding.coding_cli, permission_mode)
        await self._send_relaunch_in_pane(binding, cmd)
        return cmd

    async def handle_restart(self, channel_id: str, interaction: Any) -> None:
        """Handle /restart — graceful in-pane resume that re-reads fresh creds.

        Gracefully quits the bound session's CLI process and resumes the
        **same** session id in the **same** tmux pane, so conversation history
        is preserved. The freshly spawned process re-reads the account's
        on-disk ``.credentials.json`` at startup — the manual recovery lever
        for a session wedged on a stale/expired in-memory token.

        Boundary: if the account itself is dead (still 401 on disk) this won't
        help — that's ``/account-switch``. Restart only re-reads what is
        already on disk; it does not switch accounts or fetch credentials.
        """
        track("cmd_restart", platform=platform_for(channel_id))
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound.")
            return

        session_id = binding.cli_session_id
        if not session_id:
            await self._reply(
                interaction,
                "Nothing to resume — this binding has no session id yet. "
                "Use `/new` to start a fresh session.",
            )
            return

        # Window-gone guard: send_keys/send_text raise if the pane is missing,
        # so detect up front and report cleanly instead of crashing the handler.
        if not await self.tmux.window_exists(binding.window_id):
            await self._reply(
                interaction,
                "Session window is gone — re-`/bind` needed.",
            )
            return

        await self._reply(interaction, "♻️ Restarting session…")

        try:
            await self._graceful_resume(
                binding,
                session_id=session_id,
                permission_mode=binding.permission_mode,
            )
        except Exception:
            logger.exception("restart: failed to relaunch session %s", channel_id)
            await self._reply(
                interaction,
                "⚠️ Restart failed — couldn't relaunch the session in its pane. "
                "Try `/screenshot` to inspect, or re-`/bind`.",
            )
            return

        # Binding / thread / channel / session_id all unchanged — no persist.
        account = getattr(binding, "claude_account", None) or "default"
        await self._reply(
            interaction,
            f"✅ Resumed `{session_id[:16]}…` on account `{account}` — "
            "reading fresh credentials.",
        )

    async def handle_bash(
        self, channel_id: str, command: str, interaction: Any
    ) -> None:
        """Handle /bash — send a !command to the coding CLI via tmux.

        Sends the command with a ``!`` prefix which triggers the CLI's
        bash execution mode (Claude Code runs it directly).
        """
        track("cmd_bash", platform=platform_for(channel_id))
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
            await self._reply(interaction, MODEL_HELP)

    # ------------------------------------------------------------------
    # B. CLI Forwarding
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # /accounts and /account-switch (Phase 0.12)
    # ------------------------------------------------------------------
    # The legacy ``handle_subscriptions_list`` and ``handle_subscription_switch``
    # handlers were removed when the Discord ``/subscriptions`` and
    # ``/sub-switch`` slash commands were dropped (per user request,
    # ``add-multi-account-hotswap`` Phase 0.12). The host-side
    # ``gits subscription`` subcommands still call into ``SubscriptionVault``
    # / ``SwitchPrimitive`` for V1 transition compatibility, but no
    # Discord-facing path remains for the deprecated subscription model.
    # ------------------------------------------------------------------

    async def handle_accounts_list(self, channel_id: str, interaction: Any) -> None:
        """``/accounts`` — mirror ``gits account list`` (load-balanced ranking)."""
        from gits.cli_account import _format_header, _format_row

        from .account_load import rank_accounts

        manifest = self.account_vault.load()
        if not manifest.accounts:
            await self._reply(
                interaction,
                "No accounts configured.\n"
                "Run `gits account add <name> --capture-current` on the ghost host to start.",
            )
            return

        # Determine which account this channel's binding currently uses (for
        # the "current channel" highlight, distinct from the manifest default).
        binding = self.session_mgr.get_binding(channel_id)
        current = (
            binding.claude_account
            if binding is not None and isinstance(binding.claude_account, str)
            else None
        )

        # Per-account binding counts.
        binding_counts: dict[str, int] = {}
        for b in self.session_mgr.list_bindings():
            if isinstance(b.claude_account, str):
                binding_counts[b.claude_account] = binding_counts.get(b.claude_account, 0) + 1

        ranked = rank_accounts(
            self.account_vault,
            live_binding_counts=binding_counts,
            layout=self.account_layout,
        )
        by_name = {row.name: row for row in ranked}

        lines: list[str] = [_format_header()]
        for a in manifest.accounts:
            row = by_name.get(a.name)
            if row is None:
                continue
            if a.name == current:
                prefix = "→"
            elif a.name == manifest.default:
                prefix = "*"
            else:
                prefix = " "
            lines.append(_format_row(prefix, row))

        body = "\n".join(lines)
        footer_parts: list[str] = []
        if manifest.default:
            footer_parts.append(f"current: {manifest.default}")
        if any(row.rank is None for row in ranked):
            footer_parts.append("`—` rows excluded from auto-dispatch (no resolvable credential)")
        if manifest.default and any(row.name == manifest.default for row in ranked):
            footer_parts.append(
                f"`*` ({manifest.default}) is default → runs native; its load "
                "includes the operator's own interactive `claude` usage (real cap pressure)"
            )
        footer = ("\n" + " · ".join(footer_parts)) if footer_parts else ""

        await self._reply(
            interaction,
            f"**Claude Accounts**\n```\n{body}\n```{footer}",
        )

    async def handle_account_switch(
        self, channel_id: str, target: str, interaction: Any
    ) -> None:
        """``/account-switch <name>`` Discord handler — auto-imports current session (D16)."""
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(
                interaction,
                "❌ This channel has no binding. Run `/start` (or `/bind`) first.",
            )
            return

        if self.account_vault.get(target) is None:
            await self._reply(interaction, f"❌ Account `{target}` not found.")
            return

        if binding.claude_account == target:
            await self._reply(interaction, f"✓ Already on `{target}` — no change.")
            return

        await self._reply(interaction, f"⚙️ Switching to `{target}`...")

        try:
            result = await self.switch_account(channel_id, target, auto_import=True)
        except Exception as e:
            await self._reply(interaction, f"❌ Switch failed: {e}")
            return

        if not result.success:
            await self._reply(interaction, f"❌ Switch failed: {result.error}")
            return

        # Build status message based on import_status.
        status_msg = {
            "imported": (
                f"✅ Switched to `{target}` — session imported from "
                f"`{result.previous}`. Conversation history preserved."
            ),
            "imported_overwrote": (
                f"✅ Switched to `{target}` — session imported from "
                f"`{result.previous}` (overwrote older copy on `{target}`; "
                "newer source content prevails)."
            ),
            "target_existed": (
                f"✅ Switched to `{target}` — `{target}` already had a "
                "newer-or-equal copy of this session, kept it as is. To force "
                f"the older `{result.previous}` content over it anyway, run on "
                f"the host: `gits account import {binding.cli_session_id} "
                f"--from {result.previous} --to {target} --force`"
            ),
            "no_source": (
                f"✅ Switched to `{target}` — no current session file found; "
                "starting fresh."
            ),
            "no_session": (
                f"✅ Switched to `{target}` — binding hasn't started a session yet."
            ),
            "same_account": f"✓ Already on `{target}` — no change.",
            "skipped_no_import": f"✅ Switched to `{target}`.",
        }.get(result.import_status, f"✅ Switched to `{target}`.")

        if result.respawn_failed:
            status_msg += (
                f"\n⚠ Respawn failed; binding marked respawn_failed. "
                f"Try `/account-switch {target}` again or check logs."
            )
        await self._reply(interaction, status_msg)

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
        # A slash-command forward also counts as a user interaction with the
        # pane — opens JsonlMonitor's missing-session warning gate.
        await self.session_mgr.mark_first_interaction(channel_id)
        await self.tmux.send_text(binding.window_id, command, submit_keys=submit)
        await self._reply(interaction, f"Forwarded: `{command}`")

    # ------------------------------------------------------------------
    # /usage — capture full panel from throwaway claude session
    # ------------------------------------------------------------------

    async def handle_usage(self, channel_id: str, interaction: Any) -> None:
        """Capture the full ``/usage`` panel via a throwaway claude session.

        Spawns a fresh tmux session (separate from the gits server's
        windows so blast-radius / fd-cap impact is bounded) running claude
        with ``CLAUDE_CONFIG_DIR`` set to the channel's bound account,
        sends ``/usage``, captures the pane after settle, trims spawn
        noise, posts as a code-fenced Discord message (or attachment if
        the trimmed panel doesn't fit inline). The bound session is not
        touched.
        """
        binding = self.session_mgr.get_binding(channel_id)
        if binding is None:
            await self._reply(interaction, "Not bound. Use /bind first.")
            return

        eff = effective_account(binding.claude_account, self.account_vault)
        if eff is not None:
            account_dir = self.account_layout.account_dir(eff)
            if not account_dir.exists():
                await self._reply(
                    interaction,
                    f"Account `{eff}` not configured locally.",
                )
                return
            # Inline prefix, NOT subprocess env= — with a tmux server already
            # running, env= only reaches the client process and the pane
            # inherits the server's environment (the original [[mfgft7]] bug).
            spawn_cmd = prefix_account_env("claude", account_dir)
            account_label = eff
        else:
            # Default account runs natively against ~/.claude — no injection.
            spawn_cmd = "claude"
            try:
                account_label = self.account_vault.load().default or "default"
            except Exception:
                account_label = "default"

        session_name = f"gits-usage-{channel_id}-{secrets.token_hex(3)}"

        try:
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "tmux", "new-session", "-d", "-s", session_name,
                        "-x", "200", "-y", "60", spawn_cmd,
                    ],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or b"").decode(errors="replace").strip()
                short = stderr.splitlines()[-1] if stderr else f"exit {e.returncode}"
                await self._reply(
                    interaction,
                    f"Could not spawn capture session: {short}. "
                    "Tmux fd cap may be hit — see system logs.",
                )
                return
            except OSError as e:
                await self._reply(
                    interaction,
                    f"Could not spawn capture session: {e}. "
                    "Tmux fd cap may be hit — see system logs.",
                )
                return

            await asyncio.sleep(7)
            await asyncio.to_thread(
                subprocess.run,
                ["tmux", "send-keys", "-t", session_name, "/usage", "Enter"],
                check=False,
                capture_output=True,
            )
            await asyncio.sleep(4)
            cap = await asyncio.to_thread(
                subprocess.run,
                ["tmux", "capture-pane", "-t", session_name, "-p"],
                check=False,
                capture_output=True,
                text=True,
            )
            raw = cap.stdout or ""

            result = format_usage_panel(raw, account_label, datetime.now())
            if not result.body:
                await self._reply(
                    interaction,
                    f"Capture timed out — `claude` may have rendered a "
                    f"login prompt for `{account_label}`. Try "
                    "re-authenticating.",
                )
                return

            if result.inline:
                await self._reply(
                    interaction,
                    f"{result.header}\n```\n{result.body}\n```",
                )
            else:
                try:
                    import io

                    import discord  # local import — keeps engine import graph platform-agnostic for the inline path

                    await interaction.followup.send(
                        content=result.header,
                        file=discord.File(
                            io.BytesIO(result.body.encode()),
                            filename="usage.txt",
                        ),
                    )
                except Exception:
                    logger.exception("Failed to send /usage attachment")
        finally:
            await asyncio.to_thread(
                subprocess.run,
                ["tmux", "kill-session", "-t", session_name],
                check=False,
                capture_output=True,
            )

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
            await self.handle_done(target_channel, interaction=None, force_worktree=True)
            if self._adapter:
                await self._adapter.send_message(
                    channel_id,
                    OutgoingMessage(text="Session closed and worktree removed."),
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

        elif action == "bind_resume_id" and len(parts) >= 3:
            pending_channel = parts[1]
            session_id = parts[2]
            await self._handle_bind_resume_by_id(pending_channel, session_id, channel_id)

        elif action == "bind_new" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_bind_new(pending_channel, channel_id)

        elif action == "bind_page" and len(parts) >= 3:
            pending_channel = parts[1]
            page = int(parts[2])
            await self._handle_bind_page(pending_channel, page, channel_id)

        elif action == "bind_search" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_bind_search(pending_channel, channel_id)

        elif action == "bind_search_clear" and len(parts) >= 2:
            pending_channel = parts[1]
            await self._handle_bind_search_clear(pending_channel, channel_id)

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

        bindings = self.session_mgr.list_bindings()
        if not bindings:
            return
        channel_id = bindings[0].channel_id

        # Lazy-recovery shape: total/recovered/failed are all 0 and the
        # detail line begins with "Lazy recovery". Surface a clean operator
        # message instead of "0 | 0 | 0" which reads as "recovery did nothing".
        is_lazy = (
            result.total == 0
            and result.recovered == 0
            and result.failed == 0
            and result.details
            and result.details[0].startswith("Lazy recovery")
        )
        if is_lazy:
            await self._adapter.send_message(
                channel_id,
                OutgoingMessage(
                    text=(
                        f"**tmux Recovery (lazy)**\n"
                        f"{result.details[0]} "
                        f"({len(bindings)} persisted bindings)"
                    )
                ),
            )
            return

        details = "\n".join(result.details) if result.details else "No details"
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
            "Bind resume: channel=%s session=%s summary=%s source_cli=%s",
            pending_channel,
            session.session_id,
            session.summary[:40],
            session.source_cli,
        )

        # Detect cross-CLI import (e.g. codex session → claude)
        # Resolve both sides to base_type so aliases like "clpy" (→ claude)
        # are never mistaken for a different CLI.
        target_type = self.launcher.resolve_cli(pending["cli"]).base_type
        source_type = self.launcher.resolve_cli(session.source_cli).base_type if session.source_cli else target_type
        is_cross_cli = source_type != target_type

        if is_cross_cli:
            await self._handle_cross_cli_import(session, pending, pending_channel, reply_channel)
            return

        # Same-CLI resume — use session.source_cli so aliases like "clpy"
        # resume with "clpy --resume {id}", not "claude --resume {id}".
        resume_cli = session.source_cli or pending["cli"]
        await self._create_bind(
            channel_id=pending_channel,
            work_dir=pending["path"],
            window_name=pending["window_name"],
            cli=resume_cli,
            interaction=None,
            session_id=session.session_id,
            mode=pending.get("mode"),
            account=pending.get("account"),
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

    async def _handle_cross_cli_import(
        self,
        session: CLISession,
        pending: dict,
        pending_channel: str,
        reply_channel: str,
    ) -> None:
        """Import a session from a different CLI into a fresh target-CLI session.

        Extracts conversation text, writes it to ``.gits-import.md`` in the
        work directory, starts a fresh target-CLI session, then injects an
        initial context message via tmux after the CLI has had time to start.
        """
        from pathlib import Path as _Path

        work_dir = pending["path"]
        source_cli = session.source_cli
        target_cli = pending["cli"]

        context_text = self.launcher.extract_conversation_text(session)

        # Write conversation to .gits-import.md in the work directory
        import_file = _Path(work_dir) / ".gits-import.md"
        header = (
            f"# Imported from {source_cli} session\n"
            f"# Summary: {session.summary}\n"
            f"# Messages: {session.message_count}\n\n"
        )
        try:
            import_file.write_text(header + (context_text or "(no conversation text extracted)"))
        except OSError as exc:
            logger.warning("Could not write .gits-import.md: %s", exc)

        # Launch a fresh target-CLI session
        await self._create_bind(
            channel_id=pending_channel,
            work_dir=work_dir,
            window_name=pending["window_name"],
            cli=target_cli,
            interaction=None,
            session_id=None,
            mode=pending.get("mode"),
            account=pending.get("account"),
        )

        # After the CLI initialises, auto-inject context as the first message
        async def _inject() -> None:
            binding = self.session_mgr.get_binding(pending_channel)
            if not binding:
                return
            await _wait_for_cli_idle(self.tmux, binding.window_id)
            # Use @-file reference (Claude Code syntax); other CLIs will see
            # the absolute path and can read it themselves.
            abs_import = str(_Path(work_dir) / ".gits-import.md")
            if target_cli in ("claude",):
                context_msg = f"@{abs_import}"
            else:
                context_msg = abs_import
            submit = _submit_keys_for_cli(target_cli)
            try:
                await self.tmux.send_text(binding.window_id, context_msg, submit_keys=submit)
            except Exception:
                logger.exception("Cross-CLI context injection failed")

        asyncio.create_task(_inject())

        if self._adapter:
            age = _format_age(session.mtime)
            await self._adapter.send_message(
                reply_channel,
                OutgoingMessage(
                    text=(
                        f"Bound **#{pending['window_name']}** \u2192 `{work_dir}`\n"
                        f"Importing ↗ **{source_cli}** session: **{session.summary}** ({age})\n"
                        f"Context saved to `.gits-import.md` · Starting fresh **{target_cli}** session…"
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
            account=pending.get("account"),
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
        search_query = pending.get("search_query", "")
        msg = self._build_session_picker_message(
            sessions, pending["path"], pending_channel, page=page,
            target_cli=pending.get("cli", ""), search_query=search_query,
        )
        if self._adapter:
            await self._adapter.send_message(reply_channel, msg)

    async def _handle_bind_resume_by_id(
        self, pending_channel: str, session_id: str, reply_channel: str
    ) -> None:
        """Handle bind_resume_id — resume a session by its ID (search-safe)."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None:
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        # Find the session by ID
        sessions: list[CLISession] = pending["sessions"]
        idx = next((i for i, s in enumerate(sessions) if s.session_id == session_id), None)
        if idx is None:
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text=f"Session `{session_id[:8]}…` not found. Try again."),
                )
            return

        await self._handle_bind_resume(pending_channel, idx, reply_channel)

    async def _handle_bind_search(
        self, pending_channel: str, reply_channel: str
    ) -> None:
        """Handle bind_search — prompt user to type a search query."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None:
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        pending["search_pending"] = True
        if self._adapter:
            await self._adapter.send_message(
                reply_channel,
                OutgoingMessage(text="Type your search query below to filter sessions:"),
            )

    async def _handle_bind_search_clear(
        self, pending_channel: str, reply_channel: str
    ) -> None:
        """Handle bind_search_clear — clear search and show all sessions."""
        pending = self._pending_binds.get(pending_channel)
        if pending is None:
            if self._adapter:
                await self._adapter.send_message(
                    reply_channel,
                    OutgoingMessage(text="Session picker expired. Run `/bind` again."),
                )
            return

        pending.pop("search_query", None)
        pending.pop("search_pending", None)
        sessions = pending["sessions"]
        msg = self._build_session_picker_message(
            sessions, pending["path"], pending_channel, target_cli=pending.get("cli", ""),
        )
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


async def _wait_for_cli_idle(
    tmux: TmuxController,
    window_id: str,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> None:
    """Poll the pane until the CLI shows an idle prompt, or timeout expires.

    Detects the Claude Code chrome separator + prompt symbol (❯ / >) via
    ``parse_status_line``.  Falls back gracefully if the pane never shows
    recognisable chrome (e.g. non-Claude CLIs) — the caller still sends
    its message after the timeout.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            pane_text = await tmux.capture_pane_text(window_id)
            if parse_status_line(pane_text) == "idle":
                return
        except Exception:
            pass
    logger.debug(
        "wait_for_cli_idle: timed out after %.0fs for window %s", timeout, window_id
    )


def _format_utterance_ref(msg: IncomingMessage) -> str | None:
    """Render a compact, machine-parseable pointer to *msg* itself.

    ``[ref: <platform>:<channel_id>/<message_id> · from:<user_id>]``

    ghost hands over the facts it already holds and nothing more — it does
    not know or care what a consumer does with them (task [[utrref]]).
    Returns ``None`` when the platform gave us no message id, so callers
    forward the bare text rather than dropping the message.
    """
    if not msg.message_id or not msg.channel_id or not msg.platform:
        return None
    # Command payloads are parsed by the CLI, not read as prose: `!cmd` runs a
    # shell command and `/cmd` a slash command. Appending a ref would become an
    # extra argument, so those stay verbatim.
    stripped = (msg.text or "").lstrip()
    if stripped.startswith(("!", "/")):
        return None
    return (
        f"[ref: {msg.platform}:{msg.channel_id}/{msg.message_id}"
        f" · from:{msg.user_id}]"
    )


# CLIs that need Escape+Enter to submit (multi-line editor mode)
_ESCAPE_ENTER_CLIS = frozenset({"codex", "copilot"})


def _submit_keys_for_cli(cli: str, base_type: str | None = None) -> str:
    """Return the tmux submit key sequence for a given CLI type.

    *base_type* should be the resolved base type from CodingCLILauncher when
    *cli* is a user-defined alias; if omitted *cli* is used directly.
    """
    effective = base_type if base_type is not None else cli
    if effective in _ESCAPE_ENTER_CLIS:
        return "Escape Enter"
    return "Enter"


def _append_permission_flag(cmd: str, cli: str, mode: str, base_type: str | None = None) -> str:
    """Append the correct permission flag based on CLI type and mode.

    Mapping (our mode → CLI flag):
      claude:
        default           → (nothing)
        acceptEdits       → --permission-mode acceptEdits
        auto              → --permission-mode auto
        bypassPermissions → --dangerously-skip-permissions
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

    effective = base_type if base_type is not None else cli

    if effective == "codex":
        if mode == "bypassPermissions":
            cmd += " --dangerously-bypass-approvals-and-sandbox"
        elif mode == "auto":
            cmd += " --full-auto"
        # acceptEdits not supported by codex — skip
        # Enable hooks feature for session tracking
        cmd += " --enable codex_hooks"
    elif effective == "copilot":
        if mode == "bypassPermissions":
            cmd += " --yolo"
        elif mode == "auto":
            cmd += " --allow-all-tools"
        elif mode == "acceptEdits":
            cmd += " --allow-tool=write --allow-tool=edit"
    elif effective == "opencode":
        pass  # no permission flags supported
    else:
        # claude and others
        if mode == "bypassPermissions":
            cmd += " --dangerously-skip-permissions"
        else:
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
