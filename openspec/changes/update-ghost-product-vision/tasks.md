## 1. Mockup Updates (ui/index.html)

- [ ] 1.1 Titlebar: add "⏳ N active" agent counter (static mockup, shows "2 active")
- [ ] 1.2 Titlebar: workspace dropdown — replace color-only dots with icon + label (▶ Active / ⏸ Idle / ◼ Stopped)
- [ ] 1.3 Code pane headers: replace color dot with icon + label (▶ Active with pulse animation, ⏸ Idle, ◼ Stopped)
- [ ] 1.4 Code pane: add pane focus mode — double-click header expands to full-screen; Escape exits
- [ ] 1.5 Task badges: add icon to each status badge (⏳ Running, ✓ Done, ✗ Failed, ○ Queued, ⚠ Needs input)
- [ ] 1.6 Sidebar mode buttons: add activity badge indicator (e.g. Task [2]) — static mockup
- [ ] 1.7 Chat input: add `/` command palette (shows /browse, /skill, /data, /status on `/` keypress)
- [ ] 1.8 Chat input placeholder: change to "Ask [workspace-name]…"
- [ ] 1.9 Cross-mode notification: add toast component (top-right, auto-dismiss) — trigger on task complete
- [ ] 1.10 Simulate cross-mode notification: when user "completes" a task in mockup, show toast + chat message

## 2. OpenSpec Alignment

- [ ] 2.1 Validate this change: `openspec validate update-ghost-product-vision --strict`
- [ ] 2.2 Review `add-macos-desktop-app` tasks.md and mark items superseded by this change
- [ ] 2.3 Review `add-local-browser-agent` tasks.md for alignment (no changes expected)

## 3. Backend (when implementing for real, after Tauri shell exists)

- [ ] 3.1 Implement global activity model in `src/gits/core/engine.py` — expose active session count
- [ ] 3.2 Wire activity model to Tauri IPC so frontend can subscribe to state changes
- [ ] 3.3 Implement toast notification dispatch from Python → Tauri → frontend
- [ ] 3.4 Implement sidebar badge counts via same IPC channel
- [ ] 3.5 Implement `/` command routing: `/browse` → creates Task, `/skill` → runs Skill, etc.
