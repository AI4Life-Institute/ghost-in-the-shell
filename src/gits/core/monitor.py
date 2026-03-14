"""PaneMonitor — periodically polls tmux panes and pushes updates to Discord.

Runs an asyncio task per bound channel that captures pane text, diffs it
against the previous capture, and fires callbacks for new output and
detected interactive prompts.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from .session import SessionManager
from .terminal_parser import (
    PromptInfo,
    extract_interactive_content,
    extract_prompt_options,
    strip_pane_chrome,
)
from .tmux import TmuxController

logger = logging.getLogger(__name__)

# Type aliases for callbacks
OutputCallback = Callable[[str, str], Coroutine[Any, Any, None]]
# (channel_id, new_lines)

PromptCallback = Callable[[str, str, PromptInfo], Coroutine[Any, Any, None]]
# (channel_id, window_id, prompt_info)


class PaneMonitor:
    """Periodically polls tmux panes and pushes updates to Discord."""

    def __init__(
        self,
        tmux: TmuxController,
        session_mgr: SessionManager,
        interval: float = 2.0,
    ):
        self._tmux = tmux
        self._session_mgr = session_mgr
        self._interval = interval
        self._tasks: dict[str, asyncio.Task[None]] = {}  # channel_id -> polling task
        self._prev_content: dict[str, str] = {}  # channel_id -> last stripped content
        self._prev_prompt_key: dict[str, str] = {}  # channel_id -> hash of last prompt
        self._on_new_output: OutputCallback | None = None
        self._on_prompt_detected: PromptCallback | None = None

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_output(self, callback: OutputCallback) -> None:
        """Register callback for new terminal output."""
        self._on_new_output = callback

    def on_prompt(self, callback: PromptCallback) -> None:
        """Register callback for detected interactive prompts."""
        self._on_prompt_detected = callback

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
        self._prev_content.pop(channel_id, None)
        self._prev_prompt_key.pop(channel_id, None)

    def stop_all(self) -> None:
        """Cancel all polling tasks."""
        for channel_id, task in self._tasks.items():
            task.cancel()
            logger.debug("Cancelled polling for channel %s", channel_id)
        self._tasks.clear()
        self._prev_content.clear()
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
        """Single poll iteration: capture, diff, detect prompts."""
        # 1. Capture pane text
        try:
            pane_text = await self._tmux.capture_pane_text(window_id)
        except Exception:
            logger.debug("Failed to capture pane for channel %s", channel_id)
            return
        if not pane_text:
            return

        # 2. Strip chrome and diff for new output
        raw_lines = pane_text.split("\n")
        stripped_lines = strip_pane_chrome(raw_lines)
        current_stripped = "\n".join(stripped_lines)

        prev = self._prev_content.get(channel_id, "")
        self._prev_content[channel_id] = current_stripped

        if prev and current_stripped != prev:
            new_lines = self._compute_new_lines(prev, current_stripped)
            if new_lines and new_lines.strip():
                if self._on_new_output:
                    try:
                        await self._on_new_output(channel_id, new_lines)
                    except Exception:
                        logger.exception("Output callback failed for %s", channel_id)

        # 3. Check for interactive prompts on FULL pane text
        ui_content = extract_interactive_content(pane_text)

        if ui_content:
            # Build a key from name + content hash to detect duplicates
            prompt_key = ui_content.name + ":" + hashlib.md5(
                ui_content.content.encode()
            ).hexdigest()

            if prompt_key != self._prev_prompt_key.get(channel_id):
                self._prev_prompt_key[channel_id] = prompt_key
                prompt_info = extract_prompt_options(pane_text)
                if prompt_info is None:
                    # No numbered options, but still an interactive UI —
                    # create a minimal PromptInfo with the content as context
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
            # No prompt detected — clear tracking if there was one before
            if channel_id in self._prev_prompt_key:
                del self._prev_prompt_key[channel_id]

    # ------------------------------------------------------------------
    # Diffing
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_new_lines(prev: str, current: str) -> str:
        """Compute new lines that appear in current but not in prev.

        Uses a simple suffix-matching approach: find the longest suffix
        of prev that appears in current, then return everything after it.
        """
        prev_lines = prev.split("\n")
        current_lines = current.split("\n")

        # Find how many lines at the end of prev match lines in current
        # by looking for the last N lines of prev at the start of current
        # Actually, simpler: find where prev content ends in current
        # by matching trailing lines of prev to lines in current.

        # Strategy: find the longest common suffix between prev_lines
        # and a prefix of current_lines, then return the rest.
        # But tmux scrolls, so we match trailing prev lines to current.

        # Find the best match: look for prev's last lines in current
        match_start = -1
        # Try matching decreasing amounts of prev's tail
        for start in range(len(prev_lines)):
            tail = prev_lines[start:]
            tail_len = len(tail)
            if tail_len > len(current_lines):
                continue
            # Check if tail matches the beginning of current
            if current_lines[:tail_len] == tail:
                match_start = tail_len
                break

        if match_start >= 0:
            new = current_lines[match_start:]
        else:
            # No overlap found — treat everything as new only if content
            # has genuinely changed (not just a minor chrome update)
            # To avoid noise, only return content if there's substantial change
            new = current_lines

        # Strip trailing empty lines
        while new and not new[-1].strip():
            new.pop()
        # Strip leading empty lines
        while new and not new[0].strip():
            new.pop(0)

        return "\n".join(new)
