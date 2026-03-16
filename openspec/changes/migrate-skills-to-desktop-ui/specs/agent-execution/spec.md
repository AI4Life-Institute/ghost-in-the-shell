# agent-execution Specification

## Purpose
Defines how Ghost resolves and executes an Agent: from trigger to tmux session to log capture to database record. This is the runtime contract that AgentRunner implements.

## ADDED Requirements

### Requirement: Agent is the top-level schedulable unit
An Agent definition file (`agents/<name>.md`) SHALL be the entity Ghost schedules and monitors. Skills and Tools are subordinate; they exist only to support Agent execution.

#### Scenario: Agent references skills
- **WHEN** an agent definition contains `skills: [collect-news]`
- **THEN** Ghost SHALL resolve `skills/collect-news/skill.md` within the same project folder
- **AND** execute its steps in order when the agent triggers

#### Scenario: Agent references tools directly (no skill wrapper)
- **WHEN** an agent definition contains a step that is a tool name with no matching skill
- **THEN** Ghost SHALL resolve the tool directly and execute it as a single-step skill

---

### Requirement: Name resolution is project-local first, then global
Ghost SHALL resolve tool and skill names using a two-level lookup.

#### Scenario: Project-local tool wins
- **WHEN** `<project>/tools/discord-notify/tool.md` exists and an agent references `discord-notify`
- **THEN** Ghost SHALL use the project-local definition, ignoring any global definition with the same name

#### Scenario: Global tool fallback
- **WHEN** no project-local `tools/<name>/tool.md` exists
- **THEN** Ghost SHALL look up `~/.config/ghost/tools/<name>/tool.md`
- **AND** if not found there either, the run SHALL fail with error "tool not found: <name>"

---

### Requirement: Tool command runs in the tool's own directory
Each tool's command SHALL execute with `cwd` set to the tool's directory.

#### Scenario: Python script in tool directory
- **WHEN** `tools/fetch-news/tool.md` has `command: python fetch.py`
- **THEN** Ghost SHALL run `python fetch.py` with `cwd = <project>/tools/fetch-news/`
- **AND** `fetch.py` SHALL be able to import modules in the same directory without path manipulation

#### Scenario: GHOST_PROJECT_ROOT allows data access
- **WHEN** a tool script needs to read from or write to `data/`
- **THEN** `GHOST_PROJECT_ROOT` env var SHALL point to the project folder
- **AND** the script SHALL use `os.environ['GHOST_DATA_DIR']` (equivalent to `$GHOST_PROJECT_ROOT/data/`) to locate databases and output files

---

### Requirement: Ghost injects standard env vars into every tool run
Ghost SHALL inject the following environment variables before executing any tool command.

#### Scenario: Standard vars present at runtime
- **WHEN** any tool command is executed
- **THEN** the process environment SHALL contain:
  - `GHOST_PROJECT_ROOT` — absolute path to the project folder
  - `GHOST_DATA_DIR` — `<project>/data/`
  - `GHOST_AGENT_NAME` — name of the running agent
  - `GHOST_RUN_ID` — unique identifier for this run
  - `GHOST_LOG_FILE` — absolute path to the current run log file

#### Scenario: Tool environment overrides
- **WHEN** `tool.md` defines an `environment:` block
- **THEN** those vars SHALL be merged on top of the shell environment and GHOST vars
- **AND** tool-defined vars with the same name as GHOST vars SHALL override the GHOST defaults

---

### Requirement: Each agent run uses a dedicated tmux session
Ghost SHALL create one tmux session per agent run, named predictably.

#### Scenario: Session naming
- **WHEN** agent `news-collector` in project `news-briefing` triggers
- **THEN** Ghost SHALL create tmux session `ghost-news-briefing-news-collector`
- **AND** reuse that session if it already exists (attach a new window)

#### Scenario: Session cleanup
- **WHEN** all steps complete (success or failure)
- **THEN** the tmux session SHALL be closed within 5 seconds of completion

---

### Requirement: Log captured in real time to .ghost/logs/
Every byte of tmux pane output SHALL be captured to a per-run log file.

#### Scenario: Log file path
- **WHEN** agent `news-collector` starts run `run_042`
- **THEN** output SHALL be piped to `<project>/.ghost/logs/news-collector/run_042.log`
- **AND** `<project>/.ghost/logs/news-collector/current.log` SHALL be updated to point to this file

#### Scenario: Log rotation
- **WHEN** the number of log files for an agent exceeds `logs.max_files` (default 30)
- **THEN** the oldest files SHALL be deleted
- **WHEN** a log file is older than `logs.max_age_days` (default 7)
- **THEN** it SHALL be deleted regardless of file count

---

### Requirement: Run metadata recorded in data/ghost.db
Every agent run SHALL be recorded in the project's `data/ghost.db`.

#### Scenario: Run record created on start
- **WHEN** an agent run begins
- **THEN** a row SHALL be inserted into the `runs` table with: `agent_name`, `status=running`, `started_at`, `log_path`

#### Scenario: Run record updated on completion
- **WHEN** an agent run finishes
- **THEN** the row SHALL be updated with: `status` (success | failed | guarded), `finished_at`, `duration_s`

---

### Requirement: on_error policy applied per skill
When a step exits non-zero, the skill's `on_error` policy SHALL be applied.

#### Scenario: on_error: continue
- **WHEN** a step fails and the skill has `on_error: continue`
- **THEN** Ghost SHALL log the failure and proceed to the next step

#### Scenario: on_error: stop
- **WHEN** a step fails and the skill has `on_error: stop`
- **THEN** Ghost SHALL abort remaining steps and mark the run failed

#### Scenario: on_error: retry:N
- **WHEN** a step fails and the skill has `on_error: retry:3`
- **THEN** Ghost SHALL retry the failed step up to 3 times before marking it failed

---

### Requirement: Guard triggered on agent failure
When an agent run fails and a Guard is configured, Ghost SHALL inject context into the ops session.

#### Scenario: Guard injection
- **WHEN** an agent run fails and `guard.session` is set
- **THEN** Ghost SHALL inject a prompt into the configured tmux ops session containing: agent name, failed step, tool definition, last 50 lines of the run log
- **AND** wait up to `guard.timeout_minutes` for a `GUARD_ACTION:` response

#### Scenario: Guard response handled
- **WHEN** the ops session outputs `GUARD_ACTION: retry`
- **THEN** Ghost SHALL re-run the agent from the failed step
- **WHEN** `GUARD_ACTION: abort`
- **THEN** Ghost SHALL mark the run as `guarded` and stop
