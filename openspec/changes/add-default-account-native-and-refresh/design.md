# Design Notes

## Why this needs a design doc, not just spec deltas

Two coupled architectural decisions warrant explicit reasoning:

1. **Routing default account to native paths is a one-line check, but it ripples through 4 modules** (launcher, AccountLayout, JsonlMonitor, switch primitives). Picking the right *layer* for the check determines whether the rest stays simple.
2. **The refresh job interacts with launchd + the keychain + claude's own refresh path** — all external systems. Getting the contract right matters more than the code.

## Decision 1: Where the "default → native" check lives

**Options considered:**

- **A. Caller-side**: every caller of `AccountLayout.projects_dir(name)` first checks `if name == vault.default: name = None`. Simple but easy to miss a call site.
- **B. Inside `AccountLayout`**: the helper takes `claude_account: str | None` and internally consults the vault for the default. All call sites stay unchanged.
- **C. New helper `effective_account(claude_account)` → `str | None`**: returns `None` if the input matches the default, else the input. Caller does `eff = effective_account(b.claude_account); layout.projects_dir(eff)`.

**Chosen: C.** Reasons:

- B couples `AccountLayout` to `AccountVault`, which it currently doesn't import. That's an awkward dependency direction (layout is lower-level).
- C makes the semantics visible at every call site — easier to reason about and to grep for. It also lets us add a feature flag or env-var escape hatch later without surgery on the layout.
- The helper lives in `account.py` next to `AccountLayout`, so the import cost is zero.

## Decision 2: What `claude --print ping` actually does, and why we use it

- `claude --print "ping"` runs a one-shot completion, prints the response, exits. Crucially, claude's startup path always validates the access token and, if expired, uses the refresh token to mint a new one before the request. The new tokens get written back to both `~/.claude-{name}/.credentials.json` and (on macOS, if ACL permits) the keychain.
- This is the **only** mechanism that hits claude's refresh code without us re-implementing it. The README explicitly forbids ghost from implementing its own refresh client; this respects that boundary.
- Cost: one short turn per account per day. With Max subscription that's negligible against the 5h/7d quotas. For pay-as-you-go users this would be O($0.001) per account per day.

**Alternative rejected**: `claude mcp list` or `claude --version` — testing showed `--version` does not exercise the auth path at all (it's a pure local lookup). `mcp list` may or may not, and depending on whether MCP is configured it may fail noisily. `--print` is the most reliable choice.

## Decision 3: Launchd plist vs in-process scheduler

User picked launchd. Reasons that confirm this is right:

- Ghost is restarted regularly (via pm2, manual restarts, system reboot). A daily timer inside ghost is brittle against restarts.
- launchd's `StartCalendarInterval` is the OS-native solution and survives reboot.
- The plist runs `gits account refresh` (a normal CLI invocation), which means the same code is reachable from manual invocation for debugging.
- Linux users get a documented "add this to cron" snippet instead. We don't ship a systemd unit yet — keep scope tight.

## Decision 4: One-shot migration is *dry-run by default*

When a user upgrades, we may find that `~/.claude/.credentials.json` is older than `~/.claude-{default}/.credentials.json`. Three cases:

1. Native is newer → going native is safe, just stop injecting.
2. Isolated is newer → going native will read stale creds; user may see one re-login.
3. Files match (byte-identical) → no-op.

We do not silently overwrite `~/.claude/.credentials.json` with the isolated copy. Instead:

- On first startup after this change, ghost logs a one-line WARN comparing mtimes if they differ, and points the user at `gits account migrate-default-native --apply` to perform the copy.
- This avoids surprising the user; the operation is reversible (the isolated dir is untouched).

## Decision 5: Schema and state

No changes to `state.json` or `manifest.json` schema. Field semantics shift for `claude_account`: previously "this account always uses an isolated dir," now "this account uses an isolated dir unless it is the manifest default."

## Test strategy

- `account.py`: unit-test `effective_account()` with default set, default unset, account-matches-default, account-doesn't-match.
- `launcher.py`: unit-test `build_launch_command` with `(claude_account="X", default="X")` → no injection; `(claude_account="X", default="Y")` → injection; `(claude_account=None, default="X")` → no injection.
- `token_refresh.py`: mock subprocess to test that `claude --print ping` is invoked per non-default account with the right env, that failures are reported, and that no-network errors don't crash the run.
- `cli_account.py`: integration test `gits account refresh` with two manifest accounts (one default, one not) — the default is skipped, the non-default is invoked.
- Launchd plist install/uninstall: integration test on macOS using a temp `~/Library/LaunchAgents` path; skip on Linux.

## Out of scope (explicit)

- Linux systemd unit (`refresh-install` prints a manual-setup message instead).
- WSL / Windows (no path yet).
- Refreshing tokens for non-claude CLIs (codex/copilot/opencode have their own auth — out of scope).
- Detecting when the refresh-token itself has expired (only the access token is refreshed; refresh-token expiry requires full OAuth login). The plist will fail loudly when this happens; we don't try to auto-recover.
