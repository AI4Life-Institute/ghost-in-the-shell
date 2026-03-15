# Project Context

## Purpose
Ghost in the Shell (GITS) is a **social platform ↔ tmux bridge** that lets users remotely control coding CLIs (Claude Code, Codex, OpenCode) running in tmux sessions through Discord (and later Telegram/Slack). Core principle: **tmux is the single source of truth**; chat platforms are remote controllers and displays.

Key goals:
- Platform-agnostic core with adapter pattern for multiple chat platforms
- CLI-agnostic via tmux (no SDK coupling — any coding CLI works)
- Terminal screenshot rendering (ANSI→PNG) for visual feedback
- Auto-recovery from tmux failures with session resume
- Gradual feature rollout: Discord MVP → output monitoring → Telegram

## Tech Stack
- **Python 3.12+** — primary language
- **libtmux >=0.37.0** — tmux session/window control
- **discord.py >=2.4** — Discord bot framework
- **Pillow >=10.0** — ANSI→PNG screenshot rendering
- **pydantic-settings >=2.0** — typed configuration from env vars
- **asyncio** — async runtime; blocking libtmux calls wrapped in `asyncio.to_thread()`
- **uv** — package management
- **ruff** — linting + formatting
- **pytest + pytest-asyncio** — testing
- **hatchling** — build system

## Project Conventions

### Code Style
- Linting: `ruff` with rules E, F, I, UP, B, SIM
- Type hints throughout (Python 3.12 style — `str | None`, `list[str]`)
- Docstrings on classes and public methods (Chinese comments acceptable in spec/design docs)
- Line length: ruff defaults
- Import sorting: isort-compatible via ruff

### Architecture Patterns
- **tmux-first**: all CLI interaction goes through tmux, never SDK-direct
- **Adapter pattern**: `PlatformAdapter` ABC in `src/gits/adapters/base.py`; each platform (Discord, Telegram) implements this interface
- **Core/Adapter separation**: `src/gits/core/` contains platform-agnostic logic; `src/gits/adapters/` contains platform-specific code
- **Async throughout**: blocking I/O wrapped in `asyncio.to_thread()`
- **JSON state persistence**: `~/.gits/state.json` with atomic writes (write-to-temp + rename)
- **3-tier font fallback**: JetBrainsMono → NotoSansCJK → Symbola for screenshot rendering

### Testing Strategy
- Unit tests with pytest + pytest-asyncio (~105 tests, ~1,100 LOC)
- Tests organized by module: `test_engine.py`, `test_session.py`, `test_ansi.py`, `test_launcher.py`, `test_screenshot.py`, `test_atomic_write.py`
- Heavy use of mocking for tmux and Discord interactions
- All core modules have test coverage

### Git Workflow
- Single `master` branch (no develop/feature branches observed yet)
- Descriptive commit messages summarizing the change
- No CI/CD pipeline configured yet

## Domain Context
- **tmux** is a terminal multiplexer; GITS manages tmux sessions/windows/panes programmatically via libtmux
- **Coding CLIs** (Claude Code, Codex CLI, OpenCode) are AI-powered terminal tools; each has its own session resume mechanism
- **Session binding** = mapping a Discord channel/thread to a specific tmux window running a coding CLI
- **ANSI escape sequences** encode terminal colors/styles; the screenshot engine parses these to render PNG images
- **Reference projects**: ccbot (Python/Telegram/tmux) and claude-on-discord (TypeScript/Discord/SDK) — GITS combines the best of both
- SPEC.md contains the full architecture design document (in Chinese)

## Important Constraints
- tmux must be installed and running on the host machine
- Font files (JetBrainsMono, NotoSansCJK, Symbola) must be available for screenshot rendering
- Discord bot token required; access controlled via ALLOWED_USERS and ALLOWED_GUILDS
- ALLOWED_PATHS can restrict which directories users may bind to
- Sensitive env vars (API keys, tokens) are scrubbed from tmux session environment

## External Dependencies
- **Discord API** — via discord.py for bot interactions, slash commands, threads, buttons
- **tmux server** — local process; health-monitored with auto-recovery
- **Coding CLIs** — launched inside tmux windows:
  - **Claude Code** — JSONL at `~/.claude/projects/<hash>/*.jsonl`, hooks via `~/.claude/settings.json`
  - **Codex CLI (OpenAI)** — JSONL at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, submit via Escape+Enter
  - **Copilot CLI (GitHub)** — JSONL at `~/.copilot/session-state/<id>/events.jsonl`, hooks via `~/.copilot/hooks/hooks.json`, submit via Escape+Enter
  - **OpenCode (anomalyco)** — JSON files at `~/.local/share/opencode/storage/`, dir-polling for output, also has REST API (`opencode serve`) and SDK (`@opencode-ai/sdk`)
