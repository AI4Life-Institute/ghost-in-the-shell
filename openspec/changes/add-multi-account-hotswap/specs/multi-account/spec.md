## ADDED Requirements

### Requirement: Per-Account Isolated Config Directory

ghost SHALL maintain a dedicated `CLAUDE_CONFIG_DIR` per claude account at `~/.claude-{name}/`. Each account directory MUST be a fully self-contained physical directory tree — every subitem (`.credentials.json`, `projects/`, `settings.json`, `todos/`, `statsig/`, `shell-snapshots/`, `ide/`, `plugins/`, etc.) MUST be a real file or directory, not a symbolic link to another account's data.

Each account directory MUST contain:

- A real `~/.claude-{name}/.credentials.json` file (mode 0600) — OAuth tokens for that account.
- A `~/.claude-{name}/.gits-managed` marker file (mode 0644, content empty) — proves the directory was created by ghost.
- All other config subitems as real files/directories (no symlinks across accounts).

ghost MUST NOT create `~/.claude-shared/` or any cross-account shared directory; that mechanism was rejected because two claude CLI processes (different accounts, same `cli_session_id`) would otherwise concurrently `--resume` and append to the same JSONL file, corrupting conversation context. Cross-account session reuse MUST happen via the explicit `gits account import` command (see Requirement: Session Import).

Account isolation MUST be achieved by setting `CLAUDE_CONFIG_DIR=$HOME/.claude-{name}` when launching claude; ghost MUST NOT swap files in `~/.claude/` or modify the macOS keychain.

#### Scenario: Add a fresh account
- **WHEN** the user runs `gits account add work`
- **AND** at least one account already exists in the manifest
- **THEN** ghost creates `~/.claude-work/` with mode 0700 and writes the marker `.gits-managed`
- **AND** ghost runs `CLAUDE_CONFIG_DIR=$HOME/.claude-work claude auth login` as a subprocess inheriting stdin/stdout/stderr
- **AND** after the user completes the OAuth flow, claude CLI writes `~/.claude-work/.credentials.json` and initializes empty `projects/`, `todos/`, etc. inside `~/.claude-work/`
- **AND** ghost reads the credentials file, decodes the access token to extract `email`/`orgId`/`subscriptionType`, and writes a manifest entry
- **AND** ghost merges any ghost-owned hook entries from `~/.claude/settings.json` into `~/.claude-work/settings.json` (see Requirement: Ghost Hooks Propagation)
- **AND** no symbolic link is created between `~/.claude-work/` and any other account directory

#### Scenario: First account init from existing login
- **WHEN** the user runs `gits account add personal --capture-current`
- **AND** `~/.gits/accounts/manifest.json` does not exist or has zero accounts
- **AND** `~/.claude/.credentials.json` exists with a valid `claudeAiOauth` payload
- **THEN** ghost creates `~/.claude-personal/` (mode 0700) with marker
- **AND** ghost performs `rsync -a ~/.claude/ ~/.claude-personal/` — a full physical copy including `.credentials.json`, `projects/`, `settings.json`, `todos/`, `statsig/`, `shell-snapshots/`, `ide/`, `plugins/`, etc.
- **AND** ghost SKIPS the `claude auth login` subprocess (the copied credentials grant the same identity)
- **AND** ghost writes a manifest entry for `personal` and sets `manifest.default = "personal"`
- **AND** ghost auto-migrates every existing binding's `claude_account` from `None` to `"personal"` (see Requirement: Capture-Current Auto-Migrates Bindings)
- **AND** ghost does NOT modify or delete `~/.claude/`
- **AND** ghost does NOT create `~/.claude-shared/`

#### Scenario: --capture-current rejected when accounts exist
- **WHEN** the user runs `gits account add second --capture-current`
- **AND** `manifest.accounts` is non-empty
- **THEN** the command fails with exit code 1 and a message that `--capture-current` is only valid for the first add

#### Scenario: --capture-current rejected when no credentials exist
- **WHEN** the user runs `gits account add personal --capture-current`
- **AND** `~/.claude/.credentials.json` does not exist or has no `accessToken`
- **THEN** the command fails with exit code 1 and a message instructing the user to omit `--capture-current` (which triggers OAuth login)

#### Scenario: Account name validation
- **WHEN** the user runs `gits account add <name>` with a name not matching `^[a-z0-9][a-z0-9_-]{0,31}$`
- **OR** the name equals the reserved word `shared`
- **THEN** the command fails with exit code 1 before any directory is created

#### Scenario: Conflict with pre-existing non-managed directory
- **WHEN** the user runs `gits account add work`
- **AND** `~/.claude-work/` already exists but does not contain a `.gits-managed` marker
- **THEN** the command fails with exit code 1 and a message instructing the user to either delete the directory or pick another name; no mutation occurs

#### Scenario: Capture-current rsync failure cleans up partial directory
- **WHEN** the user runs `gits account add personal --capture-current`
- **AND** rsync of `~/.claude/ → ~/.claude-personal/` exits non-zero (disk full, permission error, or other I/O error)
- **THEN** ghost removes the partially-populated `~/.claude-personal/` directory (including the `.gits-managed` marker if it was already written)
- **AND** the manifest is NOT modified
- **AND** existing bindings are NOT migrated
- **AND** the command fails with exit code 1 reporting the rsync error
- **AND** `~/.claude/` is unaffected (capture is `cp` semantics, never modifies source)

#### Scenario: User interrupts capture-current mid-rsync
- **WHEN** the user sends SIGINT (Ctrl-C) while rsync is running during `gits account add personal --capture-current`
- **THEN** rsync exits with non-zero status
- **AND** ghost applies the same cleanup as the rsync-failure scenario above
- **AND** subsequent `gits account add personal --capture-current` retry succeeds (the residual `~/.claude-personal/` from the aborted run was removed)

