## ADDED Requirements

### Requirement: Default Account Routes To Native Claude Directory

When a binding's `claude_account` field equals the value of `manifest.default`, ghost SHALL treat that binding as if `claude_account` were `None` for the purposes of:

- Building the launch command (no `CLAUDE_CONFIG_DIR` injection).
- Resolving session JSONL paths (uses `~/.claude/projects/`).
- Resolving credentials, settings, and any other per-account filesystem paths (uses `~/.claude/`).
- JsonlMonitor's watch list (the default account is served by the `~/.claude/projects/` watcher, not a separate `~/.claude-{default}/projects/` watcher).

This requirement supersedes the behavior described in the `add-multi-account-hotswap` proposal's "Per-Binding Account Field" and "Launch Command Honors Account" requirements *only* for the case where the account name equals the manifest default. All other cases — non-default accounts, `claude_account=None` — behave exactly as specified there.

The motivation is macOS Keychain coupling: claude's OAuth refresh path writes refreshed tokens to a single global keychain entry (`Claude Code-credentials`) with no per-account scoping. Routing the most-used account through `~/.claude/` lets claude's native refresh loop keep that keychain entry warm without ghost interference, eliminating spurious re-login prompts.

#### Scenario: Default account binding launches without CLAUDE_CONFIG_DIR
- **WHEN** `manifest.default` is `"personal"`
- **AND** the launcher builds a command for a binding with `claude_account = "personal"` and base_type `claude`
- **THEN** the resulting command does NOT begin with `CLAUDE_CONFIG_DIR=...`
- **AND** the command is exactly the same as if `claude_account` had been `None`

#### Scenario: Non-default account still gets injection
- **WHEN** `manifest.default` is `"personal"`
- **AND** the launcher builds a command for a binding with `claude_account = "work"` and base_type `claude`
- **THEN** the resulting command begins with `CLAUDE_CONFIG_DIR=/Users/<user>/.claude-work `

#### Scenario: Session path lookup for default account uses native dir
- **WHEN** `manifest.default` is `"personal"`
- **AND** `launcher.get_session_file(work_dir, "claude", session_id, claude_account="personal")` is called
- **THEN** ghost looks for the JSONL at `~/.claude/projects/<work-dir-hash>/<session_id>.jsonl`
- **AND** does NOT look at `~/.claude-personal/projects/...`

#### Scenario: JsonlMonitor consolidates default-account watch path
- **WHEN** `manifest.default` is `"personal"` and accounts are `["personal", "work"]`
- **THEN** JsonlMonitor watches exactly two paths: `~/.claude/projects/` (for `None` AND default-account bindings) AND `~/.claude-work/projects/`
- **AND** does NOT watch `~/.claude-personal/projects/`

#### Scenario: Default unset reverts behavior
- **WHEN** `manifest.default` is `null` (no default set, or default was removed)
- **AND** a binding has `claude_account = "personal"`
- **THEN** the launcher injects `CLAUDE_CONFIG_DIR=$HOME/.claude-personal` exactly as the original `add-multi-account-hotswap` rule specified
- **AND** session paths resolve to `~/.claude-personal/projects/`

#### Scenario: Manifest load failure is fail-safe
- **WHEN** the account vault cannot be loaded (manifest missing, JSON corrupt)
- **AND** a binding has `claude_account = "personal"`
- **THEN** ghost treats the binding as having a non-default account (no special-casing) and injects `CLAUDE_CONFIG_DIR=$HOME/.claude-personal`
- **AND** ghost logs a WARN about the unreadable manifest but does NOT crash the launcher

### Requirement: Daily OAuth Token Refresh For Non-Default Accounts

ghost SHALL provide a mechanism to periodically exercise claude's OAuth refresh path for every non-default account, so that isolated accounts' refresh tokens do not silently expire from disuse. The mechanism MUST:

