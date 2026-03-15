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
        projects_path: Path | None = None,
    ):
        self._session_mgr = session_mgr
        self._poll_interval = poll_interval
        self._projects_path = projects_path or Path.home() / ".claude" / "projects"
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
        """Single poll iteration: check all bindings for new JSONL content.

        Also reads ~/.gits/session_map.json to pick up new CLI session IDs
        written by the ``gits hook`` subprocess.
        """
        bindings = self._session_mgr.list_bindings()

        # Try to pick up session IDs from session_map.json.
        # The hook writes keys as "{tmux_session_name}:{window_id}".
        # Rather than guessing the session name we search all keys for
        # a suffix matching ":{window_id}".
        session_map = self._read_session_map()
        if session_map:
            for binding in bindings:
                if not binding.window_id:
                    continue
                # Find matching entry by window_id suffix
                entry = None
                for key, val in session_map.items():
                    if key.endswith(f":{binding.window_id}"):
                        entry = val
                        break
                if not entry or not isinstance(entry, dict):
                    continue
                new_sid = entry.get("session_id", "")
                if new_sid and new_sid != binding.cli_session_id:
                    logger.info(
                        "Updating cli_session_id for channel %s "
                        "(window %s): %s -> %s",
                        binding.channel_id,
                        binding.window_id,
                        binding.cli_session_id,
                        new_sid,
                    )
                    await self._session_mgr.update_cli_session_id(
                        binding.channel_id, new_sid
                    )
                    binding.cli_session_id = new_sid

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

    @staticmethod
    def _read_session_map() -> dict:
        """Read ~/.gits/session_map.json if it exists.

        Returns the parsed dict, or empty dict on any error.
        """
        map_file = Path.home() / ".gits" / "session_map.json"
        if not map_file.exists():
            return {}
        try:
            return json.loads(map_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

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

        The dir-hash format is the work_dir path with ``/`` replaced by
        ``-``.  However the exact escaping can vary (e.g. underscores
        may or may not be replaced), so we try the exact hash first and
        fall back to scanning all project directories for a matching
        session file.
        """
        if not binding.cli_session_id or not binding.work_dir:
            return None

        claude_projects = self._projects_path
        if not claude_projects.exists():
            return None

        session_filename = f"{binding.cli_session_id}.jsonl"

        # Strategy 1: exact dir-hash (fast path)
        dir_hash = binding.work_dir.replace("/", "-")
        project_dir = claude_projects / dir_hash
        candidate = project_dir / session_filename
        if candidate.exists():
            return candidate

        # Strategy 2: strip leading dash variant
        dir_hash_stripped = dir_hash.lstrip("-")
        if dir_hash_stripped != dir_hash:
            candidate = claude_projects / dir_hash_stripped / session_filename
            if candidate.exists():
                return candidate

        # Strategy 3: scan all project directories for the session file.
        # This handles cases where the dir-hash format differs from our
        # expectation (e.g. underscores replaced with dashes).
        try:
            for d in claude_projects.iterdir():
                if not d.is_dir():
                    continue
                candidate = d / session_filename
                if candidate.exists():
                    logger.debug(
                        "Found JSONL via scan: %s (expected dir_hash=%s, actual=%s)",
                        candidate, dir_hash, d.name,
                    )
                    return candidate
        except OSError:
            pass

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
