# Design: Agent Dashboard

## Context

Ghost's desktop UI (`ui/`) is a vanilla TypeScript + DOM app (no React/Vue). All data arrives via IPC events from the Tauri/Python backend (`window.ghost.send` / `window.ghost.on`). The existing four-view layout (Code / Agent / Skill / Data) is implemented in `ui/src/views/` with a shared `state.ts`.

The dashboard introduces a **widget registry pattern** — each widget type registers a `render(config, data) → HTMLElement` function. The dashboard engine composes these into a grid. This keeps new widget types addable without touching the grid layout code.

## Goals / Non-Goals

- **Goals:** Zero context-switching when monitoring an agent; inline HITL review; pipeline progress visibility; file/image output browsable without leaving the agent view
- **Non-Goals:** Drag-and-drop layout; framework migration; cross-agent aggregation; mobile/responsive layout

## Decisions

### Widget registry (not switch/case)
Each widget type exports `{ type, defaultSize, render }`. The dashboard iterates `widgets[]` and calls `registry[w.type].render(w)`. Adding a new widget type = new file + one registry entry in `dashboard.ts`.

**Alternative considered:** large switch/case in dashboard render. Rejected — becomes unwieldy past 4 types.

### State: `widget.state` drives visual only
`running | review | done | idle` is a display concern (border color, badge). Business logic (what triggers a state transition) stays in the IPC layer. The widget renders what it's told.

**Alternative considered:** widget polling its own data. Rejected — all data flows inward via IPC push; widgets are dumb renderers.

### `files` widget: gallery vs list auto-detect
The widget inspects `FileEntry.mimeType`: any `image/*` → gallery; otherwise list. User can override via `config.viewMode`. This avoids a configuration step for the common case.

### Pipeline stages are optional
`AgentDashboard.pipeline` is optional. Agents without pipeline config get a plain widget grid with no progress bar. Pipeline is additive, not required.

### Library tab: wrap, don't rewrite
`#view-skill` and `#view-data` remain as-is. `#view-library` is a thin shell that shows a tab bar and toggles visibility between the two existing views. This preserves all existing functionality with ~30 lines of new code.

### Dashboard persistence
`~/.gits/dashboards/<agentId>.json` stores widget list, sizes, and config (not runtime state). On load, if no file exists for an agent, a default layout is inferred from the agent's known skills and data outputs (same logic as AI suggestion — heuristic, not LLM call).

## Risks / Trade-offs

- **`migrate-skills-to-desktop-ui` dependency:** The dashboard needs real `agentId` values from `agents_list` IPC. If that change ships later, the dashboard falls back to demo data (same pattern as current mock AGENTS constant).
- **Widget count stays at 4:** More widget types will be requested. Registry pattern ensures they're easy to add without refactoring.

## Open Questions

- Should `conversation` widget persist message history locally, or only show the current session's messages? (Current assumption: current session only, no persistence)
- `files` widget for audio (MP3): inline `<audio>` player or link-out to system player? (Current assumption: inline player, simple `<audio controls>`)
