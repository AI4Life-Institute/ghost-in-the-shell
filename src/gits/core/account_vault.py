"""AccountVault — persistent manifest of registered Claude accounts.

Per openspec change ``add-multi-account-hotswap``, the manifest lives at
``~/.gits/accounts/manifest.json`` and is the source of truth for account
metadata (name, email, orgId, subscriptionType, config_dir, lastUsed,
default account, last switch / last import records).

Token material (access_token, refresh_token, scopes) is **never** stored
here — it lives only in each account's ``.credentials.json`` (managed by
claude CLI).

This module is sync-first because most callers are CLI subcommands and
runtime helpers; atomic writes use temp-file + rename so concurrent
readers always see a consistent file.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .account import AccountLayout, validate_account_name

logger = logging.getLogger(__name__)


class AccountVaultError(Exception):
    """Raised on vault structural errors (e.g., duplicate add)."""


# ─────────────────────────────────────────────────────────────────────
# Manifest model
# ─────────────────────────────────────────────────────────────────────


@dataclass
class AccountEntry:
    """One row in ``manifest.accounts``. Token material excluded by design."""

    name: str
    email: str | None = None
    org_id: str | None = None
    subscription_type: str | None = None
    config_dir: str = ""
    last_used: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "orgId": self.org_id,
            "subscriptionType": self.subscription_type,
            "config_dir": self.config_dir,
            "lastUsed": self.last_used,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AccountEntry:
        return cls(
            name=d["name"],
            email=d.get("email"),
            org_id=d.get("orgId"),
            subscription_type=d.get("subscriptionType"),
            config_dir=d.get("config_dir", ""),
            last_used=d.get("lastUsed"),
            tags=list(d.get("tags", [])),
        )


@dataclass
class Manifest:
    """In-memory representation of ``manifest.json``.

    On disk the structure is::

        {
          "default": "<name>|null",
          "accounts": [...],
          "lastSwitch": {at, binding_id, from, to, reason} | null,
          "lastImport": {at, session_id, from, to} | null
        }
    """

    default: str | None = None
    accounts: list[AccountEntry] = field(default_factory=list)
    last_switch: dict[str, Any] | None = None
    last_import: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "accounts": [a.to_dict() for a in self.accounts],
            "lastSwitch": self.last_switch,
            "lastImport": self.last_import,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Manifest:
        # Silently drop any deprecated fields (e.g. rateLimitedUntil from a
        # prior design) — they're not propagated on the next save.
        accounts_raw = d.get("accounts", [])
        accounts = [AccountEntry.from_dict(a) for a in accounts_raw if "name" in a]
        return cls(
            default=d.get("default"),
            accounts=accounts,
            last_switch=d.get("lastSwitch"),
            last_import=d.get("lastImport"),
        )


# ─────────────────────────────────────────────────────────────────────
# JWT helper for credential metadata extraction
# ─────────────────────────────────────────────────────────────────────


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment (no padding required)."""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_access_token_metadata(access_token: str) -> dict[str, Any]:
    """Best-effort JWT payload decode for an access token.

    Returns a dict containing whatever fields appeared in the token's
    payload (email / orgId / sub / etc.). Failure modes (malformed token,
    not JWT, non-JSON payload) yield ``{}`` — never raise.

    This is **not** a token validation step; we trust the file we just
    wrote and use this only to populate the manifest's display fields.
    """
    if not isinstance(access_token, str) or access_token.count(".") != 2:
        return {}
    try:
        _, payload, _ = access_token.split(".")
        decoded = _b64url_decode(payload)
        data = json.loads(decoded)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def extract_account_metadata(credentials_path: Path) -> dict[str, Any]:
    """Read ``.credentials.json`` and return display-friendly metadata.

    Returns a dict with keys ``email`` / ``orgId`` / ``subscriptionType``
    when those can be derived. All values may be ``None``. Read or parse
    failure yields ``{}``.

    The credentials file is in claude's "Shape A" form ::

        {
          "claudeAiOauth": {
            "accessToken": "...",
            "refreshToken": "...",
            "subscriptionType": "max",
            ...
          }
        }
    """
    try:
        raw = credentials_path.read_text()
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return {}

    metadata: dict[str, Any] = {}
    sub = oauth.get("subscriptionType")
    if isinstance(sub, str):
        metadata["subscriptionType"] = sub

    access_token = oauth.get("accessToken")
    jwt_payload = decode_access_token_metadata(access_token) if access_token else {}
    # Field names commonly seen in Anthropic OAuth tokens:
    for key in ("email", "orgId", "organizationId", "sub"):
        if key in jwt_payload:
            value = jwt_payload[key]
            if isinstance(value, str):
                if key == "organizationId" and "orgId" not in metadata or key == "orgId":
                    metadata["orgId"] = value
                elif key == "email":
                    metadata["email"] = value
    return metadata


