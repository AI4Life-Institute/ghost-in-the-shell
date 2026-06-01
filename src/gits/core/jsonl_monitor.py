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
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum text length for a single message sent to Discord
MAX_MESSAGE_LENGTH = 1900
# Maximum summary length for tool_use arguments
MAX_SUMMARY_LENGTH = 200

# Bounded in-loop retry for a single outbound POST. Absorbs momentary Discord
# 429/5xx without waiting a whole poll cycle. After these are exhausted the
# caller leaves the read offset pinned, so the across-poll retry (rate-limited
# by the ~2s poll interval) self-heals when Discord recovers. v1 has no
# cross-poll cap / dead-letter — a persistent on-disk outbox is the heavier
# future option if pin+poll proves insufficient.
_DELIVER_MAX_ATTEMPTS = 3
_DELIVER_BACKOFFS = (0.5, 1.0, 2.0)  # seconds between attempts 1→2, 2→3, …


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
        tmux: Any = None,  # kept for API compatibility; no longer used
    ):
        self._session_mgr = session_mgr
        self._poll_interval = poll_interval
        self._projects_path = projects_path or Path.home() / ".claude" / "projects"
        self._launcher = launcher  # optional; used to resolve alias session paths
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

        # Pending missing-session warnings:
        #   channel_id -> (session_id, work_dir, assigned_at, attempt)
        # Retried up to _WARN_MAX_ATTEMPTS times every _WARN_RETRY_INTERVAL s;
        # warning only fires after all retries are exhausted AND the user has
        # actually interacted (binding.first_interaction_at is set).  Without
        # that gate, every fresh /bind would false-alarm because claude does
        # not flush <sid>.jsonl until the first user prompt is processed.
        self._pending_warn: dict[str, tuple[str, str, float, int]] = {}

        # Callback: (channel_id, text) -> None
        self._on_message: Callable[[str, str], Awaitable[None]] | None = None
        # Callback: (channel_id, raw_jsonl_line) -> None — fed every raw JSONL
        # line for downstream quota-pattern classification. Synchronous so it
        # can be cheap (no event-loop hop).
        self._on_jsonl_line: Callable[[str, str], None] | None = None

        # Idle-timer throttle: outbound POSTs call session_mgr.touch_active so
        # the idle-suspend heuristic sees outbound-only work as activity. Each
        # touch triggers a state.json save, and chatty sessions produce many
        # chunks/sec — coalesce to at most one touch per channel per window.
        # 5s vs the 70-min idle threshold loses ≤0.1% precision.
        self._TOUCH_THROTTLE = 5.0
        self._last_touch_at: dict[str, float] = {}

    def on_message(self, callback: Callable[[str, str], Awaitable[None]]) -> None:
        """Register callback for new assistant messages.

        The callback receives (channel_id, text).
        """
        self._on_message = callback

    def on_jsonl_line(self, callback: Callable[[str, str], None]) -> None:
        """Register a synchronous callback fed with every raw JSONL line.

        Used by ``QuotaPatternMatcher`` integration. Errors are caught and
        logged — a misbehaving callback must never break message delivery.
        """
        self._on_jsonl_line = callback

    async def _touch_active_throttled(self, channel_id: str) -> None:
        now = time.time()
        if now - self._last_touch_at.get(channel_id, 0.0) < self._TOUCH_THROTTLE:
            return
        self._last_touch_at[channel_id] = now
        await self._session_mgr.touch_active(channel_id)

    async def _deliver_chunk(self, channel_id: str, chunk: str) -> bool:
        """Deliver one chunk via the registered callback, with bounded retry.

        Retries transient failures up to ``_DELIVER_MAX_ATTEMPTS`` times with
        short backoff. Returns ``True`` if the chunk was delivered, ``False``
        if every attempt failed — in which case the caller must leave the read
        offset pinned so the next poll re-attempts (deliver-then-advance).

        Constants are read from the module at call time so tests can patch the
        backoff to zero.
        """
        assert self._on_message is not None
        for attempt in range(_DELIVER_MAX_ATTEMPTS):
            try:
                await self._on_message(channel_id, chunk)
                return True
            except Exception:
                last = attempt == _DELIVER_MAX_ATTEMPTS - 1
                logger.warning(
                    "JsonlMonitor delivery attempt %d/%d failed for ch=%s%s",
                    attempt + 1, _DELIVER_MAX_ATTEMPTS, channel_id,
                    "" if last else " — retrying",
                    exc_info=True,
                )
                if last:
                    return False
                await asyncio.sleep(_DELIVER_BACKOFFS[attempt])
        return False

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

        Session assignment uses session_map.json as the sole authoritative
        source.  The gits hook writes to this file on each SessionStart event,
        filtering out non-interactive (one-shot) invocations via ancestor walk.
        """
        bindings = self._session_mgr.list_bindings()
        session_map = self._read_session_map()

        # ── Step 0: fire deferred missing-session warnings ────────────────────
        # Two-stage gate:
        #   (1) binding.first_interaction_at must be set — claude doesn't flush
        #       <sid>.jsonl until the user actually prompts it, so any earlier
        #       alarm is a race not a real wrong-dir bug.
        #   (2) Then ``(attempt + 1) * _WARN_RETRY_INTERVAL`` seconds must
        #       elapse since ``max(assigned_at, first_interaction_at)`` — the
        #       later of "session id was assigned" and "user actually typed".
        # Warning fires only after _WARN_MAX_ATTEMPTS retries with the jsonl
        # still absent.
        _WARN_RETRY_INTERVAL = 15
        _WARN_MAX_ATTEMPTS = 3
        if self._pending_warn:
            now = time.time()
            bindings_by_ch = {b.channel_id: b for b in bindings}
            due: list[str] = []
            for ch, (_sid, _wd, assigned_at, attempt) in self._pending_warn.items():
                binding = bindings_by_ch.get(ch)
                if binding is None:
                    # Binding gone — schedule pop so the entry GCs itself.
                    due.append(ch)
                    continue
                first_int = getattr(binding, "first_interaction_at", None)
                if not isinstance(first_int, (int, float)):
                    # User hasn't interacted yet — don't fire, don't expire.
                    continue
                anchor = max(assigned_at, first_int)
                if now - anchor >= (attempt + 1) * _WARN_RETRY_INTERVAL:
                    due.append(ch)
            for ch in due:
                sid, work_dir, assigned_at, attempt = self._pending_warn.pop(ch)
                binding = bindings_by_ch.get(ch)
                if binding is None or binding.cli_session_id != sid:
                    continue  # session changed / unbound — skip
                if self._find_jsonl_file(binding) is not None:
                    continue  # file appeared — no warning needed
                if attempt + 1 < _WARN_MAX_ATTEMPTS:
                    # Re-queue for next retry, keeping original assigned_at
                    self._pending_warn[ch] = (sid, work_dir, assigned_at, attempt + 1)
                    continue
                # All retries exhausted — emit warning anchored on first_int so
                # the elapsed number reflects the actual evidence window.
                first_int = getattr(binding, "first_interaction_at", None) or assigned_at
                elapsed = now - first_int
                msg = (
                    f"⚠️ Session {sid}: no jsonl appeared in {work_dir} "
                    f"within {elapsed:.0f}s after first user input. "
                    "Possible wrong working directory."
                )
                logger.warning(
                    "Session %s: no jsonl in %s after %.0fs since first user "
                    "input (after %d retries). Possible wrong working directory.",
                    sid, work_dir, elapsed, _WARN_MAX_ATTEMPTS,
                )
                if self._on_message:
                    try:
                        await self._on_message(ch, msg)
                    except Exception:
                        logger.exception("JsonlMonitor warning callback error")

        # ── Step 1: session_map.json — assign / update session IDs ───────────
        # The hook writes keys as "{tmux_session_name}:{window_id}".
        # Search all keys for a suffix matching ":{window_id}".
        if session_map:
            for binding in bindings:
                if getattr(binding, "suspended", False):
                    continue
                if not binding.window_id:
                    continue
                entry = None
                for key, val in session_map.items():
                    if key.endswith(f":{binding.window_id}"):
                        entry = val
                        break
                if not entry or not isinstance(entry, dict):
                    continue
                new_sid = entry.get("session_id", "")
                if not new_sid or new_sid == binding.cli_session_id:
                    continue
                # Validate the session_map entry's cwd matches binding.work_dir
                # before applying. tmux reuses window @IDs after kill-server,
                # so a stale entry from a previously-killed binding (different
                # channel, different work_dir) can collide on @id. Without this
                # guard, e.g. an ai4stock session_id can pollute a
                # vault-weiliu-ghost-dev binding, after which _safe_session_id
                # can't find the JSONL at the binding's expected account+dir
                # path and clears it to fresh — losing the user's conversation.
                # Older session_map entries without "cwd" fall through (legacy
                # compat) to preserve behavior for installs predating the hook
                # change that started writing cwd.
                entry_cwd = entry.get("cwd")
                if entry_cwd and binding.work_dir and entry_cwd != binding.work_dir:
                    logger.info(
                        "Ignoring session_map[%s] for channel %s: "
                        "cwd mismatch (entry=%s, binding=%s) — "
                        "likely a reused tmux window @id from a prior binding",
                        binding.window_id,
                        binding.channel_id,
                        entry_cwd,
                        binding.work_dir,
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
                    "no_existing_session" if not binding.cli_session_id else "session_switch",
                )
                await self._session_mgr.update_cli_session_id(
                    binding.channel_id, new_sid
                )
                binding.cli_session_id = new_sid

                # Missing-session warning: Claude creates the JSONL file lazily
                # (it may not exist immediately at SessionStart).  Queue a
                # deferred check so we don't false-positive on fresh sessions.
                self._pending_warn[binding.channel_id] = (
                    new_sid,
                    getattr(binding, "work_dir", "unknown"),
                    time.time(),
                    0,  # attempt
                )

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

        # Read new content from byte offset (blocking I/O in thread).
        # If a quota callback is registered we also collect raw lines for
        # pattern classification — otherwise we skip the cost.
        raw_lines: list[str] | None = (
            [] if self._on_jsonl_line is not None else None
        )
        items, consumed = await asyncio.to_thread(
            self._read_new_entries, jsonl_path, last_offset, raw_lines
        )

        # Feed every raw (complete) line to the quota-pattern callback. This is
        # cheap, side-effect-free classification — independent of delivery, so
        # we run it on everything read this poll regardless of POST outcome.
        if raw_lines and self._on_jsonl_line is not None:
            for raw in raw_lines:
                try:
                    self._on_jsonl_line(binding.channel_id, raw)
                except Exception:
                    logger.exception("on_jsonl_line callback error")

        # Deliver-then-advance: the offset only moves past a JSONL line once
        # ALL texts that line produced have been delivered. A failed POST leaves
        # the offset pinned at the last fully-committed line so the next poll
        # re-reads and retries instead of dropping the chunk (at-least-once).
        if not items or not self._on_message:
            # Nothing deliverable — still consume past complete (e.g. user)
            # lines so we don't re-read them forever. Safe: there is nothing to
            # lose. ``consumed`` excludes any trailing partial line.
            if consumed > last_offset:
                self._offsets[file_key] = consumed
                self._mtimes[file_key] = stat.st_mtime
                self._offsets_dirty = True
            return

        logger.info(
            "JSONL new content for ch=%s: %d texts from offset %d",
            binding.channel_id, len(items), last_offset,
        )
        # Commit at line boundaries: a single JSONL line can yield multiple
        # texts (multi-block assistant message); they share one line_end and
        # commit together. ``committed`` is the highest line_end whose every
        # text has been delivered.
        committed = last_offset
        n = len(items)
        for idx, (text, line_end) in enumerate(items):
            delivered = True
            for chunk in split_message(text, MAX_MESSAGE_LENGTH):
                if not await self._deliver_chunk(binding.channel_id, chunk):
                    delivered = False
                    break
                # touch_active stays gated on a successful POST only.
                await self._touch_active_throttled(binding.channel_id)
                logger.info(
                    "JSONL forwarded to Discord ch=%s: %s",
                    binding.channel_id, chunk[:80],
                )
            if not delivered:
                # Pin at the last fully-committed line and stop; next poll
                # re-reads from here and retries the failing text.
                if committed > last_offset:
                    self._offsets[file_key] = committed
                    self._offsets_dirty = True
                logger.warning(
                    "JsonlMonitor delivery pinned for ch=%s at offset %d "
                    "(text dropped from this poll, will retry next poll)",
                    binding.channel_id, committed,
                )
                return
            # This text delivered. Advance the committed watermark past this
            # line only once it is the last text of the line (next item has a
            # different line_end, or this is the final item).
            if idx + 1 == n or items[idx + 1][1] != line_end:
                committed = line_end
                self._offsets[file_key] = committed
                self._offsets_dirty = True

        # All texts delivered — advance past any trailing complete non-text
        # lines too, and only now sync mtime (the no-change fast-path guard).
        if consumed > committed:
            self._offsets[file_key] = consumed
            self._offsets_dirty = True
        self._mtimes[file_key] = stat.st_mtime

    def _find_jsonl_file(self, binding: Any) -> Path | None:
        """Find the JSONL/session file for a binding's CLI session.

        Dispatches to CLI-specific finders based on binding.coding_cli.
        If a launcher is available, delegates to it so alias session_path
        overrides are respected. Per Phase 0.5/0.6, the binding's
        ``claude_account`` is forwarded to the launcher so account-isolated
        ``~/.claude-{name}/projects/`` paths resolve correctly.
        """
        if not binding.cli_session_id or not binding.work_dir:
            return None

        # Defensive: tests often use MagicMock bindings where unset attrs
        # auto-create magic values. Treat anything non-str as None.
        claude_account = getattr(binding, "claude_account", None)
        if not isinstance(claude_account, str):
            claude_account = None

        if self._launcher is not None:
            result = self._launcher.get_session_file(
                binding.work_dir,
                binding.coding_cli or "claude",
                binding.cli_session_id,
                claude_account=claude_account,
            )
            return Path(result) if result else None

        cli = getattr(binding, "coding_cli", "claude")
        if cli == "codex":
            return self._find_codex_jsonl(binding)
        if cli == "copilot":
            return self._find_copilot_jsonl(binding)
        return self._find_claude_jsonl(binding)

    def _find_claude_jsonl(self, binding: Any) -> Path | None:
        """Find Claude Code JSONL: ``<projects_dir>/<dir-hash>/<session_id>.jsonl``.

        ``projects_dir`` is account-aware: when ``binding.claude_account`` is
        set, looks under ``~/.claude-{name}/projects/``; otherwise the
        configured ``_projects_path`` (default ``~/.claude/projects``).
        Used as a fallback when no launcher is wired.
        """
        claude_account = getattr(binding, "claude_account", None)
        if not isinstance(claude_account, str):
            claude_account = None
        if claude_account is not None:
            from .account import AccountLayout
            claude_projects = AccountLayout().projects_dir(claude_account)
        else:
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

        db_path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        if not db_path.exists():
            return

        session_key = binding.cli_session_id
        if not session_key:
            return

        # Track last-seen part timestamp per session (stored as int in _known_parts)
        last_ts: int = self._oc_timestamps.get(session_key, -1)

        items, max_ts = await asyncio.to_thread(
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

        # Deliver-then-advance, mirror of _check_binding. The SQL filter is
        # strict (``time_updated > after_ts``), so the watermark may only
        # advance to a timestamp T once EVERY part sharing T is delivered —
        # otherwise an undelivered sibling at T would be skipped forever. On
        # failure we pin at the last fully-completed timestamp; already-sent
        # siblings at the failing timestamp duplicate next poll (at-least-once).
        if not items or not self._on_message:
            if max_ts > last_ts:
                self._oc_timestamps[session_key] = max_ts
            return

        logger.info(
            "OpenCode new content for ch=%s: %d texts",
            binding.channel_id, len(items),
        )
        committed_ts = last_ts
        n = len(items)
        for idx, (text, ts) in enumerate(items):
            delivered = True
            for chunk in split_message(text, MAX_MESSAGE_LENGTH):
                if not await self._deliver_chunk(binding.channel_id, chunk):
                    delivered = False
                    break
                await self._touch_active_throttled(binding.channel_id)
            if not delivered:
                if committed_ts > last_ts:
                    self._oc_timestamps[session_key] = committed_ts
                logger.warning(
                    "OpenCode delivery pinned for ch=%s at ts=%d "
                    "(will retry next poll)",
                    binding.channel_id, committed_ts,
                )
                return
            # Advance the watermark past this timestamp only once it is the
            # last part sharing it (next item has a higher ts, or end of list).
            if idx + 1 == n or items[idx + 1][1] != ts:
                committed_ts = ts
                self._oc_timestamps[session_key] = committed_ts

        if max_ts > committed_ts:
            self._oc_timestamps[session_key] = max_ts

    @staticmethod
    def _read_opencode_db(
        db_path: Path, session_id: str, after_ts: int,
    ) -> tuple[list[tuple[str, int]], int]:
        """Read new assistant text parts from OpenCode's SQLite DB.

        Returns ``(items, max_timestamp)`` where each item is a
        ``(text, time_updated)`` tuple, ordered by ``time_updated`` ascending.
        The per-text timestamp lets ``_check_opencode_binding`` advance its
        watermark only past fully-delivered timestamps (deliver-then-advance).
        """
        import sqlite3

        items: list[tuple[str, int]] = []
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
                    # Coerce ts to int; fall back to the running max so the
                    # watermark stays monotonic even if time_updated is null.
                    items.append((text, int(ts) if ts else max_ts))

            db.close()
        except Exception:
            logger.warning("Error reading OpenCode DB", exc_info=True)

        return items, max_ts

    @staticmethod
    def _read_new_entries(
        file_path: Path,
        offset: int,
        raw_sink: list[str] | None = None,
    ) -> tuple[list[tuple[str, int]], int]:
        """Read new JSONL entries from a file starting at byte offset.

        Returns ``(items, consumed_offset)`` where each item is a
        ``(text, line_end_offset)`` tuple — ``line_end_offset`` is the byte
        position just past the newline-terminated line that produced the text
        (all texts from one line share that offset). ``consumed_offset`` is the
        byte position past the last *complete* (newline-terminated) line read.

        Only newline-terminated lines are consumed: a trailing partial line
        (Claude mid-flush) is left unread so the next poll re-reads it whole.
        This is what lets ``_check_binding`` advance the offset only past
        content it has actually committed (deliver-then-advance).

        If *raw_sink* is provided, every non-empty *complete* raw line is
        appended to it (used for quota-pattern classification).
        Called in a thread via asyncio.to_thread.
        """
        items: list[tuple[str, int]] = []
        consumed = offset
        try:
            # Read in binary so we can track exact byte boundaries. ``\n`` is a
            # single byte that never appears inside a UTF-8 multibyte sequence,
            # so splitting on it is safe regardless of message content.
            with open(file_path, "rb") as f:
                f.seek(offset)
                data = f.read()
            pos = offset
            while True:
                nl = data.find(b"\n", pos - offset)
                if nl == -1:
                    break  # trailing partial line — leave unconsumed
                line_end = offset + nl + 1
                raw_bytes = data[pos - offset:nl + 1]
                try:
                    line = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    # Complete but undecodable line — skip it but still advance
                    # past it (it is newline-terminated, matching the
                    # skip-invalid behaviour for malformed lines).
                    consumed = line_end
                    pos = line_end
                    continue
                if raw_sink is not None and line.strip():
                    raw_sink.append(line)
                entry = parse_jsonl_line(line)
                if entry is not None:
                    for text in extract_assistant_content(entry):
                        items.append((text, line_end))
                # Complete line — safe to advance past it whether or not it
                # produced deliverable text.
                consumed = line_end
                pos = line_end
        except OSError as e:
            logger.error("Error reading JSONL file %s: %s", file_path, e)
        return items, consumed

    @staticmethod
    def _read_new_lines_raw(file_path: Path, offset: int) -> list[str]:
        """Read raw JSONL lines (one per line) since *offset*.

        Used for quota-pattern matching — the raw line is fed into
        ``QuotaPatternMatcher`` so error-type entries are visible without
        having to parse them as assistant text.
        """
        lines: list[str] = []
        try:
            with open(file_path, encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    if line.strip():
                        lines.append(line)
        except OSError:
            pass
        return lines
