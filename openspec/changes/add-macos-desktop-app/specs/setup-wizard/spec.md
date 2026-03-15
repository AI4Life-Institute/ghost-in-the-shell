## ADDED Requirements

### Requirement: Welcome Wizard
The system SHALL provide a friendly, step-by-step welcome wizard on first launch that guides users through all necessary setup — no technical knowledge required.

#### Scenario: First-time user completes setup
- **WHEN** the user opens the app for the first time
- **THEN** a welcome screen appears with a warm greeting and a "Get Started" button
- **AND** the wizard walks through: (1) log in to your AI account, (2) connect to Discord (optional), (3) choose a project folder, (4) verify everything works

#### Scenario: User skips optional steps
- **WHEN** the user already has Discord set up or doesn't want to use it
- **THEN** they can skip that step with "Skip for now"

### Requirement: AI Account Login Step
The wizard SHALL let users log in to their Claude or ChatGPT account with a single button click — the browser handles the sign-in, no API keys or tokens to deal with.

#### Scenario: User logs in during wizard
- **WHEN** the user reaches the "Sign in to your AI" step
- **THEN** they see two clear buttons: "Log in to Claude" and "Log in to ChatGPT"
- **AND** clicking either opens the browser for sign-in
- **AND** once signed in, the wizard shows a green checkmark and moves to the next step

#### Scenario: User is already signed in
- **WHEN** the user already has Claude or Codex credentials on their Mac
- **THEN** the wizard auto-detects this, shows "Already signed in", and lets the user continue

### Requirement: Guided Discord Setup
The wizard SHALL walk users through connecting to Discord with visual, step-by-step instructions — like a tutorial, not a technical manual.

#### Scenario: User sets up Discord from scratch
- **WHEN** the user selects "I need to create a Discord bot"
- **THEN** each step shows: a clear numbered instruction, an illustration or screenshot of what to click, and a "Copy" button for links and values
- **AND** required permissions are pre-selected (the user doesn't need to figure them out)

#### Scenario: User adds bot to their server
- **WHEN** the user has entered their bot token
- **THEN** the wizard generates an invite link with the right permissions already set
- **AND** shows "Copy Link" and "Open in Browser" buttons with a brief explanation of what will happen

### Requirement: Settings Panel
The system SHALL provide an easy-to-use Settings page where users can change any configuration after the initial wizard.

#### Scenario: User changes settings later
- **WHEN** the user opens Settings from the sidebar
- **THEN** they see organized sections: "AI Account" (login status + log in/out), "Discord" (token + connection), "Projects", and "Advanced"
- **AND** changes take effect immediately without needing to restart the app

### Requirement: Setup Verification
The wizard SHALL verify everything is working correctly before the user starts using the app.

#### Scenario: All checks pass
- **WHEN** the user reaches the final "Ready to Go" step
- **THEN** the wizard runs quick checks: AI login valid, Discord connection (if configured)
- **AND** shows a friendly green checkmark for each passing check with a "You're all set!" message

#### Scenario: Something isn't right
- **WHEN** a check fails (e.g., AI login expired)
- **THEN** the wizard shows a plain-language error ("Your Claude login seems to have expired — click to sign in again") with a "Go Back" button