# ─────────────────────────────────────────────────────────────────────
# Vault
# ─────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write_json_sync(path: Path, data: dict) -> None:
    """Write JSON atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class AccountVault:
    """CRUD facade over ``~/.gits/accounts/manifest.json``."""

    def __init__(self, state_dir: Path, layout: AccountLayout | None = None) -> None:
        self._state_dir = Path(state_dir)
        self._layout = layout or AccountLayout()

    # -- paths ---------------------------------------------------------

    @property
    def manifest_path(self) -> Path:
        return self._state_dir / "accounts" / "manifest.json"

    @property
    def layout(self) -> AccountLayout:
        return self._layout

    def is_initialized(self) -> bool:
        """True iff the manifest file exists."""
        return self.manifest_path.exists()

    # -- load / save ---------------------------------------------------

    def load(self) -> Manifest:
        """Load the manifest, returning an empty :class:`Manifest` if missing."""
        path = self.manifest_path
        if not path.exists():
            return Manifest()
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise AccountVaultError(f"manifest at {path} is unreadable: {e}") from e
        if not isinstance(data, dict):
            raise AccountVaultError(f"manifest at {path} is not a JSON object")
        return Manifest.from_dict(data)

    def save(self, manifest: Manifest) -> None:
        """Persist the manifest atomically (temp file + rename, mode 0600)."""
        _atomic_write_json_sync(self.manifest_path, manifest.to_dict())

    # -- CRUD ----------------------------------------------------------

    def list(self) -> list[AccountEntry]:
        return self.load().accounts

    def get(self, name: str) -> AccountEntry | None:
        for entry in self.load().accounts:
            if entry.name == name:
                return entry
        return None

    def add(self, entry: AccountEntry) -> Manifest:
        """Append an account to the manifest.

        Sets ``manifest.default = entry.name`` if no default is currently set.
        Raises :class:`AccountVaultError` if an account with the same name
        already exists.
        """
        validate_account_name(entry.name)
        manifest = self.load()
        if any(a.name == entry.name for a in manifest.accounts):
            raise AccountVaultError(f"account '{entry.name}' already in manifest")
        manifest.accounts.append(entry)
        if manifest.default is None:
            manifest.default = entry.name
        self.save(manifest)
        return manifest

    def remove(self, name: str) -> Manifest:
        """Remove an account by name. Adjusts ``default`` if it pointed there.

        When the removed account was the default, the new default becomes
        the most-recently-used remaining account (descending ``last_used``),
        or ``None`` if no accounts remain.
        """
        manifest = self.load()
        before = len(manifest.accounts)
        manifest.accounts = [a for a in manifest.accounts if a.name != name]
        if len(manifest.accounts) == before:
            raise AccountVaultError(f"no such account '{name}'")
        if manifest.default == name:
            manifest.default = self._select_next_default(manifest.accounts)
        self.save(manifest)
        return manifest

    def set_default(self, name: str | None) -> Manifest:
        """Set ``manifest.default`` to ``name`` (or clear with ``None``)."""
        manifest = self.load()
        if name is not None and not any(a.name == name for a in manifest.accounts):
            raise AccountVaultError(f"no such account '{name}'")
        manifest.default = name
        self.save(manifest)
        return manifest

    def set_tags(self, name: str, tags: list[str]) -> Manifest:
        """Replace an account's ``tags`` list. Useful for labelling plan tier.

        Per ``add-default-account-native-and-refresh``: tags surface in the
        ``/account-switch`` autocomplete so users can distinguish 20x Max
        vs 6x Team accounts at a glance.
        """
        manifest = self.load()
        for entry in manifest.accounts:
            if entry.name == name:
                entry.tags = list(tags)
                self.save(manifest)
                return manifest
        raise AccountVaultError(f"no such account '{name}'")

    def update_last_used(self, name: str, when: str | None = None) -> Manifest:
        """Stamp an account's ``lastUsed`` field. ``when`` defaults to now."""
        manifest = self.load()
        stamp = when or _now_iso()
        for entry in manifest.accounts:
            if entry.name == name:
                entry.last_used = stamp
                self.save(manifest)
                return manifest
        raise AccountVaultError(f"no such account '{name}'")

    def record_switch(
        self,
        *,
        binding_id: str,
        from_: str | None,
        to: str,
        reason: str = "",
        partial: bool = False,
        when: str | None = None,
    ) -> Manifest:
        """Write ``manifest.lastSwitch`` and bump the target's ``lastUsed``.

        Per ``add-default-account-native-and-refresh``: switching a single
        binding does NOT mutate ``manifest.default``. The default account
        is now a sticky, user-set property (set via the initial
        ``gits account add`` or explicit ``gits account set-default``) —
        not "wherever the last switch happened to land". Auto-tracking
        the default broke the new "default account routes to native
        ~/.claude/" rule by silently moving the default-routing target
        every time a binding switched.
        """
        manifest = self.load()
        stamp = when or _now_iso()
        manifest.last_switch = {
            "at": stamp,
            "binding_id": binding_id,
            "from": from_,
            "to": to,
            "reason": reason,
            "partial": partial,
        }
        for entry in manifest.accounts:
            if entry.name == to:
                entry.last_used = stamp
                break
        self.save(manifest)
        return manifest

    def record_import(
        self,
        *,
        session_id: str,
        from_: str | None,
        to: str,
        when: str | None = None,
    ) -> Manifest:
        """Write ``manifest.lastImport`` for the most recent session-import op."""
        manifest = self.load()
        manifest.last_import = {
            "at": when or _now_iso(),
            "session_id": session_id,
            "from": from_,
            "to": to,
        }
        self.save(manifest)
        return manifest

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _select_next_default(accounts: list[AccountEntry]) -> str | None:
        """Pick the most-recently-used account as the new default."""
        if not accounts:
            return None
        # Sort by last_used DESC; None values sort last.
        ranked = sorted(
            accounts,
            key=lambda a: (a.last_used or ""),
            reverse=True,
        )
        return ranked[0].name


