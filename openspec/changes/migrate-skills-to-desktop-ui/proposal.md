# Change: Migrate Existing Skills & Tools to Desktop UI

## Why

Two problems need fixing:

### Problem 1 — Wrong storage layout
The current implementation stores skill and tool definitions in `~/.gits/skills/` and `~/.gits/tools/`, and writes execution logs to `~/.gits/agents/`. This is wrong:

- **Agents, skills, tools, and data belong to the project they serve.** They should live inside the project directory, visible to the user.
- **`~/.gits/` should only hold global Ghost config** — not per-project definitions, logs, or databases.

### Problem 2 — UI shows mock data, not real agents
The desktop UI does not surface real data:

1. **Agents panel** (Runner section) shows only runs — there is no Agent concept with a definition, goal, or identity
2. **Skills panel** shows five hardcoded mock skills; the user's real skills are in a small secondary panel
3. **"＋ New Skill" modal** writes to memory only — nothing is saved to disk
4. **Runner cards** show no schedule or next-run time
5. **Tool definitions** are never surfaced in the UI

---

## Project Layout (new)

The app manages a **workspace** — one or more project folders open simultaneously (VS Code-style multi-root). Within each folder:

- `agents/`, `skills/`, `tools/`, `data/` are **user assets** — visible in Finder and in Ghost UI
- `.ghost/` is **Ghost-internal** — hidden, contains logs and config

```
~/.config/ghost/
  config.yaml                         ← Ghost global config
  tools/<name>/                       ← globally shared tools (available to all projects)
    tool.md
    <impl files>

~/.gits/
  workspace.json                      ← persisted workspace: list of open folder paths

<project>/
  agents/
    <name>.md                         ← Agent definition: trigger, skills, guard policy

  skills/
    <name>/
      skill.md                        ← Skill definition: ordered steps, on_error policy
      <supporting files>              ← templates, prompts, reference docs (optional)

  tools/
    <name>/
      tool.md                         ← Tool definition: command, environment
      <impl files>                    ← Python/TS/shell code the command runs (optional)

  data/
    ghost.db                          ← run metadata (agent name, status, timestamps)
    <name>.db / <name>.csv ...        ← output produced by agents

  .ghost/                             ← Ghost-internal — hidden
    config.yaml                       ← per-project config overrides (optional)
    logs/
      <agent>/
        <run-id>.log
        current.log                   ← symlink → latest run
```

### File formats

**`agents/<name>.md`** — YAML frontmatter + markdown description:
```markdown
---
name: news-collector
description: Collect AI and tech news from the web every hour
trigger:
  type: loop
  schedule: "0 * * * *"
skills:
  - collect-news
on_failure: retry:3
guard:
  session: ghost-ops
---

Monitors RSS feeds and web sources hourly. Saves deduplicated
articles to data/news.db for downstream processing.
```

**`skills/<name>/skill.md`** — YAML frontmatter + markdown description:
```markdown
---
name: collect-news
description: Fetch articles from configured sources and save to data/news.db
steps:
  - fetch-news
  - save-articles
on_error: continue
---

Fetches from RSS feeds. Deduplicates by URL before saving.
```

**`tools/<name>/tool.md`** — YAML frontmatter + markdown description:
```markdown
---
name: fetch-news
description: Scrape news articles from RSS feeds, output JSON to stdout
command: python fetch.py
environment:
  NEWS_SOURCES: "https://feeds.reuters.com/reuters/technologyNews"
  MAX_ARTICLES: "50"
---

Fetches articles from NEWS_SOURCES. Outputs JSON array to stdout.
```

### Name resolution order
When an Agent or Skill references a name, Ghost resolves it:
1. `<project>/tools/<name>/tool.md` — project-local (wins)
2. `~/.config/ghost/tools/<name>/tool.md` — global shared (fallback)

Same rule applies to skills referenced by agents.

---

## How Ghost Runs an Agent

### Execution flow

```
Trigger fires (cron / reactive event)
    │
    ▼
1. Read agents/<name>.md
   → get skills list

    │  for each skill:
    ▼
2. Read skills/<skill>/skill.md
   → get steps list

    │  for each step:
    ▼
3. Resolve tool
   → project tools/<name>/tool.md  (local first)
   → ~/.config/ghost/tools/<name>/tool.md  (global fallback)
   → extract command + environment

    │
    ▼
4. Open tmux session  ghost-<agent-name>
   (reuse if already exists)

    │  for each step (sequential):
    ▼
5. Execute tool command in tmux pane
   cwd       = <project>/tools/<name>/   (tool's own directory)
   env       = shell_env + tool.environment + GHOST_* vars
   pipe-pane → .ghost/logs/<agent>/<run-id>.log

    │
    ▼
6. Detect completion
   → poll tmux pane for idle shell prompt
   → on non-zero exit: apply skill on_error policy
     (continue | stop | retry:N)

    │
    ▼
7. Record run in data/ghost.db
   (agent_name, status, started_at, finished_at, duration, log_path)

    │  if any step failed:
    ▼
8. Guard (if configured)
   → inject error context into ops tmux session
   → wait for GUARD_ACTION: retry | skip | abort | fixed
```

### Environment variables injected into every tool execution

