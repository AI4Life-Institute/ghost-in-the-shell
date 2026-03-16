# skills-desktop-ui Specification

## Purpose
The Ghost desktop app surfaces agents, skills, tools, and data from all open project folders. User assets (`agents/`, `skills/`, `tools/`, `data/`) are visible. Ghost internals (`.ghost/`) are hidden.

## Project Layout Convention

```
<project>/
  agents/<name>.md              USER ASSET — Agent definitions
  skills/<name>/skill.md        USER ASSET — Skill definitions (+ optional support files)
  tools/<name>/tool.md          USER ASSET — Tool definitions (+ optional impl code)
  data/ghost.db                 USER ASSET — Run metadata
  data/<name>.db|csv|...        USER ASSET — Agent output data

  .ghost/                       Ghost-internal — hidden
    config.yaml                 Per-project config overrides (optional)
    logs/<agent>/<run-id>.log   Execution logs
    logs/<agent>/current.log    Symlink → latest run

~/.config/ghost/
  config.yaml                   Global Ghost config
  tools/<name>/tool.md          Globally shared tools (fallback for all projects)

~/.gits/workspace.json          Persisted list of open project folder paths
```

**Global `~/.config/ghost/config.yaml` fields:**

| Field | Default | Description |
|---|---|---|
| `guard.ops_session` | `ghost-ops` | tmux session used as Guard |
| `guard.timeout_minutes` | `60` | Max wait for Guard decision |
| `logs.max_files` | `30` | Max log files per agent |
| `logs.max_age_days` | `7` | Delete logs older than N days |
| `runner.default_shell` | `zsh` | Shell for environment inheritance |
| `runner.default_on_failure` | `stop` | Fallback if agent has no `on_failure` |

## ADDED Requirements

### Requirement: Workspace is a collection of project folders
The app SHALL maintain a workspace — a set of one or more project folders — persisted across restarts.

#### Scenario: Add folder to workspace
- **WHEN** the user selects a directory via the 📁 folder picker
- **THEN** the frontend SHALL send `workspace_add {work_dir}`
- **AND** the backend SHALL start an `AgentRunner` and `GitsDB` for that folder
- **AND** emit `workspace_changed`, `agents_list`, `skills_list`, `tools_list`

#### Scenario: Workspace restored on restart
- **WHEN** the desktop app starts
- **THEN** the backend SHALL read `~/.gits/workspace.json` and resume each saved folder
- **AND** emit `workspace_changed` with the restored folder list

#### Scenario: Remove folder from workspace
- **WHEN** the user clicks ✕ on a folder chip
- **THEN** the frontend SHALL send `workspace_remove {work_dir}`
- **AND** all UI entries for that folder SHALL be removed within 1 second

---

### Requirement: Agents panel driven by real agent definitions
The Agents panel SHALL display real `agents/*.md` data, not hardcoded mock data.

#### Scenario: Agent cards rendered from agents_list
- **WHEN** `agents_list` is received
- **THEN** each agent SHALL appear as a card showing: name, trigger schedule (human-readable), next run time, last run status, project folder badge
- **AND** no hardcoded mock agents SHALL appear

#### Scenario: Agent card schedule line — Loop
- **WHEN** an agent has `trigger.type: loop` with a cron schedule
- **THEN** the card SHALL show the human-readable form (e.g. "Hourly · next 14:00") from `next_run_at`

#### Scenario: Agent card — Reactive
- **WHEN** an agent has `trigger.type: reactive` with `always_on: true`
- **THEN** the card SHALL show "● Always on"

#### Scenario: Agent drawer shows execution trace
- **WHEN** a user clicks an agent card
- **THEN** the drawer SHALL show: the skill list, each skill's steps with resolved tool name + command, and live log from `.ghost/logs/<agent>/current.log`

---

### Requirement: Skills panel driven by real skill definitions
The Skills panel left list SHALL show `skills/<name>/skill.md` data per project folder.