- Iterate every account in `manifest.accounts` whose name is NOT equal to `manifest.default`.
- For each, invoke `claude --print ping` as a subprocess with `CLAUDE_CONFIG_DIR=<account_dir>` and a 60s timeout.
- Report per-account success/failure (with stderr tail on failure) and an overall summary.
- NOT call any non-claude refresh endpoint directly; the refresh is delegated entirely to claude's own startup path.

**In-process scheduler (primary mechanism)**: the ghost daemon SHALL run a background asyncio task (`TokenRefreshScheduler`) that fires the refresh once per 24h interval. State (`last_refresh_at`) MUST be persisted to `~/.gits/token_refresh_state.json` so daemon restarts do not retrigger a recent refresh. The blocking subprocess call MUST be offloaded via `asyncio.to_thread` so the event loop stays responsive. This mechanism is portable: it works on any machine where ghost runs, without host-level scheduler setup.

**Optional launchd backstop (macOS only)**: ghost MAY also provide `gits account refresh-install` / `refresh-uninstall` commands that register/remove a launchd plist (`~/Library/LaunchAgents/com.gits.token-refresh.plist`) running `gits account refresh` daily at 04:00. This is a backstop for environments where the daemon may be down for extended periods. On Linux, `refresh-install` MUST print a cron snippet and exit 0 without writing system files.

The refresh job MUST be invocable from the CLI for manual recovery (`gits account refresh`), independent of any scheduling mechanism.

#### Scenario: Refresh skips default account
- **WHEN** `manifest.default` is `"personal"` and `manifest.accounts` is `[personal, work, sandbox]`
- **AND** `gits account refresh` is run
- **THEN** ghost invokes `CLAUDE_CONFIG_DIR=~/.claude-work claude --print ping` and `CLAUDE_CONFIG_DIR=~/.claude-sandbox claude --print ping`
- **AND** does NOT invoke any command for `personal`
- **AND** prints a two-line success summary for the two non-default accounts

#### Scenario: Refresh continues on partial failure
- **WHEN** two non-default accounts exist and the first one's refresh subprocess exits 1 (e.g., refresh token expired)
- **THEN** ghost still invokes the second account's refresh
- **AND** the final exit code is 1 (non-zero because at least one failed)
- **AND** the output includes the failed account name and the tail of its stderr

#### Scenario: Refresh-install on macOS writes a launchd plist
- **WHEN** the user runs `gits account refresh-install` on macOS
- **THEN** ghost writes `~/Library/LaunchAgents/com.gits.token-refresh.plist` with `StartCalendarInterval` at 04:00 daily
- **AND** runs `launchctl bootstrap gui/$(id -u) <plist>` (or equivalent) to load it
- **AND** subsequent `launchctl list` output contains the label `com.gits.token-refresh`

#### Scenario: Refresh-install on Linux prints cron guidance
- **WHEN** the user runs `gits account refresh-install` on Linux
- **THEN** ghost prints a snippet to add to crontab (e.g., `0 4 * * * /path/to/gits account refresh`)
- **AND** exits 0 without writing any file under `~/Library/` or `~/.config/systemd/`

#### Scenario: Refresh-uninstall is idempotent
- **WHEN** the user runs `gits account refresh-uninstall` and no plist is installed
- **THEN** the command exits 0 with a message that no installation was found
- **AND** does NOT raise an error

#### Scenario: Manual single-account refresh works for default
- **WHEN** the user runs `gits account refresh --account personal` and `personal` IS the default
- **THEN** ghost invokes `claude --print ping` WITHOUT `CLAUDE_CONFIG_DIR` (because default routes to native, per the Default Account Routes To Native Claude Directory requirement)
- **AND** reports success/failure for the personal account

#### Scenario: In-process scheduler persists last refresh across daemon restarts
- **WHEN** the daemon runs refresh successfully at time T
- **AND** the daemon is restarted before T + interval_s
- **THEN** the scheduler reads `~/.gits/token_refresh_state.json`, computes `last + interval - now > 0`, and waits the remaining time rather than refreshing immediately
- **AND** ghost does NOT consume tokens on every restart

