## ADDED Requirements

### Requirement: Dead Window Detection Before Resume
The system SHALL check whether the stored `window_id` still exists in tmux before sending any command to a bound window (during message forwarding or CLI resume). If the window no longer exists, the system SHALL recreate it and update the binding before proceeding.

#### Scenario: Window exists — no action needed
- **WHEN** a message is forwarded or the CLI is resumed
- **AND** `window_exists(binding.window_id)` returns `True`
- **THEN** the system proceeds normally without recreating the window

#### Scenario: Window is dead — recreate and resume
- **WHEN** a message is forwarded or the CLI is resumed
- **AND** `window_exists(binding.window_id)` returns `False`
- **THEN** the system creates a new tmux window in the existing gits tmux session (or a new session if the session is also gone)
- **AND** updates `binding.window_id` with the new window's ID via `session_mgr.update_window_id`
- **AND** resumes the CLI in the new window using the stored `cli_session_id`
- **AND** logs a warning that the window was recreated

#### Scenario: Session also gone — new session created
- **WHEN** the bound tmux session itself no longer exists
- **THEN** the system creates a new tmux session with the configured session name
- **AND** creates a window in that session and updates the binding accordingly
