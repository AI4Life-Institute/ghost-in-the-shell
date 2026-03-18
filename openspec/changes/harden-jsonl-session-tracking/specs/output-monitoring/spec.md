## MODIFIED Requirements

### Requirement: JSONL File Polling
The system SHALL monitor Claude Code JSONL log files using byte-offset tracking and
mtime caching for incremental reads. Offsets SHALL be tracked independently per
`(channel_id, file_path)` pair so that multiple channels sharing the same session
file never steal each other's read position. Suspended bindings SHALL be excluded
from the poll loop entirely and SHALL NOT advance any offset.

#### Scenario: New JSONL event
- **WHEN** a new assistant text, tool_use, or tool_result event is appended to the
  JSONL file
- **THEN** the system SHALL parse the event and invoke the structured output callback
  for the owning channel

#### Scenario: File unchanged
- **WHEN** the JSONL file mtime has not changed since the last check
- **THEN** the system SHALL skip reading the file

#### Scenario: Two channels share one session file
- **WHEN** two active channels are both bound to the same `cli_session_id` and new
  lines are appended to the shared JSONL file
- **THEN** each channel SHALL receive all new messages independently without
  either channel's offset advancing the other's read position

#### Scenario: Suspended binding skipped
- **WHEN** a binding's `suspended` flag is `True`
- **THEN** the system SHALL skip that binding in the poll loop and SHALL NOT read
  or update its stored offset

## ADDED Requirements

### Requirement: JSONL Offset Persistence
The system SHALL persist per-channel JSONL byte offsets to
`~/.gits/jsonl_offsets.json` so that a ghost restart does not cause message replay
or loss.

#### Scenario: Offsets survive restart
- **WHEN** ghost is restarted after having tracked one or more JSONL files
- **THEN** polling SHALL resume from the last persisted byte offset for each
  `(channel_id, file_path)` pair

#### Scenario: New content after restart forwarded
- **WHEN** new lines are appended to a JSONL file after ghost restarts
- **THEN** only the lines written after the last persisted offset SHALL be forwarded
  to Discord; previously delivered lines SHALL NOT be replayed

#### Scenario: Dirty flag debounce
- **WHEN** offsets change during a poll cycle
- **THEN** the system SHALL write to disk at most once every 10 seconds (debounce)
  unless `stop()` is called, which SHALL trigger an immediate force-save

#### Scenario: Atomic write
- **WHEN** the system writes the offsets file
- **THEN** it SHALL write to a `.tmp` sibling first and then atomically rename it
  to prevent partial reads on crash

### Requirement: Background Session-Switch Guard
The system SHALL refuse to update a channel's `cli_session_id` to a new value
proposed by `session_map.json` if the current session's JSONL file still exists on
disk. This prevents background `claude -p` processes that inherit `TMUX_PANE` from
hijacking the channel's session tracking.

#### Scenario: Background job proposes new session, current file present
- **WHEN** `session_map.json` contains a new `session_id` for a window
- **AND** the channel already has a `cli_session_id` whose JSONL file exists on disk
- **THEN** the system SHALL keep the existing `cli_session_id`, log a skip at INFO
  level, and NOT update the binding

#### Scenario: Session genuinely replaced, file gone
- **WHEN** `session_map.json` contains a new `session_id` for a window
- **AND** the current session's JSONL file no longer exists on disk
- **THEN** the system SHALL update `cli_session_id` to the new value and begin
  tracking the new session file

#### Scenario: No prior session
- **WHEN** a channel has no `cli_session_id` yet and `session_map.json` has an entry
  for its window
- **THEN** the system SHALL accept the session_id unconditionally
