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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .launcher import CodingCLILauncher
from .session import SessionManager
from .tmux import TmuxController

if TYPE_CHECKING:
    from .engine import Engine

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

    def __init__(
        self,
        tmux: TmuxController,
        session_mgr: SessionManager,
        launcher: CodingCLILauncher,
        check_interval: float = 5.0,
        max_retries: int = 3,
    ):
        self.tmux = tmux
        self.session_mgr = session_mgr
        self.launcher = launcher
        self.check_interval = check_interval
        self.max_retries = max_retries
        self._running = False
        self._task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
        self._on_recovery: list = []  # callbacks
        self._engine: Engine | None = None  # set via set_engine()

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
        logger.info("HealthMonitor started (interval=%.1fs)", self.check_interval)

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        for task in (self._task, self._idle_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        logger.info("HealthMonitor stopped")

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

    async def _check_health(self) -> None:
        """Run a single health check."""
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
                    "tmux window '%s' (%s) is gone",
                    binding.window_name,
                    binding.window_id,
                )
                # Log but don't auto-recover individual windows
                # (user might have intentionally closed it)

        # Emergency: if memory is critically low, trigger idle scan immediately
        if self._engine is not None:
            avail_mb = await asyncio.to_thread(_available_memory_mb)
            if avail_mb < 1024:
                await self._suspend_idle_bindings()

    async def _recover_all(self) -> RecoveryResult:
        """Attempt to recover all bindings after tmux failure."""
        result = RecoveryResult()
        bindings = self.session_mgr.list_bindings()
        result.total = len(bindings)

        if not bindings:
            logger.info("No bindings to recover")
            return result

        # Ensure tmux session exists
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
            result.failed = result.total
            return result

        # Rebuild each window
        for binding in bindings:
            try:
                # Create new window
                win = await self.tmux.create_window(
                    name=binding.window_name,
                    cwd=binding.work_dir,
                )

                # Update window ID in state (it changed after rebuild)
                await self.session_mgr.update_window_id(
                    binding.channel_id, win.window_id
                )

                # Try to resume CLI session
                cmd = self.launcher.build_launch_command(
                    cli=binding.coding_cli,
                    session_id=binding.cli_session_id,
                )
                await self.tmux.send_text(win.window_id, cmd)

                resume_note = (
                    f" (resume {binding.cli_session_id[:8]})"
                    if binding.cli_session_id
                    else " (fresh)"
                )
                result.details.append(
                    f"recovered {binding.window_name} -> "
                    f"{binding.work_dir}{resume_note}"
                )
                result.recovered += 1

            except Exception as e:
                logger.error(
                    "Failed to recover window '%s': %s", binding.window_name, e
                )
                result.details.append(f"failed {binding.window_name}: {e}")
                result.failed += 1

        # Notify callbacks
        for cb in self._on_recovery:
            try:
                await cb(result)
            except Exception:
                logger.exception("Recovery callback error")

        logger.info(
            "Recovery complete: %d/%d recovered, %d failed",
            result.recovered,
            result.total,
            result.failed,
        )
        return result

    async def check_and_recover(self) -> RecoveryResult | None:
        """Manual check + recover. Called on bot startup."""
        if not await self.tmux.is_server_alive():
            return await self._recover_all()
        if not await self.tmux.is_session_alive():
            return await self._recover_all()
        return None
