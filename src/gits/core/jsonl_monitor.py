"""JsonlMonitor — polls Claude Code JSONL session logs for new output.

Watches JSONL files corresponding to bound channels' CLI sessions and
pushes assistant text and tool-use summaries to Discord via a callback.

Design:
- Single polling loop iterates over all active bindings
- Byte-offset tracking per session file (only read new bytes)
- mtime cache to skip files that haven't changed
- Minimal JSONL parsing (assistant text + tool_use summaries)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum text length for a single message sent to Discord
MAX_MESSAGE_LENGTH = 1900
# Maximum summary length for tool_use arguments
MAX_SUMMARY_LENGTH = 200


# -- JSONL parsing helpers ---------------------------------------------------


def parse_jsonl_line(line: str) -> dict | None:
    """Parse a JSONL line, return None if invalid or empty."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def format_tool_use_summary(name: str, input_data: dict | Any) -> str:
    """Format a tool_use block into a brief summary line.

    Returns formatted string like "**ToolName**(summary)".
    """
    if not isinstance(input_data, dict):
        return f"\U0001f527 **{name}**"

    summary = ""
    if name in ("Read", "Glob"):
        summary = input_data.get("file_path") or input_data.get("pattern", "")
    elif name == "Write":
        summary = input_data.get("file_path", "")
    elif name in ("Edit", "NotebookEdit"):
        summary = input_data.get("file_path") or input_data.get("notebook_path", "")
    elif name == "Bash":
        summary = input_data.get("command", "")
    elif name == "Grep":
        summary = input_data.get("pattern", "")
    elif name == "Task":
        summary = input_data.get("description", "")
    elif name == "WebFetch":
        summary = input_data.get("url", "")
    elif name == "WebSearch":
        summary = input_data.get("query", "")
    else:
        # Generic: first string value
        for v in input_data.values():
            if isinstance(v, str) and v:
                summary = v
                break

    if summary:
        if len(summary) > MAX_SUMMARY_LENGTH:
            summary = summary[:MAX_SUMMARY_LENGTH] + "\u2026"
        return f"\U0001f527 **{name}**({summary})"
    return f"\U0001f527 **{name}**"


def extract_assistant_content(entry: dict) -> list[str]:
    """Extract displayable content from an assistant message entry.

    Returns list of text strings to send as messages.
    Skips thinking blocks, user messages, and summary entries.
    """
    msg_type = entry.get("type")
    if msg_type != "assistant":
        return []

    message = entry.get("message")
    if not isinstance(message, dict):
        return []

    content = message.get("content", [])
    if not isinstance(content, list):
        # Sometimes content is a plain string
        if isinstance(content, str) and content.strip():
            return [content.strip()]
        return []

    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")

        if btype == "text":
            text = block.get("text", "").strip()
            if text:
                texts.append(text)

        elif btype == "tool_use":
            name = block.get("name", "unknown")
            inp = block.get("input", {})
            summary = format_tool_use_summary(name, inp)
            texts.append(summary)

        # Skip "thinking" blocks — internal reasoning

    return texts


# -- JsonlMonitor class ------------------------------------------------------


