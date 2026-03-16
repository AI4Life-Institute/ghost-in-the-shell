# migrate-skills-to-desktop-ui — Tasks

## 1. Backend — per-folder storage paths

- [ ] 1.1 Add `cwd: Path` param to `SkillRunner.__init__()`; derive `logs_dir = cwd / ".ghost" / "logs"` (hidden); remove global `AGENTS_DIR` constant
- [ ] 1.2 Update `_prepare_log_path()`: write to `<cwd>/.ghost/logs/<skill>/<run-id>.log`
- [ ] 1.3 Update `_update_current_log()` and `_list_log_files()` to use `cwd`-derived paths
- [ ] 1.4 Add `path: Path` param to `GitsDB.__init__()`; default to `cwd / "data" / "ghost.db"` (user-visible); replace hardcoded `~/.gits/gits.db`
- [ ] 1.5 Update `SkillLoader`: read skills from `<cwd>/skills/*.md` (user-visible); read tools from `~/.config/ghost/tools/*.md` (global)
- [ ] 1.6 Add `GhostConfig` dataclass: load `~/.config/ghost/config.yaml` (global), deep-merge with `<cwd>/.ghost/config.yaml` (per-project override, optional); fields: `guard.ops_session`, `guard.timeout_minutes`, `logs.max_files`, `logs.max_age_days`, `runner.default_shell`, `runner.default_on_failure`
- [ ] 1.7 Replace hardcoded constants (`MAX_LOG_FILES=30`, `MAX_LOG_AGE_DAYS=7`, guard timeout `3600s`, default shell `zsh`) with values from `GhostConfig`
- [ ] 1.8 Remove all `~/.gits/skills/`, `~/.gits/tools/`, `~/.gits/agents/`, `~/.gits/gits.db`, `~/.gits/config.md` references from all code paths

## 2. Backend — WorkspaceManager

- [ ] 2.1 Create `WorkspaceManager` class in `src/gits/core/workspace.py`: holds `dict[str, WorkspaceFolder]` keyed by resolved folder path; each entry has `skill_runner: SkillRunner`, `db: GitsDB`
- [ ] 2.2 `WorkspaceManager.add(folder: Path, shell_env, emit_fn)`: create + start `SkillRunner(cwd=folder)` + `GitsDB(path=folder/.ghost/ghost.db)`; persist to `~/.gits/workspace.json`
- [ ] 2.3 `WorkspaceManager.remove(folder: Path)`: stop SkillRunner; remove from persistence
- [ ] 2.4 `WorkspaceManager.restore(shell_env, emit_fn)`: on startup, read `~/.gits/workspace.json` and call `add()` for each saved folder (skip folders that no longer exist)
- [ ] 2.5 `WorkspaceManager.all_runners() → list[SkillRunner]` and `all_dbs() → list[GitsDB]` for broadcast IPC responses

## 3. Backend — SkillRunner extensions

- [ ] 3.1 Add `SkillRunner.next_run_at(skill_name: str) → str | None`: for Loop/cron use `croniter`, for Loop/interval compute from `last_started_at + interval`; return ISO string or `None` if paused/reactive
- [ ] 3.2 Add `SkillRunner.reload(skills, tools, shell_env)`: cancel all existing scheduled tasks; re-load with new skill/tool set and restart

## 4. Backend — IPC command handlers (workspace)

- [ ] 4.1 Add `workspace_add` handler: receives `{work_dir}`; calls `WorkspaceManager.add()`; emits `workspace_changed {folders:[...]}` + `skills_list` + `agents_list`
- [ ] 4.2 Add `workspace_remove` handler: receives `{work_dir}`; calls `WorkspaceManager.remove()`; emits `workspace_changed`
- [ ] 4.3 On `gits desktop` startup: call `WorkspaceManager.restore()`; emit `workspace_changed` with restored folders

## 5. Backend — IPC command handlers (skills/tools/agents)

- [ ] 5.1 Add `tools` handler: scan `~/.config/ghost/tools/*.md` + per-folder `<cwd>/.ghost/tools/*.md` across all workspace folders; emit `tools_list {tools:[{name, description, command, working_directory, environment, scope, work_dir}]}`
- [ ] 5.2 Extend `skills` handler: aggregate across all workspace folders; include `work_dir` and `next_run_at` per skill in `skills_list`
- [ ] 5.3 Extend `agents` handler: aggregate runs across all folder DBs; include `work_dir` per run in `agents_list`
- [ ] 5.4 Add `skill_create` handler: args `{work_dir, name, description, trigger_type, schedule, steps, on_failure}`; slugify name; write `<work_dir>/.ghost/skills/<slug>.md` in canonical format; call `WorkspaceFolder.skill_runner.reload()`; emit `skill_created` + updated `skills_list`

