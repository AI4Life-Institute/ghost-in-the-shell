## ADDED Requirements

### Requirement: Skill Definition Files
The system SHALL load Skill definitions from Markdown files in `~/.gits/skills/`.
A Skill file defines a trigger, an ordered list of steps, an on-failure policy, and an optional guard configuration.

#### Scenario: Load loop skill
- **WHEN** `~/.gits/skills/aifinance-digest-pre.md` has a `## Trigger` section with `loop: schedule: "0 5 * * 1-5"`
- **THEN** SkillRunner schedules the Skill to fire at 05:00 on weekdays

#### Scenario: Load reactive skill
- **WHEN** a Skill has `## Trigger` with `reactive: polling: peak_seconds: 60`
- **THEN** SkillRunner runs the Skill in a polling loop with the given interval

#### Scenario: Load always-on skill
- **WHEN** a Skill has `## Trigger` with `reactive: always_on: true`
- **THEN** SkillRunner starts the Skill immediately and restarts it on exit per the on-failure policy

### Requirement: Loop Trigger
The system SHALL support Loop trigger with either a cron expression (`schedule`) or a fixed interval (`interval_seconds`).
Cron expressions SHALL be parsed using standard 5-field syntax (minute hour dom month dow).

#### Scenario: Cron fire
- **WHEN** the current time matches a Skill's cron schedule
- **THEN** SkillRunner starts a new run for that Skill

#### Scenario: Interval fire
- **WHEN** `interval_seconds: 3600` is configured
- **THEN** SkillRunner fires the Skill every 3600 seconds after the previous run completes

### Requirement: Reactive Trigger
The system SHALL support Reactive trigger with polling interval and optional adaptive schedule.
Adaptive schedule adjusts the polling interval based on time-of-day and day-of-week windows.

#### Scenario: Adaptive polling — peak hours
- **WHEN** current time is within `peak_start`–`peak_end` on a weekday
- **THEN** SkillRunner uses `peak_seconds` as the interval

#### Scenario: Adaptive polling — off hours
- **WHEN** current time is outside the peak window or on a weekend
- **THEN** SkillRunner uses `off_seconds` as the interval

### Requirement: Skill Step Execution
The system SHALL execute Skill steps sequentially in a tmux shell session named `ghost-runner-<skill-name>`.
Each step runs a Tool command (by Tool filename stem) or an inline command.
stdout and stderr SHALL be captured to the run's log file in real time.
Each captured line SHALL also be emitted as an `agent_log` IPC event.

#### Scenario: Sequential steps
- **WHEN** a Skill has two steps
- **THEN** the second step starts only after the first step's process exits

#### Scenario: Log capture
- **WHEN** a step produces stdout output
- **THEN** each line is appended to `~/.gits/agents/<skill-name>/current.log` within one second

### Requirement: On-Failure Policy
The system SHALL apply the Skill's `## On Failure` policy when a step exits with a non-zero exit code.

Supported policies:
- `retry: max N` — re-run the failed step up to N times; after exhausting retries, trigger Guard
- `continue` — log the error and wait for the next trigger cycle
- `restart` — kill and restart the entire Skill process
- `stop` — mark run as failed and stop
- `notify` — emit a notification event and stop

#### Scenario: Retry exhausted triggers guard
- **WHEN** a step fails and `retry: max 2` is configured
- **AND** the step has already been retried twice
- **THEN** the Guard mechanism is triggered

#### Scenario: Continue on reactive failure
- **WHEN** a Reactive Skill step fails and `on_failure: continue` is set
- **THEN** the run is marked failed, no Guard is triggered, and the next polling cycle proceeds normally

### Requirement: Guard Mechanism
All Skills SHALL have Guard enabled by default (`on: failure`).
When Guard is triggered, the system SHALL inject a structured prompt into the designated Coding Agent session containing: the Skill's description, the failed step definition, the relevant Tool definition, and the last 50 lines of the run log.
The system SHALL wait for the Coding Agent session to return to idle, then read its output for a Guard decision.
A Guard decision is indicated by the Coding Agent outputting `GUARD_ACTION: retry|skip|abort|fixed`.
If no decision is found, the system SHALL default to `abort`.

#### Scenario: Guard on failure — retry decision
- **WHEN** a Skill step fails and Guard is triggered
- **AND** the Coding Agent outputs `GUARD_ACTION: retry` after reviewing the context
- **THEN** SkillRunner retries the failed step once more

#### Scenario: Guard on failure — fixed decision
- **WHEN** the Coding Agent outputs `GUARD_ACTION: fixed`
- **THEN** SkillRunner considers the step resolved and continues to the next step

#### Scenario: Guard disabled
- **WHEN** a Skill has `## Guard` section with `on: never`
- **THEN** Guard is never triggered for that Skill regardless of failures

#### Scenario: Guard session override
- **WHEN** a Skill has `## Guard` with `session: aifinance`
- **THEN** the Guard context is injected into the session named `aifinance` instead of the ops session

### Requirement: ops Session
The system SHALL maintain a dedicated Coding Agent tmux session named `ghost-ops` as the default Guard target for all Skills.
The ops session SHALL be created at Ghost startup if it does not exist.
The default ops session name SHALL be configurable in `~/.gits/config.md` under a `## Guard` section.

#### Scenario: ops session auto-created
- **WHEN** Ghost starts in desktop mode
- **AND** no tmux session named `ghost-ops` exists
- **THEN** Ghost creates it and starts the user's preferred coding CLI

#### Scenario: Custom ops session
- **WHEN** `~/.gits/config.md` contains `ops_session: my-ops`
- **THEN** Guard injects into the session named `my-ops` by default

### Requirement: Run Now
The system SHALL support immediate on-demand execution of any active Skill via the `skill_run` IPC command.

#### Scenario: Run now
- **WHEN** the frontend sends `{ "cmd": "skill_run", "skill_name": "aifinance-digest-pre" }`
- **THEN** SkillRunner starts a new run for that Skill immediately, regardless of its trigger schedule

### Requirement: Skill Pause and Resume
The system SHALL support pausing and resuming Skills via `skill_pause` and `skill_resume` IPC commands.
A paused Skill SHALL NOT fire on its trigger schedule until resumed.
Pause state is held in memory; Ghost reload re-reads Skill files and resumes all Skills as active.

#### Scenario: Pause prevents trigger
- **WHEN** a Skill is paused
- **AND** its cron schedule fires
- **THEN** no run is started

#### Scenario: Reload clears pause
- **WHEN** Ghost restarts
- **THEN** all Skills are loaded from their md files in active state
