## 1. Prototype (POC) — DONE

- [x] 1.1 Create plugin file `gits-session-hook.mjs` with `session.created` handler
- [x] 1.2 Install plugin via opencode.json `"plugin"` config (directory auto-load doesn't work in v1.2.26)
- [x] 1.3 Start OpenCode in a tmux window, verify `~/.gits/session_map.json` gets written
- [ ] 1.4 Bind an OpenCode channel in Discord, confirm output forwarded back

Key findings:
- Plugin must use `event` hook (not per-event named hooks)
- Session object at `event.properties.info` with `id` and `directory` fields
- Install format: `"plugin": ["gits-session-hook@file:/path/to/plugin/dir"]`
- `execSync`, `fs`, `process.env.TMUX_PANE` all work inside Bun plugin runtime

## 2. Productionize (after POC validated)

- [x] 2.1 Bundle plugin source in `src/gits/plugins/opencode/gits-session-hook.mjs`
- [x] 2.2 Add `_install_opencode_plugin()` in `__main__.py` — write plugin dir + modify opencode.json
- [x] 2.3 Add `--install-opencode` flag to `gits hook` subcommand
- [x] 2.4 Update `_ensure_hooks_installed()` in `engine.py` to auto-install plugin
- [x] 2.5 Make install idempotent (skip if plugin already in opencode.json config)

## 3. Testing

- [ ] 3.1 End-to-end test on Linux
- [ ] 3.2 End-to-end test on macOS
- [ ] 3.3 Verify no regression for Claude/Codex hooks
