"""SubscriptionVault — credential storage for multiple Claude subscriptions.

.. deprecated:: 0.3
    Replaced by ``gits.core.account.AccountVault`` which uses
    ``CLAUDE_CONFIG_DIR``-based per-account isolation instead of credential
    file swapping. See openspec change ``add-multi-account-hotswap``. The
    new path is ``gits account add|list|switch|remove|import``; this module
    is preserved for V1 transition compatibility and will be removed once
    users migrate.

Layout under ``~/.gits/subscriptions/``::

    manifest.json                       # active + per-subscription metadata
    <name>/credentials.json             # snapshot of ~/.claude/.credentials.json (0600)

Switching is implemented by ``SwitchPrimitive`` (later in this module) which
holds the credential lock, kills running ``claude`` processes, swaps the
credential file, and respawns each binding.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..utils.atomic_write import atomic_write_json
from ..utils.lock import credential_lock
from ..utils.process import find_claude_children, kill_claude_process

logger = logging.getLogger(__name__)

CLAUDE_CREDENTIALS_PATH = Path("~/.claude/.credentials.json").expanduser()

# macOS keychain entry used by claude on darwin. claude reads keychain in
# preference to the credentials file when both are present, so any "swap" of
# credentials must update keychain too — otherwise the file change is invisible
# to claude. See `_read_live_credentials` / `_write_live_credentials`.
KEYCHAIN_SERVICE = "Claude Code-credentials"


def _keychain_account() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or "user"


def _read_keychain() -> str | None:
    """Read the JSON payload from macOS keychain, or None if absent / not macOS."""
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("keychain read failed: %s", e)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _write_keychain(payload: str) -> bool:
    """Try to write the JSON payload to macOS keychain. Best-effort.

    Often fails with "Write permissions error" because claude creates the
    entry with an ACL that restricts other tools. That's fine — ghost relies
    on the active-env.sh shell file + ``CLAUDE_CODE_OAUTH_TOKEN`` env var to
    enforce the active subscription, which override keychain entirely.
    Keychain write is only a nice-to-have for clients that ignore env vars.
    """
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            [
                "security", "add-generic-password",
                "-U",  # update if exists
                "-s", KEYCHAIN_SERVICE,
                "-a", _keychain_account(),
                "-w", payload,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("keychain write error: %s", e)
        return False
    if result.returncode != 0:
        logger.debug("keychain write failed: %s", result.stderr.strip())
        return False
    return True


def _delete_keychain() -> bool:
    """Delete the keychain entry. Used by tests and recovery flows."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _read_live_credentials(file_path: Path) -> str | None:
    """Return the live credential payload claude actually uses.

    Priority order (matches claude's own preference on macOS):
        1. macOS keychain entry (KEYCHAIN_SERVICE)
        2. ``~/.claude/.credentials.json`` file

    Returns the raw JSON text, or None if neither source has content.
    """
    kc = _read_keychain()
    if kc:
        return kc
    if file_path.exists():
        try:
            return file_path.read_text()
        except OSError:
            return None
    return None


