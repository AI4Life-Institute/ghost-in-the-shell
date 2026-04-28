"""QuotaNotifier — handle ``QuotaExhaustedEvent``s without auto-switching.

.. deprecated:: 0.3
    Quota notifications are now driven by the active OAuth Usage API query
    surfaced through ``gits account list`` / ``/accounts``; passive event
    notification on output pattern matches has been removed. This module is
    preserved for V1 transition compatibility but is no longer wired into
    engine startup. See openspec change ``add-multi-account-hotswap``.

When a quota-exhaustion signal is detected on the active subscription:

* If the matched signal carried a parseable reset time, mark
  ``rate_limited_until`` on the active subscription so that the manual
  ``gits subscription switch <name>`` command will refuse switching to
  the exhausted account.
* Always broadcast a Discord notification with a hint to manually switch
  via ``/sub-switch``. The notifier never invokes the switch primitive.

This replaces the former ``SubscriptionSwitcher`` which automatically
selected a candidate and called ``switch_to``. Auto-switching has been
removed; users are expected to switch manually after seeing the notice.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .quota import QuotaExhaustedEvent
from .subscription import SubscriptionVault, SubscriptionVaultError

logger = logging.getLogger(__name__)


# Async callback used for user-visible notifications (Discord broadcast, etc.).
# Signature: ``async def notify(text: str) -> None``.
NotifyFn = Callable[[str], Awaitable[None]]


class QuotaNotifier:
    """Consume ``QuotaExhaustedEvent``s; mark rate-limit and notify users."""

    def __init__(
        self,
        vault: SubscriptionVault,
        notify: NotifyFn | None = None,
    ):
        self.vault = vault
        self.notify = notify or _noop_notify
        self._queue: asyncio.Queue[QuotaExhaustedEvent] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("QuotaNotifier started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def submit(self, event: QuotaExhaustedEvent) -> None:
        """Synchronous fire-and-forget event submission used by monitors."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("QuotaNotifier queue full; dropping event")

    async def submit_async(self, event: QuotaExhaustedEvent) -> None:
        await self._queue.put(event)

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                event = await self._queue.get()
                await self._handle_event(event)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("QuotaNotifier loop error")

    async def _handle_event(self, event: QuotaExhaustedEvent) -> None:
        manifest = self.vault.load()
        active = manifest.active
        if active is None:
            logger.debug("quota_exhausted ignored: no active subscription")
            return

        if event.match.has_reset:
            try:
                await self.vault.update_rate_limit(active, event.match.reset_at)
            except SubscriptionVaultError as e:
                logger.error("Failed to update rate limit for %s: %s", active, e)
            when = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(event.match.reset_at)
            )
            text = (
                f"⚠️ quota exhausted on `{active}` — resets at {when}. "
                f"Use `/sub-switch <name>` to switch manually."
            )
        else:
            text = (
                f"⚠️ quota exhausted on `{active}` (reset time unknown). "
                f"Raw signal: `{event.match.matched_text[:120]}`. "
                f"Use `/sub-switch <name>` to switch manually."
            )
            logger.warning("quota signal without reset time")

        await self._safe_notify(text)

    async def _safe_notify(self, text: str) -> None:
        try:
            await self.notify(text)
        except Exception:
            logger.exception("notify callback failed")


async def _noop_notify(text: str) -> None:
    logger.info("[notifier] %s", text)