# ─────────────────────────────────────────────────────────────────────
# Session Import (Phase 0.9 / D13)
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ImportResult:
    """Outcome of :func:`import_session`."""

    source_path: Path
    target_path: Path
    source_account: str | None  # None means ``~/.claude/`` (legacy)
    target_account: str
    file_size: int
    line_count: int
    overwrote_target: bool = False


class SessionImportError(Exception):
    """Base error for session import operations."""


class SessionNotFoundError(SessionImportError):
    """No JSONL with the given session_id was found in any known location."""


class SessionMultipleSourcesError(SessionImportError):
    """The session id matched multiple locations and ``--from`` is required."""

    def __init__(self, session_id: str, candidates: list[Path]) -> None:
        self.session_id = session_id
        self.candidates = candidates
        super().__init__(
            f"session id '{session_id}' found in {len(candidates)} locations; "
            f"specify --from <name> to disambiguate"
        )


class SessionAlreadyExistsError(SessionImportError):
    """The target already has the same session id and ``--force`` was not given."""


def _find_session_files(
    session_id: str,
    layout: AccountLayout,
    account_names: list[str],
) -> list[tuple[str | None, Path]]:
    """Return all (account_name, jsonl_path) pairs that hold ``session_id``.

    ``account_name`` is ``None`` for the legacy ``~/.claude/projects/``
    location. The search scans every ``<work_dir_hash>/`` subdir under
    each account's projects/ tree (cheap because we test for an exact
    filename, not a full directory walk).
    """
    matches: list[tuple[str | None, Path]] = []
    filename = f"{session_id}.jsonl"

    locations: list[tuple[str | None, Path]] = [
        (None, layout.legacy_claude_dir() / "projects"),
    ] + [(name, layout.account_dir(name) / "projects") for name in account_names]

    for account_name, projects_dir in locations:
        if not projects_dir.exists():
            continue
        try:
            for entry in projects_dir.iterdir():
                if not entry.is_dir():
                    continue
                candidate = entry / filename
                if candidate.is_file():
                    matches.append((account_name, candidate))
        except OSError:
            continue
    return matches


