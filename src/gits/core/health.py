"""HealthMonitor — tmux health check and auto-recovery.

Periodically checks tmux server/session/window health.
On failure, attempts automatic recovery from persisted state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from .launcher import CodingCLILauncher
from .session import SessionManager
from .tmux import TmuxController

logger = logging.getLogger(__name__)


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
        self._on_recovery: list = []  # callbacks

    def on_recovery(self, callback) -> None:
        """Register a callback for recovery events.

        Callback signature: ``async def callback(result: RecoveryResult) -> None``
        """
        self._on_recovery.append(callback)

    async def start(self) -> None:
        """Start the health check loop."""
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("HealthMonitor started (interval=%.1fs)", self.check_interval)

    async def stop(self) -> None:
        """Stop the health check loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("HealthMonitor stopped")

    async def _check_loop(self) -> None:
        """Periodic health check loop."""
        while self._running:
            try:
                await self._check_health()
            except Exception:
                logger.exception("Health check error")
            await asyncio.sleep(self.check_interval)

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

        # Check individual windows
        bindings = self.session_mgr.list_bindings()
        for binding in bindings:
            if not await self.tmux.window_exists(binding.window_id):
                logger.warning(
                    "tmux window '%s' (%s) is gone",
                    binding.window_name,
                    binding.window_id,
                )
                # Log but don't auto-recover individual windows
                # (user might have intentionally closed it)

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
