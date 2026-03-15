## Context

GITS is a tmux bridge for coding CLIs, currently targeting developer users comfortable with terminal setup. The goal is to make it accessible to non-technical users via a native macOS desktop app with a frosted glass aesthetic — entirely implemented in pure CSS for cross-platform portability.

**Stakeholders**: End users (non-developers), current developer users, maintainers
**Constraints**: Must preserve existing CLI workflow; desktop app is additive, not a replacement; UI must not depend on any macOS-native APIs so it can be ported to Windows/Linux later.

## Goals / Non-Goals

**Goals:**
- One-click install (`.dmg` or `brew install --cask`)
- Zero terminal interaction required for initial setup
- Frosted glass UI implemented **entirely in pure CSS** (`backdrop-filter`, gradients, transparency) — no dependency on macOS native vibrancy APIs
- Bundle all dependencies (Python, tmux, fonts) in the app
- Guided Discord bot setup with copy-paste-friendly instructions
- User provides their own API keys (Claude API key or ChatGPT API key)
- Auto-updates without manual intervention
- **Local direct chat** — users can interact with coding CLIs directly through the app without Discord, using an elegant conversation UI with frosted glass message bubbles
- Apple Silicon (arm64) only for v1
- Menu bar tray icon for background operation
- **Cross-platform portability** — the entire UI layer is pure web, enabling future Windows/Linux ports with minimal effort

**Non-Goals:**
- Windows/Linux desktop app (macOS only for v1, but UI is designed to be portable)
- Replacing the CLI workflow (existing `gits start` still works)
- Building our own AI backend (users bring their own keys)
- Mobile app
- Embedded terminal view (advanced users use tmux directly)
- Telegram setup wizard (future addition)
- Dependency on macOS-specific APIs (NSVisualEffectView, Liquid Glass, etc.)

## Decisions

