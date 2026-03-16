# Change: Add OpenCode session plugin for session_map integration

## Why

OpenCode output monitoring currently relies on directory polling to discover sessions,
but the JSONL monitor cannot pick up session IDs because there is no hook to write
`~/.gits/session_map.json` — unlike Claude Code (SessionStart hook) and Codex
(hooks.json). This means OpenCode responses never get forwarded back to Discord.

OpenCode now supports a **plugin system** (JS/TS modules in `~/.config/opencode/plugins/`)
with lifecycle events including `session.created`. We can write a plugin that fires on
session creation and writes session info to `session_map.json`, exactly like the Claude
hook does.

## What Changes

- **New**: OpenCode plugin file (`gits-session-hook.mjs`) that listens for
  `session.created` and writes `{session_id, cwd}` keyed by `{tmux_session}:{window_id}`
  to `~/.gits/session_map.json`
- **New**: Auto-install logic in `engine.py` that copies the plugin to
  `~/.config/opencode/plugins/` on startup (idempotent, like the Claude hook install)
- **Modified**: `_ensure_hooks_installed()` in engine.py to also install the OpenCode plugin
- **Modified**: `__main__.py` to add `gits hook --install-opencode` CLI entry point

## Impact

- Affected specs: `cli-output-monitoring` (OpenCode session discovery via plugin)
- Affected code:
  - `src/gits/core/engine.py` — auto-install plugin
  - `src/gits/__main__.py` — manual install command
  - New file: plugin JS module (bundled as package data or generated)
- No changes to `jsonl_monitor.py` — existing `_poll_once` already reads
  `session_map.json` and `_check_opencode_binding` already handles OpenCode output
