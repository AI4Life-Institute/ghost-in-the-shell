# CLI Bind Selection

## ADDED Requirements

### Requirement: CLI type dropdown in /bind
The `/bind` command MUST offer a `cli` dropdown parameter with choices: Claude Code, Codex CLI (OpenAI), Copilot CLI (GitHub), OpenCode. Default is the global `coding_cli_command` setting.

#### Scenario: User selects Codex via dropdown
Given a user runs `/bind /path/to/project` with `cli=codex`
When the bind is processed
Then a Codex CLI session is launched and the binding stores `coding_cli=codex`

### Requirement: Per-CLI permission flag mapping
The engine MUST map the generic permission mode (default/acceptEdits/auto/bypassPermissions) to CLI-specific flags: Claude uses `--permission-mode`, Codex uses `--full-auto`.

#### Scenario: Auto mode with Codex
Given mode="auto" and cli="codex"
When the launch command is built
Then `--full-auto` is appended (not `--permission-mode auto`)

## MODIFIED Requirements

### Requirement: handle_bind accepts cli parameter
The `Engine.handle_bind()` method MUST accept an optional `cli` parameter, defaulting to `settings.coding_cli_command` when not provided.

#### Scenario: No cli specified uses default
Given no `cli` parameter in the bind call
When handle_bind processes the request
Then `settings.coding_cli_command` (default: "claude") is used
