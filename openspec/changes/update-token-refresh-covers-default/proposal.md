# Change: Token Refresh Covers The Default Account

**Change ID:** `update-token-refresh-covers-default`
**Status:** proposed
**Supersedes (partially):** `add-default-account-native-and-refresh` — "Daily OAuth Token Refresh For Non-Default Accounts"

## Why

`add-default-account-native-and-refresh` deliberately excluded the default
account from the refresh sweep. The stated reasoning: the default account
routes through `~/.claude/` natively, so the operator's own interactive
`claude` use keeps its keychain entry warm — a scheduled ping would be
redundant token spend.

That assumption does not hold in production. Observed on 2026-09-02:

- `manifest.default` was `sharon` (61 of 153 bindings).
- Its refresh token in `Claude Code-credentials` expired at 19:24.
- At 20:22 channel `1505046563762733187` (`vault-kathy`) answered a user
  message with `Login expired · Please run /login`.
- The `sharongoogle` account — non-default, and therefore *covered* by the
  sweep — had failed the same way earlier at 13:15 and was recovered only
  because the operator happened to run `/login` in an interactive session.

The failure mode is asymmetric and user-visible: ghost is healthy, the
daemon is up, bindings are bound, and the channel still answers every
message with a login prompt until a human notices. An operator who works
mostly *through* ghost (rather than in a local terminal) generates no
native `~/.claude/` traffic at all, so the very keepalive the exclusion
depends on is absent precisely when ghost is being used as designed.

The default account is also the highest-blast-radius one to lose: it is
the fallback for bindings with `claude_account=None`.

## What Changes

- Add `refresh_all(vault, layout, *, include_default=True)` — iterates
  every account in `manifest.accounts`, passing `is_default=True` for the
  manifest default so it refreshes natively (no `CLAUDE_CONFIG_DIR`).
- Keep `refresh_all_non_default` as a thin wrapper delegating with
  `include_default=False`, for callers that deliberately want the old
  behavior. No existing caller is broken.
- Point both schedulers at `refresh_all`: the in-process
  `TokenRefreshScheduler` and the `gits account refresh` CLI.

Token cost is one additional `claude --print ping` per 24h.

## Impact

- Affected specs: `multi-account`
- Affected code: `src/gits/core/token_refresh.py`, `src/gits/cli_account.py`
- Affected tests: `tests/test_token_refresh.py`
- No migration. No config change. Operators who already ran
  `refresh-install` get the wider sweep on the next daemon restart.
