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
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum text length for a single message sent to Discord
MAX_MESSAGE_LENGTH = 1900
# Maximum summary length for tool_use arguments
MAX_SUMMARY_LENGTH = 200


# -- Message splitting -------------------------------------------------------


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into chunks that fit within *limit* characters.

    Splits on newline boundaries when possible, falling back to hard cuts.
    Returns a list with at least one element.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to split at last newline within limit
        split_pos = text.rfind("\n", 0, limit)
        if split_pos <= 0:
            # No good newline — hard cut
            split_pos = limit
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks


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

    Supports both Claude Code and Codex CLI JSONL formats:
    - Claude: type="assistant", message.content[].type="text"/"tool_use"
    - Codex:  type="response_item", payload.role="assistant",
              payload.content[].type="output_text"

    Returns list of text strings to send as messages.
    Skips thinking blocks, user messages, and summary entries.
    """
    msg_type = entry.get("type")

    # -- Claude Code format --
    if msg_type == "assistant":
        message = entry.get("message")
        if not isinstance(message, dict):
            return []

        content = message.get("content", [])
        if not isinstance(content, list):
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

    # -- Codex CLI format --
    if msg_type == "response_item":
        payload = entry.get("payload", {})
        if not isinstance(payload, dict) or payload.get("role") != "assistant":
            return []

        content = payload.get("content", [])
        if not isinstance(content, list):
            return []

        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")

            if btype == "output_text":
                text = block.get("text", "").strip()
                if text:
                    texts.append(text)

            elif btype in ("local_shell_call", "computer_call"):
                cmd = block.get("command", "") or block.get("action", "")
                if cmd:
                    texts.append(f"\U0001f527 **Shell**({cmd[:MAX_SUMMARY_LENGTH]})")

            # Skip "reasoning" blocks

        return texts

    # -- Copilot CLI format --
    if msg_type == "assistant.message":
        data = entry.get("data", {})
        if isinstance(data, dict):
            text = data.get("content", "").strip()
            if text:
                return [text]
        return []

    return []


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
        launcher: Any = None,
        tmux: Any = None,
    ):
        self._session_mgr = session_mgr
        self._poll_interval = poll_interval
        self._projects_path = projects_path or Path.home() / ".claude" / "projects"
        self._launcher = launcher  # optional; used to resolve alias session paths
        self._tmux = tmux  # optional; used for pane-based session detection
        self._running = False
        self._task: asyncio.Task | None = None

        # Per-channel tracking (JSONL-based CLIs)
        # Key: (channel_id, file_path) — each channel tracks its own position
        # independently even if two channels share the same session file.
        self._offsets: dict[tuple[str, str], int] = {}
        self._mtimes: dict[tuple[str, str], float] = {}

        # Offset persistence
        self._offsets_file = Path.home() / ".gits" / "jsonl_offsets.json"
        self._offsets_dirty = False
        self._offsets_last_save = 0.0
        self._SAVE_DEBOUNCE = 10.0  # seconds between disk writes
        self._load_offsets()

        # OpenCode tracking (SQLite timestamp-based)
        self._oc_timestamps: dict[str, int] = {}  # session_id -> last part timestamp

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
        self._save_offsets(force=True)
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
            self._save_offsets()
            await asyncio.sleep(self._poll_interval)

    async def _poll_once(self) -> None:
        """Single poll iteration: check all bindings for new JSONL content.

        Session detection order (most authoritative first):
        1. Pane-based: walk the tmux pane's process tree to find the foreground
           claude process, read its cwd, and pick the most recently modified
           JSONL in that project directory.  Works even when the hook never fires
           (e.g. plain-text conversations with no tool calls).
        2. session_map.json: hook-written fallback for bindings without a
           window_id or when tmux is not available.
        """
        bindings = self._session_mgr.list_bindings()

        # Read session_map once up front so pane detection can consult it.
        session_map = self._read_session_map()

        # ── Step 1: pane-based session detection (authoritative) ─────────────
        if self._tmux is not None:
            for binding in bindings:
                if not binding.window_id:
                    continue
                if getattr(binding, "suspended", False):
                    continue
                cli = getattr(binding, "coding_cli", "claude")
                if cli != "claude":
                    continue
                try:
                    detected = await self._detect_session_via_pane(binding)
                except Exception:
                    logger.debug(
                        "Pane session detection failed for ch=%s window=%s",
                        binding.channel_id, binding.window_id, exc_info=True,
                    )
                    detected = None
                if detected and detected != binding.cli_session_id:
                    # Guard 1: don't steal a session that belongs to another channel.
                    # If `detected` is already assigned to a different binding,
                    # the mtime fallback picked the wrong file — skip this update.
                    already_owned = any(
                        other.channel_id != binding.channel_id
                        and other.cli_session_id == detected
                        for other in bindings
                    )
                    if already_owned:
                        logger.warning(
                            "Pane detection skipped for ch=%s (window %s): "
                            "detected session %s already owned by another channel",
                            binding.channel_id, binding.window_id, detected,
                        )
                        continue
                    # Guard 2: don't let the mtime fallback override a session that
                    # session_map explicitly assigned to a DIFFERENT window.
                    # session_map is written by the hook (authoritative); mtime is a
                    # best-guess that goes wrong when two channels share a project dir.
                    if session_map:
                        our_key_suffix = f":{binding.window_id}"
                        for k, v in session_map.items():
                            if not isinstance(v, dict):
                                continue
                            if v.get("session_id") == detected and not k.endswith(our_key_suffix):
                                logger.warning(
                                    "Pane detection skipped for ch=%s (window %s): "
                                    "detected session %s belongs to a different window "
                                    "(%s) per session_map — mtime fallback overruled",
                                    binding.channel_id, binding.window_id, detected, k,
                                )
                                detected = None
                                break
                    if not detected:
                        continue
                    logger.info(
                        "Pane detection updated session for ch=%s (window %s): "
                        "%s -> %s",
                        binding.channel_id,
                        binding.window_id,
                        binding.cli_session_id,
                        detected,
                    )
                    await self._session_mgr.update_cli_session_id(
                        binding.channel_id, detected
                    )
                    binding.cli_session_id = detected

        # ── Step 2: session_map.json fallback ─────────────────────────────────
        # Try to pick up session IDs from session_map.json.
        # The hook writes keys as "{tmux_session_name}:{window_id}".
        # Rather than guessing the session name we search all keys for
        # a suffix matching ":{window_id}".
        if not session_map:
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
                    # Decide whether to follow the new session from session_map.
                    #
                    # The hook is authoritative: it only fires for interactive
                    # sessions (non-interactive `claude -p` is filtered out).
                    # So when session_map explicitly assigns a new session to this
                    # window we should follow it — UNLESS the current session was
                    # itself legitimately hook-assigned AND is still the current
                    # session_map entry for this window (meaning the hook hasn't
                    # moved on yet, so the new entry came from somewhere else).
                    #
                    # "Legitimately assigned" = the current session_id is the
                    # current session_map entry for THIS window.  If it was set
                    # by pane detection mtime or is stale in session_map, it is
                    # NOT legitimately assigned and the guard must not protect it.
                    if binding.cli_session_id:
                        our_key_suffix = f":{binding.window_id}"

                        # Check if current session is stolen (appears in session_map
                        # under a DIFFERENT window) — always follow session_map then.
                        stolen = any(
                            not k.endswith(our_key_suffix)
                            and isinstance(v, dict)
                            and v.get("session_id") == binding.cli_session_id
                            for k, v in session_map.items()
                        )
                        if stolen:
                            logger.warning(
                                "Stolen session detected for channel %s (window %s): "
                                "session %s belongs to a different window per session_map "
                                "— correcting to %s",
                                binding.channel_id,
                                binding.window_id,
                                binding.cli_session_id,
                                new_sid,
                            )
                        else:
                            # Only guard if current session is the CURRENT
                            # session_map entry for this window — i.e. hook
                            # legitimately put it here and hasn't moved on.
                            # If it isn't in session_map for this window it was
                            # set by pane mtime (unreliable) and must not block.
                            current_is_hook_assigned = any(
                                k.endswith(our_key_suffix)
                                and isinstance(v, dict)
                                and v.get("session_id") == binding.cli_session_id
                                for k, v in session_map.items()
                            )
                            if current_is_hook_assigned:
                                # Hook explicitly assigned the current session to
                                # this window and hasn't changed it yet — keep it.
                                logger.info(
                                    "Skipping session_id update for channel %s "
                                    "(window %s): current session %s is the current "
                                    "hook-assigned session [proposed_new=%s]",
                                    binding.channel_id,
                                    binding.window_id,
                                    binding.cli_session_id,
                                    new_sid,
                                )
                                continue

                    old_path = self._find_jsonl_file(binding)
                    try:
                        old_age = round(time.time() - old_path.stat().st_mtime, 1) if old_path else None
                    except OSError:
                        old_age = None
                    logger.info(
                        "Updating cli_session_id for channel %s "
                        "(window %s): %s -> %s "
                        "[old_file=%s, old_age=%ss, reason=%s]",
                        binding.channel_id,
                        binding.window_id,
                        binding.cli_session_id,
                        new_sid,
                        old_path.name if old_path else "NOT_FOUND",
                        old_age if old_age is not None else "N/A",
                        "no_existing_session" if not binding.cli_session_id else "session_stale",
                    )
                    await self._session_mgr.update_cli_session_id(
                        binding.channel_id, new_sid
                    )
                    binding.cli_session_id = new_sid

        for binding in bindings:
            cli = getattr(binding, "coding_cli", "claude")
            if not binding.cli_session_id:
                continue
            if getattr(binding, "suspended", False):
                continue
            try:
                if cli == "opencode":
                    await self._check_opencode_binding(binding)
                else:
                    await self._check_binding(binding)
            except Exception:
                logger.warning(
                    "Error checking output for channel %s", binding.channel_id,
                    exc_info=True,
                )

    # -- Offset persistence -------------------------------------------------

    def _load_offsets(self) -> None:
        """Load persisted offsets from disk on startup."""
        if not self._offsets_file.exists():
            return
        try:
            raw = json.loads(self._offsets_file.read_text())
            for key_str, val in raw.items():
                # Stored as "channel_id\x00file_path"
                parts = key_str.split("\x00", 1)
                if len(parts) == 2:
                    self._offsets[tuple(parts)] = val["offset"]  # type: ignore[index]
                    self._mtimes[tuple(parts)] = val["mtime"]    # type: ignore[index]
            logger.info("Loaded %d persisted JSONL offsets", len(self._offsets))
        except Exception:
            logger.warning("Failed to load jsonl_offsets.json — starting fresh", exc_info=True)

    def _save_offsets(self, force: bool = False) -> None:
        """Persist offsets to disk (debounced; force=True skips debounce)."""
        if not self._offsets_dirty:
            return
        now = time.time()
        if not force and (now - self._offsets_last_save) < self._SAVE_DEBOUNCE:
            return
        try:
            raw = {
                f"{ch_id}\x00{fp}": {"offset": off, "mtime": self._mtimes.get((ch_id, fp), 0.0)}
                for (ch_id, fp), off in self._offsets.items()
            }
            tmp = self._offsets_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(raw))
            tmp.replace(self._offsets_file)
            self._offsets_dirty = False
            self._offsets_last_save = now
        except Exception:
            logger.warning("Failed to save jsonl_offsets.json", exc_info=True)

    async def _detect_session_via_pane(self, binding: Any) -> str | None:
        """Detect the active Claude session by inspecting the tmux pane process tree.

        Algorithm:
        1. Ask TmuxController for the pane's shell PID.
        2. Walk /proc/<pid>/task/<pid>/children recursively to find claude
           descendant processes.  Only direct descendants of the pane shell are
           considered — background jobs that merely inherited TMUX_PANE are not
           in this tree.
        3. Read /proc/<claude_pid>/cwd to get the working directory.
        4. Hash the cwd to a Claude projects dir name and scan for the most
           recently modified *.jsonl file.  The filename stem IS the session_id.

        Returns the session_id string, or None if detection fails.
        """
        if self._tmux is None:
            return None
        pane_pid = await self._tmux.pane_pid(binding.window_id)
        if not pane_pid:
            return None
        return await asyncio.to_thread(
            self._detect_session_from_pid, pane_pid
        )

    def _detect_session_from_pid(self, pane_pid: int) -> str | None:
        """Synchronous worker for _detect_session_via_pane (runs in thread)."""
        claude_pid = self._find_claude_descendant(pane_pid)
        if claude_pid is None:
            return None

        # Read the claude process's working directory
        try:
            cwd = os.readlink(f"/proc/{claude_pid}/cwd")
        except OSError:
            return None

        # Strategy 1: find the exact JSONL file this process has open for writing.
        # Scanning /proc/<pid>/fd/ gives us the file descriptors the process holds,
        # which uniquely identifies its session even when multiple claude instances
        # share the same project directory.
        dir_hash = cwd.replace("/", "-")
        for variant in (dir_hash, dir_hash.lstrip("-")):
            project_dir = self._projects_path / variant
            if not project_dir.is_dir():
                continue
            project_dir_str = str(project_dir.resolve())
            fd_dir = Path(f"/proc/{claude_pid}/fd")
            try:
                for fd_entry in fd_dir.iterdir():
                    try:
                        target = os.readlink(str(fd_entry))
                    except OSError:
                        continue
                    if not target.endswith(".jsonl"):
                        continue
                    target_path = Path(target)
                    if str(target_path.parent.resolve()) == project_dir_str:
                        return target_path.stem
            except OSError:
                pass

        # Strategy 2: fallback — most recently modified JSONL (legacy behaviour,
        # used when /proc/fd scan fails, e.g. permission denied).
        for variant in (dir_hash, dir_hash.lstrip("-")):
            project_dir = self._projects_path / variant
            if not project_dir.is_dir():
                continue
            best: Path | None = None
            best_mtime: float = 0.0
            try:
                for f in project_dir.iterdir():
                    if f.suffix != ".jsonl":
                        continue
                    try:
                        mtime = f.stat().st_mtime
                    except OSError:
                        continue
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best = f
            except OSError:
                continue
            if best is not None:
                logger.debug(
                    "Pane detection fell back to mtime for pid=%d: %s",
                    claude_pid, best.name,
                )
                return best.stem  # stem == session_id UUID

        return None

    @staticmethod
    def _find_claude_descendant(root_pid: int) -> int | None:
        """Walk the /proc process tree from root_pid and return the first
        descendant whose executable name is 'claude'.

        Uses /proc/<pid>/task/<pid>/children for breadth-first traversal.
        Returns None if no claude process is found.
        """
        visited: set[int] = set()
        queue: list[int] = [root_pid]
        while queue:
            pid = queue.pop(0)
            if pid in visited:
                continue
            visited.add(pid)
            # Check if this process is claude (skip root pane shell itself)
            if pid != root_pid:
                try:
                    comm = Path(f"/proc/{pid}/comm").read_text().strip()
                    if comm == "claude":
                        return pid
                except OSError:
                    continue
            # Enqueue children
            try:
                children_text = Path(
                    f"/proc/{pid}/task/{pid}/children"
                ).read_text()
                for child in children_text.split():
                    child_pid = int(child)
                    if child_pid not in visited:
                        queue.append(child_pid)
            except (OSError, ValueError):
                pass
        return None

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
            cli = getattr(binding, "coding_cli", "claude")
            logger.debug(
                "No JSONL file found for channel %s (cli=%s, sid=%s)",
                binding.channel_id, cli, binding.cli_session_id,
            )
            return

        file_key = (binding.channel_id, str(jsonl_path))

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
            self._offsets_dirty = True
            logger.info(
                "JSONL first-seen for ch=%s: %s (size=%d)",
                binding.channel_id, jsonl_path.name, stat.st_size,
            )
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
        self._offsets_dirty = True

        # Fire callbacks
        if new_texts and self._on_message:
            logger.info(
                "JSONL new content for ch=%s: %d texts, offset %d->%d",
                binding.channel_id, len(new_texts), last_offset, new_size,
            )
            for text in new_texts:
                chunks = split_message(text, MAX_MESSAGE_LENGTH)
                for chunk in chunks:
                    try:
                        await self._on_message(binding.channel_id, chunk)
                        logger.info(
                            "JSONL forwarded to Discord ch=%s: %s",
                            binding.channel_id, chunk[:80],
                        )
                    except Exception:
                        logger.exception("JsonlMonitor message callback error")

    def _find_jsonl_file(self, binding: Any) -> Path | None:
        """Find the JSONL/session file for a binding's CLI session.

        Dispatches to CLI-specific finders based on binding.coding_cli.
        If a launcher is available, delegates to it so alias session_path
        overrides are respected.
        """
        if not binding.cli_session_id or not binding.work_dir:
            return None

        if self._launcher is not None:
            result = self._launcher.get_session_file(
                binding.work_dir, binding.coding_cli or "claude", binding.cli_session_id
            )
            return Path(result) if result else None

        cli = getattr(binding, "coding_cli", "claude")
        if cli == "codex":
            return self._find_codex_jsonl(binding)
        if cli == "copilot":
            return self._find_copilot_jsonl(binding)
        return self._find_claude_jsonl(binding)

    def _find_claude_jsonl(self, binding: Any) -> Path | None:
        """Find Claude Code JSONL: ~/.claude/projects/<dir-hash>/<session_id>.jsonl"""
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
        # Only do this when the expected project dir does NOT exist at all —
        # if the expected dir exists but lacks the file, the session belongs to
        # a different project and should NOT be returned (it would prevent the
        # session-switch guard from updating a binding whose work_dir changed).
        expected_dir_exists = any(
            (claude_projects / v).is_dir()
            for v in (dir_hash, dir_hash.lstrip("-"))
            if v
        )
        if not expected_dir_exists:
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

    def _find_codex_jsonl(self, binding: Any) -> Path | None:
        """Find Codex JSONL: ~/.codex/sessions/YYYY/MM/DD/rollout-*-{session_id}.jsonl

        Codex filenames are like:
            rollout-2026-03-14T18-38-36-019cef25-2ae6-73f0-97fc-795524e3cdbe.jsonl
        The session_id is the UUID suffix after the timestamp prefix.

        If the exact session_id match fails (e.g. Codex restarted with a new
        session), fall back to the most recently modified JSONL whose
        ``session_meta.cwd`` matches the binding's work_dir.
        """
        codex_dir = Path.home() / ".codex" / "sessions"
        if not codex_dir.exists():
            return None

        sid = binding.cli_session_id
        # Strategy 1: exact match by session_id in filename
        for jsonl_file in codex_dir.rglob(f"*{sid}.jsonl"):
            return jsonl_file

        # Strategy 2: find most recent JSONL for this work_dir.
        # Check the first line (session_meta) for matching cwd.
        resolved_work_dir = str(Path(binding.work_dir).resolve())
        best: Path | None = None
        best_mtime: float = 0.0

        for jsonl_file in codex_dir.rglob("rollout-*.jsonl"):
            try:
                stat = jsonl_file.stat()
                if stat.st_mtime <= best_mtime:
                    continue
                # Read only the first line for session_meta
                with open(jsonl_file) as f:
                    first_line = f.readline()
                data = json.loads(first_line)
                if data.get("type") != "session_meta":
                    continue
                cwd = data.get("payload", {}).get("cwd", "")
                if cwd and str(Path(cwd).resolve()) == resolved_work_dir:
                    best = jsonl_file
                    best_mtime = stat.st_mtime
                    # Update binding session_id to match
                    new_sid = data.get("payload", {}).get("id", "")
                    if new_sid and new_sid != sid:
                        logger.info(
                            "Codex session_id changed for ch=%s: %s -> %s",
                            binding.channel_id, sid, new_sid,
                        )
                        binding.cli_session_id = new_sid
            except (json.JSONDecodeError, OSError, ValueError):
                continue

        return best

    def _find_copilot_jsonl(self, binding: Any) -> Path | None:
        """Find Copilot JSONL: ~/.copilot/session-state/{id}/events.jsonl"""
        copilot_dir = (
            Path.home() / ".copilot" / "session-state" / binding.cli_session_id
        )
        events_file = copilot_dir / "events.jsonl"
        if events_file.exists():
            return events_file
        return None

    async def _check_opencode_binding(self, binding: Any) -> None:
        """Check an OpenCode binding for new assistant messages.

        OpenCode v1.2.26+ stores messages in SQLite at
        ``~/.local/share/opencode/opencode.db``.  We query for assistant
        text parts newer than the last-seen timestamp.
        """
        import sqlite3

        db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        if not db_path.exists():
            return

        session_key = binding.cli_session_id
        if not session_key:
            return

        # Track last-seen part timestamp per session (stored as int in _known_parts)
        last_ts: int = self._oc_timestamps.get(session_key, -1)

        new_texts, max_ts = await asyncio.to_thread(
            self._read_opencode_db, db_path, session_key, last_ts,
        )

        # First time: snapshot only (don't replay history)
        if last_ts == -1:
            self._oc_timestamps[session_key] = max_ts
            logger.info(
                "OpenCode first-seen for ch=%s sid=%s (ts=%s)",
                binding.channel_id, session_key, max_ts,
            )
            return

        if max_ts > last_ts:
            self._oc_timestamps[session_key] = max_ts

        # Fire callbacks
        if new_texts and self._on_message:
            logger.info(
                "OpenCode new content for ch=%s: %d texts",
                binding.channel_id, len(new_texts),
            )
            for text in new_texts:
                chunks = split_message(text, MAX_MESSAGE_LENGTH)
                for chunk in chunks:
                    try:
                        await self._on_message(binding.channel_id, chunk)
                    except Exception:
                        logger.exception("OpenCode monitor callback error")

    @staticmethod
    def _read_opencode_db(
        db_path: Path, session_id: str, after_ts: int,
    ) -> tuple[list[str], int]:
        """Read new assistant text parts from OpenCode's SQLite DB.

        Returns (list_of_texts, max_timestamp).
        """
        import sqlite3

        texts: list[str] = []
        max_ts = after_ts

        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = db.cursor()
            cur.execute("""
                SELECT p.data, p.time_updated, m.data
                FROM part p
                JOIN message m ON p.message_id = m.id
                WHERE m.session_id = ?
                  AND p.time_updated > ?
                ORDER BY p.time_updated ASC
            """, (session_id, after_ts))

            for pdata_str, ts, mdata_str in cur.fetchall():
                if ts and ts > max_ts:
                    max_ts = ts
                # Only assistant messages
                try:
                    mdata = json.loads(mdata_str) if mdata_str else {}
                except (json.JSONDecodeError, ValueError):
                    continue
                if mdata.get("role") != "assistant":
                    continue
                # Only text parts
                try:
                    pdata = json.loads(pdata_str) if pdata_str else {}
                except (json.JSONDecodeError, ValueError):
                    continue
                if pdata.get("type") != "text":
                    continue
                text = pdata.get("text", "").strip()
                if text:
                    texts.append(text)

            db.close()
        except Exception:
            logger.warning("Error reading OpenCode DB", exc_info=True)

        return texts, max_ts

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
