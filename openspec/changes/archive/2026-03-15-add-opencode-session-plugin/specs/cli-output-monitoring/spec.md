## ADDED Requirements

### Requirement: OpenCode Session Plugin Hook

The system SHALL provide an OpenCode plugin (`gits-session-hook.mjs`) that
listens for the `session.created` event and writes the session ID and working
directory to `~/.gits/session_map.json`, keyed by `{tmux_session}:{window_id}`.

The plugin MUST use the same `session_map.json` format as the Claude Code
SessionStart hook so that the existing JSONL monitor can discover session IDs
without modification.

#### Scenario: OpenCode session created in tmux

- **WHEN** OpenCode starts a new session inside a GITS-managed tmux window
- **AND** the `gits-session-hook.mjs` plugin is installed in `~/.config/opencode/plugins/`
- **THEN** the plugin writes `{session_id, cwd}` to `~/.gits/session_map.json`
  under the key `{tmux_session_name}:{window_id}`
- **AND** the JSONL monitor picks up the session ID on its next poll cycle
- **AND** OpenCode output is forwarded to the bound Discord channel

#### Scenario: Plugin runs outside tmux

- **WHEN** OpenCode starts a session but `TMUX_PANE` is not set
- **THEN** the plugin does nothing (no-op, no error)

#### Scenario: session_map.json does not exist yet

- **WHEN** the plugin fires and `~/.gits/session_map.json` does not exist
- **THEN** the plugin creates `~/.gits/` directory and writes a new `session_map.json`

### Requirement: OpenCode Plugin Auto-Install

The system SHALL automatically install the OpenCode session plugin to
`~/.config/opencode/plugins/` during engine startup, alongside the Claude Code
hook installation.

The install MUST be idempotent: if the plugin file already exists with matching
content, it SHALL be skipped.

#### Scenario: First engine start with OpenCode available

- **WHEN** the engine starts for the first time on a machine
- **THEN** `gits-session-hook.mjs` is copied to `~/.config/opencode/plugins/`
- **AND** subsequent OpenCode sessions trigger the session hook

#### Scenario: Plugin already installed

- **WHEN** the engine starts and the plugin file already exists with current content
- **THEN** no file write occurs

#### Scenario: Plugin outdated

- **WHEN** the engine starts and the plugin file exists but content differs
- **THEN** the plugin file is overwritten with the current version
