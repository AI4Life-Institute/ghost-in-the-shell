# skills-desktop-ui Specification

## Purpose
The Ghost desktop app manages a **workspace** — one or more project folders open simultaneously (VS Code-style multi-root). Each folder owns its skills, logs, and run database under a hidden `.ghost/` subdirectory. Global tools (installed once) are available in all folders. The UI surfaces skills and agents from all open folders, grouped by project.

## Storage Convention

User assets (skills, data) are **visible** in the project folder. Ghost's internal runtime files are **hidden** in `.ghost/`.

```
~/.config/ghost/
  config.yaml                          ← Ghost global config
  tools/<name>.md                      ← globally installed tools

~/.gits/workspace.json                 ← persisted list of open folder paths

<folder>/
  skills/                              ← USER ASSET — visible; skill definitions the user owns
    <name>.md
  data/                                ← USER ASSET — visible; databases + output files
    ghost.db

  .ghost/                              ← Ghost-internal — hidden; not user-facing
    config.yaml                        ← per-project config overrides (optional)
    logs/<skill>/<run-id>.log          ← execution logs
    logs/<skill>/current.log           ← symlink → latest run log
```

**Global `~/.config/ghost/config.yaml` fields:**

| Field | Default | Description |
|---|---|---|
| `guard.ops_session` | `ghost-ops` | tmux session used as Guard for all projects |
| `guard.timeout_minutes` | `60` | max wait time for Guard decision |
| `logs.max_files` | `30` | max log files kept per skill |
| `logs.max_age_days` | `7` | delete logs older than N days |
| `runner.default_shell` | `zsh` | shell used to inherit environment |
| `runner.default_on_failure` | `stop` | fallback if skill has no `on_failure` set |

**Per-project `.ghost/config.yaml`** overrides the same fields for that folder only.

## ADDED Requirements

### Requirement: Workspace is a collection of folders
The app SHALL maintain a workspace — a set of one or more project folders — persisted across restarts.

#### Scenario: Add folder to workspace
- **WHEN** the user selects a directory via the 📁 folder picker
- **THEN** the frontend SHALL send `workspace_add {work_dir}` IPC command
- **AND** the backend SHALL start a `SkillRunner` + `GitsDB` instance for that folder
- **AND** the backend SHALL persist the folder list to `~/.gits/workspace.json`
- **AND** the backend SHALL emit `workspace_changed`, then `skills_list` and `agents_list` aggregated across all open folders

#### Scenario: Workspace restored on restart
- **WHEN** the desktop app starts
- **THEN** the backend SHALL read `~/.gits/workspace.json` and resume each saved folder's SkillRunner
- **AND** emit `workspace_changed` with the restored folder list

#### Scenario: Remove folder from workspace
- **WHEN** the user clicks ✕ on a folder chip in the UI
- **THEN** the frontend SHALL send `workspace_remove {work_dir}`
- **AND** the backend SHALL stop that folder's SkillRunner and remove it from persistence
- **AND** the Skills panel and Agents panel SHALL remove all entries for that folder

### Requirement: Skills are scoped to their project folder
Each folder's skills SHALL be defined in `<folder>/.ghost/skills/` and managed independently.

#### Scenario: Skills panel grouped by folder
- **WHEN** `skills_list` is received with skills from multiple folders
- **THEN** the Skills panel left list SHALL render a section header per folder (directory basename)
- **AND** each skill SHALL appear under its folder's section

#### Scenario: Empty folder
- **WHEN** a folder has no `.ghost/skills/` directory or no `.md` files there
- **THEN** its section SHALL show "No skills in .ghost/skills/ yet."

### Requirement: Skill detail shows trigger, steps, and run history
Clicking a skill SHALL open a detail panel sourced entirely from live backend data.

#### Scenario: Loop skill human-readable schedule
- **WHEN** a Loop skill with a cron schedule is selected
- **THEN** the trigger section SHALL show a human-readable form (e.g. "Weekdays 4:00 AM") plus next run ("next Mon 04:00") from `next_run_at`

#### Scenario: Reactive always-on skill
- **WHEN** a Reactive `always_on: true` skill is selected
- **THEN** the trigger section SHALL show "● Always on · restarts on exit"

