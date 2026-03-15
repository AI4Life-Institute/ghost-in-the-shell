# Change: Refine Ghost Product Vision — AI Fleet Control Center

## Why

After product review, two structural issues were identified in the existing specs:

1. **No clear target persona.** Specs oscillated between "non-technical user" and "developer
   power user." The real target is an **AI-native productivity user** — someone who wants to
   build and automate things but finds terminal/VSCode too high a barrier. They are comfortable
   with AI concepts and want to dramatically amplify what they can do. The product should feel
   like a cockpit, not a consumer chat app.

2. **"Task" was wrong as a top-level concept.** A task is what an Agent *does*, not something
   the user manages. The user manages Agents — autonomous processes that run tasks on their
   behalf. Removing "Task" as a mode and introducing "Agents" gives the product a coherent
   mental model: you deploy agents, agents run tasks.

## Target Persona

**AI-native productivity user ("aspiring vibe coder"):**
- Already uses Claude.ai, ChatGPT, or Cursor daily
- Wants to build things and automate workflows, but terminal/VSCode is too high a barrier
- Comfortable with terms: agent, skill, deploy, loop, trigger
- Core job: get the AI to build things and run things for them, simultaneously
- The product should feel like **commanding a fleet**, not chatting with a single assistant

## Final Four-Mode Structure

| Mode | Icon | Purpose |
|------|------|---------|
| **Build** | 🤖 | The Building Agent — AI that writes code, creates Skills, deploys Agents into your directory |
| **Agents** | ⚡ | Your fleet — all deployed Agents (Browser / Loop / Reactive), organised by type |
| **Skills** | 🛠 | Reusable tools the Building Agent has created; callable on demand |
| **Data** | 🗄 | Structured outputs from all Agents and Skills; the fleet's shared memory |

**Key decisions:**
- "Task" is removed as a mode. Task = what an Agent does internally. User manages Agents.
- Browser automation is a type of Agent within Agents mode — not a separate mode.
  Its "real Chrome, real sessions" differentiator is surfaced prominently inside Agents view.
- No "workspace" concept in v1. One project directory = one mono-repo root. All agent
  code, Skills, and Data live under it. Switching projects = opening a different folder.
- Building Agent (Build mode) creates and deploys the other Agents; it also monitors them
  and auto-repairs Loop/Reactive Agents when they fail.

## What Changes

### New UX capabilities
- **Four-mode structure** (Build / Agents / Skills / Data) replaces the prior Code/Skill/Task/Data
- **Project directory in titlebar** replaces workspace dropdown — one folder, all modes
- **Agent Fleet View** — left panel groups agents by type: Browser (by Chrome profile), Loop, Reactive
- **Browser Agents** — explicitly surfaced as "real Chrome, your sessions, no re-logging in"
- **Loop Agents** — scheduled, continuous execution; Building Agent auto-repairs on failure
- **Reactive Agents** — event-driven; show trigger source and connection status
- **Agent auto-repair** — Building Agent monitors Loop/Reactive Agents and patches failures
- **Slash command menu** — `/agent`, `/skill`, `/data`, `/status` from Build chat input
- **Cross-mode notifications** — toast + Build chat message when any Agent completes or needs input
- **Global agent counter** — titlebar shows "⏳ N active" spanning all agent types
- **Sidebar activity badges** — Agents button shows count; `⚠` when human input needed
- **Pane focus mode** — double-click Build pane header to full-screen; Escape exits
- **Live pane state** — animated pulse when Building Agent is working
- **Accessibility** — all status indicators: icon + label + color (never color alone)
- **Empty states** — per-mode guidance for first-time users

## Impact

- Supersedes: `add-macos-desktop-app` (desktop-app, local-chat specs)
- Partially supersedes: `add-local-browser-agent` (browser-agent becomes a type within agent-view)
- New specs: `app-structure`, `agent-view`, `skill-view`, `data-view`, `agent-status`
- Affected code (future): `ui/index.html` mockup, then Tauri frontend
- No backend changes in this change — implementation deferred to Tauri phase
