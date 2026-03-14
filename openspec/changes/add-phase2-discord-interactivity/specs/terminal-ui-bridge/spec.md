## ADDED Requirements

### Requirement: Interactive Prompt Detection
The system SHALL detect Claude Code interactive prompts from captured pane text using regex pattern matching.

#### Scenario: Permission prompt detected
- **WHEN** the pane contains a PermissionPrompt (Allow/Deny pattern)
- **THEN** the system SHALL identify it as a permission prompt with available actions

#### Scenario: User question detected
- **WHEN** the pane contains an AskUserQuestion (multi-choice pattern)
- **THEN** the system SHALL identify it as a user question with available options

#### Scenario: Bash approval detected
- **WHEN** the pane contains a BashApproval prompt
- **THEN** the system SHALL identify it as a bash approval request

#### Scenario: Plan mode exit detected
- **WHEN** the pane contains an ExitPlanMode prompt
- **THEN** the system SHALL identify it as a plan mode exit confirmation

### Requirement: Status Line Detection
The system SHALL detect the coding CLI's status from the terminal status line (idle, busy, waiting for input).

#### Scenario: Busy state detected
- **WHEN** the status line shows a spinner animation or working indicator
- **THEN** the system SHALL report the CLI as busy

#### Scenario: Idle state detected
- **WHEN** the status line shows an input prompt with no spinner
- **THEN** the system SHALL report the CLI as idle

### Requirement: Auto-Screenshot on Prompt
The system SHALL automatically capture and push a screenshot with navigation buttons when an interactive prompt is detected.

#### Scenario: Prompt triggers screenshot
- **WHEN** an interactive prompt is detected during pane polling
- **THEN** the system SHALL capture a screenshot and send it to the bound Discord channel with a navigation button grid

### Requirement: Button Click Handling
The system SHALL translate Discord button clicks into tmux key sequences and refresh the screenshot.

#### Scenario: Navigation button pressed
- **WHEN** a user clicks a navigation button (arrow, Enter, Esc, etc.)
- **THEN** the system SHALL send the corresponding key to the tmux pane and re-capture the screenshot after 500ms
