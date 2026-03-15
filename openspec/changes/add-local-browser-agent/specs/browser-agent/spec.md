## ADDED Requirements

### Requirement: OpenClaw Chrome Extension Setup
The system SHALL bundle the OpenClaw Chrome extension inside the `.app` and guide the user through installing it into their Chrome browser via the Setup Wizard, requiring no manual download.

#### Scenario: First-time extension install
- **WHEN** the user reaches the "Browser Agent" step in the Setup Wizard
- **THEN** the app shows a single "Install Extension" button
- **AND** clicking it opens Chrome's extension page with the bundled `.crx` pre-loaded for one-click install
- **AND** once installed, the wizard step shows a green checkmark

#### Scenario: Extension already installed
- **WHEN** the system detects OpenClaw is already responding to `openclaw doctor`
- **THEN** the Setup Wizard skips the install step and shows "Already installed"

### Requirement: Browser Agent Executor
The system SHALL provide a browser agent that, given a natural-language goal, autonomously completes multi-step web tasks by repeatedly cycling through: snapshot the current page state → decide next action (via Claude) → execute action via OpenClaw CLI → record result → repeat until goal is achieved or max steps reached.

#### Scenario: Agent completes a web task
- **WHEN** the user sends a task like "Go to HackerNews and save the top 5 story titles"
- **THEN** the agent creates a browser session using the user's chosen Chrome profile
- **AND** executes actions (navigate, snapshot, evaluate, click, type) using `openclaw browser` CLI commands
- **AND** each action and its result is recorded as a step in the local SQLite database
- **AND** when the goal is reached, extracted data is saved as an artifact and shown in the Tasks view

#### Scenario: Agent recovers from an unexpected page state
- **WHEN** a page loads differently than expected (login wall, CAPTCHA, redirect)
- **THEN** the agent pauses, snapshots the new state, re-reasons, and either adapts or surfaces a human-in-the-loop prompt in the Tasks view
- **AND** the user can type a response in the Tasks view to unblock the agent

#### Scenario: Max steps reached without completion
- **WHEN** the agent reaches 30 steps without completing the goal
- **THEN** the task is marked "Needs review" with a summary of what was accomplished so far
- **AND** the user can either continue the task or cancel it from the Tasks view

### Requirement: OpenClaw Browser Control Primitives
The system SHALL implement the following browser control primitives, each wrapping the corresponding `openclaw` CLI command, with structured results stored in SQLite:

| Primitive     | OpenClaw command                                        | Returns              |
|---------------|---------------------------------------------------------|----------------------|
| `navigate`    | `openclaw browser --browser-profile P navigate URL`    | page title, final URL |
| `snapshot`    | `openclaw browser --browser-profile P snapshot --labels` | element ref map       |
| `click`       | `openclaw browser --browser-profile P click REF`        | success/error         |
| `type`        | `openclaw browser --browser-profile P type REF TEXT`    | success/error         |
| `evaluate`    | `openclaw browser --browser-profile P evaluate --fn JS` | JS return value (JSON)|
| `extract_text`| evaluate wrapping `document.body.innerText`             | page text             |

#### Scenario: Snapshot parses element refs
- **WHEN** the agent calls `snapshot --labels`
- **THEN** the output is parsed into a structured map of `{ role, label, ref }` tuples
- **AND** Claude receives this map (not raw HTML) so it can select the correct ref for the next action

#### Scenario: Evaluate extracts localStorage token
- **WHEN** the agent needs to read a JWT from localStorage
- **THEN** it calls evaluate with `localStorage.getItem('token')` and receives the raw string value
- **AND** the value is stored in the task's working memory in SQLite (not logged in plaintext)

### Requirement: Chrome Profile Management
The system SHALL let users select which Chrome profile the browser agent uses, and create named GITS profiles via OpenClaw when needed.

#### Scenario: User selects existing Chrome profile
- **WHEN** the user configures a task in the Tasks view
- **THEN** they can pick from their existing Chrome profiles (auto-detected via `openclaw browser list-profiles`)
- **AND** the agent uses that profile — including its cookies, saved passwords, and extensions

#### Scenario: Create a dedicated GITS profile
- **WHEN** the user wants an isolated profile for agent tasks
- **THEN** the app can create one with `openclaw browser create-profile --name gits-agent --color #6366f1`

### Requirement: Artifact Collection
The system SHALL save outputs produced by the browser agent (downloaded files, extracted tables, screenshots, text summaries) to `~/.gits/artifacts/<task-id>/` and display them in the Tasks view.

#### Scenario: Agent downloads a PDF
- **WHEN** the agent navigates to a download link and the file is saved to disk
- **THEN** the artifact appears in the Tasks view with filename, size, and a "Show in Finder" button

#### Scenario: Agent extracts a table
- **WHEN** the agent evaluates JS to extract a table from a webpage
- **THEN** the result is saved as a `.csv` artifact and shown as a preview in the Tasks view
