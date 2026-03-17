## ADDED Requirements

### Requirement: Agent Dashboard Layout
The Agents view SHALL display a two-panel layout: an agent list panel on the left and a widget grid panel on the right.

- The left panel SHALL list all known agents with a real-time status dot (running / warn / idle) and a one-line status subtitle
- The left panel SHALL include an Overview entry at the top that shows all agents as summary cards
- The left panel SHALL include a "+ New Agent" entry at the bottom
- The right panel SHALL display a header with: agent name, status pill, action buttons (Stop / Pause), and an optional pipeline progress bar
- The right panel SHALL display a 4-column widget grid with rows of 160 px height
- The selected agent SHALL be highlighted in the left panel

#### Scenario: User selects an agent
- **WHEN** the user clicks an agent in the left panel
- **THEN** that agent's widget grid is displayed in the right panel and its status dot reflects the current run state

#### Scenario: User opens Overview
- **WHEN** the user clicks the Overview entry
- **THEN** summary cards for all agents are shown in the right panel, each displaying name, status, widget chips, and a progress bar

---

### Requirement: Widget System
The Agent Dashboard SHALL render agent output via a widget grid composed of typed, configurable widgets.

- Each widget SHALL have: `id`, `type`, `size` (`2x1` | `2x2`), `agentId`, `state` (`running` | `review` | `done` | `idle`), and `config`
- The widget registry SHALL support four types: `conversation`, `chart`, `compute`, `files`
- Widget layout SHALL be persisted to `~/.gits/dashboards/<agentId>.json` (config only, not runtime state)
- If no persisted layout exists for an agent, a default layout SHALL be inferred from the agent's known skills and data outputs

#### Scenario: Widget state drives visual
- **WHEN** a widget's `state` is set to `review`
- **THEN** the widget border turns amber and any configured action buttons are shown
- **WHEN** a widget's `state` is set to `running`
- **THEN** the widget border pulses and a live badge is displayed

---

### Requirement: Conversation Widget
The `conversation` widget SHALL provide two-way interaction between the user and the agent, with a terminal-tail toggle.

- The widget SHALL display agent messages and user messages in a chat-bubble layout
- The widget SHALL include a text input and send button at the bottom
- HITL approval requests SHALL appear as agent messages with inline action buttons (no modal)
- A toggle in the widget header SHALL switch between conversation mode and terminal-tail mode
- Terminal-tail mode SHALL display the last N lines of raw agent output (`tail -f` style) in a monospace font with a blinking cursor on the last line

#### Scenario: HITL inline approval
- **WHEN** the agent emits a HITL request (e.g. "Confirm publish to Discord?")
- **THEN** the message appears in the conversation widget with confirm and skip buttons
- **WHEN** the user clicks confirm
- **THEN** the approval event is sent via IPC and the buttons are replaced with a confirmation message

#### Scenario: Terminal-tail toggle
- **WHEN** the user clicks the tail toggle in the widget header
- **THEN** the chat view is replaced by a scrolling raw-log view showing the last 200 lines
- **WHEN** the user toggles back to conversation mode
- **THEN** the chat view is restored

---

### Requirement: Chart Widget
The `chart` widget SHALL display data from a single DB table in either table view or chart view.

- The widget SHALL default to table view for text-dominant columns and chart view for tables with a timestamp column and at least one numeric column
- The widget SHALL provide a table ↔ chart toggle in the header
- Table view SHALL show up to 5 rows with a "View all N rows →" link to Library → Data
- Chart view SHALL render a line chart; bar and scatter SHALL be selectable via a `···` config menu
- The X-axis field, Y-axis field, and time range (1h / 24h / 7d / all) SHALL be configurable
- The widget SHALL automatically refresh when new rows are written to the bound table (live badge)

#### Scenario: Auto chart for time-series data
- **WHEN** the bound table has a `timestamp` column and a numeric column
- **THEN** the widget defaults to chart view with timestamp as X-axis and the first numeric column as Y-axis

