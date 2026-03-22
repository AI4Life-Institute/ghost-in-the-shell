## 1. POC / Validation (do first)
- [ ] 1.1 Write POC script `poc/weixin_onboarding.py` that:
        - checks `npx` availability
        - runs `npx -y @tencent-weixin/openclaw-weixin-cli@latest install` interactively
        - verifies account file written to `~/.openclaw/openclaw-weixin/accounts/`
        - reads account via the proposed `openclaw.accounts.discover()` interface
- [ ] 1.2 Verify POC end-to-end on a clean account (or re-login)

## 2. Refactor: openclaw accounts layer
- [ ] 2.1 Create `src/gits/openclaw/__init__.py`
- [ ] 2.2 Create `src/gits/openclaw/accounts.py`
        - `normalize_account_id(raw: str) -> str`
        - `discover(channel: str) -> dict | None`
        - `save(channel: str, account: dict) -> None`
        - `load_sync_buf(channel: str, account_id: str) -> str`
        - `save_sync_buf(channel: str, account_id: str, buf: str) -> None`
- [ ] 2.3 Refactor `src/gits/adapters/weixin/bot.py`
        — remove `_OPENCLAW_WEIXIN_DIR`, `_ACCOUNTS_DIR`, `discover_account()`,
          `_load_sync_buf()`, `_save_sync_buf()`; import from `openclaw.accounts`

## 3. `ghost weixin` subcommand
- [ ] 3.1 Add `weixin` subparser to `__main__.py`
        (`--path`, `--relogin`, `--no-start` flags)
- [ ] 3.2 Implement `_cmd_weixin()`:
        - detect `npx`; print actionable error if missing
        - check existing account; skip install unless `--relogin`
        - `subprocess.run(npx ... install)` with inherited tty
        - poll for account file (max 30 s, openclaw may restart gateway)
        - prompt for default path → write `~/.gits/config.env`
        - call `_cmd_start_normal()`

## 4. config.py
- [ ] 4.1 Change `env_file` to `[Path("~/.gits/config.env").expanduser(), ".env"]`

## 5. Tests
- [ ] 5.1 Unit-test `normalize_account_id` edge cases
- [ ] 5.2 Unit-test `discover()` with fixture account directory
