## 1. Remove mtime/pane detection from JsonlMonitor

- [ ] 1.1 Delete `_detect_session_via_pane`, `_detect_session_from_pid`,
      `_find_claude_descendant`, `_get_process_start_time` from `jsonl_monitor.py`
- [ ] 1.2 Remove `mtime_uncertain`, `pane_resolved`, `other_channels_same_dir`
      logic from `_poll_once` Step 1
- [ ] 1.3 Remove Guard 1 and Guard 2 from Step 1
- [ ] 1.4 Remove the `tmux` constructor parameter and `self._tmux` attribute
- [ ] 1.5 Confirm Step 1 is fully deleted; `_poll_once` now starts directly at
      Step 2 (session_map lookup)

## 2. Remove file-existence guard from Step 2

- [ ] 2.1 Delete the `if binding.cli_session_id and channel_id not in mtime_uncertain`
      block that calls `_find_jsonl_file` and skips the update
- [ ] 2.2 Session_map entries are now accepted unconditionally when `new_sid !=
      binding.cli_session_id`

## 3. Add suspended-binding guard to Step 2

- [ ] 3.1 Add `if getattr(binding, "suspended", False): continue` at the top of
      the Step 2 binding loop, before any session_map lookup

## 4. Add missing-session warning

- [ ] 4.1 After updating `binding.cli_session_id` from session_map, call
      `_find_jsonl_file(binding)` with the new session ID
- [ ] 4.2 If the file is `None`, log `WARNING` and invoke `self._on_message` with
      a formatted alert:
      `⚠️ Session {sid} not found in {work_dir}. Possible --resume from wrong directory.`

## 5. Extend hook non-interactive filter to Codex

- [ ] 5.1 In `_cmd_hook` (`__main__.py`), extend the ancestor-walk check to also
      look for `comm == "codex"`
- [ ] 5.2 For a codex ancestor, check cmdline for `-q` or `--quiet` flags and
      return early if found

## 6. Write tests (27 cases)

- [ ] 6.1  A1 — fresh binding picks up session from session_map
- [ ] 6.2  A2 — session switch accepted when old file still exists
- [ ] 6.3  A3 — session switch accepted when old file gone (baseline)
- [ ] 6.4  A4 — pane_file strategy overrides session_map (keep pane_file path only)
- [ ] 6.5  A5 — pane_resolved binding skipped in Step 2
- [ ] 6.6  B1 — two windows, same project dir, each has pane_file → correct sessions
- [ ] 6.7  B2 — mtime steal blocked (retained Guard via session_map Step 2 alone)
- [ ] 6.8  C1 — session file not found after assignment → WARNING + Discord message
- [ ] 6.9  D1 — suspended binding: session_map does not update session
- [ ] 6.10 D2 — suspended binding: offset not advanced
- [ ] 6.11 E1 — first-seen file: skip to end
- [ ] 6.12 E2 — new Claude content: callback fires
- [ ] 6.13 E3 — file truncated: offset reset
- [ ] 6.14 E4 — two channels share JSONL: independent offsets
- [ ] 6.15 E5 — long message split into ≤1900-char chunks
- [ ] 6.16 F1 — restart: offsets loaded, no history replay
- [ ] 6.17 G1 — hook: claude -p ancestor detected → session_map not updated
- [ ] 6.18 G2 — hook: codex -q ancestor detected → session_map not updated
- [ ] 6.19 G3 — pane detection: claude -p process skipped in process tree
- [ ] 6.20 G4 — pane detection: claude --print process skipped
- [ ] 6.21 H1 — Codex response_item format: content parsed correctly
- [ ] 6.22 H2 — Codex file found by exact session_id
- [ ] 6.23 H3 — Codex session_id changed, cwd matches → fallback to latest file
- [ ] 6.24 H4 — Codex binding: pane detection skipped
- [ ] 6.25 I1 — OpenCode first poll: snapshot only, no replay
- [ ] 6.26 I2 — OpenCode new DB content: callback fires
- [ ] 6.27 I3 — OpenCode DB missing: skip gracefully

## 7. Remove old mtime tests and update existing tests

- [ ] 7.1 Delete `TestMtimeCrossWindowContamination` from `test_jsonl_monitor.py`
- [ ] 7.2 Delete `TestFindClaudeDescendantSkipsPrint` (covered by G3/G4 in new file)
- [ ] 7.3 Delete `tests/test_session_detection.py` (interim file; merge into
      `test_jsonl_monitor.py` under new class names)
- [ ] 7.4 Update remaining tests that pass `tmux=` to monitor constructor

## 8. Validation

- [ ] 8.1 `openspec validate refactor-output-monitor --strict`
- [ ] 8.2 `uv run pytest tests/ -v` — all tests pass
