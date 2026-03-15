## ADDED Requirements

### Requirement: Tool Definition Files
The system SHALL load Tool definitions from Markdown files in `~/.gits/tools/`.
A Tool file describes a CLI command invocation, optional working directory, optional environment variables, and optional timeout.
Tools MAY reference system-installed CLIs (no source code required) or local project scripts.

#### Scenario: Load system tool
- **WHEN** `~/.gits/tools/openclaw.md` exists with a `## Command` section
- **THEN** SkillLoader parses it as a Tool with name derived from filename stem

#### Scenario: Load local script tool
- **WHEN** `~/.gits/tools/discord-run.md` has `## Command`, `## Working Directory`, and `## Environment` sections
- **THEN** SkillLoader parses all three fields and makes them available to SkillRunner

#### Scenario: Missing working directory
- **WHEN** a Tool file has no `## Working Directory` section
- **THEN** the Tool runs in the user's home directory

### Requirement: Tool Inline Command in Skill Step
The system SHALL allow Skill steps to specify a command inline (without a Tool file) by providing a full command string and optional `working_dir`.

#### Scenario: Inline step
- **WHEN** a Skill step contains a raw command string not matching any Tool filename
- **THEN** SkillRunner executes the command directly using the step's `working_dir` if provided

### Requirement: Shell Environment Inheritance
At startup, the system SHALL execute `zsh -c env` (or `bash -c env` as fallback) to capture the user's full login shell environment, including PATH, and apply it to all Tool subprocess executions.

#### Scenario: Node tools accessible
- **WHEN** Ghost is launched as a macOS .app (which strips PATH)
- **AND** the user's shell has `/opt/homebrew/bin` in PATH
- **THEN** Tool commands using `npx` or `node` resolve correctly

#### Scenario: Explicit env override
- **WHEN** a Tool file has a `## Environment` section with `KEY=value` entries
- **THEN** those values override the inherited shell environment for that Tool's executions
