# output-monitoring Specification

## Purpose
TBD - created by archiving change add-phase2-discord-interactivity. Update Purpose after archive.
## Requirements
### Requirement: Pane Output Polling
The system SHALL periodically capture tmux pane content and detect new output lines by diffing against the previous capture.

#### Scenario: New output detected
- **WHEN** the coding CLI produces new output in the tmux pane
- **THEN** the system SHALL invoke the output callback with the new lines within the configured poll interval

#### Scenario: No change detected
- **WHEN** the pane content has not changed since the last capture
- **THEN** the system SHALL NOT invoke the output callback

### Requirement: JSONL File Polling
The system SHALL monitor Claude Code JSONL log files using byte-offset tracking and mtime caching for incremental reads.

#### Scenario: New JSONL event
- **WHEN** a new assistant.text, tool_use, or tool_result event is appended to the JSONL file
- **THEN** the system SHALL parse the event and invoke the structured output callback

#### Scenario: File unchanged
- **WHEN** the JSONL file mtime has not changed since the last check
- **THEN** the system SHALL skip reading the file

### Requirement: Monitor Lifecycle
The system SHALL start output monitors when a channel is bound and stop them when unbound or killed.

#### Scenario: Bind starts monitoring
- **WHEN** a channel is bound to a tmux window via `/bind`
- **THEN** the system SHALL start both pane polling and JSONL polling for that window

#### Scenario: Unbind stops monitoring
- **WHEN** a channel is unbound via `/unbind` or `/kill`
- **THEN** the system SHALL stop all monitors for that window

