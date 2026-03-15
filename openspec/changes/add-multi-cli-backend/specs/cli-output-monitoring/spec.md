# CLI Output Monitoring

## ADDED Requirements

### Requirement: Codex JSONL output extraction
The JSONL monitor MUST extract assistant text from Codex entries where `type=response_item`, `payload.role=assistant`, and content blocks have `type=output_text`.

#### Scenario: Codex assistant message detected
Given a Codex JSONL entry with `type=response_item` and `payload.content[0].type=output_text`
When `extract_assistant_content()` is called
Then the text content is returned

### Requirement: Codex JSONL file location
The JSONL monitor MUST find Codex session files by searching `~/.codex/sessions/` recursively for the session ID filename.

#### Scenario: Codex session file found
Given a binding with `coding_cli=codex` and `cli_session_id=rollout-2026-...`
When `_find_jsonl_file()` is called
Then the matching file under `~/.codex/sessions/` is returned

### Requirement: Copilot JSONL file location
The JSONL monitor MUST find Copilot session files at `~/.copilot/session-state/<session_id>/events.jsonl`.

#### Scenario: Copilot events file found
Given a binding with `coding_cli=copilot` and a valid session ID
When `_find_jsonl_file()` is called
Then `~/.copilot/session-state/<id>/events.jsonl` is returned

### Requirement: OpenCode output monitoring
The system MUST monitor OpenCode assistant output, either via directory polling of `part/<msgID>/*.json` files or via the OpenCode server API if available.

#### Scenario: New OpenCode assistant message detected
Given OpenCode writes a new `type=text` part file
When the monitor polls
Then the text content is forwarded to the bound Discord channel

## MODIFIED Requirements

### Requirement: CLI-aware file finder dispatch
The `_find_jsonl_file()` method MUST dispatch to CLI-specific finders based on `binding.coding_cli`.

#### Scenario: Claude binding uses Claude finder
Given a binding with `coding_cli=claude`
When `_find_jsonl_file()` is called
Then `_find_claude_jsonl()` is used
