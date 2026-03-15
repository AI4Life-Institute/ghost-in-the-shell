# Design: Multi-CLI Backend Support

## Architecture Overview

GITS has 4 CLI-coupled subsystems. Each needs per-CLI adaptation:

```
┌─────────────────────────────────────────────────────┐
│                   Engine                             │
│  ┌──────────┐  ┌──────────┐  ┌─────────────────┐   │
│  │ Launcher │  │  JSONL   │  │   Terminal       │   │
│  │ (session │  │ Monitor  │  │   Parser         │   │
│  │ discover │  │ (output  │  │   (approval      │   │
│  │ + resume)│  │  stream) │  │    detection)    │   │
│  └────┬─────┘  └────┬─────┘  └────────┬────────┘   │
│       │              │                  │            │
│  ┌────┴──────────────┴──────────────────┴────────┐  │
│  │         CLI-specific adapters                  │  │
│  │  ┌────────┐ ┌──────┐ ┌────────┐ ┌──────────┐ │  │
│  │  │ Claude │ │Codex │ │Copilot │ │ OpenCode │ │  │
│  │  └────────┘ └──────┘ └────────┘ └──────────┘ │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Per-CLI Data Formats

### Session Storage

| CLI | Path | Format | Work-dir matching |
|-----|------|--------|-------------------|
| Claude | `~/.claude/projects/<dir-hash>/<sid>.jsonl` | JSONL | dir-hash from path |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | JSONL | `session_meta.payload.cwd` |
| Copilot | `~/.copilot/session-state/<sid>/events.jsonl` | JSONL | `workspace.yaml → cwd:` |
| OpenCode | `~/.local/share/opencode/storage/{project,session,message,part}/**/*.json` | JSON files | `project/<id>.json → worktree` |

### Output Monitoring

| CLI | File to watch | Event format | Assistant content |
|-----|--------------|--------------|-------------------|
| Claude | `<sid>.jsonl` | `type=assistant, message.content[].type=text` | Direct |
| Codex | `rollout-*.jsonl` | `type=response_item, payload.role=assistant, payload.content[].type=output_text` | Direct |
| Copilot | `events.jsonl` | `type=assistant.message, data.content` | Direct |
| OpenCode | `part/<msgID>/*.json` | Individual files, `type=text` has `.text` | Watch dir for new files |

### Approval UI Patterns

| CLI | Top marker | Bottom marker | Options format |
|-----|-----------|---------------|----------------|
| Claude | `Do you want to proceed?` | `Esc to cancel` | `❯ 1. Yes` |
| Codex | `Would you like to run the following command?` | `Press enter to confirm or esc to cancel` | `› 1. Yes, proceed (y)` |
| Copilot | Same as Claude: `Do you want to proceed?` / `❯ 1. Yes` | `Esc to cancel` | `❯ 1. Yes` (identical to Claude) |
| OpenCode | `△ Permission required` | `Allow once   Allow always   Reject` | Button-style (not numbered) |

### Resume Commands

| CLI | By ID | Continue latest |
|-----|-------|-----------------|
| Claude | `claude --resume <id>` | `claude --continue` |
| Codex | `codex resume <id>` | `codex resume --last` |
| Copilot | `copilot --resume <id>` | `copilot --continue` |
| OpenCode | `opencode -s <id>` | `opencode -c` |

### Input Submission via tmux

| CLI | Submit key sequence | Notes |
|-----|-------------------|-------|
| Claude | `Enter` | Standard |
| Codex | `Escape` then `Enter` | Multi-line editor; Escape exits edit mode |
| Copilot | `Escape` then `Enter` | Same as Codex; multi-line editor |
| OpenCode | Full-screen TUI | `Enter` submits; better to use `opencode run` non-interactively or `opencode serve` + `opencode attach` |

## OpenCode Monitoring Strategy

OpenCode stores data as individual JSON files rather than appending to a single JSONL. Three approaches:

### Option A: Directory polling
Monitor `~/.local/share/opencode/storage/part/` for new files. Track known file set per session, detect new `type=text` parts. Simple but polling-based.

### Option B: `opencode run --format json` (RECOMMENDED)
Launch OpenCode with `opencode run --format json` instead of bare `opencode`. This outputs **newline-delimited JSON events** to stdout, including `message.part.updated`, tool calls, and session status. We can pipe this through tmux and read it like Codex JSONL.

### Option C: `opencode serve` + REST API / SDK
Start OpenCode as a headless server with `opencode serve`. Use the official SDK (`@opencode-ai/sdk` on npm, or `opencode-sdk-python`) to subscribe to sessions via SSE. This is the most robust approach but adds a dependency.

**Decision**: Dual-path — **tmux TUI (foreground) + file/API polling (background)**.

All CLIs run as interactive TUIs inside tmux (screenshots + human takeover). Output monitoring runs independently in the background:
- **Primary**: Option A (directory polling of `part/` files) — simple, no extra deps
- **Enhanced**: Option C (SDK/REST API) — can supplement dir-polling for richer data (tool call details, token usage) without interfering with the tmux TUI

This mirrors how Claude/Codex already work: interactive CLI in tmux + JSONL polling in background. The two paths are independent and don't conflict.

### OpenCode Official SDK References
- HTTP REST API: `opencode serve` → OpenAPI docs at `/doc`
- ACP: `opencode acp` (Agent Client Protocol, JSON-RPC over stdio)
- JS/TS SDK: `npm install @opencode-ai/sdk`
- Python SDK: `opencode-sdk-python`
- Go SDK: `opencode-sdk-go`

## Permission Mode Mapping

| Mode | Claude | Codex | Copilot | OpenCode |
|------|--------|-------|---------|----------|
| default | `--permission-mode default` | (default) | TBD | (default) |
| acceptEdits | `--permission-mode acceptEdits` | N/A | TBD | N/A |
| auto | `--permission-mode auto` | `--full-auto` | TBD | TBD |
| bypassPermissions | `--permission-mode bypassPermissions` | `--full-auto` | TBD | TBD |
