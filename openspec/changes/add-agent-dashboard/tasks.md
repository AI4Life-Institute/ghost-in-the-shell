## 1. Types & State

- [x] 1.1 Add `Widget`, `WidgetType`, `WidgetSize`, `WidgetState`, `AgentDashboard`, `PipelineStage`, `PipelineAction`, `FilesConfig`, `ComputeConfig` to `ui/src/types.ts`
- [x] 1.2 Add `curDashboardAgentId: string` and `agentDashboards: Record<string, AgentDashboard>` to `ui/src/state.ts`
- [x] 1.3 Add dashboard persistence helpers: `loadDashboard(agentId)` and `saveDashboard(agentId, dashboard)` to a new `ui/src/dashboard-store.ts`

## 2. Navigation Refactor (3 tabs)

- [x] 2.1 Update `ui/index.html` sidebar: rename Agent → Agents, replace Skill + Data entries with single Library entry
- [x] 2.2 Update `ui/src/views/mode.ts`: handle `'library'` mode, remove standalone `'skill'` and `'data'` modes
- [x] 2.3 Add `#view-library` shell to `ui/index.html` with Skills / Data tab switcher that toggles existing content

## 3. Library View

- [x] 3.1 Create `ui/src/views/library.ts` with `initLibrary()`, `switchLibraryTab()`, and `linkToLibrary()` functions
- [x] 3.2 Wire existing skill/data views through library tab state

## 4. Widget Registry & Grid

- [x] 4.1 Create `ui/src/views/dashboard.ts` with widget registry interface
- [x] 4.2 Implement `renderDashboard(agentId)`: 4-column CSS grid, widget registry calls
- [x] 4.3 Implement `renderOverview()`: summary cards with status dots, progress bars

## 5. Agent List Panel

- [x] 5.1 Add left-panel HTML to `#view-agents` in `ui/index.html`
- [x] 5.2 Implement `renderAgentList()` in `dashboard.ts`
- [x] 5.3 Wire `selectDashboardAgent(agentId)` click handlers

## 6. Dashboard Header & Pipeline Bar

- [x] 6.1 Render dashboard header: agent name, status pill, Stop/Pause buttons
- [x] 6.2 Render pipeline progress bar with stage labels, status icons, click-to-navigate

## 7. Conversation Widget

- [x] 7.1 Implement `ConversationRenderer`: chat-bubble layout, user input, send button
- [x] 7.2 Add HITL message type: agent message with inline confirm/skip buttons that emit IPC on click
- [x] 7.3 Implement terminal-tail toggle: replace chat view with scrolling `<pre>` + blinking cursor
- [x] 7.4 Wire `conversation_message` IPC event → `appendConvMessage()` in `ipc.ts`
- [x] 7.5 Wire `agent_log` IPC event → `appendTailLine()` for active tail view in `ipc.ts`

## 8. Chart Widget

- [x] 8.1 Implement `ChartRenderer`: table preview + "View all →" link + chart view toggle
- [x] 8.2 Auto-detect default view: check `DB[table].cols` for timestamp column names
- [x] 8.3 Render chart view using SVG polyline (no external lib)
- [x] 8.4 Add `···` config menu: X/Y field pickers, chart type selector — inline dropdown, closes on outside click
- [x] 8.5 Wire `db_write` IPC event → `refreshDashboardChart()` in `ipc.ts`

## 9. Compute Widget

- [x] 9.1 Implement `ComputeRenderer`: regex-based Markdown renderer
- [x] 9.2 Streaming: wire `compute_chunk` IPC event → `appendComputeChunk()` with blinking cursor
- [x] 9.3 Review actions: `review` state → amber border + action buttons → emit IPC on click

## 10. Files Widget

- [x] 10.1 Implement `FilesRenderer`: auto-detect gallery vs list from MIME types
- [x] 10.2 Gallery mode: 4-column thumbnail grid, inline action buttons, NEW badge
- [x] 10.3 List mode: rows with filename/size/timestamp, inline action buttons
- [x] 10.4 Selectable mode: click-to-toggle selection, batch action bar
- [x] 10.5 PDF preview: "Preview" button → `dashboardFilePdfPreview()` → `file_thumbnail` IPC → inline thumbnail
- [x] 10.6 Audio player: `<audio controls>` inline in list row for MP3/audio files
- [x] 10.7 Wire `file_created` IPC event → `prependDashboardFile()` with NEW badge

## 11. Default Layout Inference

- [x] 11.1 Implement `inferDefaultDashboard(agentId)`:
  - Always adds `conversation` (2×1)
  - Adds `chart` (2×1) bound to agent's primary table
  - Auto-detects timestamp columns → defaults chart to chart view

## 12. Validation & Polish

- [x] 12.1 Run `openspec validate add-agent-dashboard --strict` → "Change is valid"
- [x] 12.2 Verify sidebar mode switching: Code / Agents / Library all work; 79/79 tests pass
- [x] 12.3 Widget persistence: `localStorage` via `dashboard-store.ts`; `inferDefaultDashboard` saves on first load
- [x] 12.4 Demo data: 5 agent scenarios preloaded in `ui/src/data/demo-dashboards.ts`
  - btc-monitor (tail log + live chart)
  - nash-reporter (chat + PDF list with preview)
  - discord-digest (tail + compute review with approve/discard)
  - hn-digest (chat + table view)
  - fanvue-cloner (tail + selectable gallery + pipeline bar)

## 13. Widget Catalog Files

- [x] 13.1 `~/.config/ghost/widgets/` — 4 catalog files (conversation, chart, compute, files)
- [x] 13.2 `~/.config/ghost/dashboard-samples/` — 5 sample files
- [x] 13.3 Each sample has `_description` field

## 14. `ghost dashboard` CLI

- [x] 14.1 `cli/ghost-dashboard/src/index.ts` — `generate` + `validate` subcommands
- [x] 14.2 `generate`: loads agent context file
- [x] 14.3 Prompt builder: widget catalog + sample dashboards + agent context
- [x] 14.4 Calls `claude-opus-4-6`, parses JSON response
- [x] 14.5 Auto-validates after generate; only writes if valid
- [x] 14.6 `--dry-run` flag: prints JSON to stdout
- [x] 14.7 `validate`: 6 checks, structured ✓/✗ output, exit code 0/1
- [x] 14.8 Unit tests: 7 passing (valid config, unknown type, missing pipeline widgetId, empty widgets, invalid size, valid pipeline, missing agentId)
