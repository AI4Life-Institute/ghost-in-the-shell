# discord-interactions Specification

## Purpose
TBD - created by archiving change add-phase2-discord-interactivity. Update Purpose after archive.
## Requirements
### Requirement: Prompt Option Buttons
The system SHALL display detected Claude Code prompt options as Discord buttons.

#### Scenario: Multi-choice prompt detected
- **WHEN** a multi-choice prompt is detected (e.g., "1. Yes  2. Yes, allow...  3. No")
- **THEN** the system SHALL send a Discord message with the tool context description and one button per option

#### Scenario: Button click selects option
- **WHEN** a user clicks an option button
- **THEN** the system SHALL send the corresponding number key to the tmux pane

### Requirement: Interrupt and Abort Buttons
The system SHALL provide quick-access interrupt controls.

#### Scenario: Interrupt button
- **WHEN** the user clicks the Interrupt button
- **THEN** the system SHALL send Escape to the tmux pane

#### Scenario: Abort button
- **WHEN** the user clicks the Abort button
- **THEN** the system SHALL send Ctrl-C to the tmux pane

