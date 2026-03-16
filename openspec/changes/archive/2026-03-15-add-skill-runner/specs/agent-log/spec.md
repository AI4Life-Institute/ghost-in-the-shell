## ADDED Requirements

### Requirement: Per-Agent Log Files
The system SHALL write run logs to `~/.gits/agents/<skill-name>/<run-id>.log` where `<run-id>` is the run's ISO8601 start timestamp.
`~/.gits/agents/<skill-name>/current.log` SHALL always contain the content of the most recent run's log.
Log files SHALL be written in real time as output is produced (unbuffered).

#### Scenario: Log file created on run start
- **WHEN** a Skill run starts
- **THEN** `~/.gits/agents/<skill-name>/<run-id>.log` is created immediately

#### Scenario: current.log updated
- **WHEN** a run completes
- **THEN** `current.log` reflects the content of that run's log file

#### Scenario: Tail works during run
- **WHEN** a step is actively producing output
- **THEN** `tail -f ~/.gits/agents/<skill-name>/current.log` shows new lines within one second

### Requirement: Log Rotation
The system SHALL retain at most 30 log files per agent directory.
When a new run completes and the count exceeds 30, the oldest log file SHALL be deleted.
Logs older than 7 days SHALL also be deleted regardless of count.

#### Scenario: Old logs deleted
- **WHEN** a Skill has produced 31 run log files
- **THEN** the oldest file is deleted after the 31st run completes

### Requirement: Run Metadata in SQLite
The system SHALL store run metadata in `~/.gits/gits.db` in a `runs` table.
The `runs` table SHALL contain: run id, skill name, agent type, started_at, finished_at, exit_code, status, log_path, and guard_log.
Log content SHALL NOT be stored in SQLite.

#### Scenario: Metadata written on run start
- **WHEN** a run starts
- **THEN** a row is inserted into `runs` with `status = running` and `log_path` pointing to the log file

#### Scenario: Metadata updated on run complete
- **WHEN** a run finishes
- **THEN** the row is updated with `finished_at`, `exit_code`, and final `status`

#### Scenario: Guard decision recorded
- **WHEN** a Guard is triggered and a decision is reached
- **THEN** `guard_log` is updated with a JSON object containing the Guard context and decision

### Requirement: Agent Log IPC Streaming
The system SHALL emit `agent_log` IPC events for each line of output during an active run.
The system SHALL support streaming historical log content via the `agent_log` IPC command.

#### Scenario: Live log streaming
- **WHEN** a step produces a line of stdout
- **THEN** an `agent_log` event is emitted within one second containing the skill name, run id, and line text

#### Scenario: Historical log fetch
- **WHEN** the frontend sends `{ "cmd": "agent_log", "skill_name": "...", "run_id": "..." }`
- **THEN** the system reads the corresponding log file and emits its contents as `agent_log` events in order