| Variable | Value |
|---|---|
| `GHOST_PROJECT_ROOT` | Absolute path to the project folder |
| `GHOST_DATA_DIR` | `<project>/data/` |
| `GHOST_AGENT_NAME` | Name of the running agent |
| `GHOST_RUN_ID` | Unique run identifier |
| `GHOST_LOG_FILE` | Path to the current run log |

Tool implementation code uses `GHOST_PROJECT_ROOT` and `GHOST_DATA_DIR` to locate databases and output files without hardcoding paths.

### tmux session naming
- One tmux session per agent: `ghost-<project-basename>-<agent-name>`
- e.g. `ghost-news-briefing-news-collector`
- Session is created fresh each run; closed on completion

### Demo project trace (news-briefing)

```
08:00:00  briefing-generator trigger fires (cron 0 8 * * *)
08:00:00  resolve skill: generate-briefing
08:00:00  resolve steps: [run-notebooklm, send-briefing]
08:00:01  open tmux: ghost-news-briefing-briefing-generator
08:00:01  step 1: run-notebooklm
            cwd: tools/run-notebooklm/
            env: GHOST_PROJECT_ROOT=/path/to/news-briefing
                 GHOST_DATA_DIR=/path/to/news-briefing/data/
                 NOTEBOOKLM_API_KEY=***
            cmd: python run.py
            → queries data/news.db, calls notebooklm CLI
            → writes data/briefing.md
            → exit 0
08:01:32  step 2: send-briefing
            cwd: tools/send-briefing/
            cmd: python send.py
            → reads data/briefing.md
            → POST /api/briefings  HTTP 200
            → exit 0
08:01:47  run complete  duration=104s  status=success
08:01:47  record in data/ghost.db
08:01:47  log at .ghost/logs/briefing-generator/run_005.log
```

---

## What Changes

### Storage paths
| Old | New |
|---|---|
| `~/.gits/skills/*.md` | `<project>/skills/<name>/skill.md` |
| `~/.gits/tools/*.md` | `<project>/tools/<name>/tool.md` (or global) |
| `~/.gits/agents/<skill>/` | `<project>/.ghost/logs/<agent>/` |
| `~/.gits/gits.db` | `<project>/data/ghost.db` |
| `~/.gits/config.md` | `~/.config/ghost/config.yaml` |
| (none) | `<project>/agents/<name>.md` ← **new first-class concept** |

### Backend — AgentLoader (replaces SkillLoader for agents)
- Scan `<project>/agents/*.md` → `Agent` dataclasses (trigger, skills list, guard)
- Scan `<project>/skills/<name>/skill.md` → `Skill` dataclasses (steps, on_error)
- Scan `<project>/tools/<name>/tool.md` + `~/.config/ghost/tools/<name>/tool.md` → `Tool` dataclasses
- Inject `GHOST_*` env vars into every tool execution

### Backend — AgentRunner (replaces SkillRunner)
- `AgentRunner(project_root: Path)` — all paths derived from project root
- Schedules agents (not skills) as the top-level unit
- tmux session name: `ghost-<project-basename>-<agent-name>`
- Logs to `<project>/.ghost/logs/<agent>/<run-id>.log`
- DB at `<project>/data/ghost.db`
- `next_run_at(agent_name) → str | None`
- `reload(agents, skills, tools, shell_env)`

### Backend — WorkspaceManager
- Holds one `AgentRunner` + `GitsDB` per open project folder
- IPC: `workspace_add`, `workspace_remove`; persists to `~/.gits/workspace.json`
- Restores workspace on startup

### Backend — IPC changes
- `workspace_add / workspace_remove` — add/remove project folder
- `agents {}` — emit `agents_list`: agent definitions with `work_dir`, `next_run_at`, last run status
- `skills {}` — emit `skills_list`: skill definitions per folder
- `tools {}` — emit `tools_list`: tool definitions (local + global)
- `agent_create` — write `<project>/agents/<slug>.md`; hot-reload
- `agents {}` extended: includes `work_dir` + `next_run_at` per agent

### UI — Agents panel (redesign)
- Remove mock `AGENTS` constant; driven entirely by `agents_list` IPC
- Each Agent card: name, trigger schedule, next-run, last run status, project badge
- Drawer: steps trace (which skill → which tools), live log

### UI — Skills & Tools panels
- Skills panel: driven by `skills_list`, grouped by project folder
- Tool info shown inline in skill step rows (command, working dir)
- "＋ New Agent" modal: writes real `agents/<slug>.md` via `agent_create` IPC

### UI — Workspace folder chips
- 📁 picker → `workspace_add`; folders shown as removable chips
- On `workspace_changed`: all panels refresh

## Impact

- New specs: `skills-desktop-ui` (this spec), `agent-execution` (new)
- Modified specs: `terminal-ui-bridge` (IPC), `skill-runner` (renamed AgentRunner, new paths)
- New code: `src/gits/core/agent_loader.py`, `src/gits/core/agent_runner.py`, `src/gits/core/workspace.py`
- Modified: `src/gits/__main__.py`, `src/gits/storage/db.py`, `ui/app.js`, `ui/index.html`

## Out of Scope
- Skill/tool editing in UI (edit `.md` files directly in editor)
- Tool creation in UI
- AI-generated agent/skill content
