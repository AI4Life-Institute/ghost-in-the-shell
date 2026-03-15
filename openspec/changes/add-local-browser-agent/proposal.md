# Change: Add Local Browser Agent (Manus-style, fully local)

## Why
GITS currently bridges coding CLIs (Claude Code, Codex) via terminal. Adding browser automation turns it into a **local Manus** — the AI can browse the web, fill forms, extract data, and complete multi-step web tasks, all running on the user's machine with no cloud dependency. The key enabler is OpenClaw: a Chrome extension + CLI that controls the user's real Chrome browser via WebSocket, using element snapshots and refs (no fragile CSS selectors). A local SQLite database stores task history, action logs, and agent working memory across sessions.

## What Changes

- Bundle the **OpenClaw Chrome extension** in the `.app`; the Setup Wizard gains a one-click step to install it into Chrome
- Add a **local SQLite database** (`~/.gits/gits.db`) — schema covers tasks, steps, observations, browser sessions, and extracted artifacts
- Add a **Browser Agent executor** in Python: given a user goal, loops `snapshot → think (Claude) → act (openclaw CLI)` until done or max_steps reached
- Add a **Tasks view** in the desktop UI — shows running/queued/completed agent tasks with live step-by-step progress, like Manus
- The AI decides each step autonomously: navigate, click, type, evaluate JS, extract, or call an API
- Extracted artifacts (PDFs, tables, screenshots) saved to `~/.gits/artifacts/` and surfaced in the UI
- All browser actions go through the user's own Chrome profiles — no sandboxed browser, no Playwright, uses real sessions with real cookies

## Impact
- Affected specs: `desktop-app` (MODIFIED — new Tasks view in sidebar)
- New specs: `browser-agent`, `local-memory`
- Affected code: `src/gits/core/engine.py` (add task queue), new `src/gits/adapters/browser/` adapter, new `src/gits/storage/sqlite.py`
- New UI view: `ui/index.html` — Tasks view
- New dependency: `openclaw` CLI must be installed (bundled or installed by Setup Wizard)
