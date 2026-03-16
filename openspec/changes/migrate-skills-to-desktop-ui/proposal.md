# Change: Migrate Existing Skills & Tools to Desktop UI

## Why

Two problems need fixing:

### Problem 1 — Wrong storage layout
The current implementation stores skill and tool **definitions** in `~/.gits/skills/*.md` and `~/.gits/tools/*.md`, and writes execution **logs** to `~/.gits/agents/<skill>/`.

This is wrong on both counts:

- **Skills belong to their project.** A skill runs a command inside a specific working directory. The skill definition and all its runtime artifacts should live *inside that project's directory* under `.ghost/`.
- **`~/.gits/` should only hold global Ghost config** — not per-project definitions, logs, or DB files.

### Problem 2 — UI shows mock data, not real skills
The desktop UI does not surface real skill data:

1. **Skills panel left list** shows five hardcoded mock skills — the user's real skills only appear in a small secondary panel
2. **Skill detail view** shows run history from an in-memory mock, not from real data
3. **"＋ New Skill" modal** writes to memory only — nothing is saved to disk
4. **Runner cards** show last-run time but no schedule or next-run time
5. **Tool definitions** are never surfaced in the UI

## Storage Layout (new)

The app manages a **workspace** — one or more project folders open simultaneously (VS Code-style multi-root). Within each folder, **user assets are visible**; Ghost's internal runtime files are hidden.

```
~/.config/ghost/
  config.yaml                         ← Ghost global config
  tools/<name>.md                     ← globally shared tools (available to all projects)
  skills/<name>.md                    ← globally shared skills (available to all projects)

~/.gits/
  workspace.json                      ← persisted workspace: list of open folder paths

<folder>/
  agents/                             ← USER ASSET — Agent definitions
    <name>.md                            trigger + goal + skills/tools refs + guard policy
                                         multiple agents share the skills/ and tools/ below

  skills/                             ← USER ASSET — reusable step sequences (project-local)
    <name>.md                            referenced by name from agents

  tools/                              ← USER ASSET — atomic commands (project-local)
    <name>.md                            command + working directory + environment

  data/                               ← USER ASSET — databases and output files
    ghost.db
    <name>.csv  ...

  .ghost/                             ← Ghost-internal — hidden
    config.yaml                       ← per-project config overrides (optional)
    logs/<agent>/<run-id>.log
    logs/<agent>/current.log
```

**Resolution order when an Agent references a tool or skill by name:**
1. `<project>/tools/<name>.md` — project-local (wins)
2. `~/.config/ghost/tools/<name>.md` — global shared (fallback)

This means the Nash AI project and Discord project can each define their own local tools, but both can also share a common `discord-notify` or `send-email` tool installed globally — without duplicating the definition in every project.

**Three-tier hierarchy within a project:**
```
Agent  →  Skills  →  Tools
                  →  data/
Multiple Agents in one project share the same skills/ and tools/
```

**Global `~/.config/ghost/config.yaml`:**
```yaml
guard:
  ops_session: ghost-ops        # tmux session used as Guard for all projects
  timeout_minutes: 60           # max wait for Guard decision

logs:
  max_files: 30                 # max log files kept per skill
  max_age_days: 7               # delete logs older than this

runner:
  default_shell: zsh            # shell used to inherit environment (zsh | bash)
  default_on_failure: stop      # fallback if skill has no on_failure set
```

**Per-project `<folder>/.ghost/config.yaml`** (all fields optional — override global):
```yaml
guard:
  ops_session: my-project-ops   # override Guard session for this project only

logs:
  max_files: 50
  max_age_days: 14
```

**Asset ownership principle:**
- `skills/` and `data/` are **the user's work** — they created them, they own them, visible in Finder and in the Ghost UI
- `.ghost/` is **Ghost's housekeeping** — logs, internal config overrides — hidden by default, not user-facing
- Global tools and config (`~/.config/ghost/`) are installed once and shared across all projects

**Workspace model (VS Code-style):**
- 📁 folder picker **adds** a folder to the workspace (does not replace existing ones)
- Each folder has its own `SkillRunner` instance, `GitsDB` (in `data/`), and `.ghost/` directory
- The UI shows skills and data from **all open folders**, grouped by folder
- Workspace persisted in `~/.gits/workspace.json` — survives app restarts

## What Changes

