# Default Account Uses Native ~/.claude/ + Daily OAuth Refresh

## Why

The current multi-account isolation (proposed in `add-multi-account-hotswap`) injects `CLAUDE_CONFIG_DIR=$HOME/.claude-{name}` for **every** binding whose `claude_account` is non-null — including the default account. This collides with claude's OAuth refresh path on macOS, which writes refreshed tokens into the **global** macOS Keychain entry (`Claude Code-credentials`). The keychain has no per-account scoping; when ghost rotates between accounts (or even when the default account is in steady-state use), the keychain entry drifts out of sync with whichever `~/.claude-{name}/.credentials.json` is "active." Symptoms:

- User is prompted to re-login despite never doing anything wrong (observed in 2026-05-17 session).
- `gits account list` reports `stale (run claude --resume)` even for the account the user just used.
- The README documents the workaround (manual `claude --resume`) but the experience is hostile.

Meanwhile, **native** `claude ...` invocations (no `CLAUDE_CONFIG_DIR`) almost never trigger re-login, because claude's own refresh loop keeps the keychain warm without ghost interfering.

This change does two coupled things:

1. **Default account uses native paths.** When a binding's `claude_account` equals `manifest.default`, ghost treats it as `None` for all path/injection purposes — sessions, settings, credentials, and launch commands all resolve to `~/.claude/...` with no `CLAUDE_CONFIG_DIR`. The most-used account now stays in the well-trodden refresh path.
2. **Daily background refresh for non-default accounts.** A launchd plist (macOS) runs once a day, iterating non-default accounts in the manifest and invoking `CLAUDE_CONFIG_DIR=~/.claude-{name} claude --print ping` per account. This exercises claude's built-in OAuth refresh path so isolated accounts don't drift to "stale" between manual switches.

The two are coupled because (1) eliminates the keychain-drift problem for the default account, but non-default accounts still have it. (2) is the keepalive for those.

## What Changes

- **MODIFIED**: per-binding launch command no longer injects `CLAUDE_CONFIG_DIR` when `claude_account == manifest.default`.
- **MODIFIED**: `AccountLayout.{projects_dir,settings_file,credentials_file}` route to `~/.claude/...` (not `~/.claude-{default}/...`) when the account name equals the manifest default.
- **MODIFIED**: `JsonlMonitor` watches `~/.claude/projects/` for default-account bindings.
- **ADDED**: new `gits account refresh` CLI subcommand that runs `claude --print ping` per non-default account and reports success/failure.
- **ADDED**: new `gits account refresh-install` / `refresh-uninstall` CLI subcommands that install/remove a launchd plist (`~/Library/LaunchAgents/com.gits.token-refresh.plist`) running `gits account refresh` daily.
- **ADDED**: one-shot migration when this version first runs: if `manifest.default == X` and both `~/.claude/.credentials.json` and `~/.claude-X/.credentials.json` exist, ghost picks whichever has the most recent mtime as the canonical native creds and logs the choice. No file is overwritten without an explicit user confirmation — migration runs in dry-run mode by default.

## Impact

- **Affected specs**: `multi-account` (modifies 3 requirements from the in-progress `add-multi-account-hotswap`, adds 2 new requirements).
- **Affected code**:
  - `src/gits/core/account.py` (`AccountLayout` — route default to native paths)
  - `src/gits/core/launcher.py` (`build_launch_command` — skip injection for default)
  - `src/gits/core/jsonl_monitor.py` (account-aware paths use the same default-aware helper)
  - `src/gits/cli_account.py` (add `refresh`, `refresh-install`, `refresh-uninstall` subcommands)
  - new `src/gits/core/token_refresh.py` (the refresh job logic, separate so it's testable without launchd)
  - new `scripts/com.gits.token-refresh.plist.template` (launchd plist template)
- **Operational impact**:
  - Users on macOS only get the launchd plist. Linux gets a no-op `refresh-install` that prints a message about adding to cron manually (future work — out of scope here).
  - First run after upgrade: default account's launch command changes from injected to native. Users may briefly see one re-login if `~/.claude/.credentials.json` is older than `~/.claude-{default}/.credentials.json`. The dry-run migration warning surfaces this case at upgrade time.
  - No state.json schema change. `claude_account` field semantics shift but the field itself is unchanged.
- **Backward compat**: bindings created before this change with `claude_account == manifest.default` keep working without modification — they just stop getting `CLAUDE_CONFIG_DIR` injected.
- **Relationship to `add-multi-account-hotswap`**: this change refines requirements from that proposal that have not yet been archived. Where this change's MODIFIED Requirements conflict with the older proposal, this change wins. The expectation is both archive together (or this one archives slightly later, replacing the relevant requirement text in `specs/multi-account/spec.md`).
