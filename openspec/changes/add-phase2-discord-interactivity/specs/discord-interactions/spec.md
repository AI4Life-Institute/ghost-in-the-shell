## ADDED Requirements

### Requirement: Screenshot Navigation Keyboard
The system SHALL display a button grid below screenshots for terminal navigation.

#### Scenario: Screenshot with buttons
- **WHEN** a screenshot is sent to Discord (manual or auto-triggered)
- **THEN** it SHALL include a button grid with: Esc, Up, Enter, Left, Down, Right, Ctrl-C, Space, Tab, and Refresh

#### Scenario: Refresh button
- **WHEN** the user clicks the Refresh button
- **THEN** the system SHALL re-capture the screenshot without sending any keys to tmux

### Requirement: Interrupt and Abort Buttons
The system SHALL provide quick-access interrupt controls on output messages.

#### Scenario: Interrupt button
- **WHEN** the user clicks the Interrupt button
- **THEN** the system SHALL send Escape to the tmux pane

#### Scenario: Abort button
- **WHEN** the user clicks the Abort button
- **THEN** the system SHALL send Ctrl-C to the tmux pane

### Requirement: Claude Code Hook Integration
The system SHALL automatically capture coding CLI session IDs via the hook mechanism and update bindings.

#### Scenario: Hook captures session ID
- **WHEN** Claude Code starts a new session in a tmux window managed by GITS
- **THEN** the `gits hook` command SHALL record the TMUX_PANE → session_id mapping

#### Scenario: Bot notified of new session
- **WHEN** a new session mapping is written to `session_map.json`
- **THEN** the running bot SHALL update the corresponding binding and notify the Discord channel with the session ID