### Storage paths
- `~/.gits/skills/*.md` → `<folder>/skills/` (user-visible asset)
- `~/.gits/tools/*.md` → `~/.config/ghost/tools/` (global tools registry)
- `~/.gits/agents/<skill>/` → `<folder>/.ghost/logs/<skill>/` (Ghost-internal, hidden)
- `~/.gits/gits.db` → `<folder>/data/ghost.db` (user-visible asset)
- `~/.gits/config.md` → `~/.config/ghost/config.yaml` (global Ghost config, YAML format)

### Backend — workspace manager (new)
- `WorkspaceManager` class: holds `dict[str, tuple[SkillRunner, GitsDB]]` keyed by folder path
- On `workspace_add`: create `SkillRunner(cwd=folder)` + `GitsDB(path=folder/.ghost/ghost.db)`, load + start; persist to `workspace.json`
- On `workspace_remove`: stop SkillRunner for that folder; remove from `workspace.json`
- On startup: restore folders from `workspace.json`

### Backend — SkillRunner / GitsDB / SkillLoader per-folder
- `SkillRunner.__init__(cwd: Path)` — all paths derived from `cwd`
- `SkillLoader` reads skills from `<cwd>/.ghost/skills/*.md`; tools from `~/.config/ghost/tools/` + optional `<cwd>/.ghost/tools/` overrides
- `GitsDB.__init__(path: Path)` — opens `<cwd>/.ghost/ghost.db`
- `_prepare_log_path()` writes to `<cwd>/.ghost/logs/<skill>/<run-id>.log`
- `SkillRunner.next_run_at(skill_name) → str | None` for Loop skills (croniter)
- `SkillRunner.reload(skills, tools, shell_env)` — cancel + reschedule

### Backend — New / modified IPC commands
- `workspace_add {work_dir}` → add folder → start its runner → emit `workspace_changed` + `skills_list` + `agents_list`
- `workspace_remove {work_dir}` → stop folder's runner → emit `workspace_changed`
- `tools {}` → scan global + local tools → emit `tools_list` (includes `scope:"global"|"local"`, `work_dir` for local)
- `skill_create {work_dir, name, trigger_type, schedule, steps}` → write `<work_dir>/.ghost/skills/<slug>.md` → hot-reload → emit `skill_created` + `skills_list`
- `skills {}` extended → each skill includes `work_dir` + `next_run_at`
- `agents {}` extended → each run includes `work_dir`

### UI — Workspace folder management
- 📁 button sends `workspace_add`; open folders displayed as removable chips below the folder button
- On `workspace_changed`: refresh Skills panel and Agents Runner section
- On startup: `workspace_changed` fires once with all restored folders

### UI — Skills Panel (complete redesign)
- Remove all hardcoded mock skills from `index.html` and `SKILLS` dict from `app.js`
- Left list populated from `skills_list`, **grouped by folder** (folder name as section header)
- Skill detail: folder badge, trigger (human-readable schedule + next run), steps with tool command/cwd, last 10 runs
- "＋ New Skill" modal has folder selector (dropdown of open workspace folders)

### UI — Runner Cards
- Each card shows a **folder badge** (project directory name)
- Add schedule line: Loop → "Weekdays 4:00 AM · next Mon 04:00"; Reactive → "● Always on"; paused → "⏸ Paused"

## Impact

- New specs: `skills-desktop-ui`
- Modified specs: `terminal-ui-bridge` (new IPC commands), `skill-runner` (paths, multi-instance)
- Modified code:
  - `src/gits/core/skill_runner.py` — `cwd` param, log paths → `.ghost/logs/`, `next_run_at()`, `reload()`
  - `src/gits/core/skill_loader.py` — read from `<cwd>/.ghost/skills/` + `~/.config/ghost/tools/`
  - `src/gits/storage/db.py` — `path` param → `<cwd>/.ghost/ghost.db`
  - `src/gits/__main__.py` — `WorkspaceManager`; `workspace_add/remove`, `tools`, `skill_create` handlers
  - `ui/app.js` — workspace chips; Skills panel with folder grouping; Runner cards with folder badge
  - `ui/index.html` — remove hardcoded mock skill `<div>` entries

## Out of Scope
- AI-generated skill content (modal uses a structured template, not LLM)
- Skill editing in UI (edit `.md` file directly)
- Tool creation in UI
