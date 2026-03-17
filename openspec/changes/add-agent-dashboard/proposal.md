# Change: Add Agent Dashboard with Widget Grid

## Why

The current UI splits related information across three separate views — Agent (fleet cards), Skill (list + detail), and Data (file tree + table). When monitoring a running agent the user must manually cross-reference all three views to understand what happened, what was produced, and whether action is needed. For multi-step pipelines (e.g. Midjourney → ComfyUI → video splice) there is no way to track progress or perform human-in-the-loop review without switching views repeatedly.

## What Changes

### Navigation (3 tabs, down from 4)
- **Code** tab — unchanged (tmux terminal panes)
- **Agents** tab — replaced with Agent Dashboard (widget grid per agent)
- **Library** tab — new: merges existing Skill and Data views under tab switcher; code is kept, only the nav entry point changes
- Skill and Data are removed as top-level sidebar modes; they live inside Library

### Agent Dashboard (new)
- Left panel: agent list with real-time status dots + overview shortcut
- Right panel: widget grid (4-column, 160 px row height) for the selected agent
- Dashboard header: agent name, status, action buttons, and optional pipeline progress bar
- Widget layout persisted to `~/.gits/dashboards/<agentId>.json`

### Widget System (new — 4 types)
| Type | Purpose | Sizes |
|------|---------|-------|
| `conversation` | Two-way chat with agent; terminal-tail toggle | 2×1, 2×2 |
| `chart` | DB table view ↔ chart view; live refresh | 2×1, 2×2 |
| `compute` | Claude output rendered as Markdown; streaming | 2×1, 2×2 |
| `files` | File output — gallery (images) or list (PDF/audio) | 2×2 |

### Widget Interaction Model
- Each file/image item has 2–3 action buttons rendered inline (no context menu)
- Buttons are configured per-agent via `PipelineAction` (label + IPC event + payload)
- Widget `state` field drives visual mode: `running` (live border) / `review` (amber border + action bar) / `done` / `idle`
- Multi-select in `files` widget surfaces a batch action bar at the bottom

### Pipeline Mode (new)
- Agents can declare ordered `PipelineStage[]` in their dashboard config
- Pipeline progress bar appears in dashboard header when stages are defined
- Each stage binds to one widget; user completes the stage by interacting with that widget
- Clicking a past stage navigates back to its widget

### Library View (consolidation)
- Single `Library` tab replaces separate `Skill` and `Data` sidebar entries
- Two sub-tabs inside: **Skills** (existing `#view-skill` content) and **Data** (existing `#view-data` content)
- Widgets in agent dashboards can link to Library entries ("View all →")

## Impact

- **New specs:** `agent-dashboard`, `desktop-ui` (navigation layout)
- **Modified specs:** none (Skill/Data views are structurally unchanged; only nav changes)
- **Depends on:** `migrate-skills-to-desktop-ui` for real IPC data (dashboard reads same `agents_list`, `skills_list`, `db_query` events)
- **Affected files:**
  - `ui/index.html` — sidebar modes, new `#view-library`, `#view-agents` redesign
  - `ui/src/views/dashboard.ts` — new: widget registry + render engine
  - `ui/src/views/library.ts` — new: wraps existing skill/data views with tab switcher
  - `ui/src/views/mode.ts` — 4 modes → 3 modes
  - `ui/src/state.ts` — add `curDashboardAgentId`, `agentDashboards`
  - `ui/src/types.ts` — add `Widget`, `AgentDashboard`, `PipelineStage`, `PipelineAction`
  - `~/.gits/dashboards/` — new persistence directory

## Out of Scope

- Drag-and-drop widget reordering (post-MVP)
- AI-generated widget suggestions
- Video playback in `files` widget (thumbnail only for now)
- Cross-agent widgets (one widget showing data from multiple agents)
