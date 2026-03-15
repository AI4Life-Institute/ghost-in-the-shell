## ADDED Requirements

### Requirement: Agent Fleet View
The system SHALL display all deployed Agents in a single Agents view, organised by type.
An Agent is any autonomous process that the Building Agent has created and deployed — it runs
tasks on behalf of the user, either once, on a loop, or in response to events.

The user manages Agents, not tasks. "Task" is what an Agent does internally — it is not a
concept the user interacts with directly.

Agents are grouped into three types in the left panel:

```
Left panel:
  Browser
  ├── ● Personal Chrome
  │     Nash-AI Reporter  ⏳
  │     HN Daily Digest   ✓
  ├── ● Work Chrome
  │     idle
  └── ＋ Add profile

  Loop
  ├── ⏳ BTC Price Monitor
  └── ✓  Weekly HN Digest

  Reactive
  └── ● Discord Webhook Handler
```

#### Scenario: User views their agent fleet
- **WHEN** the user opens Agents mode
- **THEN** the left panel lists all deployed Agents grouped by type: Browser, Loop, Reactive
- **AND** each Agent shows: name, current status (icon + label), and last activity time
- **AND** selecting an Agent shows its detail panel on the right

#### Scenario: Building Agent deploys a new Agent
- **WHEN** the Building Agent creates and deploys an Agent (e.g. a loop script or browser automation)
- **THEN** it appears immediately in the appropriate group in the Agents view
- **AND** the sidebar Agents badge increments

### Requirement: Browser Profiles as First-Class Fleet Units
The system SHALL display Browser Agents grouped by Chrome profile at the top of the Agents
view as prominent profile cards — not buried inside a left-panel list. Each profile card shows
the profile name, agent count, and running status at a glance.

Clicking a profile card selects it and reveals its agents as tiles below the profile row.
Multiple agents can share a profile. The "＋ Add Profile" card lets users register a new
Chrome profile from the system.

#### Scenario: User sees all Chrome profiles
- **WHEN** the user opens Agents mode
- **THEN** the Browser section shows one card per registered Chrome profile
- **AND** each card shows: profile name, agent count, current status (▶ N running / ⏸ Idle)
- **AND** clicking a profile card selects it and shows its agents as tiles beneath

#### Scenario: Browser Agent uses existing session
- **WHEN** a Browser Agent navigates to a site the user is already logged into in that Chrome profile
- **THEN** it proceeds without any login step
- **AND** the execution log shows "✓ Already logged in (session active)" rather than a login action

#### Scenario: User adds a Chrome profile
- **WHEN** the user clicks "＋ Add Profile" in the Browser section
- **THEN** Ghost lists existing Chrome profiles detected on the system
- **AND** the user can select one to make it available for Browser Agents

### Requirement: Browser Agent Live View — Embedded Screenshot Stream
The system SHALL display a live screenshot stream inside the Browser Agent detail drawer,
giving the user a real-time view of what the agent is doing in Chrome without opening a
separate browser window.

**Implementation (v1):** The Python backend uses Playwright via CDP to connect to and control
the user's real Chrome. After each action step, the backend calls `page.screenshot()` and
emits the PNG bytes as a base64-encoded Tauri IPC event. The frontend renders the latest
frame inside a browser-chrome-styled container (window controls, URL bar, Live badge).

The live view is embedded at the top of the detail drawer. Below it are two columns:
status + controls (left) and the execution log (right).

#### Scenario: User sees what the browser agent is doing
- **WHEN** the user opens a running Browser Agent's detail drawer
- **THEN** the top section shows a browser-chrome frame containing the latest screenshot
- **AND** the URL bar shows the current page URL
- **AND** a "● Live · Ns ago" badge shows when the last frame was captured
- **AND** the frame updates automatically as each new screenshot arrives via IPC

#### Scenario: Agent is done — last frame preserved
- **WHEN** the user opens a completed Browser Agent's detail drawer
- **THEN** the screenshot area shows the last captured frame
- **AND** the badge reads "Last frame · Ns ago" (no live pulse)

#### Scenario: Non-browser agent has no live view
- **WHEN** the user opens a Loop or Reactive Agent detail drawer
- **THEN** no screenshot area is shown
- **AND** the drawer shows only status + controls (left) and execution log (right)

### Requirement: Loop Agents — Continuous Execution
The system SHALL support Loop Agents: Agents that run a script or sequence of actions
repeatedly on a defined schedule (every N minutes/hours, or on a cron expression).
Loop Agents are created by the Building Agent and run as background processes.

#### Scenario: Loop Agent runs on schedule
- **WHEN** a Loop Agent's scheduled time arrives
- **THEN** it executes its defined logic (e.g. fetch price, save to Data)
- **AND** the Agents view updates its status to "⏳ Running" for the duration
- **AND** on completion, status returns to "✓ Last run: [timestamp]"
- **AND** results are written to Data

#### Scenario: Loop Agent fails and Building Agent fixes it
- **WHEN** a Loop Agent exits with an error
- **THEN** its status shows "✗ Failed" with the error summary
- **AND** if auto-repair is enabled, the Building Agent is invoked automatically:
  it reads the error, patches the script, and re-deploys the Loop Agent
- **AND** the Agents view shows "🤖 Auto-repaired by Build Agent" on the next successful run

#### Scenario: User pauses or stops a Loop Agent
- **WHEN** the user clicks "Pause" on a running Loop Agent
- **THEN** it completes its current execution and then stops scheduling further runs
- **AND** status shows "⏸ Paused" until the user resumes it

### Requirement: Reactive Agents — Event-Driven Execution
The system SHALL support Reactive Agents: Agents that are triggered by external events
(a Discord message, a webhook, a file change, a cron event, an API call) rather than running
on a fixed schedule. Reactive Agents stay connected to their trigger source and activate
when conditions are met.

#### Scenario: Reactive Agent triggers on event
- **WHEN** the configured trigger fires (e.g. a new Discord message arrives)
- **THEN** the Reactive Agent activates, processes the event, and returns to listening state
- **AND** the Agents view shows "● Connected · listening" when idle and "⏳ Running" when active

#### Scenario: User sees trigger configuration
- **WHEN** the user views a Reactive Agent's detail panel
- **THEN** the header shows the trigger type and connection status
  (e.g. "Trigger: Discord channel #dev · ● Connected")
- **AND** a recent events log shows the last N trigger activations with timestamps and outcomes

### Requirement: Agent Detail Panel
The system SHALL display a detail panel for the selected Agent showing its current state,
execution history, and control actions.

#### Scenario: User views agent detail
- **WHEN** the user selects an Agent from the left panel
- **THEN** the right panel shows:
  - Agent name and type badge (Browser / Loop / Reactive)
  - Current status with icon + label (never color alone)
  - For Browser: Chrome profile badge "🌐 Real Chrome · [profile]"
  - For Loop: schedule (e.g. "Every 60 min") and next run time
  - For Reactive: trigger source and connection status
  - Live execution log: actions taken, results, timestamps (streams in real time when running)
  - Control buttons: Run Now / Pause / Resume / Delete
  - "View outputs in Data →" link if the Agent has produced data

#### Scenario: User manually triggers an Agent
- **WHEN** the user clicks "Run Now" on any Agent
- **THEN** the Agent executes immediately, regardless of its schedule or trigger
- **AND** the execution log streams live in the detail panel
