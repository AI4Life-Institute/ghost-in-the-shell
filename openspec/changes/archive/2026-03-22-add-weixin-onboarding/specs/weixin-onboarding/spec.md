## ADDED Requirements

### Requirement: WeChat Setup Wizard
The system SHALL provide a `ghost weixin` command that guides a new user from zero
to a running WeChat bot in a single terminal session.

#### Scenario: First-time setup — no account present
- **WHEN** user runs `ghost weixin` and no openclaw-weixin account exists
- **THEN** the system SHALL run `npx -y @tencent-weixin/openclaw-weixin-cli@latest install`
  with an inherited TTY so the user can scan the WeChat QR code
- **AND** after successful login SHALL confirm the account was written to
  `~/.openclaw/openclaw-weixin/accounts/`
- **AND** SHALL start the Ghost bot automatically

#### Scenario: Account already present
- **WHEN** user runs `ghost weixin` and a valid account already exists
- **THEN** the system SHALL skip the install step, print account info, and start the bot

#### Scenario: Re-login requested
- **WHEN** user runs `ghost weixin --relogin`
- **THEN** the system SHALL run the openclaw-weixin install flow regardless of
  existing accounts

#### Scenario: npx not available
- **WHEN** `ghost weixin` is run but `npx` is not found on PATH
- **THEN** the system SHALL print a clear error message with Node.js install
  instructions and exit non-zero

#### Scenario: Default path configured
- **WHEN** user provides `--path /some/dir` or enters a path when prompted
- **THEN** the system SHALL persist `GITS_DEFAULT_PATH=/some/dir` to
  `~/.gits/config.env` so subsequent `ghost start` invocations auto-bind

### Requirement: openclaw-Compatible Account Layer
The system SHALL provide a generic account storage module compatible with the
openclaw file layout so that accounts written by the real openclaw gateway and
accounts written by Ghost are interchangeable.

#### Scenario: Account discovery
- **WHEN** `openclaw.accounts.discover("openclaw-weixin")` is called
- **THEN** it SHALL read `~/.openclaw/openclaw-weixin/accounts.json` and return
  the first account's `{ token, base_url, user_id, account_id }`

#### Scenario: Account ID normalisation
- **WHEN** a raw account ID such as `"b0d5982b9b4d@im.bot"` is stored
- **THEN** the file name SHALL be `b0d5982b9b4d-im-bot.json`
  (matching openclaw's `normalizeAccountId` rule: lowercase, non-alphanum → `-`)

#### Scenario: Sync-buf persistence
- **WHEN** `save_sync_buf("openclaw-weixin", account_id, buf)` is called
- **THEN** the buffer SHALL be written to
  `~/.openclaw/openclaw-weixin/accounts/{account_id}.sync.json`
  in the same format the real openclaw-weixin plugin uses