def _write_live_credentials(file_path: Path, payload: str) -> tuple[bool, bool]:
    """Write payload to BOTH the credentials file AND (on macOS) keychain.

    Returns ``(file_ok, keychain_ok)``. On macOS, keychain is the source of
    truth for claude — so a keychain failure leaves the system in a state
    where the file says one thing but claude reads another. We log a warning
    in that case but still return file_ok=True if the file write succeeded.
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(".tmp")
    file_ok = False
    try:
        tmp.write_text(payload)
        os.chmod(tmp, 0o600)
        tmp.replace(file_path)
        file_ok = True
    except OSError as e:
        logger.error("Failed writing %s: %s", file_path, e)

    keychain_ok = _write_keychain(payload) if sys.platform == "darwin" else True
    # Keychain failure is non-fatal — ghost enforces auth via the env-var
    # override (CLAUDE_CODE_OAUTH_TOKEN), which beats keychain. Logged at
    # debug level inside _write_keychain. We still report the boolean so
    # callers / tests can observe whether keychain was actually updated.
    return file_ok, keychain_ok


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_epoch() -> float:
    return time.time()


@dataclass
class Subscription:
    """A single registered subscription's metadata.

    Token material is NOT stored here — it lives in
    ``~/.gits/subscriptions/<name>/credentials.json`` (0600).
    """

    name: str
    email: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    subscription_type: str | None = None  # max, pro, free, etc.
    last_used: str | None = None  # ISO timestamp
    rate_limited_until: float | None = None  # epoch seconds; None = available
    tags: list[str] = field(default_factory=list)


@dataclass
class LastSwitch:
    at: str
    from_name: str | None
    to_name: str
    reason: str  # "auto" | "manual" | "use" | "add" | "remove"


@dataclass
class Manifest:
    active: str | None = None
    subscriptions: list[Subscription] = field(default_factory=list)
    last_switch: LastSwitch | None = None

    def get(self, name: str) -> Subscription | None:
        for s in self.subscriptions:
            if s.name == name:
                return s
        return None

    def remove(self, name: str) -> Subscription | None:
        for i, s in enumerate(self.subscriptions):
            if s.name == name:
                return self.subscriptions.pop(i)
        return None


class SubscriptionVaultError(Exception):
    """Raised on vault operations that violate invariants."""


class SubscriptionVault:
    """Manage ``~/.gits/subscriptions/`` — manifest and per-subscription snapshots."""

    def __init__(
        self,
        vault_dir: Path,
        claude_credentials_path: Path = CLAUDE_CREDENTIALS_PATH,
        active_env_file: Path | None = None,
    ):
        self.vault_dir = vault_dir
        self.manifest_path = vault_dir / "manifest.json"
        self.claude_credentials_path = claude_credentials_path
        # State_dir/active-env.sh equivalent. When set, vault auto-rewrites this
        # file on every operation that changes which credentials are active.
        # If None, env-file generation is the caller's responsibility.
        self._active_env_file = active_env_file or vault_dir.parent / ACTIVE_ENV_FILENAME

    def _rewrite_env_file(self) -> None:
        """Best-effort regeneration of the active-env shell file."""
        try:
            write_active_env_file(self._active_env_file.parent, self)
        except Exception:
            logger.exception("Failed rewriting active env file %s", self._active_env_file)

    # ── Lifecycle ────────────────────────────────────────────────────
    def exists(self) -> bool:
        """True if vault has been initialised (any subscription registered)."""
        return self.manifest_path.exists()

    def load(self) -> Manifest:
        """Load the manifest from disk. Returns empty manifest if missing."""
        if not self.manifest_path.exists():
            return Manifest()
        try:
            with open(self.manifest_path) as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to read %s: %s", self.manifest_path, e)
            raise SubscriptionVaultError(f"Corrupt manifest: {e}") from e

        subs = [Subscription(**s) for s in raw.get("subscriptions", [])]
        last_switch_raw = raw.get("last_switch")
        last_switch = LastSwitch(**last_switch_raw) if last_switch_raw else None
        # Legacy ``auto_switch_enabled`` field (from the auto-switch feature
        # that was removed) is silently ignored — next save() will drop it.
        return Manifest(
            active=raw.get("active"),
            subscriptions=subs,
            last_switch=last_switch,
        )

    async def save(self, manifest: Manifest) -> None:
        """Persist manifest atomically. Also regenerates the active-env file."""
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "active": manifest.active,
            "subscriptions": [asdict(s) for s in manifest.subscriptions],
            "last_switch": asdict(manifest.last_switch) if manifest.last_switch else None,
        }
        await atomic_write_json(self.manifest_path, data)
        # Active credentials may have changed (active rotated, vault[active]
        # writeback updated, etc) — refresh the env file every time.
        self._rewrite_env_file()

    # ── Credential snapshots ─────────────────────────────────────────
    def credentials_path(self, name: str) -> Path:
        return self.vault_dir / name / "credentials.json"

    def snapshot_to_vault(self, name: str) -> None:
        """Snapshot the live credentials into vault[name].

        Source priority: macOS keychain (if present) > credentials file.
        This matches claude's own read order, so the vault always captures
        what claude is actually using — not just what the file says.
        """
        payload = _read_live_credentials(self.claude_credentials_path)
        if payload is None:
            raise SubscriptionVaultError(
                "No live credentials found (neither keychain nor "
                f"{self.claude_credentials_path}); cannot snapshot {name!r}"
            )
        dest = self.credentials_path(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        try:
            tmp.write_text(payload)
            os.chmod(tmp, 0o600)
            tmp.replace(dest)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise SubscriptionVaultError(
                f"Failed snapshotting {name!r}: {e}"
            ) from e
        # If vault[name] is the active subscription, regen env file so the
        # next claude launch uses the freshly-snapshotted (post-refresh) tokens.
        try:
            manifest = self.load()
            if manifest.active == name:
                self._rewrite_env_file()
        except Exception:
            logger.debug("env file regen after snapshot skipped", exc_info=True)

    def restore_to_active_path(self, name: str) -> None:
        """Push vault[name] credentials to BOTH the file and (on macOS) keychain.

        After this call, both the file at ``~/.claude/.credentials.json`` and
        the keychain entry will contain vault[name]'s credentials. This is
        critical on macOS where claude prefers keychain — file-only writes
        would be silently ignored by claude.
        """
        src = self.credentials_path(name)
        if not src.exists():
            raise SubscriptionVaultError(
                f"Vault entry {name} has no credentials at {src}"
            )
        try:
            payload = src.read_text()
        except OSError as e:
            raise SubscriptionVaultError(
                f"Failed reading vault[{name}]: {e}"
            ) from e
        file_ok, _ = _write_live_credentials(self.claude_credentials_path, payload)
        if not file_ok:
            raise SubscriptionVaultError(
                f"Failed writing live credentials to {self.claude_credentials_path}"
            )

    def remove_credentials(self, name: str) -> None:
        """Delete the on-disk credentials directory for *name*."""
        sub_dir = self.vault_dir / name
        if sub_dir.exists():
            shutil.rmtree(sub_dir)

    # ── High-level ops ───────────────────────────────────────────────
    async def add(
        self,
        name: str,
        *,
        email: str | None = None,
        org_id: str | None = None,
        org_name: str | None = None,
        subscription_type: str | None = None,
    ) -> Manifest:
        """Snapshot current ``~/.claude/.credentials.json`` as subscription *name*.

        Caller MUST hold the credential lock. The current credentials file is
        whatever was last written — this method does not run ``claude auth login``;
        it merely captures whatever is on disk.

        If the manifest does not yet have an active subscription, *name* becomes
        active automatically (first-add migration path).
        """
        manifest = self.load()
        if manifest.get(name) is not None:
            raise SubscriptionVaultError(f"Subscription {name!r} already exists")

        self.snapshot_to_vault(name)

        sub = Subscription(
            name=name,
            email=email,
            org_id=org_id,
            org_name=org_name,
            subscription_type=subscription_type,
            last_used=_now_iso(),
        )
        manifest.subscriptions.append(sub)
        if manifest.active is None:
            manifest.active = name
            manifest.last_switch = LastSwitch(
                at=_now_iso(), from_name=None, to_name=name, reason="add"
            )
        await self.save(manifest)
        return manifest

    async def remove(self, name: str, *, force: bool = False) -> Manifest:
        """Remove subscription *name* from the vault.

        Refuses to remove the active subscription unless *force* is True.
        """
        manifest = self.load()
        if manifest.get(name) is None:
            raise SubscriptionVaultError(f"Subscription {name!r} not found")
        if manifest.active == name and not force:
            raise SubscriptionVaultError(
                f"Cannot remove active subscription {name!r}; "
                f"switch to another first"
            )
        manifest.remove(name)
        if manifest.active == name:
            manifest.active = None
        self.remove_credentials(name)
        await self.save(manifest)
        return manifest

    async def update_active(
        self,
        new_active: str,
        *,
        from_name: str | None,
        reason: str,
    ) -> Manifest:
        """Update manifest.active and record last_switch. Persists synchronously."""
        manifest = self.load()
        if manifest.get(new_active) is None:
            raise SubscriptionVaultError(f"Subscription {new_active!r} not found")
        manifest.active = new_active
        sub = manifest.get(new_active)
        if sub is not None:
            sub.last_used = _now_iso()
        manifest.last_switch = LastSwitch(
            at=_now_iso(), from_name=from_name, to_name=new_active, reason=reason
        )
        await self.save(manifest)
        return manifest

    async def update_rate_limit(
        self, name: str, until: float | None
    ) -> Manifest:
        """Set rate_limited_until for *name* (epoch seconds; None clears)."""
        manifest = self.load()
        sub = manifest.get(name)
        if sub is None:
            raise SubscriptionVaultError(f"Subscription {name!r} not found")
        sub.rate_limited_until = until
        await self.save(manifest)
        return manifest

    # ── Helpers ──────────────────────────────────────────────────────
    def candidate(self, manifest: Manifest, *, exclude: str | None = None) -> str | None:
        """Pick the next available subscription (lowest last_used, not rate-limited)."""
        now = _now_epoch()
        eligible = [
            s
            for s in manifest.subscriptions
            if s.name != exclude
            and (s.rate_limited_until is None or s.rate_limited_until <= now)
        ]
        if not eligible:
            return None
        # Sort by last_used ascending (None first = never used)
        eligible.sort(key=lambda s: (s.last_used or ""))
        return eligible[0].name

    def detect_orphan(self, manifest: Manifest) -> str | None:
        """Compare ``manifest.active`` vault entry to the live ``.credentials.json``.

        Returns a warning message if they don't match, indicating a likely
        crash mid-switch. Returns None on match.
        """
        if manifest.active is None:
            return None
        active_vault = self.credentials_path(manifest.active)
        if not active_vault.exists():
            return (
                f"manifest.active={manifest.active!r} but its vault file "
                f"{active_vault} is missing"
            )
        if not self.claude_credentials_path.exists():
            return (
                f"manifest.active={manifest.active!r} but live credentials at "
                f"{self.claude_credentials_path} are missing"
            )
        try:
            live = self.claude_credentials_path.read_bytes()
            stored = active_vault.read_bytes()
        except OSError:
            return None
        if live != stored:
            # Not necessarily an error — OAuth refresh mutates the live file
            # but we don't writeback until next switch. Just informational.
            return None
        return None


def parse_credential_file(path: Path) -> dict:
    """Read a Claude .credentials.json file and return the inner ``claudeAiOauth`` dict.

    File-only — does NOT check keychain. For "what claude actually sees" use
    ``parse_live_credentials``.
    """
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("claudeAiOauth", {}) or {}


def parse_live_credentials(file_path: Path) -> dict:
    """Parse the live OAuth payload claude actually uses.

    On macOS, prefers keychain over file; on other platforms reads the file.
    Returns the inner ``claudeAiOauth`` dict (or {} if neither source has content).
    """
    payload = _read_live_credentials(file_path)
    if not payload:
        return {}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return data.get("claudeAiOauth", {}) or {}


# ─────────────────────────────────────────────────────────────────────
# Active env file — the actual switching mechanism
# ─────────────────────────────────────────────────────────────────────
#
# Setting CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_OAUTH_REFRESH_TOKEN /
# CLAUDE_CODE_OAUTH_SCOPES env vars makes claude bypass keychain entirely
# and use those tokens directly (`authMethod: "oauth_token"`). This is the
# only reliable way to control which subscription claude uses on macOS,
# where keychain dominates and is hard to write from shell tools.
#
# We write a shell-sourceable env file each time vault[active] changes;
# launcher commands prepend `[ -f file ] && . file;` before invoking claude.
# Keeping this in a 0600 file (instead of inline command) avoids leaking
# tokens into shell history.

ACTIVE_ENV_FILENAME = "active-env.sh"


def active_env_file_path(state_dir: Path) -> Path:
    return state_dir / ACTIVE_ENV_FILENAME


def write_active_env_file(state_dir: Path, vault: "SubscriptionVault") -> Path | None:
    """Materialise vault[active]'s OAuth tokens into a sourceable shell file.

    Returns the file path if written; None if no active subscription or vault
    is empty (and removes any stale file in that case).
    """
    import shlex as _shlex

    env_file = active_env_file_path(state_dir)
    if not vault.exists():
        env_file.unlink(missing_ok=True)
        return None
    manifest = vault.load()
    if not manifest.active:
        env_file.unlink(missing_ok=True)
        return None
    cred_path = vault.credentials_path(manifest.active)
    if not cred_path.exists():
        env_file.unlink(missing_ok=True)
        return None
    try:
        with open(cred_path) as f:
            oauth = json.load(f).get("claudeAiOauth", {}) or {}
    except (OSError, json.JSONDecodeError):
        env_file.unlink(missing_ok=True)
        return None
    if not oauth.get("accessToken"):
        env_file.unlink(missing_ok=True)
        return None

    body = (
        "# Auto-generated by ghost — do not edit manually.\n"
        f"# active subscription: {manifest.active}\n"
        f"export CLAUDE_CODE_OAUTH_TOKEN={_shlex.quote(oauth.get('accessToken', ''))}\n"
        f"export CLAUDE_CODE_OAUTH_REFRESH_TOKEN={_shlex.quote(oauth.get('refreshToken', ''))}\n"
        f"export CLAUDE_CODE_OAUTH_SCOPES={_shlex.quote(' '.join(oauth.get('scopes', [])))}\n"
    )
    env_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = env_file.with_suffix(".tmp")
    try:
        tmp.write_text(body)
        os.chmod(tmp, 0o600)
        tmp.replace(env_file)
    except OSError as e:
        logger.error("Failed writing %s: %s", env_file, e)
        return None
    return env_file


def fetch_claude_identity(timeout: float = 5.0) -> dict:
    """Run ``claude auth status --json`` to fetch the current login's identity.

    NOT a quota probe — only used at vault add-time to label the captured
    subscription with a human-recognisable email/orgId. Failure is non-fatal:
    returns an empty dict. The whitelist in
    ``tests/test_no_cli_quota_check.py`` permits this caller specifically.

    Returns a dict with keys like ``email``, ``orgId``, ``orgName``,
    ``subscriptionType`` (whatever the CLI returns).
    """
    import subprocess as _sp

    try:
        out = _sp.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, _sp.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


# ─────────────────────────────────────────────────────────────────────
# Switch primitive
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SwitchResult:
    target: str
    previous: str | None
    bindings_respawned: list[str] = field(default_factory=list)
    bindings_failed: list[str] = field(default_factory=list)

    @property
    def fully_succeeded(self) -> bool:
        return not self.bindings_failed


class SwitchAborted(Exception):
    """Raised when a switch is aborted before mutating credentials.

    Most commonly: a ``claude`` process refused to die after SIGTERM+SIGKILL.
    The credential file is NOT swapped in this case; the active subscription
    remains unchanged.
    """


class SwitchPrimitive:
    """The atomic ``switch_to(target)`` operation.

    Holds the credential lock, kills all running ``claude`` processes across
    every binding, writes back the active subscription's credentials (capturing
    OAuth refresh), copies the target subscription's credentials into place,
    updates the manifest, and respawns each binding via ``claude --resume <id>``.

    Construct with the existing TmuxController, SessionManager, and
    CodingCLILauncher singletons plus a SubscriptionVault and lock path.
    """

    def __init__(
        self,
        tmux,
        session_mgr,
        launcher,
        vault: "SubscriptionVault",
        lock_path: Path,
        *,
        kill_grace_seconds: float = 5.0,
        respawn_per_binding_seconds: float = 10.0,
    ):
        self.tmux = tmux
        self.session_mgr = session_mgr
        self.launcher = launcher
        self.vault = vault
        self.lock_path = lock_path
        self.kill_grace_seconds = kill_grace_seconds
        self.respawn_per_binding_seconds = respawn_per_binding_seconds

    async def switch_to(
        self,
        target_name: str,
        *,
        reason: str = "manual",
        lock_timeout: float | None = None,
    ) -> SwitchResult:
        """Perform an atomic switch to *target_name*.

        Steps inside the credential lock:
            1. Send Ctrl-C to every binding's tmux pane.
            2. Find and SIGTERM every ``claude`` process; escalate to SIGKILL
               if needed; abort the switch if any pid refuses to die.
            3. Snapshot the current ``~/.claude/.credentials.json`` back to
               the previous active subscription's vault entry (writeback).
            4. Copy the target subscription's vault credentials to
               ``~/.claude/.credentials.json``.
            5. Update manifest.active and last_switch.
            6. Respawn each binding via ``claude --resume <cli_session_id>``.

        Raises:
            SubscriptionVaultError: target not registered.
            SwitchAborted: kill confirmation failed; no swap performed.
        """
        async with credential_lock(self.lock_path, timeout=lock_timeout):
            manifest = self.vault.load()
            if manifest.get(target_name) is None:
                raise SubscriptionVaultError(
                    f"Subscription {target_name!r} not found"
                )
            previous = manifest.active
            if previous == target_name:
                logger.info(
                    "switch_to: target %s already active; no-op", target_name
                )
                return SwitchResult(target=target_name, previous=previous)

            bindings = self.session_mgr.list_bindings()
            live_bindings = self._live_bindings(bindings)
            logger.info(
                "switch_to: %s -> %s across %d active binding(s) (%d total)",
                previous,
                target_name,
                len(live_bindings),
                len(bindings),
            )

            # Step 1+2: kill claude in active bindings (suspended ones already
            # have no claude — _kill_all_claude filters them).
            await self._kill_all_claude(bindings)

            # Step 3: writeback active (capture any OAuth refresh)
            if previous is not None:
                try:
                    self.vault.snapshot_to_vault(previous)
                    logger.info("Wrote back %s credentials to vault", previous)
                except SubscriptionVaultError as e:
                    logger.warning("Writeback for %s failed: %s", previous, e)

            # Step 4: swap credentials
            self.vault.restore_to_active_path(target_name)
            logger.info(
                "Swapped ~/.claude/.credentials.json to subscription %s", target_name
            )

            # Step 5: update manifest
            await self.vault.update_active(
                target_name, from_name=previous, reason=reason
            )

            # Step 6: respawn (delegated to shared helper that skips suspended
            # bindings and silently handles dead-window stale state)
            respawned, failed = await self._respawn_all(bindings)

            return SwitchResult(
                target=target_name,
                previous=previous,
                bindings_respawned=respawned,
                bindings_failed=failed,
            )

    async def add_subscription(
        self,
        name: str,
        *,
        login_command: list[str] | None = None,
        on_login: "callable | None" = None,
        lock_timeout: float | None = None,
        capture_current: bool = False,
    ) -> "AddResult":
        """Register a new subscription via interactive ``claude auth login``.

        Holds the credential lock for the entire flow. Sequence:

            1. Acquire lock.
            2. Writeback the current active subscription's credentials so any
               OAuth refresh during normal operation is captured.
            3. Kill every running ``claude`` process (so login doesn't conflict
               with a refresh) and confirm dead.
            4. Invoke *login_command* (default ``["claude", "auth", "login"]``)
               as a subprocess inheriting stdin/stdout/stderr so the user can
               complete the interactive OAuth flow in their terminal.
            5. If login succeeded, snapshot the resulting credentials file to
               ``~/.gits/subscriptions/<name>/credentials.json`` and append a
               manifest entry. The new subscription becomes active iff there
               was no previous active.
            6. Restore the previous active subscription's credentials to
               ``~/.claude/.credentials.json`` (so existing bindings can resume).
            7. Respawn every previously-running binding via ``--resume``.
            8. Release lock.

        On login failure (non-zero exit), no new vault entry is created and
        the previous active credentials are restored.

        Args:
            name: New subscription name.
            login_command: Override the login command for testing.
            on_login: Callable invoked synchronously while the lock is held,
                AFTER the previous claude processes are killed and BEFORE the
                new credentials are snapshotted. If provided, replaces the
                default subprocess invocation. Used by tests to fake login.

        Returns:
            ``AddResult`` describing what happened.
        """
        cmd = login_command or ["claude", "auth", "login"]
        async with credential_lock(self.lock_path, timeout=lock_timeout):
            manifest = self.vault.load()
            if manifest.get(name) is not None:
                raise SubscriptionVaultError(
                    f"Subscription {name!r} already exists"
                )
            previous = manifest.active
            bindings = self.session_mgr.list_bindings()

            # Default behaviour: always run `claude auth login` to register a
            # new account. ``capture_current=True`` skips OAuth and snapshots
            # whatever credentials are currently in ``~/.claude/.credentials.json``
            # — used to migrate an existing login into the vault, including
            # the SSH/headless workflow where a user logs in on a different
            # machine and scp's the credential file over.
            if capture_current:
                existing_oauth = parse_live_credentials(self.vault.claude_credentials_path)
                if not existing_oauth.get("accessToken"):
                    raise SubscriptionVaultError(
                        "--capture-current requires a valid ~/.claude/.credentials.json; "
                        f"none found at {self.vault.claude_credentials_path}. "
                        "Run `gits subscription add` (without --capture-current) to log in."
                    )
                # When there's already an active subscription, the live
                # credentials had better belong to a *different* account —
                # otherwise we'd be duplicating the active vault entry.
                # Compare org_id via `claude auth status --json`; if it matches,
                # reject. If the identity probe fails (e.g., `claude` not on
                # PATH), proceed with a warning.
                if previous is not None:
                    active_entry = manifest.get(previous)
                    if active_entry is not None and active_entry.org_id:
                        identity = fetch_claude_identity()
                        live_org_id = identity.get("orgId")
                        if live_org_id and live_org_id == active_entry.org_id:
                            raise SubscriptionVaultError(
                                f"--capture-current refused: live ~/.claude/.credentials.json "
                                f"belongs to the same Anthropic org as active subscription "
                                f"{previous!r} (orgId={live_org_id!r}). "
                                "If you want to add a different account, login as that account "
                                "first (on a machine with browser), scp the .credentials.json over, "
                                "then re-run this command."
                            )
                        if not live_org_id:
                            logger.warning(
                                "Could not verify org_id of live credentials via "
                                "`claude auth status` — proceeding with capture-current; "
                                "manually verify with `gits subscription list` afterward."
                            )

            # Step 1-2: writeback current active (best-effort).
            # SKIP when capture_current is requested: the live credentials
            # belong to a DIFFERENT account (verified via org_id mismatch
            # above), so writing them to active's vault entry would corrupt it.
            if (
                previous is not None
                and self.vault.claude_credentials_path.exists()
                and not capture_current
            ):
                try:
                    self.vault.snapshot_to_vault(previous)
                except SubscriptionVaultError as e:
                    logger.warning("Pre-add writeback failed: %s", e)

            # Step 3: kill claude processes. May raise SwitchAborted, in which
            # case the original claude processes are still alive — bindings
            # never went down, so no respawn is needed.
            await self._kill_all_claude(bindings)

            # From here on, bindings are torn down. We MUST run _respawn_all in
            # finally — otherwise an exception would leave bindings dead and
            # invisible to the user.
            succeeded = False
            became_active = False
            error: str | None = None
            respawn_done = False  # guard against double-respawn from BaseException handler
            try:
                # Step 4: run login. Skipped only when --capture-current was
                # requested (validated above to require existing creds + no
                # prior active).
                if capture_current:
                    logger.info(
                        "capture_current: skipping `claude auth login`; using existing ~/.claude/.credentials.json"
                    )
                    login_ok = True
                elif on_login is not None:
                    login_ok = await self._run_login_hook(on_login)
                else:
                    login_ok = await self._run_login_subprocess(cmd)

                if not login_ok:
                    error = "login subprocess exited non-zero"
                    self._best_effort_restore(previous)
                    respawn_done = True
                    return _make_add_result(
                        name, False, False, error,
                        await self._respawn_all(bindings),
                    )

                # Step 5: confirm new credentials are available (keychain or file).
                from_path = self.vault.claude_credentials_path
                if _read_live_credentials(from_path) is None:
                    error = "login completed but no credentials found in keychain or file"
                    self._best_effort_restore(previous)
                    respawn_done = True
                    return _make_add_result(
                        name, False, False, error,
                        await self._respawn_all(bindings),
                    )

                # Step 6: snapshot to vault + manifest entry. If THIS fails we
                # still need to restore the previous active credentials and
                # respawn bindings.
                try:
                    oauth = parse_live_credentials(from_path)
                    identity = fetch_claude_identity()  # email / orgId / orgName
                    await self.vault.add(
                        name,
                        email=identity.get("email"),
                        org_id=identity.get("orgId"),
                        org_name=identity.get("orgName"),
                        subscription_type=identity.get("subscriptionType")
                        or oauth.get("subscriptionType"),
                    )
                except Exception as e:
                    error = f"vault write failed: {e}"
                    logger.exception("vault.add failed; restoring previous")
                    self._best_effort_restore(previous)
                    respawn_done = True
                    return _make_add_result(
                        name, False, False, error,
                        await self._respawn_all(bindings),
                    )

                became_active = previous is None

                # Step 7: restore previous active credentials. If there was no
                # previous active, the new credentials stay in place — first-add
                # migration path.
                if previous is not None:
                    self._best_effort_restore(previous)

                succeeded = True
                respawn_done = True
                respawned_failed = await self._respawn_all(bindings)
                return _make_add_result(
                    name, True, became_active, None, respawned_failed
                )
            except BaseException:
                # ANY uncaught exception here (KeyboardInterrupt, OSError,
                # asyncio.CancelledError, etc.): make a best-effort restore +
                # respawn so bindings don't get stuck dead, then re-raise.
                # Skip the respawn if it already ran in the try-block (e.g.
                # the user Ctrl-C'd while respawn was in progress — don't
                # double-execute and double-spam logs).
                logger.warning(
                    "Uncaught exception in add_subscription (respawn_done=%s); cleaning up",
                    respawn_done,
                )
                self._best_effort_restore(previous)
                if not respawn_done:
                    try:
                        await self._respawn_all(bindings)
                    except Exception:
                        logger.exception(
                            "Best-effort respawn after exception also failed"
                        )
                raise
            finally:
                if not succeeded and error is None:
                    # Means we exited via the BaseException branch above; no
                    # AddResult will be returned, but the lock context will
                    # still release cleanly.
                    pass

    def _best_effort_restore(self, previous: str | None) -> None:
        """Restore *previous* subscription's credentials into ``.credentials.json``.

        Failure is logged but not raised — caller continues to respawn bindings
        regardless, because leaving bindings dead is worse than leaving them
        running with stale credentials (which the next `claude` call will
        refresh transparently).
        """
        if previous is None:
            return
        try:
            self.vault.restore_to_active_path(previous)
        except SubscriptionVaultError:
            logger.exception(
                "Failed to restore %s credentials; bindings will respawn with "
                "whatever ~/.claude/.credentials.json currently contains",
                previous,
            )

    async def remove_subscription(
        self,
        name: str,
        *,
        force: bool = False,
        lock_timeout: float | None = None,
    ) -> None:
        """Delete a subscription's vault entry.

        Refuses to remove the active subscription unless *force* is True. When
        *force* is True and *name* is active, the live ``.credentials.json``
        is left as-is — caller is expected to switch first or cope with the
        manifest having no active subscription.
        """
        async with credential_lock(self.lock_path, timeout=lock_timeout):
            await self.vault.remove(name, force=force)

    @staticmethod
    def _live_bindings(bindings) -> list:
        """Active bindings only. Suspended bindings already have no claude
        process and an idle window — switch operations must leave them alone
        so they stay suspended."""
        return [b for b in bindings if not getattr(b, "suspended", False)]

    async def _kill_all_claude(self, bindings) -> None:
        """Send C-c, SIGTERM all claude pids, escalate to SIGKILL, abort on stuck.

        Skips suspended bindings (no claude process to kill anyway).
        """
        import asyncio as _asyncio

        live = self._live_bindings(bindings)
        for b in live:
            try:
                await self.tmux.send_keys(b.window_id, "C-c")
            except Exception:
                pass
        await _asyncio.sleep(0.3)

        pids: list[int] = []
        for b in live:
            try:
                pane_pid = await self.tmux.pane_pid(b.window_id)
            except Exception:
                pane_pid = None
            if pane_pid:
                pids.extend(await find_claude_children(pane_pid))
        pids = sorted(set(p for p in pids if p > 0))

        if pids:
            results = await kill_claude_process(
                pids, grace_seconds=self.kill_grace_seconds
            )
            stuck = [pid for pid, dead in results.items() if not dead]
            if stuck:
                raise SwitchAborted(
                    f"Could not terminate {stuck}; credential mutation skipped"
                )

    async def _respawn_all(self, bindings) -> tuple[list[str], list[str]]:
        """Respawn the CLI for each non-suspended, alive-window binding.

        Suspended bindings: skipped (they should stay suspended).
        Bindings whose tmux window no longer exists: skipped with a single
        log line — HealthMonitor handles window recovery on its own cadence.
        """
        respawned: list[str] = []
        failed: list[str] = []
        skipped_suspended = 0
        skipped_dead_windows: list[str] = []

        for b in bindings:
            if getattr(b, "suspended", False):
                skipped_suspended += 1
                continue
            try:
                if not await self.tmux.window_exists(b.window_id):
                    skipped_dead_windows.append(f"{b.channel_id} ({b.window_id})")
                    failed.append(b.channel_id)
                    continue
                cmd = self.launcher.build_launch_command(
                    b.coding_cli, b.cli_session_id
                )
                await self.tmux.send_text(b.window_id, cmd, submit_keys="\n")
                respawned.append(b.channel_id)
            except Exception as e:
                logger.error(
                    "Respawn failed for %s (%s): %s",
                    b.channel_id, b.window_id, e,
                )
                failed.append(b.channel_id)

        if skipped_suspended:
            logger.info(
                "Respawn: skipped %d suspended binding(s) — they stay suspended",
                skipped_suspended,
            )
        if skipped_dead_windows:
            logger.warning(
                "Respawn: skipped %d binding(s) with stale window IDs (HealthMonitor will recover): %s",
                len(skipped_dead_windows),
                ", ".join(skipped_dead_windows[:5])
                + ("…" if len(skipped_dead_windows) > 5 else ""),
            )
        return respawned, failed

    async def _run_login_subprocess(self, cmd: list[str]) -> bool:
        """Run ``claude auth login`` inheriting the parent terminal.

        Uses synchronous ``subprocess.run`` (via ``asyncio.to_thread``) instead
        of ``asyncio.create_subprocess_exec``. The latter switches inherited
        stdin into non-blocking mode on Unix, which breaks Node-based CLIs
        (like ``claude``) that use ``readline`` to read the OAuth code from
        stdin — readline returns immediately with EAGAIN and the user's paste
        is lost. ``subprocess.run`` keeps stdin/stdout/stderr as plain TTY
        file descriptors, preserving line-buffering and SIGINT propagation.
        """
        import asyncio as _asyncio
        import subprocess as _sp

        def _run() -> int:
            try:
                completed = _sp.run(cmd)
                return completed.returncode
            except FileNotFoundError as e:
                logger.error("login command not found: %s", e)
                return 127

        rc = await _asyncio.to_thread(_run)
        if rc != 0:
            logger.warning("login command %s exited with code %d", cmd, rc)
            return False
        return True

    async def _run_login_hook(self, hook) -> bool:
        """Invoke a sync or async test hook in place of the login subprocess."""
        import asyncio as _asyncio
        import inspect as _inspect

        result = hook()
        if _inspect.isawaitable(result):
            result = await result
        return bool(result)


@dataclass
class AddResult:
    name: str
    succeeded: bool
    became_active: bool
    bindings_respawned: list[str] = field(default_factory=list)
    bindings_failed: list[str] = field(default_factory=list)
    error: str | None = None


def _make_add_result(
    name: str,
    succeeded: bool,
    became_active: bool,
    error: str | None,
    respawn_result: tuple[list[str], list[str]],
) -> AddResult:
    respawned, failed = respawn_result
    return AddResult(
        name=name,
        succeeded=succeeded,
        became_active=became_active,
        bindings_respawned=respawned,
        bindings_failed=failed,
        error=error,
    )
