"""HealthMonitor — tmux health check and auto-recovery.

Periodically checks tmux server/session/window health.
On failure, attempts automatic recovery from persisted state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..utils.lock import is_locked
from .launcher import CodingCLILauncher
from .session import SessionManager
from .tmux import TmuxController

if TYPE_CHECKING:
    from .account import AccountLayout
    from .account_vault import AccountVault
    from .engine import Engine
    from .watchdog_config import WatchdogConfig

# Async user-visible notify callback. Signature: ``async def(text) -> None``.
NotifyFn = Callable[[str], Awaitable[None]]

logger = logging.getLogger(__name__)


def _available_memory_mb() -> int:
    """Return available system memory in MB using vm_stat (macOS) or /proc/meminfo (Linux)."""
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            page_size = 16384  # default macOS page size
            m = re.search(r"page size of (\d+) bytes", result.stdout)
            if m:
                page_size = int(m.group(1))
            free = inactive = 0
            for line in result.stdout.splitlines():
                if "Pages free:" in line:
                    free = int(re.sub(r"\D", "", line))
                elif "Pages inactive:" in line:
                    inactive = int(re.sub(r"\D", "", line))
            return (free + inactive) * page_size // 1024 // 1024
    except Exception:
        pass
    # Linux fallback
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 9999  # unknown → assume plenty


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""

    total: int = 0
    recovered: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)


class HealthMonitor:
    """Monitor tmux health and recover from failures.

    Checks every ``check_interval`` seconds:
    1. Is the tmux server alive?
    2. Is our session alive?
    3. Are all bound windows alive?

    On server/session failure, attempts to rebuild all bindings
    from persisted state, including CLI session resume.
    """

    IDLE_SUSPEND_SECONDS = 2 * 60 * 60  # 2 hours
    IDLE_SCAN_INTERVAL = 30 * 60  # scan every 30 minutes

    # Watchdog cadences — independent of the 5s main tick (task [[jeyuxq]]
    # hard req 2: heavy samplers must never burden the health/recovery
    # cadence). Resource sub-tick samples lsof/vm_stat/df off-thread; token
    # sub-tick runs the ~5min JSONL scan.
    RESOURCE_WATCH_INTERVAL = 45  # seconds
    TOKEN_WATCH_INTERVAL = 5 * 60  # seconds

    def __init__(
        self,
        tmux: TmuxController,
        session_mgr: SessionManager,
        launcher: CodingCLILauncher,
        check_interval: float = 5.0,
        max_retries: int = 3,
        credential_lock_path: Path | None = None,
        notify: NotifyFn | None = None,
        account_vault: AccountVault | None = None,
        account_layout: AccountLayout | None = None,
        watchdog_config: WatchdogConfig | None = None,
        watchdog_state_path: Path | None = None,
    ):
        self.tmux = tmux
        self.session_mgr = session_mgr
        self.launcher = launcher
        self.check_interval = check_interval
        self.max_retries = max_retries
        self._running = False
        self._task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._resource_task: asyncio.Task | None = None
        self._token_task: asyncio.Task | None = None
        self._on_recovery: list = []  # callbacks
        self._engine: Engine | None = None  # set via set_engine()
        self._credential_lock_path = credential_lock_path
        # Watchdog wiring (task [[jeyuxq]]). All optional so existing
        # callers/tests that don't pass them simply run no watchdog loops.
        self._notify = notify
        self._account_vault = account_vault
        self._account_layout = account_layout
        self._watchdog_config = watchdog_config
        self._watchdog_state_path = watchdog_state_path
        self._watchdog_state = None  # lazy WatchdogState

    def set_engine(self, engine: Engine) -> None:
        """Set engine reference for idle suspension."""
        self._engine = engine

    def on_recovery(self, callback) -> None:
        """Register a callback for recovery events.

        Callback signature: ``async def callback(result: RecoveryResult) -> None``
        """
        self._on_recovery.append(callback)

    async def start(self) -> None:
        """Start the health check loop."""
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        self._idle_task = asyncio.create_task(self._idle_scan_loop())
        if self._watchdog_enabled():
            self._resource_task = asyncio.create_task(self._resource_watch_loop())
            self._token_task = asyncio.create_task(self._token_watch_loop())
            logger.info(
                "Watchdog loops started (resource=%ds, token=%ds)",
                self.RESOURCE_WATCH_INTERVAL,
                self.TOKEN_WATCH_INTERVAL,
            )
        logger.info("HealthMonitor started (interval=%.1fs)", self.check_interval)

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        for task in (
            self._task,
            self._idle_task,
            self._resource_task,
            self._token_task,
        ):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("HealthMonitor stopped")

    # ------------------------------------------------------------------
    # Watchdog (task [[jeyuxq]]) — resource + token faces on independent
    # slow sub-ticks. Read-only + zero-network; heavy work in to_thread so
    # the 5s main tick is never burdened (hard req 2).
    # ------------------------------------------------------------------

    def _watchdog_enabled(self) -> bool:
        return self._notify is not None and self._watchdog_config is not None

    def _state(self):
        """Lazily build the persisted WatchdogState (edge de-dupe + digest)."""
        if self._watchdog_state is None:
            from .watchdog_state import WatchdogState

            path = self._watchdog_state_path or (
                self._account_layout.legacy_claude_dir().parent / ".gits"
                / "watchdog_state.json"
                if self._account_layout
                else Path("~/.gits/watchdog_state.json").expanduser()
            )
            self._watchdog_state = WatchdogState(path)
        return self._watchdog_state

    async def _safe_notify(self, text: str) -> bool:
        """Attempt one send. Returns whether it landed; never raises.

        Swallowing is deliberate — an exception escaping here would take down
        the watch loop, which is worse than a missed alert. Returning the
        outcome instead of discarding it is what lets the caller keep the edge
        un-consumed so the next tick retries (ghost#42). See
        :func:`gits.core.resource_watch.deliver`.
        """
        if self._notify is None:
            return False
        try:
            result = await self._notify(text)
        except Exception:
            logger.exception("watchdog notify failed")
            return False
        # A notifier that reports delivery explicitly is believed; one that
        # returns None (the older callback shape) is taken at its word that
        # returning without raising means it sent.
        return True if result is None else bool(result)

    async def _resource_watch_loop(self) -> None:
        """Sample host resources on the slow sub-tick and edge-alert."""
        from . import resource_watch as rw

        await asyncio.sleep(self.RESOURCE_WATCH_INTERVAL)
        while self._running:
            try:
                cfg = self._watchdog_config
                state = self._state()
                sample = await asyncio.to_thread(rw.sample_resources, cfg)
                verdicts = rw.classify_resources(sample, cfg.thresholds, state)
                alerts = rw.reconcile(verdicts, state, cfg)
                # Edge committed on delivery only — an undelivered alert stays
                # outstanding and is retried next tick (ghost#42).
                await rw.deliver(alerts, state, self._safe_notify)
            except Exception:
                logger.exception("resource watch error")
            await asyncio.sleep(self.RESOURCE_WATCH_INTERVAL)

    def _live_binding_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in self.session_mgr.list_bindings():
            acct = b.claude_account
            if acct:
                counts[acct] = counts.get(acct, 0) + 1
        return counts

    async def _token_watch_loop(self) -> None:
        """Token face: per-account cap-% + skew (edge) + daily digest.

        The daily digest folds into this loop via a persisted date-gate —
        no separate daily task (task [[jeyuxq]] AC-7)."""
        from . import resource_watch as rw

        if self._account_vault is None:
            return
        await asyncio.sleep(self.TOKEN_WATCH_INTERVAL)
        while self._running:
            try:
                cfg = self._watchdog_config
                counts = self._live_binding_counts()
                sample = await asyncio.to_thread(
                    rw.sample_tokens,
                    self._account_vault,
                    cfg,
                    layout=self._account_layout,
                    live_binding_counts=counts,
                )
                state = self._state()
                # Cap-% (inert when unconfigured) + skew, both edge-triggered.
                verdicts = rw.classify_token(sample, cfg.thresholds, state)
                verdicts.append(rw.classify_skew(sample, state))
                await rw.deliver(
                    rw.reconcile(verdicts, state, cfg), state, self._safe_notify
                )
                # Daily balance digest — date-gated, fires once/day past the
                # configured local hour.
                await self._maybe_send_digest(sample, cfg, state)
            except Exception:
                logger.exception("token watch error")
            await asyncio.sleep(self.TOKEN_WATCH_INTERVAL)

    async def _maybe_send_digest(self, sample, cfg, state) -> None:
        from . import resource_watch as rw

        now = time.localtime()
        today = time.strftime("%Y-%m-%d", now)
        if now.tm_hour < cfg.digest_hour:
            return
        if state.last_digest_date() == today:
            return
        # Same posture as the edge state: the date-gate is a de-dupe ledger
        # too, so burning it on a failed send buys a full day of silence
        # (ghost#42). Gate on delivery, not on "we got as far as trying".
        if await self._safe_notify(rw.format_digest(sample, cfg)):
            state.mark_digest_sent(today)

    async def _check_loop(self) -> None:
        """Periodic health check loop."""
        while self._running:
            try:
                await self._check_health()
            except Exception:
                logger.exception("Health check error")
            await asyncio.sleep(self.check_interval)

    async def _idle_scan_loop(self) -> None:
        """Periodically suspend bindings that have been idle too long."""
        # Initial delay so startup isn't immediately scanning
        await asyncio.sleep(self.IDLE_SCAN_INTERVAL)
        while self._running:
            try:
                await self._suspend_idle_bindings()
            except Exception:
                logger.exception("Idle scan error")
            await asyncio.sleep(self.IDLE_SCAN_INTERVAL)

    async def _suspend_idle_bindings(self) -> None:
        """Suspend bindings that haven't been active, using memory-aware thresholds."""
        if self._engine is None:
            return

        avail_mb = await asyncio.to_thread(_available_memory_mb)
        if avail_mb < 1024:
            # Critical: suspend everything not active in last 10 min
            threshold = 10 * 60
            logger.warning("Memory critical (%d MB available) — aggressive suspend", avail_mb)
        elif avail_mb < 2048:
            threshold = 30 * 60   # < 2GB: 30 min
        elif avail_mb < 4096:
            threshold = 60 * 60   # < 4GB: 1 hour
        else:
            threshold = self.IDLE_SUSPEND_SECONDS  # 2 hours

        logger.debug("Idle scan: available=%d MB, threshold=%.0f min", avail_mb, threshold / 60)

        now = time.time()
        bindings = self.session_mgr.list_bindings()
        for binding in bindings:
            if binding.suspended:
                continue
            idle_secs = now - binding.last_active_at
            if idle_secs >= threshold:
                logger.info(
                    "Idle suspend: %s (%.0f min idle, %d MB avail)",
                    binding.channel_id,
                    idle_secs / 60,
                    avail_mb,
                )
                await self._engine._suspend_binding(binding.channel_id)

    def _credential_lock_held(self) -> bool:
        """True if a subscription switch is currently in flight."""
        if self._credential_lock_path is None:
            return False
        try:
            return is_locked(self._credential_lock_path)
        except Exception:
            return False

    async def _check_health(self) -> None:
        """Run a single health check."""
        # Skip recovery while a subscription switch holds the credential lock —
        # the switch primitive is mid-kill or mid-respawn and HealthMonitor
        # spawning new claude processes here would race with credential swap.
        if self._credential_lock_held():
            logger.debug(
                "credential lock held; HealthMonitor skipping this tick"
            )
            return

        # Check tmux server
        if not await self.tmux.is_server_alive():
            logger.warning("tmux server is down, attempting recovery...")
            await self._recover_all()
            return

        # Check session
        if not await self.tmux.is_session_alive():
            logger.warning(
                "tmux session '%s' is gone, attempting recovery...",
                self.tmux.session_name,
            )
            await self._recover_all()
            return

        # Check individual windows (skip suspended — they have no tmux window by design)
        bindings = self.session_mgr.list_bindings()
        for binding in bindings:
            if binding.suspended:
                continue
            if not await self.tmux.window_exists(binding.window_id):
                logger.warning(
                    "tmux window '%s' (%s) for channel %s is gone — marking suspended",
                    binding.window_name,
                    binding.window_id,
                    binding.channel_id,
                )
                # Mark suspended so the next inbound message hits the normal
                # _resume_suspended path (which recreates window + relaunches
                # claude with --resume). Without this we'd log this warning
                # every check_interval forever — the window won't come back
                # on its own. JsonlMonitor polling is also stopped since
                # there's no claude process writing to the JSONL.
                await self.session_mgr.mark_suspended(binding.channel_id)
                if self._engine is not None:
                    self._engine.monitor.stop_polling(binding.channel_id)

        # Emergency: if memory is critically low, trigger idle scan immediately
        if self._engine is not None:
            avail_mb = await asyncio.to_thread(_available_memory_mb)
            if avail_mb < 1024:
                await self._suspend_idle_bindings()

    async def _recover_all(self) -> RecoveryResult:
        """Recover from tmux server/session death by rebuilding only the tmux
        session itself. Individual bindings recover lazily on first inbound
        message via ``Engine._ensure_window_alive`` + ``_resume_suspended``.

        Eager per-binding rebuild was removed after a 2026-05-24 incident
        where a ``tmux kill-server`` cascaded into 234 rebuilt windows and
        40+ ``claude --resume`` processes (22.6 GB RSS, near-OOM). Of the
        280 persisted bindings only ~4 were active; the rest were stale.
        ``handle_message`` already recreates a window and relaunches claude
        just-in-time for any channel that actually receives traffic, so
        eager rebuild paid full cost for work that almost never mattered.
        """
        result = RecoveryResult()

        # Always ensure the tmux session is back, even with zero bindings —
        # future create_window calls (driven by handle_message) need it.
        for attempt in range(self.max_retries):
            try:
                await self.tmux.ensure_session()
                break
            except Exception as e:
                logger.warning(
                    "Failed to create tmux session (attempt %d): %s", attempt + 1, e
                )
                await asyncio.sleep(2)
        else:
            logger.error(
                "Could not create tmux session after %d retries", self.max_retries
            )
            result.failed = 1
            result.details.append(
                f"Failed to rebuild tmux session after {self.max_retries} retries"
            )
            for cb in self._on_recovery:
                try:
                    await cb(result)
                except Exception:
                    logger.exception("Recovery callback error")
            return result

        result.details.append(
            "Lazy recovery: tmux session rebuilt; bindings will recover on "
            "first inbound message."
        )

        # Notify callbacks
        for cb in self._on_recovery:
            try:
                await cb(result)
            except Exception:
                logger.exception("Recovery callback error")

        logger.info(
            "Lazy recovery complete: tmux session restored; %d persisted "
            "bindings will rebuild on demand",
            len(self.session_mgr.list_bindings()),
        )
        return result

    async def check_and_recover(self) -> RecoveryResult | None:
        """Manual check + recover. Called on bot startup."""
        if not await self.tmux.is_server_alive():
            return await self._recover_all()
        if not await self.tmux.is_session_alive():
            return await self._recover_all()
        return None