def import_session(
    session_id: str,
    *,
    to: str,
    vault: AccountVault,
    layout: AccountLayout | None = None,
    from_: str | None = None,
    force: bool = False,
) -> ImportResult:
    """Copy a session JSONL from one account's ``projects/`` to another's.

    Per openspec spec ``Session Import`` (D13). Snapshot semantics — after
    import, source and target evolve independently; re-import with
    ``force=True`` to refresh.

    :param session_id: Session UUID (without ``.jsonl`` suffix).
    :param to: Target account name (must exist in vault).
    :param vault: AccountVault for metadata + ``record_import`` write.
    :param layout: Path resolver; defaults to a fresh :class:`AccountLayout`.
    :param from_: Optional source account name (``None`` ≡ ``~/.claude/``).
        Required when the session id matches multiple locations.
    :param force: If True, overwrite an existing target file (mv .gits-bak →
        cp → rm bak on success; bak preserved on failure).
    :returns: :class:`ImportResult` with paths and metadata.
    :raises:
        :class:`AccountVaultError` — target account not in vault.
        :class:`SessionNotFoundError` — no source location had the session.
        :class:`SessionMultipleSourcesError` — multiple sources, no ``from_``.
        :class:`SessionAlreadyExistsError` — target file exists, no ``force``.
    """
    layout = layout or AccountLayout()

    if vault.get(to) is None:
        raise AccountVaultError(f"target account '{to}' not in vault")

    manifest = vault.load()
    account_names = [a.name for a in manifest.accounts]
    matches = _find_session_files(session_id, layout, account_names)

    # Filter by --from if given.
    if from_ is not None:
        if from_ != to and vault.get(from_) is None and from_ != "":
            # Allow empty/None semantics for "legacy ~/.claude/", but if a
            # name is provided it should be a real account.
            raise AccountVaultError(f"source account '{from_}' not in vault")
        matches = [m for m in matches if m[0] == from_]

    if not matches:
        raise SessionNotFoundError(
            f"no JSONL named '{session_id}.jsonl' found in any account or legacy claude dir"
        )

    if len(matches) > 1:
        raise SessionMultipleSourcesError(session_id, [m[1] for m in matches])

    source_account, source_path = matches[0]

    # Same-source-as-target is a no-op.
    if source_account == to:
        return ImportResult(
            source_path=source_path,
            target_path=source_path,
            source_account=source_account,
            target_account=to,
            file_size=source_path.stat().st_size,
            line_count=_count_lines(source_path),
            overwrote_target=False,
        )

    # Compute target path: same dir-hash subdir name as the source.
    target_projects = layout.projects_dir(to)
    target_dir = target_projects / source_path.parent.name
    target_path = target_dir / source_path.name

    # Check existence + force semantics.
    backup_path: Path | None = None
    if target_path.exists():
        if not force:
            raise SessionAlreadyExistsError(
                f"target {target_path} already exists; pass force=True to overwrite"
            )
        backup_path = target_path.with_suffix(target_path.suffix + ".gits-bak")

    # Perform copy with backup-on-overwrite.
    target_dir.mkdir(parents=True, exist_ok=True)
    overwrote = False
    try:
        if backup_path is not None:
            target_path.replace(backup_path)
            overwrote = True
        shutil.copy2(source_path, target_path)
        if backup_path is not None:
            backup_path.unlink(missing_ok=True)
    except BaseException:
        # Best-effort recovery: if we moved the original aside but copy failed,
        # try to restore the backup. The .gits-bak is preserved if restoration
        # also fails — user can recover manually.
        import contextlib
        if backup_path is not None and backup_path.exists() and not target_path.exists():
            with contextlib.suppress(Exception):
                backup_path.replace(target_path)
        raise

    # Record the operation in the manifest.
    vault.record_import(
        session_id=session_id,
        from_=source_account,
        to=to,
    )

    return ImportResult(
        source_path=source_path,
        target_path=target_path,
        source_account=source_account,
        target_account=to,
        file_size=target_path.stat().st_size,
        line_count=_count_lines(target_path),
        overwrote_target=overwrote,
    )


def _count_lines(path: Path) -> int:
    """Cheap line count for display."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0
