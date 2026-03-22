## MODIFIED Requirements

### Requirement: JSONL File Polling
The system SHALL monitor coding-CLI output files using byte-offset tracking and
mtime caching for incremental reads, supporting Claude (JSONL), Codex (JSONL),
Copilot (JSONL), and OpenCode (SQLite) formats.

#### Scenario: New JSONL content — Claude format
- **WHEN** a new `assistant` entry is appended to a Claude JSONL file
- **THEN** the system SHALL parse `message.content` blocks and invoke the output
  callback for each `text` and `tool_use` item

#### Scenario: New JSONL content — Codex format
- **WHEN** a new `response_item` entry with `payload.role == "assistant"` is
  appended to a Codex JSONL file
- **THEN** the system SHALL extract `output_text` blocks and invoke the callback

#### Scenario: New database content — OpenCode format
- **WHEN** a new assistant text part with `time_updated > last_seen_timestamp`
  exists in the OpenCode SQLite database
- **THEN** the system SHALL query and invoke the callback for each new text part

#### Scenario: File unchanged
- **WHEN** the JSONL file mtime and size have not changed since the last check
- **THEN** the system SHALL skip reading the file

#### Scenario: First-seen file
- **WHEN** a JSONL file is observed for the first time
- **THEN** the system SHALL record the current end-of-file position and NOT replay
  existing content

#### Scenario: File truncated
- **WHEN** the JSONL file size is smaller than the last recorded offset
- **THEN** the system SHALL reset the offset to zero and read from the beginning

#### Scenario: Two channels share one JSONL file
- **WHEN** two Discord channels are both bound to the same CLI session
- **THEN** each channel SHALL maintain an independent byte offset so neither
  channel's reads advance the other's position

#### Scenario: Long message split
- **WHEN** a single text block exceeds 1900 characters
- **THEN** the system SHALL split it into chunks of at most 1900 characters and
  invoke the callback once per chunk

#### Scenario: Callback error
- **WHEN** the output callback raises an exception
- **THEN** the system SHALL log the error and continue without crashing

#### Scenario: Suspended binding skipped
- **WHEN** a binding's `suspended` flag is `True`
- **THEN** the system SHALL skip both session detection and file reading for that
  binding, leaving all stored offsets unchanged


## ADDED Requirements

### Requirement: Session Assignment
The system SHALL assign and update CLI session IDs for each binding using
`session_map.json` as the authoritative source, updated by the gits hook on each
`SessionStart` event.

#### Scenario: Fresh binding picks up session
- **WHEN** a binding has no `cli_session_id` and `session_map.json` contains an
  entry matching the binding's `window_id`
- **THEN** the system SHALL assign that session ID to the binding and begin
  monitoring the corresponding output file

#### Scenario: Session switch followed
- **WHEN** the user starts a new CLI session in the bound window, the hook fires
  and updates `session_map.json` with a new session ID
- **THEN** the system SHALL update the binding's session ID on the next poll cycle,
  regardless of whether the previous session's output file still exists on disk

#### Scenario: Same session — no-op
- **WHEN** `session_map.json` contains the same session ID already assigned to the
  binding
- **THEN** the system SHALL NOT call `update_cli_session_id` again

#### Scenario: Suspended binding — session not updated
- **WHEN** a binding's `suspended` flag is `True` and `session_map.json` has a new
  session ID for that window
- **THEN** the system SHALL NOT update the binding's session ID

#### Scenario: No session_map entry
- **WHEN** `session_map.json` has no entry for the binding's window
- **THEN** the system SHALL leave the binding's session ID unchanged

### Requirement: Missing-Session Warning
The system SHALL detect when a session ID from `session_map.json` cannot be
resolved to an output file and notify the user.

#### Scenario: Session file not found after assignment
- **WHEN** the session ID has just been assigned from `session_map.json` and the
  corresponding output file does not exist in the expected project directory
- **THEN** the system SHALL log a WARNING and send a Discord message to the bound
  channel explaining that the session may have been created in a different project
  directory (e.g., via `claude --resume` pointing at a session from another dir)

#### Scenario: Session file found — no warning
- **WHEN** the session ID resolves to an existing output file
- **THEN** the system SHALL NOT emit any warning

### Requirement: Non-Interactive CLI Filter
The gits hook SHALL skip writing to `session_map.json` when invoked from a
non-interactive (one-shot) CLI process, for all supported coding CLIs.

#### Scenario: claude -p filtered
- **WHEN** the hook is triggered by a `claude -p` or `claude --print` invocation
- **THEN** the hook SHALL detect the `-p`/`--print` flag in the claude ancestor
  process and exit without updating `session_map.json`

#### Scenario: codex -q filtered
- **WHEN** the hook is triggered by a `codex -q` or `codex --quiet` invocation
- **THEN** the hook SHALL detect the `-q`/`--quiet` flag in the codex ancestor
  process and exit without updating `session_map.json`

#### Scenario: Interactive session not filtered
- **WHEN** the hook is triggered by an interactive `claude` or `codex` session
  (no `-p`/`--print`/`-q`/`--quiet` flags)
- **THEN** the hook SHALL update `session_map.json` normally

### Requirement: Offset Persistence
The system SHALL persist byte offsets and mtimes to disk so that a monitor restart
does not replay already-forwarded content.

#### Scenario: Offsets saved on stop
- **WHEN** the monitor is stopped
- **THEN** the system SHALL force-write all current offsets to
  `~/.gits/jsonl_offsets.json` atomically

#### Scenario: Offsets loaded on start
- **WHEN** the monitor starts and `~/.gits/jsonl_offsets.json` exists
- **THEN** the system SHALL load the persisted offsets so subsequent polls resume
  from the last known position without replaying history
