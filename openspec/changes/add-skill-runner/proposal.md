# Change: Add Skill Runner — Tool/Skill/Agent automation system

## Why

Ghost currently manages coding CLI sessions (Claude Code, Codex, OpenCode) in tmux panes, but has no way to run scheduled or reactive automation tasks. Users must rely on pm2 to manage recurring jobs (financial report downloads, Discord polling, image generation queues). This change adds a file-based automation platform: users define **Tools** (CLI command specs in Markdown) and **Skills** (trigger + steps + guard in Markdown), Ghost schedules and executes them as **Runner Agents**, with logs isolated per agent and a Coding Agent acting as default guardian when things go wrong.

## What Changes

- Add `~/.gits/tools/*.md` — Tool definitions: describe any CLI command (system tool or local script) with its invocation, working directory, env, and timeout
- Add `~/.gits/skills/*.md` — Skill definitions: trigger (Loop cron/interval or Reactive polling/always-on), ordered steps referencing Tools, guard configuration, and on-failure policy
- Add **SkillLoader** — parses Tool and Skill Markdown files at startup and on file change
- Add **SkillRunner** — asyncio scheduler that fires Loop/Reactive triggers, executes Skill steps in a tmux shell session, streams stdout to per-agent log files, and triggers the Guard on failure
- Add **Guard mechanism** — on step failure, injects skill context + error log into a designated Coding Agent session (default: `ops` session); the Coding Agent decides how to proceed
- Add **GitsDB** — SQLite at `~/.gits/gits.db` storing run metadata only (start/end time, status, log path); log content stays in files
- Add **per-agent log files** at `~/.gits/agents/<skill-name>/` — one file per run, `current.log` always points to the latest; never truncated
- Extend **IPC protocol** — new commands (`skills`, `agents`, `skill_run`, `skill_pause`, `agent_log`) and events (`skills_list`, `agents_list`, `agent_log`, `agent_run_done`)
- Extend **Agents UI panel** — show Runner Agents alongside Coding Agents: status dot, last run time, live log stream, Run Now / Pause controls
- Add **ops session** — Ghost maintains a dedicated Coding Agent tmux session as the default Guard for all Skills; configurable in Settings

## Impact

- New specs: `tool-registry`, `skill-runner`, `agent-log`
- Affected specs: `terminal-ui-bridge` (MODIFIED — new IPC commands/events), `discord-interactions` (no change, Runner Agents replace pm2 but don't touch Discord adapter)
- Affected code:
  - New: `src/gits/storage/db.py` (GitsDB)
  - New: `src/gits/core/skill_loader.py` (SkillLoader)
  - New: `src/gits/core/skill_runner.py` (SkillRunner + Guard)
  - Modified: `src/gits/__main__.py` (wire SkillRunner into desktop command, add IPC handlers)
  - Modified: `ui/app.js` (Agents panel real data, Skills panel)
- New dependency: `croniter` (Python, parse cron expressions)
- No changes to existing coding CLI session management
- **Supersedes**: `add-local-browser-agent` storage model (GitsDB replaces that proposal's SQLite schema; browser agent itself is out of scope for this change)
