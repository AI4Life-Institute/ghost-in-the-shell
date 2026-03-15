<!-- SUPERSEDED NOTE (update-ghost-product-vision, 2026-03-15)
  Sections 3 (Frosted glass UI) and 6 (Built-in chat) are fully superseded by
  update-ghost-product-vision: the four-mode structure (Build/Agents/Skills/Data)
  and multi-pane Build chat replace the original Code/Chat/Dashboard design.
  The desktop-app spec and local-chat spec referenced in update-ghost-product-vision
  proposal.md are both superseded. Tauri scaffold (§1), bundling (§2), wizard (§4),
  credentials (§5), and distribution (§9-10) remain relevant.
-->

## 1. Foundation — Tauri + pytauri scaffold
- [ ] 1.1 Initialize Tauri v2 project in `src-tauri/` with macOS arm64 target
- [ ] 1.2 Integrate pytauri for Python↔Rust bridge (PyO3)
- [ ] 1.3 Configure Tauri window with transparent background (for pure CSS glass effects)
- [ ] 1.4 Verify existing `gits.core.engine` loads via pytauri bridge
- [ ] 1.5 Set up Tauri build pipeline (`tauri build` → `.app` + `.dmg`)

## 2. Bundle dependencies (arm64)
- [ ] 2.1 Write `scripts/build-tmux.sh` to statically compile tmux + libevent + ncurses (arm64)
- [ ] 2.2 Embed compiled tmux in `Contents/Helpers/tmux`
- [ ] 2.3 Update `TmuxController` to use bundled binary when running inside .app
- [ ] 2.4 Bundle fonts (JetBrainsMono, NotoSansCJK, Symbola) in `Contents/Resources/fonts/`
- [ ] 2.5 Test bundled components launch correctly from within .app

## 3. Frosted glass UI (pure CSS)
- [ ] 3.1 Set up frontend project in `ui/` (vanilla JS or lightweight framework)
- [ ] 3.2 Build CSS design system: glass panels (`backdrop-filter: blur()`, `rgba` backgrounds), edge highlights, elevation shadows, gradient backgrounds
- [ ] 3.3 Build Light/Dark mode CSS variables with auto-switching via `prefers-color-scheme`
- [ ] 3.4 Build activity dashboard: workspaces list, AI assistant status, Discord connection indicator
- [ ] 3.5 Build settings page with organized sections: Discord, AI Account, Projects, Advanced
- [ ] 3.6 Add menu bar tray icon with quick-status dropdown

## 4. Welcome wizard
- [ ] 4.1 Build wizard flow: welcome → Discord setup (optional) → AI account → project folder → verification → done
- [ ] 4.2 Create guided Discord bot setup with illustrations, numbered steps, and copy buttons
- [ ] 4.3 Generate invite link with pre-selected permissions (user just clicks "Copy" and "Open")
- [ ] 4.4 Add "Log in to Claude" / "Log in to ChatGPT" buttons that trigger `claude login` / `codex login` and detect completion
- [ ] 4.5 Build "Ready to Go" verification screen with friendly pass/fail messages
- [ ] 4.6 Add "Skip" and "I already have this" shortcuts throughout

## 5. AI account login & credential management
- [ ] 5.1 Implement `claude login` subprocess trigger: spawn process, detect browser open, detect login success
- [ ] 5.2 Implement `codex login` subprocess trigger (use `--device-auth` if available for better UX)
- [ ] 5.3 Detect existing login state: check Claude credentials in macOS Keychain (`Claude Code-credentials`), check Codex credentials in `~/.codex/auth.json`
- [ ] 5.4 Build login status UI: show "Logged in" / "Not connected" per provider with log in/out buttons
- [ ] 5.5 Store Discord bot token via `tauri-plugin-keychain` (Discord token is the only thing users paste manually)

## 6. Built-in chat
- [ ] 6.1 Build conversation UI with frosted glass message bubbles and fade-in animations
- [ ] 6.2 Implement markdown rendering + syntax-highlighted code blocks with copy button
- [ ] 6.3 Wire chat input → AI assistant (send text to workspace)
- [ ] 6.4 Wire AI output → chat view (parse responses, display as formatted messages)
- [ ] 6.5 Build workspace management: create, switch, resume, close — all from the sidebar
- [ ] 6.6 Support drag-and-drop file attachment in chat input
- [ ] 6.7 Ensure built-in chat and Discord can control the same workspace simultaneously
- [ ] 6.8 Add smooth animations and transitions for message appearance

## 7. Advanced mode (terminal view for power users)
- [ ] 7.1 Add "Show Terminal" toggle in Settings → Advanced (hidden by default)
- [ ] 7.2 Embed xterm.js terminal connected to active workspace's tmux session
- [ ] 7.3 Hide all terminal UI when advanced mode is off

## 8. Config migration & dual-mode support
- [ ] 8.1 Detect existing `~/.gits/` config on first launch and offer to import
- [ ] 8.2 Ensure `gits start` CLI still works independently of desktop app
- [ ] 8.3 Add `--desktop` flag or auto-detect `.app` bundle context in `__main__.py`

## 9. Distribution
- [ ] 9.1 Set up code signing with Developer ID certificate
- [ ] 9.2 Configure notarization for `.dmg` distribution
- [ ] 9.3 Create Homebrew Cask formula (`brew install --cask gits`)
- [ ] 9.4 Configure auto-updater (check GitHub Releases for new versions)
- [ ] 9.5 Write user-facing installation guide in plain, friendly language

## 10. Testing
- [ ] 10.1 Test full welcome wizard on a clean Mac (Apple Silicon, no dev tools installed)
- [ ] 10.2 Test bundled components on macOS 14 (Sonoma) and macOS 15 (Sequoia)
- [ ] 10.3 Test frosted glass styling in Light and Dark mode
- [ ] 10.4 Test Claude login and Codex login flow from within .app
- [ ] 10.5 Test auto-update from GitHub Releases
- [ ] 10.6 Test config migration from existing setup
- [ ] 10.7 Test built-in chat send/receive without Discord
- [ ] 10.8 Test built-in chat + Discord dual control of same workspace
- [ ] 10.9 Test advanced mode: terminal view appears only when enabled