## 6. UI — Workspace folder management

- [ ] 6.1 On `workspace_changed` event: update displayed folder chips (add `work_dir` to `openFolders` set; remove on `workspace_remove`)
- [ ] 6.2 Wire `folderPickerClick()`: on directory selected, send `workspace_add {work_dir}`
- [ ] 6.3 Render open folders as removable chips near the 📁 button (folder name + ✕ button → sends `workspace_remove`)
- [ ] 6.4 On startup: request `skills`, `agents`, `tools` after receiving first `workspace_changed`

## 7. UI — Skills Panel rebuild

- [ ] 7.1 Remove hardcoded `SKILLS` dict, `SKILLS` mock data, and all five static `<div class="ski">` entries from `index.html` + `app.js`
- [ ] 7.2 On `skills_list`: populate left list grouped by folder — section header per folder (directory basename), then skill items with name + description snippet + Loop/Reactive badge
- [ ] 7.3 `selSkill(el, skillName, workDir)`: look up `skillDefs[workDir][skillName]`
- [ ] 7.4 Skill detail: folder badge showing directory name; trigger section (Loop → human-readable cron + "next Mon 04:00", Reactive → "● Always on · restarts on exit")
- [ ] 7.5 Skill detail steps: for each step, show tool command + working_directory from `toolDefs` if found; else show raw step string; unknown tool shows name + "(not found)"
- [ ] 7.6 Skill detail run history: last 10 runs from `runnerAgents` filtered by `skill_name + work_dir`; columns: status dot, start time, duration, "View Log" link
- [ ] 7.7 On `tools_list`: populate `toolDefs` map `{name → tool}`

## 8. UI — Runner Cards improvements

- [ ] 8.1 Add folder badge to each runner card (directory basename of `work_dir`)
- [ ] 8.2 Add schedule + next-run line: Loop → "Weekdays 4:00 AM · next Mon 04:00"; Reactive → "● Always on"; paused → "⏸ Paused"
- [ ] 8.3 In runner drawer steps: show tool command + working_directory from `toolDefs`

## 9. UI — New Skill Modal

- [ ] 9.1 Replace mock `generateNewSkill()` with structured form: Name, Description, Trigger type (Loop/Reactive), Schedule (cron input, Loop only), Steps (comma-separated tool names or commands), Folder (dropdown of `openFolders`)
- [ ] 9.2 Replace `saveNewSkill()`: send `skill_create {work_dir, name, ...}` IPC; show spinner
- [ ] 9.3 On `skill_created`: close modal; show toast; skills list auto-refreshes from `skills_list` event
- [ ] 9.4 On `error` (from skill_create): show inline error in modal without closing

## 10. Migration helper (one-time)

- [ ] 10.1 On `gits desktop` first run: if `~/.gits/skills/*.md` exist, print a notice pointing to the new `.ghost/skills/` location; do NOT auto-move (user may have multiple projects)
- [ ] 10.2 Document new layout in `README.md`: `<project>/.ghost/skills/` for skill definitions, `~/.config/ghost/tools/` for global tools

## 11. Validation

- [ ] 11.1 Add folder via 📁 → `<folder>/.ghost/skills/` scanned → skills appear grouped under that folder in Skills panel
- [ ] 11.2 Add a second folder → both folders' skills appear in Skills panel under separate section headers
- [ ] 11.3 Skill detail shows trigger schedule, steps with tool command/cwd, run history
- [ ] 11.4 Runner card shows folder badge + schedule + next-run time
- [ ] 11.5 Create skill via "＋ New Skill" → file written to `<selected_folder>/.ghost/skills/<slug>.md`
- [ ] 11.6 Run a skill → log written to `<folder>/.ghost/logs/<skill>/current.log`; `.ghost/` is a hidden directory not visible in Finder by default
- [ ] 11.7 Unit test: `SkillRunner.next_run_at()` returns correct next datetime for cron Loop skill
- [ ] 11.8 Unit test: `WorkspaceManager.restore()` re-adds saved folders on startup
