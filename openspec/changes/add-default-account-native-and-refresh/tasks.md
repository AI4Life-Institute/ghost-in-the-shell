# Tasks

## 1. Core: `effective_account` helper

- [ ] 1.1 Add `effective_account(claude_account: str | None, vault: AccountVault) -> str | None` in `src/gits/core/account.py`. Returns `None` when input matches `vault.load().default`, else returns the input unchanged. Handle vault-load errors by returning the input (fail-safe to existing behavior).
- [ ] 1.2 Unit tests in `tests/test_account_layout.py`: default-set + match → None; default-set + no-match → unchanged; default-unset → unchanged; vault-load-error → unchanged.

## 2. Launcher: skip injection for default account

- [ ] 2.1 In `src/gits/core/launcher.py:build_launch_command`, call `effective_account()` before the `if isinstance(claude_account, str)` injection branch. When the effective account is `None`, take the no-injection path.
- [ ] 2.2 In `Launcher.resolve_cli` and `Launcher.get_session_file`, apply the same `effective_account()` translation so session-path lookups for default-account bindings hit `~/.claude/projects/`.
- [ ] 2.3 Unit tests in `tests/test_launcher.py`: build_launch_command with `(claude_account="X", default="X")` produces no `CLAUDE_CONFIG_DIR=`; with `(claude_account="X", default="Y")` produces `CLAUDE_CONFIG_DIR=~/.claude-X`; with `claude_account=None` is unchanged.

## 3. JsonlMonitor: default-aware paths

- [ ] 3.1 In `src/gits/core/jsonl_monitor.py`, wherever `binding.claude_account` is read and used to compute a projects dir, route through `effective_account()` first.
- [ ] 3.2 Test: a binding with `claude_account=<default>` has its JSONL read from `~/.claude/projects/`, not the isolated dir.

## 4. New module: `token_refresh`

- [ ] 4.1 Create `src/gits/core/token_refresh.py` with `refresh_account(name: str, layout: AccountLayout, *, timeout_s: int = 60) -> RefreshResult`. Runs `claude --print ping` as a subprocess with `CLAUDE_CONFIG_DIR=<layout.account_dir(name)>` and a clean env. Returns a dataclass with `success: bool`, `account: str`, `exit_code: int`, `duration_s: float`, `stderr_tail: str`.
- [ ] 4.2 Add `refresh_all_non_default(vault: AccountVault, layout: AccountLayout) -> list[RefreshResult]` — loads manifest, skips the default account, iterates the rest sequentially (not parallel — parallel claude invocations could race on shared state like statsig).
- [ ] 4.3 Unit tests in `tests/test_token_refresh.py`: subprocess mocked. Verify env contains correct `CLAUDE_CONFIG_DIR`, default is skipped, failures don't abort the loop, timeout produces a clean RefreshResult with `success=False`.

## 5. CLI: `gits account refresh`

- [ ] 5.1 Add `refresh` subparser in `src/gits/cli_account.py` that calls `refresh_all_non_default()` and prints one line per account (✓/✗ name in 1.2s) plus a final summary. Exit code 0 if all succeeded or there were zero non-default accounts; 1 if any failed.
- [ ] 5.2 Add `refresh --account <name>` flag to refresh a single account (including the default, for manual recovery).
- [ ] 5.3 Integration test in `tests/test_cli_account.py`: runs `gits account refresh` against a two-account manifest, asserts default is skipped and non-default is invoked.

## 6. Launchd plist install

- [ ] 6.1 Create `scripts/com.gits.token-refresh.plist.template` with placeholders `{GITS_BIN}` and `{LOG_DIR}`. Schedule: `StartCalendarInterval` at 04:00 daily (low-usage window).
- [ ] 6.2 Add `gits account refresh-install` subcommand: resolves `gits` binary path, fills the template, writes to `~/Library/LaunchAgents/com.gits.token-refresh.plist`, runs `launchctl bootstrap gui/$(id -u) <plist>`. On Linux, prints a cron-snippet message and exits 0.
- [ ] 6.3 Add `gits account refresh-uninstall` subcommand: `launchctl bootout gui/$(id -u)/com.gits.token-refresh` then removes the plist. Idempotent (safe if not installed).
- [ ] 6.4 Integration test (macOS-only via `pytest.mark.skipif`): install → check `launchctl list` mentions the label → uninstall → check it's gone. Uses a per-test prefix to avoid clobbering the real installation.

## 7. Migration aid

- [ ] 7.1 At ghost startup (in the same place where account vault is loaded), if `manifest.default` is set and both `~/.claude/.credentials.json` and `~/.claude-{default}/.credentials.json` exist with different mtimes, log a single WARN line comparing mtimes and pointing at `gits account migrate-default-native --apply`.
- [ ] 7.2 Add `gits account migrate-default-native` subcommand. Default mode is dry-run: prints the planned copy direction (newer → older) and exits. With `--apply`, performs the copy after a y/N prompt; uses atomic temp+rename; never touches the keychain (claude will sync it on next run).
- [ ] 7.3 Unit test for the dry-run vs apply paths.

## 8. Docs

- [ ] 8.1 Update `README.md` section around `gits account list` "stale" message to describe the new `gits account refresh` workflow and the launchd plist.
- [ ] 8.2 Add a note to `CLAUDE.md` (or wherever the multi-account onboarding lives) explaining the default-is-native semantics.

## 9. Validation

- [ ] 9.1 `openspec validate add-default-account-native-and-refresh --strict` passes.
- [ ] 9.2 Full test suite passes: `uv run pytest`.
- [ ] 9.3 Manual smoke on macOS: install plist → `launchctl start com.gits.token-refresh` → verify log file shows refresh ran → uninstall.
