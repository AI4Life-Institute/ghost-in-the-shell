# Change: Add WeChat Onboarding — `ghost weixin` Setup Wizard

## Why

New users have no guided path to connect WeChat. The current adapter requires
openclaw-weixin to be pre-installed and configured manually before `ghost start`
can detect it. There is no single command that takes a user from zero to running.

## What Changes

- **New subcommand `ghost weixin`**: one-command setup wizard that installs
  openclaw-weixin (via `npx`), guides the user through QR-code login, optionally
  sets a default project path, then starts the bot.
- **New module `src/gits/openclaw/accounts.py`**: generic openclaw-compatible
  account discovery and sync-buf storage, extracted from `WeixinAdapter`. All
  channel adapters share this layer; the paths and JSON format remain 100%
  compatible with the real openclaw gateway so existing accounts keep working.
- **`WeixinAdapter` refactored**: removes inline path constants and account-file
  logic; delegates to `openclaw.accounts`.
- **`config.py` extended**: reads `~/.gits/config.env` as a user-level fallback
  so `ghost weixin` can persist `GITS_DEFAULT_PATH` without touching any
  project-local `.env`.

## Impact

- Affected specs: `weixin-onboarding` (new capability)
- Affected code:
  - `src/gits/__main__.py` — new `weixin` subparser + `_cmd_weixin()`
  - `src/gits/openclaw/accounts.py` — new file
  - `src/gits/adapters/weixin/bot.py` — refactor account helpers
  - `src/gits/config.py` — multi-file env support
