## Context

Ghost is a desktop app that bridges AI coding CLIs (Claude Code, Codex, OpenCode) running in tmux
sessions, with a local browser agent layer on top. The UI mockup exists at `ui/index.html`.

**Stakeholders:** AI-native productivity users (primary), developer power users (secondary)
**Constraints:** Pure CSS frosted-glass UI; no macOS-native APIs; must be portable to Windows/Linux later

## Goals / Non-Goals

**Goals:**
- Establish a single, consistent product persona that all design decisions derive from
- Make "multiple AI agents working simultaneously" visible and tangible in the UI
- Ensure status communication never relies on color alone
- Make cross-mode workflows (chat → browse → data) discoverable without documentation

**Non-Goals:**
- Changing the backend architecture
- Supporting mobile
- Simplifying to a consumer/non-technical audience
- Hiding terminal access (Dev Mode stays)

## Decisions

### Decision 1: AI-native productivity user, not "non-technical user"
Prior specs said "non-technical user" as the primary audience. This led to proposals to hide
terminals, remove the Data view, and simplify terminology. Reversed.

The real target has used Claude/Cursor, understands AI concepts, and wants power — not simplification.
We should feel like a cockpit. Terminology like "workspace", "agent", "skill", "task" stays.

### Decision 2: Code multi-pane dashboard is the hero view
The multi-pane grid (multiple Claude sessions visible simultaneously) IS the differentiating
experience. It is not an advanced mode. It is the first thing users see in Code mode.

Single-pane focus is achieved via Pane Focus Mode (double-click), not by defaulting to one pane.

### Decision 3: Color + icon + label — all three required for status
Color blindness affects ~8% of men. Any status that is currently color-only (green dot = active,
purple border = selected pane) must also carry an icon and/or short label.

Implementation pattern:
```
❌ Before:  [●]  (green dot only)
✅ After:   [▶ Active]  (icon + label; color is enhancement, not primary signal)
```

### Decision 4: Chat as the universal entry point
`/` command menu in any chat input surfaces all cross-mode actions. Users never need to know
which mode handles what — they type in chat, and the app routes appropriately.

This mirrors how Slack, Linear, and Notion use `/` commands as the "I want to do something" gesture.

### Decision 5: Live state must be visible
The UI should make it obvious that AI agents are working. A static chat bubble is not enough.

Required live indicators:
- Titlebar: `⏳ 2 agents active` counter
- Code pane header: animated pulse dot when AI is responding
- Sidebar mode badge: `Task  [2]` when tasks are running
- Toast notification when any agent completes while user is in another mode

### Decision 6: Data mode stays, SQL is hidden
The Data view shows structured results from agent tasks and skill runs. The table grid view is
correct for this persona — they understand tabular data. What they should NOT see: raw SQL
CREATE TABLE statements, database file paths, or migration logs.

AI-rendered data views are the long-term direction: the AI picks the best visualization
(table, card, chart) based on the data shape. The current table grid is the v1 implementation.

## Information Architecture

```
Titlebar
  [traffic lights]  [● myproject ▾]  ·  Ghost  ·  [⏳ N active]  [⚙]

Sidebar (168px)
  💻  Code          — multi-pane AI session dashboard
  ⚡  Skill         — saved automations / recipes
  🌐  Task          — browser agent (local Manus)     [badge: N running]
  🗄  Data          — structured outputs & artifacts
  ─────────────────
  [avatar]  Wei Liu · Settings

Code view
  [main ●] [debug] [ci] [+]          ← Window tabs (within active workspace)
  ┌────────────┬────────────┐
  │ ▶ Active   │ ⏸ Idle     │        ← Pane headers (icon+label, not color only)
  │ Chat / AI  │ Chat / AI  │
  └────────────┴────────────┘
  [+ add pane]
  [>_ Dev Mode]                       ← Global terminal reveal

Chat input (all modes)
  [ Ask anything...  /browse /skill … ]
  Typing / shows command palette
```

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| "Cockpit" UI overwhelms first-time users | Drop-off on first launch | Empty state with a single guided action; panes appear as user adds them |
| Live state polling is expensive | Battery/CPU drain | Only poll active workspace; pause when app is backgrounded |
| `/` command menu scope creep | Complexity | Start with 4 commands: `/browse`, `/skill`, `/data`, `/status` |
| Cross-mode notifications are noisy | User annoyance | Toast auto-dismiss in 4s; badge clears on mode visit |

## Open Questions

1. Should Pane Focus Mode (full-screen expand) be a double-click or a button in the pane header?
2. Should the `N agents active` counter in the titlebar be always visible or only when N > 0?
3. Should the `/` command menu also accept natural language routing ("find me the BTC price"
   → auto-selects `/browse`)?
