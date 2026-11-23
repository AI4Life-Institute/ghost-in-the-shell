# Tasks

## 1. Core refresh
- [x] 1.1 Add `refresh_all(vault, layout, *, timeout_s, include_default=True)` in `src/gits/core/token_refresh.py`, iterating all accounts and passing `is_default=(entry.name == manifest.default)` to `refresh_account`.
- [x] 1.2 Reduce `refresh_all_non_default` to a delegating wrapper (`include_default=False`) so existing callers and tests keep working.
- [x] 1.3 Update the module docstring to record why the default is no longer excluded.

## 2. Callers
- [x] 2.1 Point `TokenRefreshScheduler._loop` at `refresh_all`.
- [x] 2.2 Point `gits account refresh` (`cli_account.py`) at `refresh_all`; update its empty-manifest message from "no non-default accounts to refresh" to "no accounts to refresh".

## 3. Tests
- [x] 3.1 `test_refresh_all_includes_default` — both accounts refreshed.
- [x] 3.2 `test_refresh_all_flags_default_natively` — `is_default` True only for the manifest default.
- [x] 3.3 `test_refresh_all_can_skip_default` — opt-out still works.
- [x] 3.4 `test_refresh_all_default_only_manifest` — single-account install is refreshed.
- [x] 3.5 Existing `refresh_all_non_default` tests still pass unchanged.

## 4. Validation
- [x] 4.1 `pytest tests/test_token_refresh.py tests/test_cli_account_refresh.py`
- [x] 4.2 `openspec validate update-token-refresh-covers-default --strict`
