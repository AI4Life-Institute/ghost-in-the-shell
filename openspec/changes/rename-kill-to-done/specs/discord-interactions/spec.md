# discord-interactions Spec Delta

## REMOVED Requirements

### Requirement: /kill Command
The `/kill` slash command is removed and replaced by `/done`.

## ADDED Requirements

### Requirement: /done Command
The system SHALL provide a `/done` slash command that ends the current work session
and permanently closes the Discord thread.

#### Scenario: Done closes session and thread
- **GIVEN** a channel/thread has an active session binding
- **WHEN** the user runs `/done`
- **THEN** the system SHALL reply to the interaction with a status message
- **AND THEN** archive and lock the Discord thread so it disappears from the sidebar
- **AND THEN** kill the tmux window and remove the session binding
- **AND** if the session has child threads (from `/fork`), close each child first

#### Scenario: Done on worktree with dirty changes
- **GIVEN** the session uses a git worktree with uncommitted changes
- **WHEN** the user runs `/done`
- **THEN** the system SHALL send a confirmation prompt with "Yes, delete worktree" /
  "No, keep worktree" buttons before proceeding
- **AND** only proceed with close after the user confirms

#### Scenario: Done with no active session
- **GIVEN** the channel has no active session binding
- **WHEN** the user runs `/done`
- **THEN** the system SHALL reply "Not bound." (ephemeral)

## MODIFIED Requirements

### Requirement: Thread Archive Locks Thread
The system SHALL set both `archived=True` and `locked=True` when archiving a thread
so that the thread permanently disappears from the Discord sidebar and cannot be
re-opened by subsequent messages.

#### Scenario: Thread closed via /done stays closed
- **GIVEN** `/done` has been executed on a thread
- **WHEN** any message is sent to the thread (e.g., a bot followup or a user message)
- **THEN** the message SHALL fail or be ignored — the thread SHALL NOT reappear in
  the sidebar

#### Scenario: Archive ordering — reply before close
- **GIVEN** `/done` is executed
- **WHEN** the system processes the command
- **THEN** the reply to the interaction SHALL be sent before `archive_thread` is
  called, so the followup does not attempt to post to an already-locked thread
