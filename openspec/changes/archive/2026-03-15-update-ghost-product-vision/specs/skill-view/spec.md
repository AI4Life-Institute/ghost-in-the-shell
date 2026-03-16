## ADDED Requirements

### Requirement: Skill Library
The system SHALL display a searchable library of saved Skills in the left panel of the Skill
view. Each Skill is a reusable automation with a name, description, category tag, and a set
of configurable parameters.

A Skill is the equivalent of a saved workflow or script: it may call AI, run shell commands,
trigger browser Tasks, or read/write Data — all defined once and run repeatedly with different
parameter values.

#### Scenario: User browses saved Skills
- **WHEN** the user opens Skill mode
- **THEN** the left panel lists all saved Skills with name, one-line description, and category tag
- **AND** a search input at the top filters the list by name or tag as the user types

#### Scenario: User selects a Skill
- **WHEN** the user clicks a Skill in the library
- **THEN** the right panel shows the Skill detail: full description, parameter form, Run button,
  and run history below

### Requirement: Skill Parameter Form and Run
The system SHALL provide a parameter form for each Skill, allowing the user to fill in values
and run the Skill with a single click. The form is generated from the Skill's parameter
definition — no code editing required to use a Skill.

#### Scenario: User fills parameters and runs
- **WHEN** the user fills in the parameter fields and clicks "Run"
- **THEN** a run entry appears immediately at the top of the run history with status "⏳ Running"
- **AND** a real-time output panel expands below the Run button showing live output lines
  as they are produced (streamed, not buffered)
- **AND** the Skill sidebar badge increments by 1

#### Scenario: Skill completes successfully
- **WHEN** a Skill run finishes without error
- **THEN** the run entry status updates to "✓ Done" with elapsed time
- **AND** the live output panel shows the final output and a "Copy output" button
- **AND** if the Skill produced structured data (JSON, CSV), a "View in Data →" link appears
  that navigates to Data mode with that result pre-loaded

#### Scenario: Skill fails
- **WHEN** a Skill run exits with an error
- **THEN** the run entry status shows "✗ Failed"
- **AND** the error message is shown in a red-tinted output panel with the full stack trace
- **AND** an "AI Debug" section appears automatically below the error, showing a one-paragraph
  diagnosis and suggested fix written by the AI (Claude reads the error and explains it in plain
  language, without the user having to ask)
- **AND** an "Apply fix in Code →" button opens the relevant file in Code mode with the fix
  highlighted

### Requirement: Skill Run History
The system SHALL persist a run history for each Skill, showing past runs with their parameters,
status, timestamp, and output, so the user can audit what ran and replay runs with the same
parameters.

#### Scenario: User views run history
- **WHEN** the user scrolls below the parameter form in Skill detail
- **THEN** they see a list of past runs, newest first, each showing:
  - Status icon + label (✓ Done / ✗ Failed / ⏳ Running)
  - Timestamp and elapsed time
  - Parameter values used (compact single-line summary)
  - Expand arrow to see full output or error

#### Scenario: User replays a past run
- **WHEN** the user clicks "Replay" on a past run entry
- **THEN** the parameter form above is pre-filled with that run's parameter values
- **AND** the user can edit and click Run, or click Run immediately to repeat with identical inputs

### Requirement: Skill Creation
The system SHALL allow users to create new Skills by describing what they want in natural
language; the AI generates the Skill definition, which the user can review and save.

#### Scenario: User creates a Skill from natural language
- **WHEN** the user clicks "＋ New Skill" and types a description
  (e.g. "Every morning, fetch AAPL stock price and save to Data")
- **THEN** the AI generates a Skill definition with name, parameters, and implementation
- **AND** the user sees a preview of the generated Skill and can edit it before saving
- **AND** once saved, it appears in the Skill library immediately