#### Scenario: Live refresh
- **WHEN** the backend emits a `db_write` event for the bound table
- **THEN** the widget re-fetches and re-renders within 2 seconds without full page reload

---

### Requirement: Compute Widget
The `compute` widget SHALL render Claude-generated text as Markdown, with streaming support.

- The widget SHALL render Markdown including headings, bold, lists, and code blocks
- While the agent is streaming output, the widget SHALL show a blinking cursor at the end of the current content
- When `reviewActions` are configured, the widget SHALL display action buttons below the content (e.g. "Confirm", "Regenerate", "Copy")
- A copy-to-clipboard button SHALL always be available

#### Scenario: Streaming output
- **WHEN** the agent begins emitting streamed text via IPC
- **THEN** the widget renders each chunk as it arrives with a blinking cursor at the end

#### Scenario: Review action
- **WHEN** the widget has `reviewActions` configured and the content is fully generated
- **THEN** the widget border turns amber (review state) and action buttons are displayed
- **WHEN** the user clicks an action button
- **THEN** the corresponding IPC event is emitted with the configured payload

---

### Requirement: Files Widget
The `files` widget SHALL display file outputs from an agent in either gallery mode (images) or list mode (documents/audio).

- The widget SHALL auto-detect mode: any `image/*` MIME type → gallery; otherwise list
- The user SHALL be able to override the detected mode via a toggle
- **Gallery mode:** items SHALL be displayed in a 4-column thumbnail grid; each item SHALL have 2–3 inline action buttons below the thumbnail; newly arrived files SHALL be marked with a "NEW" badge
- **List mode:** items SHALL be displayed as rows with filename, size, timestamp, and 2–3 inline action buttons
- Action buttons SHALL be configured via `PipelineAction[]` in `config.actions` (label + IPC event + optional payload); the widget does not hardcode button behavior
- When `config.selectable` is true, items SHALL be individually selectable (click to toggle); a batch action bar SHALL appear at the bottom when at least one item is selected
- The batch action bar SHALL show a count of selected items and the configured batch actions

#### Scenario: Gallery item action
- **WHEN** the user clicks an action button on a gallery item (e.g. "✓ Use")
- **THEN** the configured IPC event is emitted with the file's path and any configured payload
- **THEN** the button is visually confirmed (e.g. label changes to "✓ Used")

#### Scenario: Batch selection and submit
- **WHEN** the user selects 3 images in a selectable gallery
- **THEN** the batch action bar appears showing "3 selected" and the configured batch action buttons
- **WHEN** the user clicks a batch action (e.g. "Submit to RunningHub")
- **THEN** the IPC event is emitted with all selected file paths

#### Scenario: PDF preview
- **WHEN** the user clicks "Preview" on a PDF item in list mode
- **THEN** the first page of the PDF is displayed as a thumbnail inline in the widget

---

### Requirement: Pipeline Mode
The Agent Dashboard SHALL support optional multi-stage pipeline display for agents with sequential human-review steps.

- An `AgentDashboard` MAY declare an ordered `PipelineStage[]`
- When stages are defined, the dashboard header SHALL show a pipeline progress bar with stage labels and status icons (✓ done / ⚠ review / ○ todo)
- Each stage SHALL reference a widget by `widgetId`; when the widget transitions to `done`, the stage is marked complete
- The user SHALL be able to click any completed stage to navigate back and inspect its widget output

#### Scenario: Pipeline advances on widget completion
- **WHEN** the user completes a review action in a `files` widget (e.g. submits selected images)
- **THEN** that widget's state transitions to `done`, the corresponding stage is marked ✓ in the progress bar, and the next stage's widget transitions to `running`

#### Scenario: Navigate to past stage
- **WHEN** the user clicks a completed stage in the pipeline bar
- **THEN** the view scrolls to or highlights the widget bound to that stage

---

### Requirement: Dashboard Generate CLI
The system SHALL provide a `ghost dashboard generate <agent-id>` command that uses Claude to produce a widget layout for an agent.