#### Scenario: Skills grouped by project
- **WHEN** `skills_list` is received with skills from multiple folders
- **THEN** the left list SHALL show a folder section header per project
- **AND** each skill appears under its folder section with name and step count

#### Scenario: Skill detail shows steps with tool info
- **WHEN** a skill is selected and `tools_list` is available
- **THEN** each step row SHALL show the tool name, its command, and its working directory
- **WHEN** a step's tool is not found
- **THEN** the step SHALL show the name with "(not found)" — no crash

#### Scenario: Skill detail shows run history
- **WHEN** a skill's agents have prior runs in `data/ghost.db`
- **THEN** the detail panel SHALL show the last 10 runs: status dot, start time, duration, "View Log" link

---

### Requirement: Tools list available to UI
The frontend SHALL maintain a `toolDefs` map from `tools_list` for use in step displays.

#### Scenario: Global tools visible in any project
- **WHEN** `tools_list` is received
- **THEN** it SHALL include tools from `~/.config/ghost/tools/` with `scope: global`
- **AND** project-local tools with `scope: local` and their `work_dir`

#### Scenario: Local tool overrides global
- **WHEN** a project has `tools/discord-notify/tool.md` and a global tool with the same name exists
- **THEN** `tools_list` SHALL include only the local version for that project

---

### Requirement: New Agent modal creates a real agent file
The "＋ New Agent" modal SHALL write `<project>/agents/<slug>.md` via `agent_create` IPC.

#### Scenario: Successful creation
- **WHEN** the user fills Name, Trigger, Schedule (if Loop), Skills/Steps, and selects a folder
- **THEN** the backend SHALL write the agent file in canonical frontmatter + markdown format
- **AND** AgentRunner SHALL reload and schedule the new agent
- **AND** the agent SHALL appear in the Agents panel within 2 seconds

#### Scenario: Duplicate agent name
- **WHEN** `<project>/agents/<slug>.md` already exists
- **THEN** the backend SHALL emit `{event: "error", msg: "Agent '<name>' already exists"}`
- **AND** the modal SHALL show the error inline without closing

---

### Requirement: Runner cards show folder badge and schedule
Each Agent card SHALL identify its project and display scheduling information.

#### Scenario: Folder badge
- **WHEN** an agent card is rendered for project `/path/to/news-briefing`
- **THEN** the card SHALL show a folder badge with the directory basename ("news-briefing")

#### Scenario: Paused agent
- **WHEN** an agent is paused
- **THEN** the card sub-line SHALL show "⏸ Paused" and `next_run_at` SHALL be null

## MODIFIED Requirements

### Requirement: Agent logs written inside project folder
Agent run logs SHALL be written to `<project>/.ghost/logs/` — NOT to `~/.gits/agents/`.

#### Scenario: Log path on run start
- **WHEN** agent `news-collector` starts in project `/path/to/news-briefing`
- **THEN** the log SHALL be at `/path/to/news-briefing/.ghost/logs/news-collector/<run-id>.log`

#### Scenario: Run metadata in project DB
- **WHEN** a run completes
- **THEN** metadata SHALL be stored in `<project>/data/ghost.db`, not in `~/.gits/gits.db`

### Requirement: agents_list includes work_dir and next_run_at
Each agent object in `agents_list` SHALL carry its source folder and next scheduled run.

#### Scenario: Multi-folder workspace
- **WHEN** two folders are open and both have agents
- **THEN** `agents_list` SHALL include all agents each with a `work_dir` field

#### Scenario: next_run_at for active Loop agent
- **WHEN** a Loop agent is scheduled and not paused
- **THEN** `next_run_at` SHALL be an ISO 8601 string of the next trigger time

#### Scenario: next_run_at absent
- **WHEN** an agent is paused or is Reactive type
- **THEN** `next_run_at` SHALL be `null`
