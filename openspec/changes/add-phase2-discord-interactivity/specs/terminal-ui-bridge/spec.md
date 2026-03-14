## ADDED Requirements

### Requirement: Interactive Prompt Detection
The system SHALL detect Claude Code interactive prompts from captured pane text using regex pattern matching.

#### Scenario: Multi-choice prompt detected
- **WHEN** the pane contains lines matching `❯ N. option_text` pattern
- **THEN** the system SHALL extract the option list and tool context description

#### Scenario: Tool context extracted
- **WHEN** a prompt is preceded by a tool description block (e.g., "Bash command\n  tail -30 ...")
- **THEN** the system SHALL include the tool type and command/file info in the parsed result

### Requirement: Status Line Detection
The system SHALL detect the coding CLI's status from the terminal content.

#### Scenario: Busy state detected
- **WHEN** the pane contains "Thinking…", a spinner, or "⏺" activity indicator
- **THEN** the system SHALL report the CLI as busy

#### Scenario: Idle state detected
- **WHEN** the pane shows an "❯" input prompt with no spinner or activity
- **THEN** the system SHALL report the CLI as idle

#### Scenario: Waiting state detected
- **WHEN** the pane contains a multi-choice prompt with "Esc to cancel"
- **THEN** the system SHALL report the CLI as waiting for user input

### Requirement: Auto-Push Prompt to Discord
The system SHALL automatically push detected prompts to Discord as button messages.

#### Scenario: Prompt triggers button message
- **WHEN** an interactive prompt is detected during pane polling
- **THEN** the system SHALL send a Discord message with tool context + option buttons to the bound channel
