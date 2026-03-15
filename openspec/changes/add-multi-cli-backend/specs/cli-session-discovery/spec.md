# CLI Session Discovery

## ADDED Requirements

### Requirement: Codex session discovery
The launcher MUST discover Codex CLI sessions by scanning `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` files, matching work directory via `session_meta.payload.cwd`, and extracting the real session UUID from `session_meta.payload.id`.

#### Scenario: Codex sessions exist for work directory
Given a Codex JSONL file with `session_meta.payload.cwd` matching the target directory
When `discover_sessions(work_dir, cli="codex")` is called
Then a CLISession is returned with the UUID from `payload.id` and a user-message summary

#### Scenario: Codex summary extraction skips system injections
Given a Codex JSONL with user role messages containing `<environment_context>` and `# AGENTS.md` prefixes
When extracting the summary
Then those messages are skipped and the first real user prompt is used

### Requirement: OpenCode session discovery
The launcher MUST discover OpenCode sessions by scanning `~/.local/share/opencode/storage/`, matching via `project/<id>.json` `worktree` or `sandboxes` fields, then reading `session/<projectID>/<sessionID>.json` for metadata.

#### Scenario: OpenCode sessions found via worktree match
Given a project JSON with `worktree` matching the target directory
When `discover_sessions(work_dir, cli="opencode")` is called
Then sessions for that project are returned with titles and message counts

### Requirement: Copilot session discovery
The launcher MUST discover Copilot CLI sessions by scanning `~/.copilot/session-state/<id>/events.jsonl` and `workspace.yaml`.

#### Scenario: Copilot sessions found via workspace.yaml
Given a Copilot session directory with `workspace.yaml` containing the target path
When `discover_sessions(work_dir, cli="copilot")` is called
Then matching sessions are returned

## MODIFIED Requirements

### Requirement: Session discovery routing
The `discover_sessions()` method MUST route to the correct CLI-specific discoverer based on the `cli` parameter, supporting `claude`, `codex`, `copilot`, and `opencode`.

#### Scenario: Unknown CLI returns empty
Given cli="vim" (unsupported)
When `discover_sessions()` is called
Then an empty list is returned
