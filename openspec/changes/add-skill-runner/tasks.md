## 1. Storage — GitsDB

- [x] 1.1 Create `src/gits/storage/db.py` — initialize `~/.gits/gits.db`, create `runs` and `artifacts` tables (idempotent)
- [x] 1.2 Implement `insert_run`, `finish_run`, `update_guard_log`, `query_runs` methods
- [x] 1.3 Implement `insert_artifact`, `query_artifacts` methods
- [x] 1.4 Write unit tests for GitsDB

## 2. File Definitions — SkillLoader

- [x] 2.1 Create `src/gits/core/skill_loader.py` — parse `~/.gits/tools/*.md` into Tool dataclasses
- [x] 2.2 Parse `~/.gits/skills/*.md` into Skill dataclasses (trigger, steps, on_failure, guard)
- [x] 2.3 Resolve step Tool references by filename stem; fall back to inline command
- [x] 2.4 Parse `~/.gits/config.md` for `ops_session` setting
- [x] 2.5 Implement shell environment inheritance: run `zsh -c env` (fallback `bash -c env`) at startup, cache result
- [x] 2.6 Write unit tests for SkillLoader (loop trigger, reactive trigger, inline step, missing tool)

## 3. Scheduler & Executor — SkillRunner

- [x] 3.1 Create `src/gits/core/skill_runner.py` — `SkillRunner` class with `start()`, `run_now()`, `pause()`, `resume()`
- [x] 3.2 Implement Loop trigger: use `croniter` for cron schedules, asyncio sleep for interval
- [x] 3.3 Implement Reactive trigger: polling loop with adaptive interval, `always_on` restart mode
- [x] 3.4 Implement step execution: create `ghost-runner-<skill>` tmux session, inject commands, detect completion via shell prompt
- [x] 3.5 Implement log capture: pipe tmux pane output to `~/.gits/agents/<skill>/<run-id>.log` in real time
- [x] 3.6 Emit `agent_log` IPC event for each captured line
- [x] 3.7 Implement `on_failure` policies: retry, continue, restart, stop, notify
- [x] 3.8 Implement log rotation: keep last 30 files, delete files older than 7 days
- [x] 3.9 Write integration tests for Loop and Reactive triggers

## 4. Guard Mechanism

- [x] 4.1 Implement `_trigger_guard()`: format Guard prompt (skill description + failed step + tool md + log tail)
- [x] 4.2 Inject Guard prompt into designated Coding Agent session via `engine.inject_message()`
- [x] 4.3 Wait for session idle via `parse_status_line`, timeout after 10 minutes
- [x] 4.4 Parse `GUARD_ACTION: retry|skip|abort|fixed` from session output; default to `abort`
- [x] 4.5 Update `guard_log` in SQLite with decision and context JSON
- [x] 4.6 Implement ops session auto-creation at Ghost startup

## 5. IPC Extensions

- [x] 5.1 Add `skills` command handler → scan `~/.gits/skills/*.md` → emit `skills_list`
- [x] 5.2 Add `agents` command handler → `db.query_runs()` → emit `agents_list`
- [x] 5.3 Add `skill_run` command handler → `SkillRunner.run_now(skill_name)`
- [x] 5.4 Add `skill_pause` / `skill_resume` command handlers
- [x] 5.5 Add `agent_log` command handler → stream log file → emit `agent_log` events
- [x] 5.6 Wire `SkillRunner.start()` into `_cmd_desktop()` alongside engine start

## 6. UI — Agents Panel

- [x] 6.1 On app load, send `agents` command and render Runner Agent cards alongside Coding Agent cards
- [x] 6.2 Render status dot: green (success), red (failed), yellow (running), orange (guarded)
- [x] 6.3 Show last run time and duration on each card
- [x] 6.4 Implement live log panel: on card expand, send `agent_log` for latest run, subscribe to `agent_log` events
- [x] 6.5 Implement "▶ Run Now" button → send `skill_run`
- [x] 6.6 Implement "⏸ Pause / ▶ Resume" toggle → send `skill_pause` / `skill_resume`

## 7. UI — Skills Panel

- [x] 7.1 On Skills panel open, send `skills` command and render Skill cards
- [x] 7.2 Show trigger type badge (Loop / Reactive), on-failure policy, guard status
- [x] 7.3 Show steps list with Tool names

## 8. Tooling & Sample Files

- [x] 8.1 Create sample `~/.gits/tools/` files for existing pm2 Tools (discord-run, aifinance-digest, etc.)
- [x] 8.2 Create sample `~/.gits/skills/` files for existing pm2 processes
- [x] 8.3 Create `~/.gits/config.md` with default `ops_session: ghost-ops`

## 9. Validation

- [x] 9.1 `openspec validate add-skill-runner --strict`
- [x] 9.2 End-to-end test: create a Loop Skill with a simple echo command, verify run fires, log written, IPC event emitted
- [x] 9.3 End-to-end test: simulate step failure, verify Guard prompt injected into ops session
