# multi-account — delta for `update-token-refresh-covers-default`

## MODIFIED Requirements

### Requirement: Daily OAuth Token Refresh For All Accounts

ghost SHALL provide a mechanism to periodically exercise claude's OAuth
refresh path for **every** account in the manifest — the default included —
so that no account's refresh token silently expires from disuse. The
mechanism MUST:

- Iterate every account in `manifest.accounts`.
- For the account whose name equals `manifest.default`, invoke
  `claude --print ping` with `CLAUDE_CONFIG_DIR` **removed** from the
  environment, so the native `~/.claude/` path is exercised.
- For every other account, invoke `claude --print ping` with
  `CLAUDE_CONFIG_DIR=<account_dir>` and a 60s timeout.
- Report per-account success/failure (with stderr tail on failure) and an
  overall summary.
- NOT call any non-claude refresh endpoint directly; the refresh is
  delegated entirely to claude's own startup path.
- Expose an opt-out (`include_default=False`) for callers that
  deliberately want the default left alone.

This requirement supersedes "Daily OAuth Token Refresh For Non-Default
Accounts" from `add-default-account-native-and-refresh`. The reason: that
requirement assumed the operator's interactive `claude` use keeps the
default account's keychain entry warm. An operator who works through ghost
generates no native `~/.claude/` traffic, so the default account expires
and every binding routed to it answers `Login expired · Please run /login`
while ghost itself reports healthy.

The scheduling mechanisms (in-process `TokenRefreshScheduler` at a 24h
interval with `last_refresh_at` persisted to
`~/.gits/token_refresh_state.json`, and the optional macOS launchd
backstop at 04:00) are unchanged; only the set of accounts they sweep
widens.

#### Scenario: Refresh includes the default account
- **WHEN** `manifest.default` is `"personal"` and `manifest.accounts` is `[personal, work, sandbox]`
- **AND** `gits account refresh` is run
- **THEN** ghost invokes `claude --print ping` for `personal` with no `CLAUDE_CONFIG_DIR` in the environment
- **AND** invokes `CLAUDE_CONFIG_DIR=~/.claude-work claude --print ping` and `CLAUDE_CONFIG_DIR=~/.claude-sandbox claude --print ping`
- **AND** prints a three-line success summary

#### Scenario: Default-only install is still refreshed
- **WHEN** `manifest.accounts` is `[personal]` and `manifest.default` is `"personal"`
- **AND** the refresh sweep fires
- **THEN** ghost invokes `claude --print ping` for `personal` natively
- **AND** the CLI does NOT print "no accounts to refresh"

#### Scenario: Caller opts out of refreshing the default
- **WHEN** `refresh_all(vault, layout, include_default=False)` is called with default `"personal"` and accounts `[personal, work]`
- **THEN** only `work` is refreshed
- **AND** exactly one `RefreshResult` is returned

#### Scenario: A failing default does not abort the sweep
- **WHEN** the default account's refresh subprocess exits 1 (refresh token already expired)
- **THEN** ghost records that failure and continues to the remaining accounts
- **AND** the CLI exits 1 while still reporting each account's individual outcome
