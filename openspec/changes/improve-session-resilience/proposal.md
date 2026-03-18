# Change: Improve Session Resilience and Info/Bind Alignment

## Why

Two related gaps in session management:

1. **Dead tmux window**: When a tmux session/window is killed (server restart, manual `tmux kill-session`, machine reboot), the bot's stored `window_id` (e.g. `@1`) becomes stale. Currently `_resume_suspended` blindly sends commands to the dead window, causing silent failures. The window must be recreated before the CLI can be resumed.

2. **Info/Bind mismatch**: `/info` shows a raw `cli_session_id` UUID. The `/bind` dropdown shows each session by its human-readable **summary** (e.g. "Add dark mode toggle"). Users cannot match what they see in `/info` with the item they need to pick in the `/bind` dropdown.

## What Changes

- **tmux-window-recovery**: Before resuming or forwarding any message to a bound window, detect if `window_id` is dead; if so, open a new tmux window in the same session (or a new session), update the stored `window_id`, and then resume the CLI as normal.
- **info-bind-alignment**: `/info` adds a **Session summary** field (the human-readable first line returned by the session scanner — the same text shown as the dropdown label in `/bind`), so users can identify their session in the bind picker.

## Impact

- Affected specs: `tmux-window-recovery` (new), `info-bind-alignment` (new)
- Affected code:
  - `src/gits/core/engine.py` — `_resume_suspended`, `handle_message`, `handle_status`
  - `src/gits/core/session.py` — `SessionBinding` (add `session_summary` field)
  - `src/gits/core/launcher.py` — `CLISession.summary` already exists; reuse it
