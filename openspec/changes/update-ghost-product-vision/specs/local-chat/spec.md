## ADDED Requirements

### Requirement: Slash Command Menu
The system SHALL provide a command palette in every chat input field, triggered by typing `/`,
that surfaces actions across all modes. This makes cross-mode capabilities discoverable from
the chat interface without requiring the user to navigate to a specific mode.

#### Scenario: User opens command palette
- **WHEN** the user types `/` in any chat input field
- **THEN** a floating menu appears above the input showing available commands:
  `/browse <goal>` — start a browser agent task
  `/skill <name>` — run a saved skill
  `/data <query>` — query saved data
  `/status` — show current agent activity summary

#### Scenario: User runs a browse command from chat
- **WHEN** the user selects `/browse` and enters a goal
- **THEN** a new browser task is created and the Task view becomes active
- **AND** a confirmation message appears in the chat: "Starting browser task: [goal]"

#### Scenario: Command palette filters on input
- **WHEN** the user types `/br`
- **THEN** the menu filters to show only commands starting with "br" (e.g. `/browse`)

### Requirement: Cross-Mode Notifications
The system SHALL notify the user when a significant event occurs in a background mode — such
as a task completing, a skill failing, or an agent requiring human input — so no work is lost
when the user is focused elsewhere.

#### Scenario: Browser task completes while user is in Code mode
- **WHEN** a browser agent task reaches "done" or "failed" status
- **AND** the user is currently viewing a different mode
- **THEN** a toast notification appears in the top-right corner with the task goal and outcome
- **AND** the toast includes a "View →" deep link to the Task detail panel
- **AND** the toast auto-dismisses after 4 seconds

#### Scenario: Task result linked into chat
- **WHEN** a browser task completes successfully
- **THEN** the chat pane of the active workspace receives an automated message:
  "Browser task done: [goal] — saved [filename] → [View in Data]"
- **AND** "[View in Data]" is a clickable link that navigates to the Data mode showing that artifact

#### Scenario: Agent requires human input while user is elsewhere
- **WHEN** a browser task pauses waiting for human-in-the-loop input
- **AND** the user is not in Task mode
- **THEN** the Task sidebar badge shows "⚠ 1" and a toast appears:
  "Agent waiting for your input: [task goal]"
- **AND** clicking the toast navigates directly to the HITL input in that task

### Requirement: Workspace Context in Chat
The system SHALL always display which workspace a chat pane belongs to, and the chat input
placeholder SHALL indicate the active AI provider, so the user is never confused about
which session they are talking to.

#### Scenario: Chat input shows workspace context
- **WHEN** the user views a Code pane chat input
- **THEN** the placeholder reads "Ask [workspace-name]…" (e.g. "Ask myproject…")
- **AND** the pane header shows workspace name, AI provider, and live status icon
