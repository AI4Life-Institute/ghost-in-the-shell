## 1. Dead Window Recovery

- [x] 1.1 Add helper `_ensure_window_alive(binding)` in `engine.py` that calls `tmux.window_exists`, and if dead: creates a new window via `tmux.new_window`, calls `session_mgr.update_window_id`, logs a warning, and returns `True` (recreated) / `False` (was alive)
- [x] 1.2 Call `_ensure_window_alive` at the top of `handle_message` (before the suspend/shell-check logic), and also at the top of `_resume_suspended`
- [x] 1.3 Handle the edge case where the tmux *session* itself is also gone: if `new_window` raises, fall back to creating a new session first (reuse the session-name from settings), then open the window
- [x] 1.4 Write unit tests for `_ensure_window_alive`: window alive (no-op), window dead session alive (recreate window), both dead (recreate session + window)

## 2. Info/Bind Alignment

- [x] 2.1 In `handle_status` (`engine.py`), after resolving `binding.cli_session_id`, call `launcher.list_sessions(work_dir, cli)` and find the matching `CLISession` by `session_id`
- [x] 2.2 If found, insert `Session summary: "{session.summary}"` into the info output immediately after the `Session ID:` line
- [x] 2.3 Write a unit test confirming the summary appears in `/info` output when a matching session exists, and is absent when no session matches

## 3. Validation

- [x] 3.1 Run `openspec validate improve-session-resilience --strict` and fix any issues
- [x] 3.2 Run `pytest` and ensure all existing tests pass
