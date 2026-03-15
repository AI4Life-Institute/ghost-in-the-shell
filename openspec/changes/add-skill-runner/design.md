# Design: Skill Runner

## Context

Ghost manages tmux sessions for coding CLIs. This change adds a parallel execution path for scheduled/reactive automation. Key constraints:
- All definitions are Markdown files — no local ID system, git-trackable, human-editable
- Logs are plain files, not database blobs — `tail -f` works, no truncation
- Runner Agents reuse the existing tmux infrastructure for execution visibility
- Guard mechanism reuses existing `inject_message` / `parse_status_line` from engine

## Goals / Non-Goals

**Goals:**
- Replace pm2 cron/autorestart with Ghost-managed Loop and Reactive Skills
- All Skills are guarded by default (Coding Agent intervenes on failure)
- Zero changes to existing business scripts (they run as Tool commands, unmodified)
- Ghost inherits full shell PATH from user's login shell at startup

**Non-Goals:**
- Multi-step Skill orchestration with data passing between steps (v2)
- Browser Agent think-act loop (separate change)
- `data_records` structured output (v2, requires script cooperation)
- Cross-machine execution

## Decisions

### File format: Markdown with structured sections
- Sections identified by `## SectionName` headers
- Values are plain text or indented YAML-like blocks
- Freeform description text is preserved for Guard context
- Rationale: readable without tooling, editable in any editor, parseable with simple regex

### Skill steps reference Tools by filename stem
- `aifinance-digest` in a step → loads `~/.gits/tools/aifinance-digest.md`
- Steps can also inline a command directly (no Tool file required)
- Rationale: avoids ID registry, natural for humans to write

### Runner Agent uses tmux shell session
- Each Skill gets a named tmux session `ghost-runner-<skill-name>`
- Commands run via `tmux send-keys` + prompt detection (reuses existing `parse_status_line`)
- Stdout captured by `tmux pipe-pane` to log file
- Rationale: execution visible in UI, consistent with Coding Agent model

### Guard: inject into ops session, wait for idle
- Ghost formats a Guard prompt: skill description + failed step + Tool definition + log tail
- Injects into the ops session via existing `engine.inject_message()`
- Waits for ops session to return to idle (`parse_status_line == "idle"`)
- Reads last N lines of ops session output to determine Guard decision
- Guard decision format: Coding Agent outputs `GUARD_ACTION: retry|skip|abort|fixed`
- Rationale: reuses existing session infrastructure, no new protocol needed

### ops session
- Created at Ghost startup if not exists: `tmux new-session -d -s ghost-ops`
- Starts a coding CLI (user's preferred CLI from Settings, default: claude-code)
- Configurable in `~/.gits/config.md` under `## Guard` section
- All Skills use ops session unless Skill specifies `session: <name>`

### Log rotation
- Keep last 30 run log files per agent, delete older ones
- `current.log` is a copy (not symlink) of the latest run file for simplicity
- Rotation runs after each run completion

### GitsDB schema (metadata only)
```sql
CREATE TABLE runs (
  id          TEXT PRIMARY KEY,  -- ISO8601 start time, also log filename prefix
  skill_name  TEXT NOT NULL,
  agent_type  TEXT NOT NULL,     -- runner | coding
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  exit_code   INT,
  status      TEXT NOT NULL,     -- running | success | failed | guarded
  log_path    TEXT NOT NULL,
  guard_log   TEXT               -- JSON: Guard decision + context, NULL if no Guard ran
);

CREATE TABLE artifacts (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  type        TEXT NOT NULL,
  path        TEXT NOT NULL,
  label       TEXT,
  metadata    TEXT,
  created_at  TEXT NOT NULL
);
```

## On Failure Semantics

| Value | Behavior |
|---|---|
| `retry: max N` | Re-run failed step up to N times; after exhausting retries, trigger Guard |
| `continue` | Record error, move to next trigger cycle (Reactive polling) |
| `restart` | Kill and restart the process (always-on daemon) |
| `stop` | Mark run failed, stop Skill |
| `notify` | Send notification (Discord / macOS notification), then stop |

Guard is triggered after on_failure handling is exhausted, unless `guard: never`.

## Risks / Trade-offs

- **tmux prompt detection reliability**: `parse_status_line` already handles multiple CLI types; shell prompt detection for Runner Agent sessions needs a known PS1 pattern → mitigated by using a fixed PS1 in ghost-runner sessions
- **PATH inheritance**: `zsh -c env` at startup captures the user's login environment; if user later installs tools, Ghost needs restart → acceptable trade-off
- **Guard decision parsing**: relying on `GUARD_ACTION:` keyword in Coding Agent output is fragile → mitigated by treating any non-matching output as "abort" (safe default)

## Migration Plan

1. Write Tool md files for each existing pm2 process (no pm2 changes)
2. Write Skill md files
3. Test each Skill's Tool with `skill_run` before migrating
4. `pm2 stop <name>` → enable Skill in Ghost (one at a time)
5. Rollback: `pm2 start ecosystem.config.cjs` + pause Skill in Ghost

## Open Questions

- Guard decision format: `GUARD_ACTION:` keyword vs structured JSON output from Coding Agent?
- ops session CLI: auto-start claude-code, or wait for user to open it?