#### Scenario: Step with known tool
- **WHEN** a skill step names a tool present in `toolDefs`
- **THEN** the step row SHALL display: tool name, command string, working directory

#### Scenario: Inline or unknown step
- **WHEN** a skill step has no matching tool in `toolDefs`
- **THEN** the step SHALL display the raw step string; unknown tool names SHALL show "(not found)" suffix

#### Scenario: Run history
- **WHEN** a skill has prior runs in `<folder>/.ghost/ghost.db`
- **THEN** the detail panel SHALL show the last 10 runs: status dot, start time, duration, "View Log" link
- **WHEN** "View Log" is clicked
- **THEN** the fleet drawer SHALL open and stream `<folder>/.ghost/logs/<skill>/current.log`

### Requirement: Runner cards show folder, schedule, and next-run time
Each Runner Agent card SHALL identify its project folder and display scheduling information.

#### Scenario: Card folder badge
- **WHEN** a runner card is rendered for a skill in folder `/path/to/aifinance`
- **THEN** the card SHALL show a folder badge with the directory basename ("aifinance")

#### Scenario: Loop card schedule line
- **WHEN** a Loop skill is active and not paused
- **THEN** the sub-line SHALL include the human-readable schedule and "next <day> <HH:MM>"

#### Scenario: Paused card
- **WHEN** `next_run_at` is null because the skill is paused
- **THEN** the sub-line SHALL show "⏸ Paused"

#### Scenario: Reactive card
- **WHEN** a Reactive always_on runner card is rendered
- **THEN** the card SHALL show "● Always on" in place of a schedule

### Requirement: New Skill modal writes a real file
The "＋ New Skill" modal SHALL write `<folder>/.ghost/skills/<slug>.md` and trigger live reload.

#### Scenario: Successful creation
- **WHEN** the user fills Name, Trigger, Schedule (if Loop), Steps, and selects a target folder, then clicks Save
- **THEN** the backend SHALL write `<work_dir>/.ghost/skills/<slug>.md` in canonical Markdown format
- **AND** the folder's SkillRunner SHALL reload and schedule the new skill
- **AND** the Skills panel SHALL show the new skill in the correct folder section

#### Scenario: Duplicate slug
- **WHEN** `<work_dir>/.ghost/skills/<slug>.md` already exists
- **THEN** the backend SHALL emit `{event:"error", msg:"Skill '<name>' already exists"}`
- **AND** the modal SHALL display the error inline without closing

### Requirement: Tools list merges global and local sources
The `tools_list` IPC event SHALL include globally installed tools merged with any project-local overrides.

#### Scenario: Global tools visible everywhere
- **WHEN** the `tools` IPC command is handled
- **THEN** `tools_list` SHALL include all tools from `~/.config/ghost/tools/` with `scope:"global"`

#### Scenario: Local tool override
- **WHEN** `<folder>/.ghost/tools/<name>.md` exists with the same name as a global tool
- **THEN** the local version SHALL take precedence with `scope:"local"`

## MODIFIED Requirements

### Requirement: Skill logs written inside project folder
Skill run logs SHALL be written to `<folder>/.ghost/logs/` — NOT to `~/.gits/agents/`.

#### Scenario: Log file created on run start
- **WHEN** a skill run starts in folder `/path/to/project`
- **THEN** the log SHALL be created at `/path/to/project/.ghost/logs/<skill>/<run-id>.log`
- **AND** `/path/to/project/.ghost/logs/<skill>/current.log` SHALL be updated

#### Scenario: Run metadata in folder DB
- **WHEN** a skill run completes
- **THEN** run metadata SHALL be stored in `<folder>/.ghost/ghost.db`, not in `~/.gits/gits.db`

### Requirement: skills IPC response includes work_dir and next_run_at
Each skill object in `skills_list` SHALL carry its source folder and next scheduled run.

#### Scenario: Skills from multiple folders
- **WHEN** two folders are open and both have skills
- **THEN** `skills_list` SHALL include all skills with a `work_dir` field identifying their folder

#### Scenario: next_run_at for active Loop skill
- **WHEN** a Loop skill is scheduled and not paused
- **THEN** `next_run_at` SHALL be an ISO 8601 string of the next trigger time

#### Scenario: next_run_at absent
- **WHEN** a skill is paused or is Reactive type
- **THEN** `next_run_at` SHALL be `null`
