## 1. Mockup Updates (ui/index.html)

### 1a. Global chrome & navigation
- [ ] 1.1 Titlebar: add "⏳ 2 active" agent counter button (static); click shows popover listing active agents
- [ ] 1.2 Titlebar: workspace dropdown — replace color-only dots with icon + label (▶ Active / ⏸ Idle / ◼ Stopped)
- [ ] 1.3 Sidebar: add activity badge on Task button (static "2") and ⚠ badge variant for HITL-waiting state

### 1b. Code view
- [ ] 1.4 Pane headers: replace color dot with icon + label; active pane shows animated pulse dot + "Active"
- [ ] 1.5 Pane headers: idle pane shows "⏸ Idle", stopped pane shows "◼ Stopped" with dimmed content
- [ ] 1.6 Pane focus mode: double-click header → pane expands full-screen; show "⤢ Exit focus" button; Escape exits
- [ ] 1.7 Chat input placeholder: change to "Ask myproject…" (workspace-name aware)
- [ ] 1.8 Chat input: `/` keypress opens command palette (floating menu: /browse, /skill, /data, /status)
- [ ] 1.9 Code view empty state: center CTA "No sessions open. Open a project folder to start." + button

### 1c. Skill view
- [ ] 1.10 Skill run: clicking Run shows real-time output panel below the form (simulated streaming lines)
- [ ] 1.11 Skill run success: output panel shows "✓ Done · 2.3s" + "Copy output" + "View in Data →" link
- [ ] 1.12 Skill run failure: red output panel + auto-expanded "AI Debug" section with diagnosis text + "Apply fix in Code →" button
- [ ] 1.13 Run history: each entry has expand arrow showing full output; "Replay" button pre-fills the form
- [ ] 1.14 "＋ New Skill" button: opens a modal with natural-language description input

### 1d. Task view
- [ ] 1.15 Task status badges: add icon prefix (⏳ Running / ✓ Done / ✗ Failed / ○ Queued / ⚠ Needs input)
- [ ] 1.16 Task list empty state: "No tasks yet. Describe a web goal…" + "＋ New Task" + hint "/browse in any chat"
- [ ] 1.17 Cross-mode notification: toast component top-right; simulate by clicking "Mark done" on a running task

### 1e. Data view
- [ ] 1.18 Rename sidebar label from "Data" → "Data" (keep), but update section header inside view to "Outputs & Artifacts"
- [ ] 1.19 Left panel: replace raw table list with named collections grouped by source (Tasks / Skills / Manual)
- [ ] 1.20 Each collection shows: source icon, name, row count, last updated
- [ ] 1.21 Collection detail header: show "Source: Task — [task goal]" with clickable link
- [ ] 1.22 Presentation tabs: Table / Cards (add tab switcher above the grid)
- [ ] 1.23 Row detail drawer: already exists — verify it shows all fields un-truncated
- [ ] 1.24 Export button: add CSV download (mockup: shows browser save-as dialog with correct filename)
- [ ] 1.25 Data view empty state: "No data yet. Run a Task or Skill to start collecting."

### 1f. Cross-mode flow (simulated)
- [ ] 1.26 When a Task is marked done in mockup: inject a chat message in Code pane 0:
      "Browser task done: [goal] — saved btc_price.json → [View in Data]"
- [ ] 1.27 That "[View in Data]" link navigates to Data mode with the relevant collection highlighted

## 2. OpenSpec Validation

- [ ] 2.1 `openspec validate update-ghost-product-vision --strict`
- [ ] 2.2 Review `add-macos-desktop-app` tasks.md — mark items now covered by this change
- [ ] 2.3 Review `add-local-browser-agent` tasks.md — no changes expected

## 3. Backend (after Tauri shell exists)

- [ ] 3.1 Global activity model in `src/gits/core/engine.py` — expose active agent count + per-agent status
- [ ] 3.2 Wire to Tauri IPC — frontend subscribes to activity updates via event stream
- [ ] 3.3 Toast notification dispatch: Python emits event → Tauri → frontend renders toast
- [ ] 3.4 Sidebar badge counts via same IPC channel
- [ ] 3.5 `/` command routing: `/browse` → Task, `/skill` → Skill, `/data` → Data query, `/status` → popover
- [ ] 3.6 Skill real-time output: stream stdout lines from subprocess → IPC → output panel
- [ ] 3.7 Data view: query SQLite, return JSON; frontend decides table vs cards based on schema shape
- [ ] 3.8 AI Debug on Skill failure: send error + stack trace to Claude → stream response into debug panel
