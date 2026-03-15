## 1. Mockup Updates (ui/index.html)

### 1a. Global chrome
- [ ] 1.1  Titlebar: replace workspace dropdown with project directory path button (shows `~/myproject`)
- [ ] 1.2  Titlebar: add "⏳ 2 active" agent counter; click shows popover listing all active agents
- [ ] 1.3  Sidebar: rename modes → Build / Agents / Skills / Data with new icons
- [ ] 1.4  Sidebar: Agents button shows count badge `[3]`; `⚠` variant when agent waiting for input
- [ ] 1.5  Sidebar: remove "Task" button entirely

### 1b. Build mode (was Code)
- [ ] 1.6  Rename "Code" view → "Build"; update all labels and IDs
- [ ] 1.7  Pane headers: replace color-only dot with icon + label (▶ Active pulse / ⏸ Idle / ◼ Stopped)
- [ ] 1.8  Pane focus mode: double-click header → full-screen expand; "⤢ Exit focus" button; Escape exits
- [ ] 1.9  Chat input placeholder: "Build something in ~/myproject…"
- [ ] 1.10 Chat input: `/` keypress opens command palette (/agent, /skill, /data, /status)
- [ ] 1.11 Build empty state: centered input "What do you want to build?" with example hints

### 1c. Agents mode (replaces Task)
- [ ] 1.12 Replace Task view entirely with new Agents view
- [ ] 1.13 Left panel: three groups — Browser (by Chrome profile), Loop, Reactive
- [ ] 1.14 Browser group: show Chrome profiles (Personal, Work, nash-ai); each lists its Agents below
- [ ] 1.15 Browser Agent detail: show "🌐 Real Chrome · [profile]" badge prominently in header
- [ ] 1.16 Browser Agent detail: add explainer line "Your sessions. Your cookies. No re-logging in."
- [ ] 1.17 Loop Agent item: show schedule ("Every 60 min") + next run time + last run status
- [ ] 1.18 Reactive Agent item: show trigger source + connection status dot + label
- [ ] 1.19 Agent detail panel: status (icon+label), live execution log, Run Now / Pause / Delete controls
- [ ] 1.20 Agent detail panel: "View outputs in Data →" link
- [ ] 1.21 Simulate auto-repair: failed Loop Agent shows "🤖 Auto-repaired by Build Agent" badge
- [ ] 1.22 Agents empty state: "No agents running yet. Ask the Build agent to create one." + link to Build

### 1d. Skills mode
- [ ] 1.23 Skill run: real-time output panel below form (simulated streaming)
- [ ] 1.24 Skill run success: "✓ Done · 2.3s" + "Copy output" + "View in Data →" link
- [ ] 1.25 Skill run failure: red output + auto-expanded "AI Debug" section + "Apply fix in Build →"
- [ ] 1.26 Run history: expand arrow per entry; "Replay" pre-fills form
- [ ] 1.27 "＋ New Skill" button → natural-language description modal

### 1e. Data mode
- [ ] 1.28 Left panel: named collections grouped by source (Agents / Skills / Manual)
- [ ] 1.29 Collection header: "Source: Agent — [agent name]" with link back to agent
- [ ] 1.30 Presentation tabs: Table / Cards above the grid
- [ ] 1.31 Export button: CSV download with meaningful filename
- [ ] 1.32 Data empty state: "No data yet. Run an Agent or Skill to start collecting."

### 1f. Cross-mode flow (simulated in mockup)
- [ ] 1.33 Toast component: top-right, auto-dismiss 4s, icon + message + "View →" link
- [ ] 1.34 Simulate: marking an Agent "done" fires toast + injects summary message in Build chat
- [ ] 1.35 Simulate: HITL-waiting Agent fires "⚠" badge on Agents sidebar button + toast

## 2. OpenSpec Validation

- [ ] 2.1 `openspec validate update-ghost-product-vision --strict`
- [ ] 2.2 Annotate `add-macos-desktop-app` tasks.md: mark items superseded by this change
- [ ] 2.3 Annotate `add-local-browser-agent` tasks.md: browser-agent is now agent-view type, not Task

## 3. Backend (after Tauri shell — deferred)

- [ ] 3.1 Global activity model: expose active agent count + per-agent status from `engine.py`
- [ ] 3.2 Tauri IPC: frontend subscribes to agent state changes via event stream
- [ ] 3.3 Toast dispatch: Python emits event → Tauri → frontend renders toast
- [ ] 3.4 `/agent` command: Building Agent decides type (browser/loop/reactive) from natural language goal
- [ ] 3.5 Skill streaming: stdout lines streamed → IPC → output panel
- [ ] 3.6 Loop Agent runner: background process manager (start/stop/restart), persists across app restarts
- [ ] 3.7 Reactive Agent runner: event listener process, reconnects on disconnect
- [ ] 3.8 Auto-repair: on Agent failure, invoke Building Agent with error context; re-deploy patched script
- [ ] 3.9 Data view: query SQLite by source (agent_id / skill_id); return schema + rows as JSON
