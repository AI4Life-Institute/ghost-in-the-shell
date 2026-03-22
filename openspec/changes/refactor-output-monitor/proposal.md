# Change: Refactor Output Monitor — Simplify Session Tracking

## Why

The current `JsonlMonitor` accumulated four layers of complexity to handle session
detection edge cases, making the code hard to reason about and the tests fragile:

1. **File-existence guard** — originally added to prevent background `claude -p`
   processes from hijacking `session_map.json`.  The root cause is now fixed at the
   hook level (ancestor-walk `-p` filter), so the guard only blocks legitimate user-
   initiated session switches and is no longer needed.

2. **mtime-based session detection** (`_detect_session_via_pane`) — introduced to
   handle plain-text conversations where the hook never fires.  The three-strategy
   approach (pane_file → open_fd → mtime) adds `/proc` process-tree traversal on
   every poll cycle and caused cross-window session contamination when multiple
   windows share the same project directory (the @28 and @33 failures).

3. **mtime_uncertain / other_channels_same_dir guards** — patches added on top of
   mtime to compensate for cross-window contamination, resulting in complex
   conditional logic that was difficult to test correctly.

4. **Suspended bindings not protected in Step 2** — the suspended-binding skip only
   existed in Step 1 (pane detection); Step 2 (session_map fallback) still updated
   suspended bindings, which is a bug.

5. **Missing coverage for Codex one-shot mode** — the `-p` filter in the hook is
   Claude-specific (`comm == "claude"`).  Codex has an equivalent non-interactive
   mode (`codex -q`) that is not filtered, leaving the same hijack vector open for
   Codex bindings.

6. **No warning for `--resume` cross-directory fallback** — when a user runs
   `claude --resume X` in a directory that does not contain session X, Claude creates
   a new session Y.  The hook reports X (the requested session) but Claude writes to
   Y.  Ghost silently tracks the wrong file with no diagnostic.

## What Changes

- **REMOVED: file-existence guard** — session_map entries are now always trusted.
  The hook is the correct place to prevent bad writes, not the reader.

- **REMOVED: mtime / pane-process detection** — delete `_detect_session_via_pane`,
  `_detect_session_from_pid`, `_find_claude_descendant`, `_get_process_start_time`,
  `mtime_uncertain`, `pane_resolved`, Guard 1, Guard 2, and all related logic.
  `pane_sessions/` files written by the hook are also no longer needed.

- **ADDED: suspended-binding guard in Step 2** — session_map fallback must skip
  suspended bindings, matching the existing Step 1 behaviour.

- **ADDED: missing-session warning** — when session_map assigns session X to a
  binding but `X.jsonl` cannot be found after the update, ghost SHALL log a WARNING
  and send a Discord notification explaining the likely cause (`--resume` pointed at
  a session from a different project directory).

- **ADDED: Codex one-shot filter in hook** — extend the non-interactive detection in
  `_cmd_hook` to also skip Codex `codex -q` / `codex --quiet` invocations by
  checking `comm == "codex"` in addition to `comm == "claude"`.

- **ADDED: comprehensive test suite** — 27 test cases covering all scenarios across
  Claude, Codex, and OpenCode, replacing the current fragile mtime-based tests.

## Impact

- Affected specs: `output-monitoring`
  - MODIFIED: JSONL File Polling (session assignment simplified)
  - ADDED: Session Assignment, Non-Interactive Filter, Missing-Session Warning,
    Multi-CLI Content Parsing
- Affected code:
  - `src/gits/core/jsonl_monitor.py` — primary simplification
  - `src/gits/__main__.py` — Codex one-shot filter
  - `tests/test_jsonl_monitor.py` — full rewrite
  - `tests/test_session_detection.py` — new file (replaces mtime tests)
- No breaking changes to external interfaces
- `pane_sessions/` directory can be left on disk; it will simply stop being written
