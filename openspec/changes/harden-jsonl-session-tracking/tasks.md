## 1. Per-Channel Offset Isolation

- [x] 1.1 Change `_offsets` and `_mtimes` dict key from `str(file_path)` to
      `(channel_id, str(file_path))` in `JsonlMonitor.__init__`
- [x] 1.2 Update `_check_binding` to compute `file_key = (binding.channel_id, str(jsonl_path))`
- [x] 1.3 Verify that two channels sharing the same session file each see the full content

## 2. Offset Persistence

- [x] 2.1 Add `_offsets_file`, `_offsets_dirty`, `_offsets_last_save`, `_SAVE_DEBOUNCE`
      attributes to `JsonlMonitor.__init__`; call `_load_offsets()` at end of `__init__`
- [x] 2.2 Implement `_load_offsets`: read `~/.gits/jsonl_offsets.json`; deserialise key
      format `"channel_id\x00file_path"` → `(channel_id, file_path)` tuple
- [x] 2.3 Implement `_save_offsets(force=False)`: skip if not dirty; debounce 10 s unless
      forced; atomic write via `.tmp` → `Path.replace()`; clear dirty flag
- [x] 2.4 Set `_offsets_dirty = True` whenever an offset or mtime is written in
      `_check_binding`; call `_save_offsets()` at end of each `_poll_loop` iteration
- [x] 2.5 Call `_save_offsets(force=True)` in `stop()`

## 3. File-Existence Session-Switch Guard

- [x] 3.1 In `_poll_once`, when a new `session_id` is proposed for a binding that already
      has a `cli_session_id`, call `_find_jsonl_file(binding)` with the current session
- [x] 3.2 If the file exists, log the skip at INFO level (channel, window, current session
      filename, proposed session id) and `continue` — do not update
- [x] 3.3 Remove the previously implemented time-based `_PROPOSED_SESSION_MAX_AGE_SECS`
      constant and the unused `_current_session_is_active` helper (replaced by 3.1–3.2)

## 4. Skip Suspended Bindings

- [x] 4.1 In `_poll_once` binding loop, add `if getattr(binding, "suspended", False): continue`
      before any offset read/write

## 5. Tests

- [x] 5.1 Unit test: two channels sharing the same JSONL file both receive all messages
      (no offset theft)
- [x] 5.2 Unit test: offsets saved to disk and reloaded across a simulated restart; new
      content after reload is forwarded without replaying history
- [x] 5.3 Unit test: session-switch guard — proposed new session rejected when current
      JSONL file exists; accepted when file is absent
- [x] 5.4 Unit test: suspended binding is skipped and its offset is not advanced

## 6. Validation

- [x] 6.1 `openspec validate harden-jsonl-session-tracking --strict`
- [x] 6.2 `uv run pytest tests/ -v` — all existing tests pass