#### Scenario: In-process scheduler does not block the event loop
- **WHEN** the scheduler fires `refresh_all_non_default`
- **THEN** the blocking subprocess call runs in a thread pool via `asyncio.to_thread`
- **AND** other engine tasks (jsonl_monitor, health monitor) continue processing during the refresh

### Requirement: `record_switch` Does Not Auto-Update Manifest Default

`AccountVault.record_switch` MUST NOT mutate `manifest.default`. The default account is a sticky, user-set property — set on the first `gits account add` (when no default exists yet) and only changed via an explicit `set_default()` call. Switching a single binding's account does NOT redefine the manifest default.

This requirement supersedes the "Default Account Auto-tracking" requirement from `add-multi-account-hotswap` for the "switch updates default" scenario. The reason: with the Default Account Routes To Native Claude Directory rule above, auto-updating the default on every switch causes the *new* target to become default → which routes it through native `~/.claude/` → which uses the *previous* default's credentials. The visible bug: switching from personal to work respawns claude with no `CLAUDE_CONFIG_DIR`, so the "work" binding actually runs as personal.

#### Scenario: Switch preserves manifest default
- **WHEN** `manifest.default` is `"personal"` and the user switches binding X from `personal` to `work`
- **THEN** `record_switch` writes `manifest.last_switch` and bumps work's `lastUsed`
- **AND** `manifest.default` remains `"personal"`
- **AND** the respawned binding X gets `CLAUDE_CONFIG_DIR=~/.claude-work` injected (because `"work" != manifest.default`)

#### Scenario: First account add still sets default
- **WHEN** `manifest.accounts` is empty and the user runs `gits account add personal`
- **THEN** `AccountVault.add` sets `manifest.default = "personal"` (first-add default-track is preserved — only `record_switch`'s auto-update is removed)

### Requirement: `oauth_usage.py` Reads Per-CONFIG_DIR Keychain

`UsageClient._read_access_token` SHALL try the macOS per-CONFIG_DIR keychain entry first (where claude writes refreshed tokens), then fall back to the on-disk `.credentials.json`. The keychain service name is derived as:

- `Claude Code-credentials` (no suffix) for native `~/.claude/` invocations (default-routed accounts).
- `Claude Code-credentials-<sha256(absolute_config_dir_path)[:8]>` for isolated accounts.

When a vault is available, the client SHALL try the default service first for default-routed accounts, and the suffix-derived service first for non-default accounts. On non-macOS the keychain step is a no-op; file-only reading proceeds.

This requirement exists because claude on macOS often does NOT write refreshed access tokens back to `.credentials.json` — keychain is the live source. Without this fallback, `gits account list` shows `stale` / `no credentials` for accounts that are actually healthy.

The AUDIT INVARIANT from the parent `oauth_usage` module is preserved: only `claudeAiOauth.accessToken` is extracted; `refreshToken` is never read by this code path.

#### Scenario: Keychain token preferred when available
- **WHEN** an account has both a (stale) `.credentials.json` access token AND a live keychain entry
- **THEN** ghost uses the keychain token to query Usage
- **AND** the API returns 200 with real quota data (not 401 from the stale file token)

#### Scenario: File fallback when keychain absent
- **WHEN** an account's per-CONFIG_DIR keychain entry does not exist (or platform is Linux)
- **AND** `.credentials.json` exists with a valid access token
- **THEN** ghost uses the file token to query Usage

#### Scenario: Service candidate order honors default-routing
- **WHEN** `manifest.default` is `"personal"`
- **AND** `UsageClient._keychain_service_candidates("personal")` is called
- **THEN** the first candidate is `"Claude Code-credentials"` (no suffix; default account is native-routed)
- **AND** the second candidate is the suffix-derived service

- **WHEN** `UsageClient._keychain_service_candidates("work")` is called (non-default)
- **THEN** the first candidate is the sha256-derived service
- **AND** the second candidate is `"Claude Code-credentials"`
