# Change: Harden JSONL Session Tracking

## Why

Three real-world failure modes caused missed Discord messages in the JSONL monitor:

1. **Offset theft between channels**: Two channels sharing the same Claude session file
   (e.g. an active channel and a suspended one) used `file_path` as the offset key.
   The suspended channel would advance the shared offset, causing the active channel
   to skip messages it had never seen.

2. **Offset loss on restart**: Byte offsets were stored only in memory. Any ghost
   restart (deploy, crash, PM2 restart) reset all offsets to 0 or EOF, causing
   either replayed history or missed new messages depending on the first-seen logic.

3. **Background process session hijack**: Processes like `ai4stock-orchestrator`
   inherit `TMUX_PANE` from their parent shell when started from a tmux window.
   When they spawn `claude -p "..."` one-shot jobs, those jobs trigger the gits hook,
   which overwrites `session_map.json` for that window. Ghost would then start
   tracking the short-lived background session instead of the user's interactive one,
   silently dropping all future messages from the real session.

4. **Hook-blind session drift** (known gap, not yet fixed): The gits hook fires only
   on `PostToolUse`. When the user sends a plain conversational message (e.g. "hi")
   and Claude responds with text only — no tool calls — the hook never fires.
   If Claude auto-resumed a different session than what ghost is tracking (e.g.
   because the most recent session in the project directory is not the one stored in
   state.json), the JSONL file being written to will never be discovered and all
   messages are silently lost. The file-existence guard (fix 3) makes this worse:
   the stale session file is still present on disk, so even when the hook eventually
   fires, the switch is blocked.

## What Changes

- **Per-channel offset isolation**: Change offset/mtime tracking key from
  `str(file_path)` to `(channel_id, file_path)` so each channel independently
  tracks its own read position regardless of file sharing.

- **Offset persistence**: Persist `(channel_id, file_path) → {offset, mtime}` to
  `~/.gits/jsonl_offsets.json` with atomic write (tmp → rename) and a 10-second
  debounce. Load on startup. Force-save on stop.

- **File-existence session-switch guard**: When `session_map.json` proposes a new
  session ID for a window, only accept it if the current session's JSONL file no
  longer exists on disk. If the file is still present, the session is merely idle
  (or the new entry was written by a background job); skip the update and log the
  reason.

- **Skip suspended bindings in poll loop**: Suspended channels must not consume
  JSONL offsets. Skip them entirely in `_poll_once` so their stored offset stays
  valid for when they are resumed.

## Known Gap (Future Work)

Failure mode 4 requires a separate approach: **tmux-process-based session
detection**. Instead of relying solely on the hook writing to `session_map.json`,
ghost should proactively detect the active session on each poll cycle by:

1. Reading the pane PID via `tmux display-message -p '#{pane_pid}'`
2. Walking `/proc/<pid>/task/<pid>/children` to find the foreground `claude`
   process (a direct descendant of the pane shell, not an inherited background job)
3. Reading `/proc/<claude_pid>/cwd` to get the working directory
4. Scanning `~/.claude/projects/<dir-hash>/` for the most recently modified JSONL
   file — that file IS the active session regardless of whether any hook fired

This eliminates the dependency on hook timing and correctly handles plain-text
conversations. It will be proposed in a follow-up change.

## Impact

- Affected specs: `output-monitoring` (MODIFIED: JSONL File Polling, Monitor
  Lifecycle; ADDED: Offset Persistence, Session-Switch Guard)
- Affected code: `src/gits/core/jsonl_monitor.py` — all changes are contained here
- No breaking changes to external interfaces