#### Scenario: No cross-account symlinks exist
- **WHEN** any account directory `~/.claude-{name}/` is inspected
- **THEN** every subitem (file or directory) under it is a real file/directory, never a symbolic link pointing outside that account's own directory
- **AND** no `~/.claude-shared/` directory is created or referenced by ghost code paths

### Requirement: Account Vault

ghost SHALL maintain `~/.gits/accounts/manifest.json` as the source of truth for account metadata. The manifest MUST be written atomically (temp file + rename) and MUST contain at minimum:

```
{
  "default": "<name>|null",
  "accounts": [
    {
      "name": "<string>",
      "email": "<string>",
      "orgId": "<string>",
      "subscriptionType": "<string>",
      "config_dir": "<absolute path to ~/.claude-{name}/>",
      "lastUsed": "<ISO 8601 timestamp>",
      "tags": []
    }
  ],
  "lastSwitch": {
    "at": "<ISO 8601 timestamp>",
    "binding_id": "<string>",
    "from": "<name|null>",
    "to": "<name>",
    "reason": "<string>"
  },
  "lastImport": {
    "at": "<ISO 8601 timestamp>",
    "session_id": "<string>",
    "from": "<name|null (None means ~/.claude/)>",
    "to": "<name>"
  }
}
```

The manifest MUST NOT contain a `rateLimitedUntil` field on accounts — quota state is queried in real time via the OAuth Usage API.

Token material (access tokens, refresh tokens, scopes) MUST NOT appear in the manifest — those live only in each account's `.credentials.json`.

#### Scenario: Atomic write
- **WHEN** ghost updates the manifest
- **THEN** ghost writes to a sibling temp file, fsyncs, and renames over the destination
- **AND** a crash mid-write leaves the prior manifest intact

#### Scenario: No rate-limit timestamp persisted
- **WHEN** ghost writes a manifest entry for any account
- **THEN** the entry does NOT contain `rateLimitedUntil` or any equivalent timestamp field

#### Scenario: Vault accidentally points to missing dir
- **WHEN** ghost starts and `manifest.accounts` lists an account whose `config_dir` does not exist
- **THEN** ghost logs WARN and excludes that account from runtime use

### Requirement: Default Account Auto-tracking

ghost SHALL maintain `manifest.default` automatically without exposing a CLI command for it. The field MUST be updated on the following events:

- The first account is added → `manifest.default` is set to that account's name.
- Any `gits account switch <name>` succeeds → `manifest.default` is set to `<name>`.
- The currently-default account is removed → `manifest.default` is reset to the next account by descending `lastUsed`, or `null` if no accounts remain.

`manifest.default` MUST be honored by binding creation: a newly-created `SessionBinding` inherits `claude_account = manifest.default` (which may be `None`).

#### Scenario: First add sets default
- **WHEN** the user adds the first account `gits account add personal --capture-current`
- **THEN** `manifest.default` becomes `"personal"`

#### Scenario: Switch updates default
- **WHEN** `switch_account(binding_id, "work")` succeeds for any binding
- **THEN** `manifest.default` becomes `"work"`

#### Scenario: Removing default resets to next-most-recent
- **WHEN** `manifest.default` is `"alice"` and `gits account remove alice` is run
- **AND** other accounts exist
- **THEN** `manifest.default` becomes the account with the most recent `lastUsed`
- **AND** if `manifest.accounts` becomes empty, `manifest.default` is `null`

### Requirement: Per-Binding Account Field

`SessionBinding` SHALL gain a `claude_account: str | None` field (default `None`). When the field is `None`, the binding uses claude's default config directory (`~/.claude/`). When the field is a string, it MUST match a name in `manifest.accounts`; the binding's claude launches MUST be invoked with `CLAUDE_CONFIG_DIR=$HOME/.claude-{name}`.

#### Scenario: Backward compat for state schema
- **WHEN** ghost loads an old `state.json` whose `SessionBinding` entries lack `claude_account`
- **THEN** each binding deserializes with `claude_account = None`
- **AND** subsequent launches do not inject `CLAUDE_CONFIG_DIR`

#### Scenario: New binding inherits default account
- **WHEN** a new binding is created (Discord `/start` or equivalent)
- **AND** `manifest.default` is set to `<name>`
- **THEN** the binding is persisted with `claude_account = <name>`

#### Scenario: Default cleared (no accounts)
- **WHEN** `manifest.default` is null
- **THEN** new bindings get `claude_account = None`

### Requirement: Capture-Current Auto-Migrates Bindings

When `gits account add <name> --capture-current` runs successfully, ghost SHALL automatically migrate every existing `SessionBinding` whose `claude_account` is `None` to `claude_account = <name>`. This is to prevent a "dual-source" hazard: after capture, the same physical session JSONL exists at both `~/.claude/projects/<x>` (original, used by `None` bindings) and `~/.claude-{name}/projects/<x>` (copied), and they would diverge as each is independently updated.

The migration MUST NOT kill or restart any running binding — the new `claude_account` takes effect on the next natural respawn (binding re-creation, HealthMonitor recovery, manual switch, etc.). The user is informed via stdout how many bindings were migrated.

#### Scenario: Migration runs after successful capture
- **WHEN** `gits account add personal --capture-current` completes successfully
- **AND** state.json contained 5 bindings, all with `claude_account = None`
- **THEN** all 5 bindings are updated to `claude_account = "personal"` and state.json is atomically written
- **AND** none of the running claude processes are killed
- **AND** the command output contains a line indicating "已迁移 5 个现有 binding 到 personal" (or English equivalent)

