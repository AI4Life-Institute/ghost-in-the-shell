# Multi-CLI Backend Support

## Summary
Add Codex CLI (OpenAI), Copilot CLI (GitHub), and OpenCode (anomalyco) as first-class coding CLI backends alongside Claude Code. Users select which CLI to use via a Discord `/bind` dropdown.

## Motivation
GITS's architecture is already CLI-agnostic by design (tmux-first), but the implementation has Claude Code assumptions baked into session discovery, JSONL monitoring, terminal prompt detection, and hook installation. This change makes the multi-CLI promise real by implementing concrete support for each CLI's data formats and UI patterns.

## Scope
- **In scope**: Session discovery, JSONL/output monitoring, terminal approval UI detection, `/bind` CLI selector, per-CLI permission flags, per-CLI resume commands
- **Out of scope**: CLI installation/upgrade, provider auth management, per-CLI slash command variants (e.g. Codex-specific `/model` choices)

## Current State (Prototype Complete)
A working prototype has been implemented and validated against real data:
- **Codex**: Session discovery from `~/.codex/sessions/`, JSONL parsing, Azure config, approval UI regex (`CodexApproval`), interactive mode tested (Escape+Enter to submit)
- **OpenCode**: Session discovery from `~/.local/share/opencode/storage/`, project/session/message JSON structure mapped, approval UI regex (`OpenCodePermission`), Azure config
- **Copilot**: Resume templates and JSONL finder scaffolded (not yet installed/tested)

## Key Technical Decisions
1. **Codex submit = Escape then Enter** (not bare Enter) — affects tmux `send-keys` forwarding
2. **OpenCode uses per-file JSON** (not JSONL) — needs directory-watching monitor, not byte-offset tail
3. **Codex JSONL has no approval events** — approval detection must use terminal parser, not JSONL
4. **Codex user messages are mixed with system injections** — summary extraction skips `<`-prefixed and `# AGENTS`-prefixed blocks

## Capabilities
1. **cli-session-discovery** — Discover and resume sessions for each CLI type
2. **cli-output-monitoring** — Monitor assistant output from each CLI's log format
3. **cli-approval-detection** — Detect interactive approval prompts in terminal output
4. **cli-bind-selection** — Let users choose CLI type when binding a channel
