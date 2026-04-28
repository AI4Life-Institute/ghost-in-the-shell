"""PaneMonitor — periodically polls tmux panes for interactive prompts.

Runs an asyncio task per bound channel that captures pane text and
detects Claude Code interactive prompts (permission, multi-choice, etc.)
to surface them as Discord buttons.

Output push is handled separately by JSONL monitoring (not pane diff).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from .terminal_parser import (
    PromptInfo,
    extract_interactive_content,
    extract_prompt_options,
)
from .tmux import TmuxController

logger = logging.getLogger(__name__)

# Type alias for prompt callback
PromptCallback = Callable[[str, str, PromptInfo], Coroutine[Any, Any, None]]
# (channel_id, window_id, prompt_info)


class PaneMonitor:
    """Periodically polls tmux panes to detect interactive prompts."""

    def __init__(
        self,
        tmux: TmuxController,
        session_mgr: Any,
        interval: float = 5.0,
    ):
        self._tmux = tmux
        self._session_mgr = session_mgr
        self._interval = interval
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._prev_prompt_key: dict[str, str] = {}
        self._on_prompt_detected: PromptCallback | None = None
        # Synchronous callback fed with each captured pane text — used for
        # quota-pattern classification.
        self._on_pane_text: Any = None  # Callable[[str, str], None]

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_prompt(self, callback: PromptCallback) -> None:
        """Register callback for detected interactive prompts."""
        self._on_prompt_detected = callback

    def on_pane_text(self, callback) -> None:
        """Register a synchronous callback fed with raw pane text.

        Used by ``QuotaPatternMatcher`` integration. ``callback(channel_id, text)``.
        Errors are caught and logged.
        """
        self._on_pane_text = callback

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_polling(self, channel_id: str, window_id: str) -> None:
        """Start polling a pane for a bound channel."""
        if channel_id in self._tasks:
            logger.debug("Already polling channel %s, restarting", channel_id)
            self._tasks[channel_id].cancel()

        task = asyncio.create_task(
            self._poll_loop(channel_id, window_id),
            name=f"pane-poll-{channel_id[:8]}",
        )
        self._tasks[channel_id] = task
        logger.info("Started polling channel %s -> window %s", channel_id, window_id)

    def stop_polling(self, channel_id: str) -> None:
        """Stop polling for a channel."""
        task = self._tasks.pop(channel_id, None)
        if task:
            task.cancel()
            logger.info("Stopped polling channel %s", channel_id)
        self._prev_prompt_key.pop(channel_id, None)

    def stop_all(self) -> None:
        """Cancel all polling tasks."""
        for channel_id, task in self._tasks.items():
            task.cancel()
            logger.debug("Cancelled polling for channel %s", channel_id)
        self._tasks.clear()
        self._prev_prompt_key.clear()

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self, channel_id: str, window_id: str) -> None:
        """Main polling loop for a single channel/pane."""
        logger.debug("Poll loop started for channel %s", channel_id)
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._poll_once(channel_id, window_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Error polling pane for channel %s", channel_id
                    )
        except asyncio.CancelledError:
            logger.debug("Poll loop cancelled for channel %s", channel_id)

    async def _poll_once(self, channel_id: str, window_id: str) -> None:
        """Single poll iteration: capture pane and detect prompts."""
        # 1. Capture pane text
        try:
            pane_text = await self._tmux.capture_pane_text(window_id)
        except Exception:
            logger.debug("Failed to capture pane for channel %s", channel_id)
            return
        if not pane_text:
            return

        # Feed quota-pattern callback first (sync, cheap).
        if self._on_pane_text is not None:
            try:
                self._on_pane_text(channel_id, pane_text)
            except Exception:
                logger.exception("on_pane_text callback error for %s", channel_id)

        # 2. Check for interactive prompts
        ui_content = extract_interactive_content(pane_text)

        if ui_content:
            # Deduplicate by name + content hash
            prompt_key = ui_content.name + ":" + hashlib.md5(
                ui_content.content.encode()
            ).hexdigest()

            if prompt_key != self._prev_prompt_key.get(channel_id):
                self._prev_prompt_key[channel_id] = prompt_key
                prompt_info = extract_prompt_options(pane_text)
                if prompt_info is None:
                    prompt_info = PromptInfo(
                        options=[], tool_context=ui_content.content
                    )
                elif not prompt_info.tool_context:
                    prompt_info.tool_context = ui_content.content

                if self._on_prompt_detected:
                    try:
                        await self._on_prompt_detected(
                            channel_id, window_id, prompt_info
                        )
                    except Exception:
                        logger.exception(
                            "Prompt callback failed for %s", channel_id
                        )
        else:
            # No prompt — clear tracking so it can re-trigger
            if channel_id in self._prev_prompt_key:
                del self._prev_prompt_key[channel_id]