### Decision 1: Tauri v2 + pytauri for app shell
- **Why**: Tauri produces small binaries (~10MB vs Electron's 150MB+), and pytauri provides zero-IPC Python integration via PyO3.
- **Alternatives considered**:
  - *Electron*: Heavy (150-300MB RAM), overkill
  - *SwiftUI native*: Requires rewriting backend in Swift, no Python bridge, locks to macOS
  - *PyQt/PySide*: Looks non-native, hard to get frosted glass
  - *py2app + web server*: Fragile, no real integration

### Decision 2: Static tmux bundle (arm64 only)
- **Why**: Users shouldn't need Homebrew or Xcode CLI tools. Statically compile tmux + libevent + ncurses for arm64 (Apple Silicon only), embed in `Contents/Helpers/tmux`.
- **Risk**: tmux updates require rebuilding. Mitigated by pinning a known-good version and testing in CI.

### Decision 3: Pure CSS frosted glass — zero native dependency
- **Why**: Using macOS-native `NSVisualEffectView` or Liquid Glass API would lock the UI to macOS and make future Windows/Linux ports require a complete UI rewrite. Instead, the frosted glass effect is achieved entirely with pure CSS:
  - `backdrop-filter: blur(20px) saturate(180%)` for glass panels
  - `background: rgba(255, 255, 255, 0.15)` (light) / `rgba(0, 0, 0, 0.3)` (dark) for transparency
  - CSS `linear-gradient` with subtle noise texture for depth
  - `border: 1px solid rgba(255, 255, 255, 0.18)` for glass edge highlights
  - `box-shadow` for elevation/depth layers
  - The Tauri window is set to transparent background so the desktop shows through the CSS blur
- **Trade-off**: Pure CSS blur doesn't sample the actual desktop wallpaper like native vibrancy — it blurs the app's own content layers. To compensate, we use a subtle gradient background within the app that the glass panels blur against, creating a convincing frosted glass illusion.
- **Portability gain**: The exact same HTML/CSS/JS works on Windows (Tauri) and Linux (Tauri) with zero changes.

### Decision 4: Direct login via CLI OAuth — no API keys (prototype verified)
- **Why**: Non-technical users shouldn't deal with API keys. Instead, the app triggers `claude auth login` or `codex login --device-auth` as a subprocess, parses the output, and shows the login flow in the app's own UI.
- **Prototype results** (2026-03-14, verified on this machine):
  - `claude auth status` → returns JSON, no TTY needed ✅
  - `claude auth login` → outputs OAuth URL to stdout (`Opening browser to sign in… If the browser didn't open, visit: https://claude.ai/oauth/authorize?...`), auto-opens browser, no TTY needed ✅
  - `codex login status` → returns text status, no TTY needed ✅
  - `codex login --device-auth` → outputs URL (`https://auth.openai.com/codex/device`) + one-time code (e.g. `XFOS-3TQ4L`) to stdout, no TTY needed ✅
  - Both CLIs store their own credentials (Claude → macOS Keychain, Codex → `~/.codex/auth.json`)
- **App login UX**:
  - Claude: app spawns `claude auth login`, parses the OAuth URL from stdout, opens browser (or shows clickable link in UI). Polls `claude auth status` to detect completion.
  - Codex: app spawns `codex login --device-auth`, parses URL + code from stdout, shows in UI: "Open this link and enter code XXXX". Waits for process to exit (success).
  - **No terminal/command-line is shown to the user at any point.**
- **v1 providers**: Claude Code + Codex CLI only. OpenCode and others planned for future.
- **Fallback for advanced users**: Can still set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` env vars manually.

### Decision 5: Guided Discord bot setup (Discord only, no Telegram for v1)
- **Why**: Creating a Discord bot is the #1 friction point. The setup wizard walks users through: (1) visit Discord Developer Portal, (2) create application, (3) create bot, (4) copy token, (5) generate invite link with correct permissions, (6) invite to server.
- Each step shows a screenshot/illustration and a "copy" button for URLs.
- Telegram support will be added in a future change.

## Architecture

```
+-----------------------------------------------+
|  GITS.app (macOS .app bundle, arm64)           |
|  +-----------------------------------------+   |
|  | Web UI (Tauri WebView)                  |   |
|  | - Pure CSS frosted glass (no native API)|   |
|  | - Local chat (primary interface)        |   |
|  | - Setup wizard                          |   |
|  | - Status dashboard                      |   |
|  | - Settings panel                        |   |
|  +-----------------------------------------+   |
|  | Rust layer (src-tauri/)                  |   |
|  | - Transparent window (no vibrancy API)  |   |
|  | - PyO3 bridge to Python                 |   |
|  | - Keychain access                       |   |
|  | - Auto-updater                          |   |
|  | - Menu bar tray                         |   |
|  +-----------------------------------------+   |
|  | Python backend (pytauri)                |   |
|  | - gits.core.engine (existing logic)     |   |
|  | - gits.adapters.discord (existing)      |   |
|  | - gits.config (reads from GUI state)    |   |
|  +-----------------------------------------+   |
|  | Bundled binaries:                       |   |
|  |   Contents/Helpers/tmux (static, arm64) |   |
|  |   Contents/Resources/fonts/             |   |
|  |   Contents/Frameworks/Python.framework  |   |
|  +-----------------------------------------+   |
+-----------------------------------------------+
```

### UI Layout Concept — Chat Mode (Primary)

```
┌──────────────────────────────────────────────────┐
│  ░░░░░░░ Frosted glass title bar ░░░░░░░░░░░░░░ │
├──────────────────────────────────────────────────┤
│ ░░ Sidebar ░░░ │ ░░░ Chat view ░░░░░░░░░░░░░░ │
│ ░ (vibrancy) ░ │                               │
│                │  ┌─────────────────────────┐   │
│  💬 Chat       │  │ ░ AI response bubble ░░ │   │
│  📁 Sessions   │  │ (frosted glass, markdown│   │
│  ⚙️ Settings   │  │  + syntax highlighting) │   │
│  📊 Status     │  └─────────────────────────┘   │
│                │                               │
│ ─────────────  │  ┌─────────────────────────┐   │
│ Active:        │  │  User message bubble    │   │
│  myproject/    │  └─────────────────────────┘   │
│  ├ claude      │                               │
│  └ idle 2m     │  ┌─────────────────────────┐   │
│                │  │ ░ AI response bubble ░░ │   │
│                │  │ ```python               │   │
│                │  │ def hello():            │   │
│                │  │     print("hi")  [copy] │   │
│                │  │ ```                     │   │
│                │  └─────────────────────────┘   │
│                │                               │
│                │  ┌─░░ Input area ░░░░░░░──┐   │
│                │  │ Ask anything...    [⏎]  │   │
│                │  └────────────────────────┘   │
└──────────────────────────────────────────────────┘
```

### UI Layout Concept — Dashboard Mode

```
┌──────────────────────────────────────────────────┐
│  ░░░░░░░ Frosted glass title bar ░░░░░░░░░░░░░░ │
├──────────────────────────────────────────────────┤
│ ░░ Sidebar ░░░ │ ░░░ Dashboard ░░░░░░░░░░░░░░ │
│ ░ (CSS blur) ░ │                               │
│                │  [Connection status]          │
│  💬 Chat       │  Discord: 🟢 connected        │
│  📁 Sessions   │                               │
│  ⚙️ Settings   │  [Active sessions]            │
│  📊 Status     │  - #dev → ~/myproject (claude)│
│                │  - #ops → ~/infra (codex)     │
│                │                               │
│                │  (no terminal view — advanced  │
│                │   users use tmux directly)     │
└──────────────────────────────────────────────────┘
```

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| pytauri is young (v0.2) | API instability | Pin version, contribute upstream, have fallback plan (subprocess bridge) |
| Static tmux breaks on macOS updates | App crashes | CI tests on multiple macOS versions, quick patch releases |
| App bundle size (~100MB with Python) | Download size | Use Python 3.12 slim, tree-shake unused stdlib modules |
| Keychain access requires entitlements | App Store rejection | Distribute via `.dmg` + Homebrew first, not App Store |
| Users confused by tmux (visible in Activity Monitor) | Support load | Hide tmux process, name it "GITS Helper" |

## Migration Plan

1. Desktop app is additive — existing CLI workflow (`gits start`) unchanged
2. v1: Ship `.dmg` for manual install + `brew install --cask gits`
3. v2: Add auto-updater via Tauri updater (checks GitHub releases)
4. Config migration: if `~/.gits/` exists, import settings into GUI on first launch

## Resolved Questions

1. **Terminal view?** → No. Advanced users use tmux directly; the app focuses on the chat interface.
2. **Apple Silicon + Intel?** → Apple Silicon (arm64) only for v1.
3. **Menu bar tray?** → Yes, required for background operation.
4. **Telegram wizard?** → Not in v1, future addition.
5. **Native vibrancy API?** → No. Pure CSS implementation for cross-platform portability. See Decision 3.

## Open Questions

1. Should the pure CSS frosted glass use a static gradient background or a dynamic blur of actual app content layers?
