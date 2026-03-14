# Change: Add Phase 2 — Output Monitoring, Terminal UI Bridge, and Discord Interactivity

## Why
MVP can send messages to tmux and take manual screenshots, but the experience is one-directional. Users must `/screenshot` to see what's happening. Phase 2 closes the loop: auto-push coding CLI output to Discord, detect interactive prompts and surface them as buttons, and handle Discord message limits gracefully.

## What Changes
- **Output Monitor** — dual-channel monitoring (JSONL file polling + tmux pane polling) that auto-pushes coding CLI output to the bound Discord channel
- **Terminal UI Bridge** — regex-based detection of Claude Code interactive prompts (permission, multi-choice, plan mode, etc.) → auto-screenshot + navigation buttons pushed to Discord
- **Message Chunking** — split long outputs at 2000-char Discord limit with code fence awareness
- **Screenshot Navigation Keyboard** — button grid below screenshots for terminal navigation (arrows, Esc, Enter, Ctrl-C, Tab, Refresh)
- **Interrupt/Abort Buttons** — persistent buttons on output messages for quick Escape / Ctrl-C
- **Streaming Message Updates** — edit existing Discord messages instead of flooding new ones (debounced)
- **Claude Code Hook** — `gits hook` integration for automatic session ID capture on CLI start

## Impact
- Affected specs: output-monitoring (new), terminal-ui-bridge (new), message-formatting (new), discord-interactions (new)
- Affected code: `src/gits/core/monitor.py` (new), `src/gits/core/terminal_parser.py` (new), `src/gits/core/ui_bridge.py` (new), `src/gits/core/engine.py` (modify), `src/gits/adapters/discord/bot.py` (modify), `src/gits/adapters/discord/formatter.py` (new), `src/gits/adapters/discord/buttons.py` (new), `src/gits/__main__.py` (modify hook command)
