## MODIFIED Requirements

### Requirement: Pure CSS Frosted Glass UI
The system SHALL render its user interface with a frosted glass aesthetic — translucent panels,
soft blurs, and subtle depth — implemented entirely in pure CSS for future cross-platform portability.
No macOS-specific visual APIs are used.

All status indicators throughout the application SHALL communicate state using a combination of
icon, label text, AND color — never color alone. This ensures the UI remains usable for users
with color vision deficiency.

#### Scenario: App displays frosted glass styling
- **WHEN** the app window is open
- **THEN** panels, cards, and chat bubbles show a frosted glass look with blur, transparency, and soft edge highlights
- **AND** the visual style is consistent and elegant throughout the app

#### Scenario: Appearance follows system Light/Dark mode
- **WHEN** the user switches between Light and Dark mode in System Settings
- **THEN** the app automatically adapts its color palette

#### Scenario: Status indicators are accessible
- **WHEN** a workspace is active, idle, or stopped
- **THEN** its status is shown with an icon (▶ / ⏸ / ◼) and label text alongside the color dot
- **AND** a user with monochrome vision can still distinguish all states

#### Scenario: Task status badges are accessible
- **WHEN** a browser task has status Running, Done, Failed, or Queued
- **THEN** the badge shows an icon (⏳ / ✓ / ✗ / ○) alongside the colored label text

## ADDED Requirements

### Requirement: Workspace Dropdown in Titlebar
The system SHALL display a workspace selector as a compact dropdown button in the titlebar,
always visible regardless of which mode is active. Clicking it opens a popover listing all
workspaces with their status, path, and an option to add a new workspace.

#### Scenario: User switches workspace from any mode
- **WHEN** the user clicks the workspace dropdown in the titlebar
- **THEN** a popover appears listing all workspaces with status icon, name, and path
- **AND** clicking a workspace closes the popover and updates the active context for all modes

#### Scenario: User adds a new workspace
- **WHEN** the user clicks "＋ Add workspace" in the dropdown
- **THEN** a prompt or picker appears to select a project folder
- **AND** the new workspace appears in the dropdown and becomes active

### Requirement: Agent Status Bar
The system SHALL display a global agent activity indicator in the titlebar showing how many AI
agents are currently active across all modes. This gives the user an at-a-glance "mission control"
view without leaving their current mode.

#### Scenario: Agents are working
- **WHEN** one or more AI sessions are actively processing (Code panes responding, Tasks running,
  Skills executing)
- **THEN** the titlebar shows "⏳ N active" where N is the count of active agents
- **AND** clicking the indicator expands a summary popover listing each active agent by name and status

#### Scenario: No agents active
- **WHEN** all AI sessions are idle or stopped
- **THEN** the agent status indicator is hidden or shows nothing (zero-noise default)

### Requirement: Live Pane State in Code View
The system SHALL make AI agent activity visible within each Code view pane through animated
state indicators, so the user can see at a glance which sessions are working and which are idle.

#### Scenario: AI is responding in a pane
- **WHEN** an AI agent in a Code pane is actively generating a response
- **THEN** the pane header shows an animated pulse dot alongside the label "Active"
- **AND** the pane border has a subtle glow effect to draw attention

#### Scenario: Session is idle
- **WHEN** a Code pane's AI session is connected but not currently responding
- **THEN** the pane header shows "⏸ Idle" with a static indicator (no animation)

#### Scenario: Session is stopped
- **WHEN** a Code pane's tmux session has stopped
- **THEN** the pane header shows "◼ Stopped" and the pane content is dimmed

### Requirement: Pane Focus Mode
The system SHALL allow the user to expand any single Code pane to fill the entire Code view,
temporarily hiding other panes, for focused work on one session.

#### Scenario: User focuses a pane
- **WHEN** the user double-clicks a pane header
- **THEN** that pane expands to fill the full Code view area
- **AND** a "⤢ Exit focus" button appears in the pane header

#### Scenario: User exits focus mode
- **WHEN** the user presses Escape or clicks "⤢ Exit focus"
- **THEN** the grid layout is restored with all panes visible

### Requirement: Sidebar Activity Badges
The system SHALL display activity count badges on sidebar mode buttons when background work
is in progress, so the user knows something needs attention without leaving their current mode.

#### Scenario: Tasks are running while user is in Code mode
- **WHEN** one or more browser tasks are running
- **AND** the user is viewing the Code mode
- **THEN** the Task sidebar button shows a badge with the count of running tasks (e.g. "Task  [2]")

#### Scenario: Badge clears on visit
- **WHEN** the user navigates to the mode that had the badge
- **THEN** the badge is cleared

### Requirement: Advanced Mode — Developer Terminal
The system SHALL provide a "Dev Mode" toggle in the Code view that simultaneously switches all
panes from chat display to live terminal view. This is visible and accessible in the Code view
header, not hidden in settings.

#### Scenario: Power user enables Dev Mode
- **WHEN** the user clicks the ">_ Dev" button in the Code view tab bar
- **THEN** all panes switch to terminal view showing the tmux session output
- **AND** each pane shows a terminal prompt for direct command entry

#### Scenario: User returns to chat mode
- **WHEN** the user clicks ">_ Dev" again (toggle off)
- **THEN** all panes return to chat view
