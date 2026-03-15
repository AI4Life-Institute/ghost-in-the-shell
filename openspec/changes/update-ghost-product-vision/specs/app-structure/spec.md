## ADDED Requirements

### Requirement: Four-Mode Application Structure
The system SHALL organise its user interface into exactly four top-level modes, each accessible
from the sidebar. Switching modes changes the main content area while the titlebar (workspace
dropdown + agent status) remains constant. All four modes share the same active workspace context.

The four modes are:

| Mode | Icon | Purpose |
|------|------|---------|
| **Code** | 💻 | Multi-pane AI coding session dashboard. Run multiple Claude Code / Codex sessions simultaneously, each in its own pane. The hero view for daily work. |
| **Skill** | ⚡ | Saved automations. Define reusable scripts with parameters; run them on demand; see run history and AI-generated debug suggestions on failure. |
| **Task** | 🌐 | Browser agent. Give the AI a natural-language goal; it autonomously browses the web, fills forms, extracts data, and saves artifacts — using the user's real Chrome session. |
| **Data** | 🗄** | Structured outputs. View and explore data extracted or saved by Tasks and Skills. AI selects the best presentation format (table, cards, chart). No SQL knowledge required. |

#### Scenario: User navigates between modes
- **WHEN** the user clicks a mode button in the sidebar
- **THEN** the main area switches to that mode immediately
- **AND** the active workspace and titlebar state remain unchanged
- **AND** any background work in other modes continues uninterrupted

#### Scenario: All modes share the same workspace context
- **WHEN** the user switches the active workspace via the titlebar dropdown
- **THEN** all four modes update to reflect that workspace — Code shows its panes, Task shows its
  tasks, Data shows its saved data, Skill shows its run history for that workspace

### Requirement: Sidebar Navigation Structure
The system SHALL display a narrow sidebar (≤170px) containing only mode buttons and a footer
with user identity and settings access. The sidebar SHALL NOT contain workspace lists, session
lists, or any context-specific content — those belong in the main area of each mode.

```
Sidebar layout (top to bottom):
  ┌──────────────┐
  │ 💻  Code     │  ← active mode highlighted
  │ ⚡  Skill    │
  │ 🌐  Task  [2]│  ← badge when tasks running
  │ 🗄  Data     │
  │              │
  │ ─────────── │
  │ [av] Wei Liu │  ← avatar + name; click → Settings
  └──────────────┘
```

#### Scenario: Sidebar shows only navigation
- **WHEN** the user opens the app
- **THEN** the sidebar shows the four mode buttons plus the user footer
- **AND** there are no workspace items, session lists, or file trees in the sidebar

#### Scenario: Active mode is visually distinct
- **WHEN** the user is in a given mode
- **THEN** that mode button has a filled/highlighted background clearly distinguishing it
  from inactive modes — using background + border + weight, not color alone

### Requirement: Empty State — First Run
The system SHALL display a helpful empty state when the user has no workspaces configured,
guiding them to create their first workspace without requiring documentation.

#### Scenario: First launch with no workspaces
- **WHEN** the user opens the app for the first time with no workspaces configured
- **THEN** the main area shows a welcome empty state with:
  - A brief one-line description of what Ghost does
  - A single primary CTA: "＋ Open a project folder"
  - Below it, two secondary options: "Connect Discord bot" and "Try a browser task"
- **AND** the sidebar mode buttons are visible but clicking them shows the same empty state
  with mode-specific CTAs

#### Scenario: Code mode empty state
- **WHEN** the user is in Code mode with no panes open
- **THEN** the grid area shows: "No sessions open. Choose a folder to start coding with AI."
  with a "＋ Open folder" button in the center

#### Scenario: Task mode empty state
- **WHEN** the user is in Task mode with no tasks created
- **THEN** the task list shows: "No tasks yet. Describe a web goal and Ghost will handle it."
  with a "＋ New Task" button and a hint: "or type /browse in any chat"
