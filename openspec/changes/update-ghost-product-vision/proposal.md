# Change: Refine Ghost Product Vision — AI-Native Productivity UX

## Why

The existing desktop app specs (`add-macos-desktop-app`, `add-local-browser-agent`) were written before
a clear target persona was established. A product review identified two structural issues:

1. **Target persona mismatch.** The specs oscillate between "non-technical user" and "developer power
   user", producing UX compromises that serve neither well. Based on product review, the primary user is
   an **AI-native productivity user** — someone who already uses Claude/ChatGPT/Cursor, understands
   concepts like workspace and session, and wants to dramatically amplify what they can get done with AI.
   They are moving toward "vibe coder" but are not there yet.

2. **Static UI for a live-agent product.** Ghost's core value is running multiple AI agents
   simultaneously. The current UI design treats this like a chat app with tabs. It should feel like a
   **mission control** — alive, connected, showing agents at work.

This change locks in the product direction and adds the UX requirements that follow from it, so
`add-macos-desktop-app` and `add-local-browser-agent` can be implemented against a clear target.

## Target Persona

**AI-native productivity user** ("aspiring vibe coder"):
- Already uses Claude.ai, ChatGPT, or Cursor daily
- Comfortable with terms: workspace, session, agent, task, skill
- Does not want to configure terminals, but is not afraid of technical UI
- Core job: get more done with AI, faster — especially multi-step and multi-project work
- The product should feel like a **cockpit**, not a consumer chat app

## What Changes

### Product structure (no code changes, informs implementation of pending changes)
- Clarify that **Code** (multi-pane dashboard) is the **hero view**, not an advanced-mode detail
- Keep **Skill** mode as-is — the term is appropriate for this persona
- Rename **Data** mode to **Data** but surface it as "structured outputs & artifacts" — AI-rendered,
  not raw SQL. Users do not need to know the backend is SQLite
- Confirm **Task** (browser agent) as the primary differentiator; surface it prominently

### New UX capabilities (ADDED to desktop-app spec)
- **Agent Status Bar** — global indicator in titlebar showing number of active agents; click to see
  per-agent live status
- **Live Pane State** — Code view panes show animated active state (pulse, typing indicator) when
  AI is working; idle and stopped states use icon + label, not color alone
- **Pane Focus Mode** — double-click a pane header to expand it full-screen; Escape to return to grid
- **Sidebar Activity Badges** — mode buttons show count of active agents/running tasks

### New UX capabilities (ADDED to local-chat spec)
- **Slash Command Menu** — typing `/` in any chat input opens a command palette:
  `/browse`, `/skill`, `/data`, `/status` — makes cross-mode actions discoverable from chat
- **Cross-Mode Notifications** — when a Task or Skill completes while the user is in another mode,
  a toast appears and the relevant sidebar badge updates; chat pane receives a summary message
  with a deep link (e.g. "Browser task done: saved btc_price.json → [View in Data]")

### Accessibility (ADDED to desktop-app spec)
- All status indicators MUST use icon + label + color, never color alone. This applies to:
  workspace status dots, task status badges, pane active state, agent working state

### Workspace dropdown (MODIFIED in desktop-app spec)
- Workspace selector stays in the titlebar as a dropdown (already implemented in mockup)
- Sidebar is reserved exclusively for mode navigation + footer

## Impact

- Affected changes: `add-macos-desktop-app` (MODIFIED specs), `add-local-browser-agent` (no change)
- Affected specs (delta): `desktop-app`, `local-chat`, `browser-agent`, new `agent-status`
- Affected code: `ui/index.html` (mockup update), later Tauri frontend
- Does NOT change: backend engine, Discord adapter, tmux bridge, SQLite schema
