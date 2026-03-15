## ADDED Requirements

### Requirement: Direct Account Login
The system SHALL let users log in to their AI provider accounts (Claude, Codex) directly from the app by triggering the CLI's built-in login flow — no API keys, no copying tokens, just click "Log in" and sign in with your browser.

#### Scenario: User logs in to Claude
- **WHEN** the user clicks "Log in to Claude" in the welcome wizard or settings
- **THEN** the app runs `claude login` in the background
- **AND** the system browser opens the Claude sign-in page
- **AND** once the user completes sign-in in the browser, the app detects the login succeeded and shows a green checkmark with "Claude: logged in"

#### Scenario: User logs in to Codex (ChatGPT)
- **WHEN** the user clicks "Log in to ChatGPT" in the welcome wizard or settings
- **THEN** the app runs `codex login` in the background
- **AND** the system browser opens the ChatGPT sign-in page
- **AND** once login completes, the app shows "ChatGPT: logged in"

#### Scenario: User is already logged in
- **WHEN** the user has previously logged in to Claude or Codex (credentials exist on the system)
- **THEN** the app detects this automatically and shows "Already logged in" — no action needed

### Requirement: No API Keys Required
The system SHALL NOT ask users for API keys. Login is handled entirely through the CLI's OAuth flow (browser sign-in). Users only need their existing Claude Pro/Max or ChatGPT Plus/Pro subscription.

#### Scenario: Setup wizard uses login, not key entry
- **WHEN** the user reaches the AI account step in the welcome wizard
- **THEN** they see two buttons: "Log in to Claude" and "Log in to ChatGPT"
- **AND** there is no text field for entering an API key (advanced users can set env vars themselves)

#### Scenario: App explains what's needed
- **WHEN** the user hasn't logged in to any AI provider
- **THEN** the app shows a friendly message: "You'll need a Claude or ChatGPT subscription to use the AI features. Click below to sign in with your account."

### Requirement: Login Status Display
The system SHALL clearly show which AI accounts are connected, in both the welcome wizard and the settings page.

#### Scenario: Settings shows login status
- **WHEN** the user opens Settings → AI Account
- **THEN** each provider shows its status:
  - "Claude: Logged in" (with a "Log out" option)
  - "ChatGPT: Not connected" (with a "Log in" button)

#### Scenario: User logs out
- **WHEN** the user clicks "Log out" next to a provider
- **THEN** the app clears the stored credentials for that provider
- **AND** the status changes to "Not connected"

### Requirement: Supported Providers (v1)
The system SHALL support Claude Code (Anthropic) and Codex CLI (OpenAI) in v1. OpenCode and other providers are planned for future releases.

#### Scenario: Provider selection when creating a workspace
- **WHEN** the user creates a new workspace and has both Claude and ChatGPT logged in
- **THEN** they can choose which AI assistant to use: "Claude" or "ChatGPT"
- **AND** the workspace launches with the selected assistant
