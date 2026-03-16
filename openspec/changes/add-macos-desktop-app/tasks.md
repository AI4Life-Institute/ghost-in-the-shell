# Ghost Desktop App — Active Tasks
<!-- Consolidated 2026-03-15: merges add-macos-desktop-app + update-ghost-product-vision §3 -->

## Done ✅

- [x] Tauri v2 project in `src-tauri/`; Python↔Rust stdio JSON IPC bridge
- [x] Transparent window + macOS glass effects; aurora background
- [x] `gits desktop` subcommand in `__main__.py`; engine + SkillRunner auto-start
- [x] Tauri v2 capabilities (`capabilities/default.json`) — core:event:allow-listen
- [x] Session sidebar: reads all tmux sessions via IPC, platform icon, CLI badge, work_dir, sort desktop-first
- [x] xterm.js embedded; PTY infrastructure (`open_pty`, `pty_input`, `resize_pty`, `close_pty` via portable-pty)
- [x] macOS entitlements + auto-sign in Ghost.app wrapper — no TCC dialog on launch
- [x] Agents panel: Loop / Reactive / Runner cards, status dots, live log, Run Now / Pause
- [x] Skills panel: lists Runner skills from `~/.gits/skills/`, trigger badge, steps
- [x] Data panel: file tree + table view + row drawer
- [x] `debug_log` Rust command + IPC event log for self-verification without screenshots

---

## 1. PTY Terminal (next — unblocks core usability)

- [ ] 1.1 `activateSession`: call `open_pty` with correct `tmux_session` + `window_id`; show `#main-term`; hide `#term-empty`
- [ ] 1.2 Wire xterm.js input → `pty_input` (base64 encode); wire `pty-output` event → xterm.js write
- [ ] 1.3 `ResizeObserver` on `#main-term` → `resize_pty` on size change; FitAddon on open
- [ ] 1.4 Close PTY on session switch (`close_pty` for old channel_id)
- [ ] 1.5 End-to-end test: click session → interactive tmux/claude renders in xterm.js

## 2. Backend wiring (from update-ghost-product-vision §3)

- [ ] 2.1 Global activity model: expose active agent count + per-agent status from `engine.py`
- [ ] 2.2 Toast dispatch: Python emits `toast` event → Tauri → frontend renders toast
- [ ] 2.3 `/agent` command: Building Agent decides type (browser/loop/reactive) from natural language goal
- [ ] 2.4 Skill log streaming: stdout lines streamed in real-time → IPC → output panel
- [ ] 2.5 Auto-repair: on Agent failure, inject error context into ops session → re-deploy patched script
- [ ] 2.6 Data view: query SQLite by source (`agent_id` / `skill_id`) → schema + rows as JSON via IPC

## 3. Build view — interactive terminal improvements

- [ ] 3.1 `new_session` button: open dialog (name + work_dir + CLI picker) → IPC → new tmux window → select it
- [ ] 3.2 Topbar shows session status dot (idle/busy) live from `pane_update` events
- [ ] 3.3 Keyboard shortcut `⌘T` → new session; `⌘W` → close current session

## 4. UI polish

- [ ] 4.1 Light/Dark mode CSS variables with `prefers-color-scheme` auto-switch
- [ ] 4.2 Settings panel: Discord token, ops session name, default CLI, theme
- [ ] 4.3 Menu bar tray icon: show active agent count; click → bring window to front

## 5. Distribution (deferred — do after core is stable)

- [ ] 5.1 `tauri build` pipeline → signed `.app` + `.dmg`
- [ ] 5.2 Developer ID code signing + notarization
- [ ] 5.3 Auto-updater (GitHub Releases)
- [ ] 5.4 Homebrew Cask formula