The command SHALL build a prompt containing three sections in order:
1. **Widget catalog** — for each supported widget type: type name, config schema, supported sizes, and when to use it
2. **Sample dashboards** — 3–5 reference layouts covering common agent patterns (loop+data, browser+files, pipeline+review); loaded from `~/.config/ghost/dashboard-samples/`
3. **Agent context** — the agent's definition file, its skill definitions, known DB tables it writes to, and known output directories

The command SHALL call Claude (model: `claude-opus-4-6`) with this prompt and parse the JSON response into an `AgentDashboard` object.

The command SHALL run `ghost dashboard validate <agent-id>` automatically after generation and report any issues before writing.

The command SHALL write the result to `~/.gits/dashboards/<agentId>.json`, overwriting any existing layout (with a `--dry-run` flag to preview without writing).

#### Scenario: Generate for a loop agent
- **WHEN** `ghost dashboard generate btc-monitor` is run
- **THEN** Claude receives the widget catalog, sample dashboards, and btc-monitor's agent definition
- **THEN** the generated layout includes a `conversation` widget and a `chart` widget bound to `btc_prices`
- **THEN** the layout is validated and written to `~/.gits/dashboards/btc-monitor.json`

#### Scenario: Generate for a pipeline agent
- **WHEN** `ghost dashboard generate mtv-agent` is run and the agent definition references music generation, image selection, and video splicing steps
- **THEN** the generated layout includes a `PipelineStage[]` with stages matching those steps
- **THEN** the `files` widget for image selection has `selectable: true` and appropriate `actions`

#### Scenario: Dry run
- **WHEN** `ghost dashboard generate <id> --dry-run` is run
- **THEN** the generated JSON is printed to stdout but not written to disk

---

### Requirement: Dashboard Validate CLI
The system SHALL provide a `ghost dashboard validate <agent-id>` command that checks a dashboard config for correctness.

The command SHALL validate:
- All `widget.type` values are in the supported registry (`conversation` | `chart` | `compute` | `files`)
- All `widget.size` values are valid (`2x1` | `2x2`)
- `chart` widgets: `config.table` references a DB table that exists in the project's `data/` directory
- `files` widgets: `config.dir` is a non-empty string
- `pipeline` stages (if present): each `widgetId` references a widget that exists in the layout
- No two widgets share the same `id`

The command SHALL print a structured report: ✓ for each passing check, ✗ with a message for each failure.

The command SHALL exit with code 0 if valid, code 1 if any check fails.

#### Scenario: Valid dashboard
- **WHEN** `ghost dashboard validate btc-monitor` is run on a correct config
- **THEN** all checks print ✓ and the command exits with code 0

#### Scenario: Invalid widget type
- **WHEN** the dashboard contains a widget with `type: "unknown"`
- **THEN** the validate command prints `✗ widget <id>: unknown type "unknown"` and exits with code 1

#### Scenario: Missing pipeline widget reference
- **WHEN** a `PipelineStage` references a `widgetId` that does not exist in the layout
- **THEN** the validate command prints `✗ pipeline stage <id>: widgetId "<ref>" not found` and exits with code 1

---

### Requirement: Widget Catalog Files
The system SHALL maintain a widget catalog as structured files that both the CLI and the UI can consume.

- Widget catalog files SHALL live at `~/.config/ghost/widgets/<type>.md`, one file per widget type
- Each catalog file SHALL contain: description, config schema (as a TypeScript interface comment), supported sizes, and a "when to use" section
- Sample dashboard files SHALL live at `~/.config/ghost/dashboard-samples/<name>.json`, one file per sample
- Each sample SHALL be a valid `AgentDashboard` JSON with a `_description` field explaining what agent pattern it represents
- The `ghost dashboard generate` command SHALL load catalog and sample files at runtime (not hardcoded), so new widget types can be added by dropping in a new catalog file

#### Scenario: New widget type added via catalog file
- **WHEN** a new file `~/.config/ghost/widgets/metric.md` is created
- **THEN** `ghost dashboard generate` includes `metric` in the widget catalog section of the Claude prompt on the next run