class JsonlMonitor:
    """Monitors Claude Code JSONL session logs for new assistant output.

    Uses a single polling loop that checks all bound channels' JSONL files
    for new content. Byte-offset tracking and mtime caching avoid redundant
    reads.
    """

    def __init__(
        self,
        session_mgr: Any,
        poll_interval: float = 2.0,
    ):
        self._session_mgr = session_mgr
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

        # Per-file tracking
        self._offsets: dict[str, int] = {}   # file_path -> byte offset
        self._mtimes: dict[str, float] = {}  # file_path -> last mtime

        # Callback: (channel_id, text) -> None
        self._on_message: Callable[[str, str], Awaitable[None]] | None = None

    def on_message(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        """Register callback for new assistant messages.

        The callback receives (channel_id, text).
        """
        self._on_message = callback

    def start(self) -> None:
        """Start the JSONL monitoring loop."""
        if self._running:
            logger.warning("JsonlMonitor already running")
            return
        self._running = True
        self._task = asyncio.create_task(
            self._poll_loop(), name="jsonl-monitor"
        )
        logger.info("JsonlMonitor started (interval=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Stop the JSONL monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("JsonlMonitor stopped")

    # -- Internal -----------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Background polling loop."""
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("JsonlMonitor poll error")
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Single poll iteration: check all bindings for new JSONL content."""
        bindings = self._session_mgr.list_bindings()
        for binding in bindings:
            if not binding.cli_session_id:
                continue
            try:
                await self._check_binding(binding)
            except Exception:
                logger.debug(
                    "Error checking JSONL for channel %s", binding.channel_id,
                    exc_info=True,
                )

    async def _check_binding(self, binding: Any) -> None:
        """Check a single binding's JSONL file for new content."""
        jsonl_path = self._find_jsonl_file(binding)
        if jsonl_path is None:
            return

        file_key = str(jsonl_path)

        # Check mtime — skip if unchanged
        try:
            stat = jsonl_path.stat()
        except OSError:
            return

        last_mtime = self._mtimes.get(file_key, 0.0)
        last_offset = self._offsets.get(file_key, 0)

        if stat.st_mtime <= last_mtime and stat.st_size <= last_offset:
            return

        # First time seeing this file — skip to end (don't replay history)
        if file_key not in self._offsets:
            self._offsets[file_key] = stat.st_size
            self._mtimes[file_key] = stat.st_mtime
            return

        # Detect file truncation
        if stat.st_size < last_offset:
            logger.info(
                "JSONL file truncated for session %s, resetting offset",
                binding.cli_session_id,
            )
            last_offset = 0

        # Read new content from byte offset (blocking I/O in thread)
        new_texts = await asyncio.to_thread(
            self._read_new_entries, jsonl_path, last_offset
        )

        # Update tracking
        try:
            # Re-stat to get accurate size after read
            new_size = jsonl_path.stat().st_size
        except OSError:
            new_size = last_offset
        self._offsets[file_key] = new_size
        self._mtimes[file_key] = stat.st_mtime

        # Fire callbacks
        if new_texts and self._on_message:
            for text in new_texts:
                if len(text) > MAX_MESSAGE_LENGTH:
                    text = text[:MAX_MESSAGE_LENGTH] + "\n\u2026 (truncated)"
                try:
                    await self._on_message(binding.channel_id, text)
                except Exception:
                    logger.exception("JsonlMonitor message callback error")

    def _find_jsonl_file(self, binding: Any) -> Path | None:
        """Find the JSONL file for a binding's CLI session.

        Claude Code stores sessions at:
            ~/.claude/projects/<dir-hash>/<session_id>.jsonl

        where dir-hash is the work_dir path with / replaced by -.
        """
        if not binding.cli_session_id or not binding.work_dir:
            return None

        claude_projects = Path.home() / ".claude" / "projects"
        if not claude_projects.exists():
            return None

        # Claude Code directory hash: path with / replaced by -
        dir_hash = binding.work_dir.replace("/", "-")
        project_dir = claude_projects / dir_hash

        if not project_dir.exists():
            return None

        jsonl_file = project_dir / f"{binding.cli_session_id}.jsonl"
        if jsonl_file.exists():
            return jsonl_file

        return None

    @staticmethod
    def _read_new_entries(file_path: Path, offset: int) -> list[str]:
        """Read new JSONL entries from a file starting at byte offset.

        Returns a list of displayable text strings.
        Called in a thread via asyncio.to_thread.
        """
        texts: list[str] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    entry = parse_jsonl_line(line)
                    if entry is None:
                        continue
                    content_texts = extract_assistant_content(entry)
                    texts.extend(content_texts)
        except OSError as e:
            logger.error("Error reading JSONL file %s: %s", file_path, e)
        return texts