#### Scenario: Migration is idempotent
- **WHEN** `gits account add` is run a second time after migration already happened (no `--capture-current` since that's rejected)
- **THEN** no further binding migration occurs (the existing `claude_account` values are preserved)

#### Scenario: User opts out of migration by skipping --capture-current
- **WHEN** the user runs `gits account add personal` (no `--capture-current`) as the first add
- **THEN** ghost runs OAuth login and creates an empty `~/.claude-personal/`
- **AND** existing bindings with `claude_account = None` are NOT migrated
- **AND** they continue using `~/.claude/` (independent of the new account)

### Requirement: Account-Aware Path Resolution

ghost SHALL provide a single `AccountLayout` helper that maps a binding's `claude_account` to the correct filesystem paths for its session JSONL files, settings, and credentials. ghost code that previously hardcoded `~/.claude/projects` or `~/.claude/settings.json` MUST be updated to consult `AccountLayout` so that per-account bindings reach `~/.claude-{name}/<path>` instead.

The minimal layout API:

```
AccountLayout.projects_dir(claude_account: str | None) -> Path
AccountLayout.settings_file(claude_account: str | None) -> Path
AccountLayout.credentials_file(claude_account: str | None) -> Path
AccountLayout.all_active_projects_dirs() -> list[Path]
```

When `claude_account is None`, paths resolve to `~/.claude/<x>` (legacy/back-compat). When set, paths resolve to `~/.claude-{name}/<x>`. The launcher's existing `cli_aliases.session_path` and `cli_aliases.config_dir` overrides take precedence over the layout's default for that alias.

#### Scenario: Launcher resolves session path by account
- **WHEN** `launcher.get_session_file(work_dir, "claude", session_id, claude_account="work")` is called
- **THEN** ghost looks for the JSONL at `~/.claude-work/projects/<work-dir-hash>/<session_id>.jsonl`
- **AND** does NOT look at `~/.claude/projects/...`

#### Scenario: Launcher falls back for None account
- **WHEN** `launcher.get_session_file(work_dir, "claude", session_id, claude_account=None)` is called
- **THEN** ghost looks for the JSONL at `~/.claude/projects/<work-dir-hash>/<session_id>.jsonl` (legacy path)

#### Scenario: JsonlMonitor watches multiple account paths
- **WHEN** the AccountVault has accounts `personal` and `work` registered
- **THEN** `JsonlMonitor` watches `~/.claude/projects/` (for `None` bindings) AND `~/.claude-personal/projects/` AND `~/.claude-work/projects/`
- **AND** offsets are tracked per `(channel_id, file_path)` so the same `cli_session_id` referenced by two bindings under different accounts (post-import) is monitored independently
- **AND** the monitor's account path registry is updated when `gits account add` or `gits account remove` runs

#### Scenario: Hook installer respects account scoping
- **WHEN** the user runs `gits hook --install` (no flags)
- **THEN** the hook is written only to `~/.claude/settings.json`
- **WHEN** the user runs `gits hook --install --all-accounts`
- **THEN** the hook is written to `~/.claude/settings.json` AND every `~/.claude-{name}/settings.json` in the manifest
- **AND** existing identical hook entries are detected (by matcher) and not duplicated

### Requirement: Launch Command Honors Account

`CodingCLILauncher.build_launch_command` SHALL accept the binding's `claude_account` and prepend `CLAUDE_CONFIG_DIR=<dir>` to the resulting command, but ONLY when:

- The resolved CLI's `base_type` is `claude` (codex/copilot/opencode are not affected)
- `claude_account` is non-null and corresponds to an existing account directory

The injection MUST appear as `CLAUDE_CONFIG_DIR=<shell-quoted path> <existing command>`. Ghost MUST NOT use the legacy `~/.gits/active-env.sh` mechanism.

#### Scenario: Inject for claude-base CLI with account
- **WHEN** the launcher builds a command for a binding with `claude_account = "sharon"` and base_type `claude`
- **THEN** the resulting command starts with `CLAUDE_CONFIG_DIR=/Users/<user>/.claude-sharon `

#### Scenario: No injection without account
- **WHEN** the binding has `claude_account = None`
- **THEN** the resulting command has no `CLAUDE_CONFIG_DIR` prefix

#### Scenario: No injection for non-claude CLIs
- **WHEN** the resolved CLI's `base_type` is `codex`, `copilot`, or `opencode`
- **THEN** the launcher does NOT inject `CLAUDE_CONFIG_DIR` regardless of `claude_account`

### Requirement: Per-Binding Switch Primitive

ghost SHALL provide an atomic `switch_account(binding_id, target, *, auto_import=False)` operation that swaps the named binding's account by killing its claude process(es) and respawning with a different `CLAUDE_CONFIG_DIR`. The operation MUST hold a per-binding asyncio lock for its full duration. Concurrent `switch_account` calls on different bindings MUST be allowed to run in parallel — there is no global mutex.

The operation MUST NOT pre-check quota state and MUST NOT modify any other binding's state, claude processes, or pane content.

When `auto_import=True` (Discord default — see Requirement: Discord Manual Switch via /account-switch), ghost SHALL inline-copy the binding's current `cli_session_id` JSONL file from the source account's `projects/` directory to the target's, but only when target does not yet have that file. The auto-import step MUST run within the lock, AFTER the kill phase confirmed source claude is dead, and BEFORE the binding's `claude_account` field is mutated. The operation MUST return a `SwitchResult` carrying an `import_status` enum (`"imported"`, `"target_existed"`, `"no_source"`, `"no_session"`, `"same_account"`) so the caller can present accurate feedback.

CLI callers (`gits account switch`) SHALL invoke `switch_account` with `auto_import=False` (the default), preserving the explicit "import + switch" two-step flow on the host.

#### Scenario: Successful per-binding switch
- **WHEN** `switch_account(binding_X, "bob")` is invoked while binding_X is running with account "alice"
- **AND** binding_Y is concurrently running with account "carol"
- **THEN** ghost acquires the per-binding lock for binding_X (binding_Y is unaffected)
- **AND** ghost sends `C-c` to binding_X's pane and waits 300ms
- **AND** ghost SIGTERMs every claude process attached to binding_X's pane
- **AND** ghost polls each pid until it is no longer alive, escalating to SIGKILL after 5 seconds, then waits up to 1 second for kernel reap
- **AND** ghost confirms zero claude processes remain in binding_X's pane before proceeding
- **AND** ghost sets `binding_X.claude_account = "bob"` and atomically persists state.json
- **AND** ghost updates `accounts["bob"].lastUsed` and `manifest.default = "bob"`
- **AND** ghost respawns claude in binding_X's pane with `CLAUDE_CONFIG_DIR=$HOME/.claude-bob claude --resume <cli_session_id>`
- **AND** binding_Y's claude process is NOT killed
- **AND** ghost releases binding_X's lock

#### Scenario: Concurrent switches on different bindings proceed in parallel
- **WHEN** `switch_account(binding_A, "x")` and `switch_account(binding_B, "y")` are invoked simultaneously
- **THEN** both operations proceed without serialization

#### Scenario: Same-binding concurrent switches serialized
- **WHEN** two `switch_account(binding_X, ...)` calls are issued in rapid succession
- **THEN** the second waits on binding_X's per-binding lock

#### Scenario: Process kill timeout aborts switch
- **WHEN** a claude process does not exit within 5 seconds of SIGTERM, then 1 second after SIGKILL
- **THEN** ghost aborts the switch, logs the failure, and releases the lock without modifying the binding's `claude_account`

#### Scenario: Switch fails mid-flight (respawn error)
- **WHEN** the kill phase succeeds but `claude --resume` fails to start
- **THEN** the binding is marked `respawn_failed` (visible in `gits account list`)
- **AND** `binding.claude_account` is left at the new value
- **AND** `manifest.lastSwitch.partial = true`
- **AND** the lock is released

#### Scenario: Switch attempts to resume a session not present in target account (auto_import=False)
- **WHEN** `switch_account(binding_X, "bob", auto_import=False)` is invoked (CLI path)
- **AND** binding_X has `cli_session_id = "abc"`, currently on account "alice"
- **AND** `~/.claude-bob/projects/<work-dir-hash>/abc.jsonl` does not exist
- **THEN** ghost still issues the kill + respawn — `claude --resume abc` will fail to find the session and either start a new one or report an error (claude CLI behavior, not ghost's concern)
- **AND** the user is responsible for first running `gits account import abc --to bob` if they want to preserve history
- **AND** the binding may end up `respawn_failed`; the user can run `gits account import` and retry the switch

#### Scenario: auto_import copies session when target is missing it
- **WHEN** `switch_account(binding_X, "bob", auto_import=True)` is invoked (Discord path)
- **AND** binding_X has `cli_session_id = "abc"`, currently on account "alice"
- **AND** `~/.claude-alice/projects/<hash>/abc.jsonl` exists
- **AND** `~/.claude-bob/projects/<hash>/abc.jsonl` does NOT exist
- **THEN** ghost (after killing alice's claude in binding_X) creates `~/.claude-bob/projects/<hash>/` if needed and copies the JSONL with `shutil.copy2` (preserves mode and mtime)
- **AND** ghost writes `manifest.lastImport = {at, session_id="abc", from="alice", to="bob"}`
- **AND** `SwitchResult.import_status = "imported"`
- **AND** the rest of the switch (field update, respawn) proceeds normally
- **AND** after respawn, `claude --resume abc` finds the just-imported file in `~/.claude-bob/projects/<hash>/`

#### Scenario: auto_import preserves target's existing session (no overwrite)
- **WHEN** `switch_account(binding_X, "bob", auto_import=True)` is invoked
- **AND** binding_X has `cli_session_id = "abc"`, currently on account "alice"
- **AND** both `~/.claude-alice/.../abc.jsonl` AND `~/.claude-bob/.../abc.jsonl` exist (perhaps from a prior switch cycle)
- **THEN** ghost SKIPS the copy — `~/.claude-bob/.../abc.jsonl` is byte-for-byte unchanged
- **AND** `SwitchResult.import_status = "target_existed"`
- **AND** the user is informed via the Discord embed that the existing target copy was preserved, and that explicit overwrite (if desired) requires the host CLI: `gits account import abc --from alice --to bob --force`

#### Scenario: auto_import skips when source session file missing
- **WHEN** `switch_account(binding_X, "bob", auto_import=True)` is invoked
- **AND** binding_X has `cli_session_id = "abc"`, currently on account "alice"
- **AND** `~/.claude-alice/.../abc.jsonl` does NOT exist (claude never wrote anything for this session yet)
- **THEN** ghost skips import; no file is created in target
- **AND** `SwitchResult.import_status = "no_source"`
- **AND** the switch proceeds; `claude --resume abc` after respawn will start fresh or error per claude CLI semantics

#### Scenario: auto_import skips when binding has no session id
- **WHEN** `switch_account(binding_X, "bob", auto_import=True)` is invoked
- **AND** binding_X has `cli_session_id = None`
- **THEN** ghost skips import (nothing to copy)
- **AND** `SwitchResult.import_status = "no_session"`

#### Scenario: auto_import runs after kill (concurrent-write safety)
- **WHEN** `switch_account(..., auto_import=True)` runs
- **THEN** the copy step is invoked AFTER the kill phase has confirmed every claude process in the binding's pane is dead
- **AND** the source JSONL is not being concurrently appended to during the copy
- **AND** if the kill phase aborts (timeout), the copy is NOT performed and `claude_account` is not changed

### Requirement: Session Import

ghost SHALL provide `gits account import <session_id> --to <target> [--from <source>] [--force]` that copies a single session JSONL file from one account's `projects/` directory to another's, so the target account can `--resume` that session id with the same conversation history as a starting point. After import, the two copies evolve independently — there is no ongoing sync.

The import MUST be a snapshot copy (not a symlink, not a hardlink, not a sync). The semantic is "give the target account a starting point identical to the source's current state of this session"; subsequent writes by the source account are NOT propagated.

#### Scenario: Auto-locate source by single match
- **WHEN** the user runs `gits account import abc-123 --to work`
- **AND** `abc-123.jsonl` exists in exactly one location across `~/.claude/projects/<*>/` and `~/.claude-*/projects/<*>/`
- **THEN** ghost identifies that location as the source
- **AND** copies the file (preserving mode and mtime) to `~/.claude-work/projects/<same work-dir-hash>/abc-123.jsonl`
- **AND** creates the target's `<work-dir-hash>/` subdirectory if it doesn't exist
- **AND** writes `manifest.lastImport = {at, session_id, from, to}`
- **AND** prints the source path, target path, file size, line count, and mtime

#### Scenario: Multiple matches require --from disambiguation
- **WHEN** the user runs `gits account import abc-123 --to work`
- **AND** `abc-123.jsonl` exists in multiple locations (e.g., both `~/.claude-personal/projects/.../abc-123.jsonl` and `~/.claude-legacy/projects/.../abc-123.jsonl`)
- **THEN** the command fails with exit code 1
- **AND** lists all candidate source paths, with a message instructing the user to retry with `--from <name>`

#### Scenario: Source equals target is a no-op
- **WHEN** the user runs `gits account import abc-123 --to work --from work`
- **OR** auto-located source happens to equal `--to`
- **THEN** the command exits 0 with a message that source and target are the same; no copy occurs

#### Scenario: Target already has the session, --force required
- **WHEN** `~/.claude-work/projects/<hash>/abc-123.jsonl` already exists
- **AND** the user did NOT pass `--force`
- **THEN** the command fails with exit code 1 and a message advising `--force` to overwrite, or showing how to inspect the existing target file

#### Scenario: Forced overwrite preserves backup briefly
- **WHEN** the user runs `gits account import abc-123 --to work --force`
- **AND** `~/.claude-work/projects/<hash>/abc-123.jsonl` already exists
- **THEN** ghost moves the existing file to `<...>/abc-123.jsonl.gits-bak`, copies the source over, and then removes the backup on success
- **AND** if the copy step fails, the backup is preserved so the user can manually restore

#### Scenario: Forced overwrite warns if a target binding is currently using that session
- **WHEN** the user runs `gits account import abc-123 --to work --force`
- **AND** at least one binding has `claude_account = "work"` AND `cli_session_id = "abc-123"` AND its tmux pane has a running claude process
- **THEN** ghost logs a WARN identifying the active binding(s) and prints a console warning recommending the user `gits account switch <other-account> --binding <id>` (or kill the claude process) before importing
- **AND** ghost still proceeds with the overwrite (V1 does NOT auto-kill — user responsibility)
- **AND** the running claude process may be reading a stale-on-disk JSONL after the overwrite; the user is responsible for restarting that binding to pick up the new file
- **AND** V2 candidate: a `--strict` flag that refuses overwrite when a target binding is active

#### Scenario: Session id not found
- **WHEN** the user runs `gits account import nonexistent --to work`
- **AND** no JSONL with that id exists in any account's `projects/` or `~/.claude/projects/`
- **THEN** the command fails with exit code 1 and a message that no source was found

#### Scenario: Import is a snapshot, not a sync
- **WHEN** the user imports session `abc-123` from account A to account B
- **AND** later, A's binding continues writing to `~/.claude-A/projects/<hash>/abc-123.jsonl`
- **THEN** B's copy at `~/.claude-B/projects/<hash>/abc-123.jsonl` is unchanged
- **AND** if the user wants B to reflect A's latest state, they must re-run `gits account import abc-123 --to B --force --from A`

#### Scenario: Discord does not expose import
- **WHEN** the Discord adapter registers slash commands
- **THEN** no `/account-import` (or equivalent) command is registered
- **AND** the only credential-affecting Discord command is `/account-switch`

### Requirement: Ghost Hooks Propagation

ghost SHALL ensure its installed hooks (added to `~/.claude/settings.json` via `gits hook --install`) propagate to per-account `settings.json` files so per-account bindings honor the same hooks. The propagation happens at two trigger points:

- `gits account add <name>` end: ghost copies any ghost-owned hook entries from `~/.claude/settings.json` into `~/.claude-{name}/settings.json`.
- `gits hook --install --all-accounts`: ghost writes the hook to `~/.claude/settings.json` AND every account's `<dir>/settings.json` listed in the manifest. Symmetric `--uninstall --all-accounts`.

**Ghost-owned hook identification**: a hook entry in `settings.json` is considered ghost-owned iff its `command` field equals `"gits hook"` or ends with `"/gits hook"` (matching the existing `_HOOK_COMMAND_SUFFIX` constant in `src/gits/__main__.py:1166`). This is the same predicate `_is_hook_installed()` already uses for `~/.claude/settings.json`. ghost MUST NOT introduce a new marker field — it reuses the existing convention.

**Identity check for "already installed"**: when propagating a hook to a target settings.json, ghost MUST detect existing identical entries by `(matcher, command)` tuple. Re-running `gits hook --install --all-accounts` MUST be idempotent.

**Preservation of user customizations**: if a target `settings.json` contains a hook entry with the same matcher pattern but a DIFFERENT command (the user replaced ghost's hook with their own), ghost MUST preserve the user's entry — no overwrite — and log INFO that the per-account customization was respected.

**Settings.json malformed**: if reading or parsing `~/.claude/settings.json` (source) or any `~/.claude-{name}/settings.json` (target) fails (file missing, JSON syntax error), ghost MUST log a WARN identifying the file and reason, skip propagation for that file, and proceed with other accounts. Hook propagation failures MUST NOT abort the parent operation (`gits account add` still completes successfully; the user is told hooks were not propagated).

#### Scenario: Account add copies ghost hooks
- **WHEN** `~/.claude/settings.json` contains a ghost-installed hook (e.g., a `Stop` hook with command `gits hook --stop`)
- **AND** the user runs `gits account add work`
- **THEN** at the end of the add flow, the same hook entry appears in `~/.claude-work/settings.json`
- **AND** if the source has no ghost hooks, the add does not create or modify the target settings.json beyond what claude CLI itself wrote during OAuth login

#### Scenario: --all-accounts propagates everywhere
- **WHEN** the user runs `gits hook --install --all-accounts`
- **AND** the manifest has accounts `personal` and `work`
- **THEN** the hook is written to `~/.claude/settings.json`, `~/.claude-personal/settings.json`, and `~/.claude-work/settings.json`
- **AND** the operation is idempotent (re-running does not duplicate the entry)

#### Scenario: Per-account user customization preserved
- **WHEN** `~/.claude-personal/settings.json` already has a hook with the same matcher as the one being installed but a DIFFERENT command (e.g., user replaced ghost's command with their own wrapper)
- **AND** ghost runs hook propagation
- **THEN** the existing entry is preserved (not overwritten)
- **AND** ghost logs INFO identifying the file and noting the user's customization was respected

#### Scenario: Identical hook is detected and not duplicated
- **WHEN** ghost runs `gits hook --install --all-accounts` and a target `~/.claude-{name}/settings.json` already contains an identical `(matcher, command)` entry
- **THEN** ghost detects the existing entry and skips writing
- **AND** the target file's mtime and content are unchanged

#### Scenario: Malformed settings.json does not abort propagation
- **WHEN** ghost attempts hook propagation to `~/.claude-work/settings.json`
- **AND** that file is malformed JSON (e.g., truncated, mid-edit)
- **THEN** ghost logs a WARN identifying the file and the parse error
- **AND** ghost continues propagation to other accounts in the manifest
- **AND** the parent operation (`gits account add` or `gits hook --install --all-accounts`) still exits successfully if the OAuth/install step itself succeeded
- **AND** the user is told via stdout that hook propagation was skipped for `work` due to malformed settings.json

#### Scenario: Source ~/.claude/settings.json missing or empty
- **WHEN** `gits account add work` runs and `~/.claude/settings.json` does not exist (user never ran `gits hook --install`)
- **THEN** ghost skips the hook propagation step entirely
- **AND** logs INFO that no source hooks were found to propagate
- **AND** `~/.claude-work/settings.json` is left at whatever claude CLI's OAuth login wrote (or nonexistent)

### Requirement: OAuth API Endpoints Verified Before Adoption

The OAuth Usage endpoint MUST have been empirically verified to exist and respond correctly before any code that depends on it is shipped. The verification record (commands run, responses observed, claude binary constants extracted) MUST be retained in design.md §Reference §C so any future maintainer can re-run the same checks.

ghost MUST NOT depend on any other Anthropic OAuth endpoint (in particular it MUST NOT call the OAuth refresh endpoint) so the trust surface stays minimal.

#### Scenario: Verification record exists
- **WHEN** a developer reads design.md
- **THEN** §Reference §C contains the exact `curl` invocations used to validate `https://api.anthropic.com/api/oauth/usage`
- **AND** the record includes the date of verification, the HTTP responses observed (success with beta header, 401 without beta header), and the claude binary version and the `mSH = "oauth-2025-04-20"` constant the beta header was derived from

#### Scenario: Failure mode if Anthropic deprecates the endpoint
- **WHEN** the Usage endpoint starts returning 410 Gone, 404, or persistent 401 with "OAuth authentication is currently not supported"
- **THEN** ghost MUST log a high-severity warning identifying the endpoint and suggested env-var override (`GITS_OAUTH_USAGE_URL`, `GITS_OAUTH_BETA_HEADER`)
- **AND** `gits account list` rows render `usage: api unsupported (see ghost log)` for every account
- **AND** ghost MUST NOT silently fall back to passive pattern matching — operator must override env or upgrade ghost

### Requirement: OAuth Usage Query

ghost SHALL provide an active quota query that resolves an account's current usage by calling the Anthropic OAuth Usage API. The endpoint and headers below are confirmed working as of 2026-04-27 (see design.md §Reference §C):

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken read from ~/.claude-{name}/.credentials.json>
anthropic-beta: oauth-2025-04-20
```

The `anthropic-beta` header is REQUIRED — without it the endpoint returns HTTP 401 `"OAuth authentication is currently not supported."`.

The query MUST be invoked on demand (when `gits account list` or `/accounts` runs) and MUST NOT be invoked on a periodic background timer in V1.

Endpoint values SHALL be configurable via environment variables:

- `GITS_OAUTH_USAGE_URL` (default: `https://api.anthropic.com/api/oauth/usage`)
- `GITS_OAUTH_BETA_HEADER` (default: `oauth-2025-04-20`)

ghost MUST NOT call any OAuth endpoint other than the Usage endpoint. Specifically, ghost MUST NOT POST to the OAuth refresh endpoint.

ghost SHALL parse these response fields (real schema sampled in design §D8):

- `five_hour.utilization` (number, 0–100) — 5-hour rolling utilization percentage
- `five_hour.resets_at` (ISO 8601 timestamp) — when the 5-hour window resets
- `seven_day.utilization` / `seven_day.resets_at` — weekly window
- `seven_day_opus.{utilization, resets_at}` — Opus-specific weekly window (may be `null`)
- `seven_day_sonnet.{utilization, resets_at}` — Sonnet-specific weekly window (may be `null`)
- `extra_usage.{is_enabled, monthly_limit, used_credits, utilization, currency}` — paid burst credits

Other fields appearing in the response are internal codenames and MUST be ignored. Unknown fields MUST NOT cause errors (schema drift tolerated).

#### Scenario: Successful usage query
- **WHEN** `gits account list` is run with three accounts
- **THEN** ghost performs three (or fewer, if cached) GET requests, one per account
- **AND** each request includes the proper headers
- **AND** ghost parses responses into normalized usage records and renders them per row

#### Scenario: Network or 5xx error returns unavailable
- **WHEN** the usage request fails with a network error or 5xx
- **THEN** the affected row renders `usage: unavailable (network)` or `usage: unavailable (5xx)`
- **AND** other rows still render

#### Scenario: 429 from usage endpoint
- **WHEN** the usage request returns HTTP 429
- **THEN** the affected row renders `usage: api rate limited`

#### Scenario: 60-second cache hit
- **WHEN** the user runs `gits account list` twice within 60 seconds with unchanged access tokens
- **THEN** the second call uses cached usage data without new HTTP requests

#### Scenario: Schema drift tolerated
- **WHEN** the response contains unknown fields
- **THEN** ghost ignores them without erroring

### Requirement: Stale Credentials Reported, Not Refreshed

ghost MUST NOT implement its own OAuth token refresh path. When the OAuth Usage API returns HTTP 401 (with the beta header verified present), ghost SHALL render `usage: stale credentials, run claude --resume to refresh` for that account and proceed with rendering other accounts. ghost MUST NOT POST to any refresh endpoint, MUST NOT read `refreshToken` for the purpose of network calls, and MUST NOT write `~/.claude-{name}/.credentials.json`.

Rationale: claude CLI itself refreshes tokens transparently when a binding's claude process starts with a near-expired access token; reducing ghost's surface to a single Anthropic endpoint minimizes upgrade fragility and avoids concurrent-write races on credentials files.

#### Scenario: 401 renders stale credentials, no retry, no refresh
- **WHEN** the Usage request for account "alice" returns HTTP 401
- **THEN** ghost renders the alice row as `usage: stale credentials, run claude --resume to refresh`
- **AND** ghost does NOT issue a POST request to any OAuth refresh endpoint
- **AND** ghost does NOT modify `~/.claude-alice/.credentials.json`

#### Scenario: ghost source enforces no-refresh invariant
- **WHEN** the `oauth_usage` module is audited
- **THEN** the module contains no HTTP POST call sites
- **AND** the module does not read the `refreshToken` field from any credentials file
- **AND** the module does not open `.credentials.json` for write

#### Scenario: User self-recovery flow
- **WHEN** a user sees `usage: stale credentials` for an account
- **AND** the user runs any claude session for that account
- **THEN** claude CLI refreshes the token and writes it to `~/.claude-{name}/.credentials.json`
- **AND** the next `gits account list` invocation gets HTTP 200

### Requirement: Manual Switch CLI

ghost SHALL provide `gits account switch <name> --binding <id>` to switch a specific binding's claude account. `--binding <id>` MUST be required because the local CLI has no channel context.

There is no "force" or "use" variant — `switch` is the single command and never pre-checks quota.

#### Scenario: Switch a specific binding
- **WHEN** the user runs `gits account switch work --binding b1`
- **AND** account "work" exists and binding b1 exists
- **THEN** ghost invokes `switch_account(b1, "work")`

#### Scenario: --binding required
- **WHEN** the user runs `gits account switch work` without `--binding`
- **THEN** the command fails with exit code 2

#### Scenario: Unknown binding or account
- **WHEN** the user runs `gits account switch <name> --binding <id>` with either name unrecognized
- **THEN** the command fails with exit code 1 and a message listing valid choices

### Requirement: Account Listing with Live Usage

ghost SHALL provide `gits account list` that prints one row per account with name, email, subscriptionType, lastUsed, live usage from the OAuth Usage API, and the count of bindings currently using that account. The default account MUST be marked. Token material MUST NOT appear in the output.

#### Scenario: Renders accounts with live usage
- **WHEN** the user runs `gits account list` with accounts `personal` (default) and `work`
- **THEN** the output shows two rows
- **AND** `personal` is marked with `*` or `[default]`
- **AND** each row shows `5h <pct> / 7d <pct> / resets in <duration>` when API returns parseable values
- **AND** rows for accounts whose API call failed show `usage: <error reason>`
- **AND** each row shows the binding count
- **AND** no token material appears

#### Scenario: No accounts configured
- **WHEN** the user runs `gits account list` with no manifest
- **THEN** the command prints a hint about `gits account add <name> --capture-current` and exits 0

### Requirement: Account Removal

ghost SHALL provide `gits account remove <name>` to delete an account's directory and manifest entry. The command MUST refuse to remove an account in use by any binding; the user MUST first switch all such bindings to another account.

#### Scenario: Remove unused account
- **WHEN** account `bob` is not referenced by any binding's `claude_account`
- **AND** the user runs `gits account remove bob`
- **THEN** ghost deletes `~/.claude-bob/` (recursive — since there are no symlinks across accounts, only `bob`'s own data is removed)
- **AND** ghost removes the `bob` entry from `manifest.accounts`
- **AND** if `manifest.default == "bob"`, ghost resets it to the most-recently-used remaining account or `null`
- **AND** other accounts and `~/.claude/` are untouched

#### Scenario: Remove account in use is refused
- **WHEN** binding b1 has `claude_account = "alice"`
- **AND** the user runs `gits account remove alice`
- **THEN** the command fails with exit code 1
- **AND** the error lists every binding using `alice`

### Requirement: CLI Surface Is Exactly Five Commands

The `gits account` subcommand tree SHALL expose exactly five commands: `add`, `list`, `switch`, `remove`, `import`. Earlier draft commands `use`, `default`, `repair`, `status` MUST NOT be registered.

- `default` → automatic (Default Account Auto-tracking)
- `repair` → not needed (no symlinks to repair under strict isolation)
- `status` → folded into `list` output
- `use` (force switch over rate-limit) → unnecessary (no `rateLimitedUntil` field)

#### Scenario: Only five subcommands are registered
- **WHEN** ghost initializes the argparse tree
- **THEN** `gits account --help` lists exactly `add`, `list`, `switch`, `remove`, `import`
- **AND** invoking other previously-drafted subcommands produces argparse "invalid choice"

#### Scenario: Short alias is `acct`
- **WHEN** the user runs `gits acct list`
- **THEN** it behaves identically to `gits account list`

### Requirement: Local-Only Credential Operations

Credential-creating, credential-deleting, and credential-affecting filesystem operations (`add`, `remove`, `import`) MUST be available only via the local `gits` CLI on the ghost host machine. They MUST NOT be exposed via Discord or any other remote interface.

#### Scenario: Discord exposes only listing and switching
- **WHEN** the Discord adapter registers slash commands
- **THEN** only `/accounts` (list) and `/account-switch <name>` (switch a binding) are registered
- **AND** no Discord command exists for `add`, `remove`, `import`, or any other credential-mutating operation beyond per-binding switching

#### Scenario: Account add attempted via remote interface
- **WHEN** any external interface attempts a credential-add path
- **THEN** the request is rejected with a message directing the user to run `gits account add` on the host

### Requirement: Discord Manual Switch via /account-switch

The Discord adapter SHALL register `/account-switch <name>` that resolves the invoking channel to its bound binding and invokes `switch_account(binding_id, name, auto_import=True)`. The command MUST provide autocomplete for `<name>` from the manifest.

`auto_import=True` is the Discord-specific default — the channel context provides all information needed to compute source/target session paths without user input, so ghost can transparently copy the binding's current `cli_session_id` JSONL to the target account if it's missing there. CLI callers do NOT use `auto_import` (they use the explicit `gits account import` + `gits account switch` two-step flow).

The Discord adapter MUST report the resulting `import_status` to the user via the completion embed so the user knows whether their conversation history was carried over, preserved, or absent.

#### Scenario: Channel bound, auto_import imports session
- **WHEN** an authorized user invokes `/account-switch work` in a channel bound to binding b1 with `claude_account="alice"` and `cli_session_id="abc"`
- **AND** `~/.claude-alice/.../abc.jsonl` exists and `~/.claude-work/.../abc.jsonl` does not
- **THEN** the adapter posts placeholder "⚙️ 切换到 work..."
- **AND** invokes `switch_account(b1, "work", auto_import=True)`
- **AND** edits the message to "✅ 已切换到 work — session abc 已从 alice 导入。对话历史保留。"

#### Scenario: Channel bound, auto_import preserves target's existing session
- **WHEN** the user invokes `/account-switch work` and target work already has the same `cli_session_id` JSONL from a prior switch cycle
- **THEN** the completion embed reads "✅ 已切换到 work — work 上已有此 session 的历史（未覆盖）" plus a hint that explicit overwrite uses host CLI `gits account import ... --force`
- **AND** the existing target file is unchanged

#### Scenario: Channel bound, no source file
- **WHEN** the user invokes `/account-switch work` and the binding's source-side session file does not exist
- **THEN** the completion embed reads "✅ 已切换到 work — 未找到当前 session 文件，新对话从空开始。"
- **AND** no copy is performed

#### Scenario: Channel bound, binding has no session id
- **WHEN** the user invokes `/account-switch work` and the binding has `cli_session_id = None`
- **THEN** the completion embed reads "✅ 已切换到 work — binding 尚未启动过 session。"
- **AND** the switch otherwise proceeds normally

#### Scenario: Channel not bound
- **WHEN** the user invokes `/account-switch work` in a channel with no bound binding
- **THEN** the adapter posts an error directing the user to `/start` first

#### Scenario: Already on target account
- **WHEN** the binding's `claude_account` already equals the requested account
- **THEN** the adapter posts "✓ Already on `work` — no change."
- **AND** `switch_account` is not invoked (the early-return path bypasses the lock)

### Requirement: Discord Listing via /accounts

The Discord adapter SHALL register `/accounts` that returns a Discord embed showing each account's name, email, subscriptionType, live usage from the OAuth Usage API, and the binding count. The output MUST highlight which account the invoking channel's binding currently uses. Token material MUST NOT appear.

#### Scenario: List with channel context
- **WHEN** the user invokes `/accounts` in a channel whose binding has `claude_account = "personal"`
- **THEN** the embed shows every account, with `personal` highlighted
- **AND** each row shows live usage or an error label
- **AND** no token material appears

### Requirement: Backward Compatibility

When `~/.gits/accounts/manifest.json` does not exist, ghost MUST behave exactly as it did before this change: a single shared `~/.claude/` directory, no account vault, no `CLAUDE_CONFIG_DIR` injection, no OAuth Usage API calls, no creation of any `~/.claude-*/` directories.

#### Scenario: Fresh install without accounts feature
- **WHEN** ghost starts and `~/.gits/accounts/manifest.json` does not exist
- **THEN** ghost does not load the AccountVault or the OAuth Usage client
- **AND** no `~/.claude-{name}/` directory is created spontaneously
- **AND** all existing bindings continue using `~/.claude/` with bare `claude --resume <id>` commands

#### Scenario: User wipes account state to roll back
- **WHEN** the user runs `rm -rf ~/.gits/accounts/ ~/.claude-*/`
- **THEN** the next launcher rebuild produces commands without the `CLAUDE_CONFIG_DIR` prefix
- **AND** existing bindings remain runnable using `~/.claude/.credentials.json`
- **AND** `~/.claude/` was never modified by this change (capture is `cp` not `mv`); user's pre-existing data is intact

#### Scenario: Account marker missing on existing dir
- **WHEN** `~/.claude-foo/` exists from a prior unrelated tool but lacks `.gits-managed`
- **AND** the user runs `gits account add foo`
- **THEN** ghost refuses and prints a message identifying the unmanaged directory

#### Scenario: Stale ~/.claude-shared/ from earlier design draft
- **WHEN** ghost starts and `~/.claude-shared/` exists (left over from a prior development build that used the symlink-based design)
- **THEN** ghost logs a WARN identifying the directory as a residue from a deprecated design
- **AND** suggests the user verify and remove it manually
- **AND** ghost does NOT use it for anything
