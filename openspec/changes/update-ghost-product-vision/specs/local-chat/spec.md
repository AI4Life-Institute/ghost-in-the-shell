## ADDED Requirements

### Requirement: Slash Command Menu
The system SHALL provide a command palette in the Build mode chat input, triggered by typing `/`,
that surfaces actions across all modes. The user never needs to navigate away from Build to
start an Agent, run a Skill, or query Data — they type in chat and Ghost routes it.

Available commands:

| Command | Action |
|---------|--------|
| `/agent <goal>` | Deploy a new Agent to accomplish a goal (Building Agent decides type: browser, loop, or reactive) |
| `/skill <name>` | Run a saved Skill with optional parameters |
| `/data <query>` | Query Data and show results inline in chat |
| `/status` | Show a summary of all running Agents in the chat |

#### Scenario: User opens command palette
- **WHEN** the user types `/` in the Build chat input
- **THEN** a floating menu appears above the input listing available commands with descriptions
- **AND** the menu filters as the user types more characters

#### Scenario: User deploys an Agent from chat
- **WHEN** the user types `/agent fetch the top 10 HN stories every morning`
- **THEN** the Building Agent creates a Loop Agent for that goal and deploys it
- **AND** a confirmation appears in chat: "Deployed: HN Morning Digest · Loop · every day 9am → [View in Agents]"
- **AND** the Agents sidebar badge increments

#### Scenario: User deploys a Browser Agent from chat
- **WHEN** the user types `/agent download the Goldman Sachs Q2 report from Nash-AI`
- **THEN** the Building Agent creates a Browser Agent using the user's default Chrome profile
- **AND** the Agents view becomes active showing the new agent's step log streaming live

#### Scenario: Command palette filters on input
- **WHEN** the user types `/ag`
- **THEN** the menu shows only `/agent` — the closest match

### Requirement: Cross-Mode Notifications
The system SHALL notify the user when a significant event occurs in the Agents or Skills
background — such as an Agent completing, failing, or requiring human input.

#### Scenario: Agent completes while user is in Build mode
- **WHEN** an Agent finishes its execution
- **AND** the user is not viewing Agents mode
- **THEN** a toast notification appears top-right: "[Agent name] · ✓ Done → [View in Agents]"
- **AND** the toast auto-dismisses after 4 seconds
- **AND** a summary message appears in the Build chat: "Agent done: [goal] — outputs saved → [View in Data]"

#### Scenario: Agent requires human input
- **WHEN** an Agent pauses waiting for human-in-the-loop input
- **AND** the user is not in Agents mode
- **THEN** the Agents sidebar badge shows "⚠" and a toast appears:
  "⚠ [Agent name] is waiting for your input"
- **AND** clicking the toast navigates to that Agent's detail panel with the input field focused

#### Scenario: Agent auto-repair completes
- **WHEN** the Building Agent auto-repairs a failed Loop Agent
- **THEN** a toast appears: "🤖 Auto-repaired: [Agent name] — back online"

### Requirement: Build Mode Chat Context
The system SHALL always display the active project directory in the Build chat, so the user
knows which codebase the Building Agent is working in.

#### Scenario: Build chat shows project context
- **WHEN** the user views the Build mode
- **THEN** the input placeholder reads "Build something in ~/myproject…"
- **AND** the header shows the active directory path and the AI provider (e.g. "claude-code · ~/myproject")
