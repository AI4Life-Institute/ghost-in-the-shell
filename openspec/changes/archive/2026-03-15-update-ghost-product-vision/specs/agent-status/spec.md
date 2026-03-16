## ADDED Requirements

### Requirement: Global Agent Activity Model
The system SHALL maintain a unified activity model across all modes — tracking which AI agents
(Code pane sessions, browser Tasks, Skill runs) are currently active — and expose this as a
shared data source for the titlebar indicator, sidebar badges, and toast notifications.

#### Scenario: Activity model updates on agent state change
- **WHEN** any AI session transitions to active, idle, or stopped state
- **THEN** the global activity count updates within 1 second
- **AND** all dependent UI elements (titlebar counter, sidebar badge, pane header) reflect the new state

#### Scenario: Activity pauses when app is backgrounded
- **WHEN** the app window is minimised or the user switches to another application
- **THEN** activity polling frequency drops to conserve CPU and battery
- **AND** resumes normal frequency when the app returns to focus

### Requirement: Per-Agent Status Detail
The system SHALL provide a per-agent status breakdown accessible from the titlebar indicator,
listing each active agent with its name, mode, workspace, and current action.

#### Scenario: User inspects active agents
- **WHEN** the user clicks the "⏳ N active" indicator in the titlebar
- **THEN** a popover lists each active agent:
  - Code sessions: workspace name + AI provider + last message preview
  - Browser tasks: task goal + current step (e.g. "Navigating to coingecko.com…")
  - Skill runs: skill name + elapsed time
- **AND** clicking any item navigates to that agent's detail view
