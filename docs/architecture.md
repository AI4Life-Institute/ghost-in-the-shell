# Architecture

## Overview

```
Discord (slash commands, messages, buttons)
  ↕
Engine (orchestrator)
  ├── TmuxController      — libtmux, create/manage tmux windows
  ├── JsonlMonitor         — poll CLI session files, forward new output → Discord
  ├── PaneMonitor          — detect interactive prompts → Discord buttons
  ├── SessionManager       — persist channel ↔ window bindings
  ├── CodingCLILauncher    — multi-CLI launch/resume/session discovery
  ├── ScreenshotEngine     — ANSI terminal capture → PNG (Pillow)
  └── HealthMonitor        — health checks and auto-recovery
  ↕
tmux session ("gits")
  ├── Window @1  (Claude Code)
  ├── Window @2  (Codex)
  └── Window @N  (any supported CLI)
```

Key principle: **all CLIs run as interactive TUIs inside tmux** — never via SDK or non-interactive mode. tmux is the single source of truth.

The org / agent-coordination subsystem (the `reports_to` tree, `scope` ownership, dispatch guard, `onboard`/`lint`) is documented separately — see [org-model.md](org-model.md) for the narrative and [org-schema.md](org-schema.md) for the field-level spec.

## Python dependencies

| Package | Purpose |
|---|---|
| `discord.py >= 2.4` | Discord bot (slash commands, messages, buttons, threads) |
| `libtmux >= 0.37.0` | Programmatic tmux control (create windows, send keys, capture panes) |
| `Pillow >= 10.0` | Render ANSI terminal output to PNG screenshots |
| `pydantic-settings >= 2.0` | Typed config from environment variables / .env |

## Output monitoring

GITS monitors CLI output through two mechanisms:

### 1. JSONL Monitor

Polls CLI session files every 2 seconds for new assistant messages. Each CLI stores session data at known paths:

| CLI | Session file path | Format |
|---|---|---|
| Claude | `~/.claude/projects/<dir-hash>/<session-id>.jsonl` | JSONL: `type=assistant`, `message.content` |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*-<session-id>.jsonl` | JSONL: `type=response_item`, `payload.role=assistant` |
| OpenCode | `~/.local/share/opencode/storage/message/<session-id>/*.json` | Individual JSON files per message |

The monitor tracks byte offsets per file and only reads new content. On first discovery it skips to end-of-file to avoid replaying history.

### 2. Pane Monitor

Captures tmux pane content to detect interactive prompts:

- Permission requests (Allow/Deny)
- Multi-choice menus
- Bash command confirmations
- Plan mode confirmations

Detected prompts are converted to Discord button components. Button clicks send the corresponding keystrokes to tmux.

## Session tracking (Hook system)

Hooks map tmux windows to CLI session IDs:

```
CLI starts → SessionStart hook fires
  → gits hook reads session info from stdin
  → writes to ~/.gits/session_map.json: { "gits:@61": { "session_id": "xxx", "cwd": "/path" } }
  → JSONL monitor reads this map to find the correct session file
```

### Hook configuration per CLI

**Claude Code** — `~/.claude/settings.json`:
```json
{ "hooks": { "SessionStart": [{ "hooks": [{ "type": "command", "command": "gits hook", "timeout": 5 }] }] } }
```

**Codex** — `~/.codex/hooks.json` (requires `codex_hooks` feature flag in `config.toml`):
```json
{ "hooks": { "SessionStart": [{ "hooks": [{ "type": "command", "command": "gits hook", "timeout": 5 }] }] } }
```

**OpenCode** — uses a session plugin (`gits hook --install-opencode`) to write session info.

### Fallback (when hooks miss)

When the session ID doesn't match (e.g. CLI restarted), the JSONL monitor falls back to:
- **Codex**: scans all rollout JSONL files, checks `session_meta.cwd` against work_dir
- **OpenCode**: scans `project/*.json` matching `worktree` path

## CLI launch parameters

| CLI | Submit key | YOLO mode flag | Auto mode flag |
|---|---|---|---|
| Claude | `Enter` | `--permission-mode bypassPermissions` | `--permission-mode auto` |
| Codex | `Escape Enter` | `--dangerously-bypass-approvals-and-sandbox` | `--full-auto` |
| OpenCode | `Enter` | (not supported) | (not supported) |

Codex also gets `--enable codex_hooks` appended to enable hook support.

## State persistence

`~/.gits/` directory:

| File | Contents |
|---|---|
| `state.json` | Active bindings (channel → tmux window, CLI type, session ID, work dir) |
| `session_map.json` | tmux window ID → CLI session ID mapping (written by hooks) |
| `gits.log` | Application logs |

## Screenshot system

1. `tmux capture-pane -e -p` captures pane content with ANSI escape codes
2. Parse ANSI sequences (16-color, 256-color, true color)
3. Render with Pillow using font fallback: JetBrains Mono → Noto Sans CJK → Symbola
4. Runs in thread pool (`asyncio.to_thread`)

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `GITS_DISCORD_TOKEN` | (required) | Discord bot token |
| `ALLOWED_GUILDS` | `[]` | Allowed server IDs |
| `ALLOWED_USERS` | `[]` | Allowed user IDs |
| `TMUX_SESSION_NAME` | `gits` | tmux session name |
| `CODING_CLI_COMMAND` | `claude` | Default CLI |
| `LOG_LEVEL` | `INFO` | Log level |
| `ALLOWED_PATHS` | `[]` | Directories allowed for `/bind` |
| `SCREENSHOT_FONT_SIZE` | `28` | Screenshot font size |
| `PANE_POLL_INTERVAL` | `2.0` | Pane polling interval (seconds) |
| `JSONL_POLL_INTERVAL` | `2.0` | JSONL polling interval (seconds) |
| `HEALTH_CHECK_INTERVAL` | `5.0` | Health check interval (seconds) |
| `GITS_DIR` | `~/.gits` | State file directory |
| `THREAD_AUTO_ARCHIVE_MINUTES` | `10080` | Thread auto-archive (minutes, default 7 days) |

## Source layout

```
src/gits/
  __main__.py              — CLI entry point (gits start / hook / status)
  config.py                — Pydantic Settings configuration
  core/
    engine.py              — Main orchestrator, handles all Discord commands
    launcher.py            — CLI launch/resume/session discovery
    jsonl_monitor.py       — JSONL output polling
    monitor.py             — Pane interactive prompt detection
    session.py             — Binding persistence
    tmux.py                — tmux control wrapper
    screenshot.py          — ANSI → PNG rendering
    terminal_parser.py     — Claude Code prompt parsing
    health.py              — Health monitoring
  adapters/
    base.py                — PlatformAdapter abstract interface
    discord/bot.py         — Discord.py implementation
```
