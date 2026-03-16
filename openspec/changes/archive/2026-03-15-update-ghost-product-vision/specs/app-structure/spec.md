## ADDED Requirements

### Requirement: Four-Mode Application Structure
The system SHALL organise its interface into exactly four top-level modes. All modes operate
within the same project directory (the mono-repo root). There is no "workspace" concept —
the user opens a directory, and everything lives there.

| Mode | Icon | One-line purpose |
|------|------|-----------------|
| **Build** | 🤖 | Talk to the Building Agent — the AI that writes code, creates Skills, and deploys Agents into your directory |
| **Agents** | ⚡ | Your deployed agent fleet — every Agent currently running or paused, organised by type |
| **Skills** | 🛠 | Reusable tools the Building Agent has created; callable by any Agent or by the user directly |
| **Data** | 🗄 | Structured outputs produced by Agents and Skills; the shared memory of the fleet |

"Task" is not a mode or a navigation concept. A task is what an Agent *does* — an internal
implementation detail, not something the user manages directly. The user manages Agents.

#### Scenario: User navigates between modes
- **WHEN** the user clicks a mode button in the sidebar
- **THEN** the main area switches to that mode immediately
- **AND** all Agents continue running uninterrupted in the background

#### Scenario: All modes share the same project directory
- **WHEN** the user is in any mode
- **THEN** the titlebar shows the current project directory path (e.g. `~/myproject`)
- **AND** Build, Agents, Skills, and Data all reference files and outputs within that directory

### Requirement: Project Directory as the Root Context
The system SHALL use a single project directory as the root context for all operations.
The directory is equivalent to a mono-repo: all agent code, skill scripts, and data outputs
live under it. There is no multi-workspace concept in v1.

Opening a different project means opening a different directory — like VS Code's "Open Folder."
Recent directories are accessible from the titlebar path button.

#### Scenario: User opens a project directory
- **WHEN** the user clicks the directory path in the titlebar or chooses "Open Folder"
- **THEN** a native folder picker appears
- **AND** selecting a directory sets it as the active project context for all four modes
- **AND** the titlebar updates to show the new path

#### Scenario: User switches to a recent project
- **WHEN** the user clicks the directory path in the titlebar
- **THEN** a dropdown shows recent directories with their last-active timestamp
- **AND** clicking one switches the project immediately

### Requirement: Sidebar Navigation Structure
The system SHALL display a narrow sidebar (≤170px) containing only the four mode buttons
and a footer. Mode buttons show activity badges when background work is in progress.

```
Sidebar layout:
  ┌──────────────┐
  │ 🤖  Build    │  ← active mode highlighted
  │ ⚡  Agents[3]│  ← badge: 3 agents running
  │ 🛠  Skills   │
  │ 🗄  Data     │
  │              │
  │ ──────────── │
  │ [av] Wei Liu │  ← click → Settings
  └──────────────┘
```

#### Scenario: Sidebar shows activity badges
- **WHEN** one or more Agents are running
- **THEN** the Agents mode button shows a count badge (e.g. `[3]`)
- **AND** if any Agent is waiting for human input, the badge shows `⚠` instead

#### Scenario: Active mode is visually distinct
- **WHEN** the user is in a given mode
- **THEN** that mode button has a filled background, distinct border, and heavier font weight
- **AND** the distinction does not rely on color alone

### Requirement: Empty State — First Run
The system SHALL display a useful empty state when no project directory is open, guiding
the user to their first action without requiring documentation.

#### Scenario: No project open
- **WHEN** the user opens Ghost for the first time with no project configured
- **THEN** the main area shows:
  - Headline: "Open a project folder to get started"
  - Primary CTA: "＋ Open Folder"
  - Secondary hint: "Ghost works inside your project directory — all AI-built code, agents, and data live there"

#### Scenario: Build mode empty (project open, no conversation yet)
- **WHEN** the user opens a project but has not started a Build conversation
- **THEN** the Build view shows a centered input: "What do you want to build?"
  with example hints: "Write a web scraper", "Build a price monitor", "Automate my Notion workflow"

#### Scenario: Agents mode empty
- **WHEN** no Agents have been deployed yet
- **THEN** the Agents view shows: "No agents running yet. Ask the Build agent to create one."
  with a link that opens Build mode
