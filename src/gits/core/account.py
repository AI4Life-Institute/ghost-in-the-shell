"""AccountLayout — filesystem layout for per-account Claude config dirs.

Per openspec change ``add-multi-account-hotswap``, multi-account isolation is
implemented via the claude CLI's ``CLAUDE_CONFIG_DIR`` env var. Each account
has a fully self-contained directory at ``~/.claude-{name}/`` with no
cross-account symlinks; cross-account session reuse goes through the explicit
``gits account import`` command.

This module provides:

* :class:`AccountLayout` — resolves account name → filesystem paths for
  credentials, session JSONL, settings, and the managed-by-ghost marker.
* :func:`validate_account_name` — enforces the kebab-case name regex and
  rejects the reserved word ``shared``.
* :func:`is_ghost_managed` — checks whether an account directory was created
  by ghost (presence of the ``.gits-managed`` marker file).
* :meth:`AccountLayout.create_account_dir` — atomically creates a new account
  directory; with ``capture_current=True`` rsyncs from ``~/.claude/`` for the
  first-account migration. Cleans up the partial directory on any failure
  (including SIGINT).

The launcher and JsonlMonitor consult this layout (Phase 0.5 / 0.6) to find
the right paths per binding's ``claude_account`` field.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .account_vault import AccountVault

logger = logging.getLogger(__name__)


class AccountLayoutError(Exception):
    """Raised when an account-layout operation cannot complete safely."""


def effective_account(
    claude_account: str | None,
    vault: AccountVault | None,
) -> str | None:
    """Translate a binding's ``claude_account`` for path/injection purposes.

    Per the ``add-default-account-native-and-refresh`` change: when a binding's
    account name equals the manifest default, we route it through native
    ``~/.claude/`` paths (returning ``None``) so claude's own OAuth refresh
    loop keeps the macOS keychain warm without ghost interference.

    Fail-safe: any error (vault is None, manifest unreadable, etc.) preserves
    the original ``claude_account`` so behavior degrades to the pre-change
    isolation rules rather than silently breaking.
    """
    if claude_account is None or vault is None:
        return claude_account
    try:
        default = vault.load().default
    except Exception:
        return claude_account
    if default is not None and claude_account == default:
        return None
    return claude_account


# ─────────────────────────────────────────────────────────────────────
# SwitchResult — return type of Engine.switch_account
# ─────────────────────────────────────────────────────────────────────


#: ``import_status`` enum used by :class:`SwitchResult` (Phase 0.7 / D16).
#:
#: * ``"imported"`` — auto-import copied source JSONL to target (target file
#:   was missing).
#: * ``"imported_overwrote"`` — auto-import replaced an older target file
#:   with the newer source (mtime-based decision; the previous target was
#:   moved to ``<target>.gits-bak`` during the copy and removed on success).
#: * ``"target_existed"`` — target had a copy at least as fresh as source
#:   (target mtime ≥ source mtime); preserved unchanged. Override with host
#:   CLI ``gits account import ... --force``.
#: * ``"no_source"`` — auto-import skipped because the source-side JSONL did
#:   not exist (binding never wrote one).
#: * ``"no_session"`` — auto-import skipped because the binding has no
#:   ``cli_session_id``.
#: * ``"same_account"`` — switch was a no-op because target == binding's
#:   current account.
#: * ``"skipped_no_import"`` — caller passed ``auto_import=False`` (CLI path);
#:   no copy attempted regardless of file presence.
#  Literal["imported", "imported_overwrote", "target_existed",
#          "no_source", "no_session", "same_account", "skipped_no_import"]
ImportStatus = str


@dataclass
class SwitchResult:
    """Outcome of :meth:`Engine.switch_account`."""

    success: bool
    binding_id: str
    target: str
    previous: str | None
    import_status: ImportStatus = "skipped_no_import"
    source_path: str | None = None
    target_path: str | None = None
    error: str | None = None
    respawn_failed: bool = False


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_RESERVED_NAMES = frozenset({"shared"})

_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Subitems that previously were considered "shared" between accounts. In the
#: current strict-isolation design these are NOT symlinked; each account has
#: its own real copies. The list is kept for ``rsync`` exclusion logic and
#: for the eventual hooks-propagation flow (Phase 0.10).
SHARED_SUBITEMS: tuple[str, ...] = (
    "projects",
    "settings.json",
    "todos",
    "statsig",
    "shell-snapshots",
    "ide",
    "plugins",
)

#: Marker filename written into every ghost-created account directory so
#: ``is_ghost_managed`` can distinguish ghost-owned dirs from user-created
#: ones with the same name pattern.
MARKER_FILENAME = ".gits-managed"


def validate_account_name(name: str) -> None:
    """Raise :class:`ValueError` if ``name`` violates the spec.

    Rules (per spec ``Account name validation``):

    * Must match ``^[a-z0-9][a-z0-9_-]{0,31}$`` — lowercase alphanumeric,
      hyphen, underscore; 1–32 chars; first char alphanumeric.
    * Must not be the reserved word ``shared`` (would collide with an
      earlier-design directory name).
    """
    if not isinstance(name, str):
        raise ValueError(f"account name must be a string, got {type(name).__name__}")
    if name in _RESERVED_NAMES:
        raise ValueError(f"account name '{name}' is reserved")
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            f"account name '{name}' must match ^[a-z0-9][a-z0-9_-]{{0,31}}$"
        )


def validate_model_name(name: str) -> None:
    """Raise :class:`ValueError` if ``name`` is not a safe CLI model name.

    Deliberately a charset check, not an allowlist — the claude CLI accepts
    stable aliases (``sonnet``, ``opus``, …) and arbitrary full model IDs,
    and an allowlist would drift as models ship (same reasoning as
    ``MODEL_HELP`` in :mod:`gits.core.engine`). The name is embedded in a
    shell launch command, so anything outside
    ``^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`` is rejected.
    """
    if not isinstance(name, str):
        raise ValueError(f"model name must be a string, got {type(name).__name__}")
    if not _MODEL_NAME_PATTERN.match(name):
        raise ValueError(
            f"model name {name!r} must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,127}}$"
        )


def is_ghost_managed(account_dir: Path) -> bool:
    """Return True iff ``account_dir`` contains the ghost marker file."""
    return (account_dir / MARKER_FILENAME).is_file()


class AccountLayout:
    """Resolve account name → filesystem paths.

    Constructed with an optional ``home`` for tests; defaults to ``Path.home()``.
    All path methods are pure — they perform no I/O.
    """

    def __init__(self, home: Path | None = None) -> None:
        self._home = home if home is not None else Path.home()

    @property
    def home(self) -> Path:
        return self._home

    # -- path resolution ------------------------------------------------

    def legacy_claude_dir(self) -> Path:
        """``~/.claude`` — used by ``claude_account=None`` bindings and external claude."""
        return self._home / ".claude"

    def account_dir(self, name: str) -> Path:
        """``~/.claude-{name}`` — per-account isolated config dir."""
        validate_account_name(name)
        return self._home / f".claude-{name}"

    def marker_path(self, name: str) -> Path:
        """``~/.claude-{name}/.gits-managed`` — the ghost-ownership marker."""
        return self.account_dir(name) / MARKER_FILENAME

    def projects_dir(self, claude_account: str | None) -> Path:
        """Account-aware projects path.

        ``None`` → ``~/.claude/projects`` (back-compat for unmigrated bindings
        and external ``claude``); a name → ``~/.claude-{name}/projects``.
        """
        if claude_account is None:
            return self.legacy_claude_dir() / "projects"
        return self.account_dir(claude_account) / "projects"

    def settings_file(self, claude_account: str | None) -> Path:
        if claude_account is None:
            return self.legacy_claude_dir() / "settings.json"
        return self.account_dir(claude_account) / "settings.json"

    def credentials_file(self, claude_account: str | None) -> Path:
        if claude_account is None:
            return self.legacy_claude_dir() / ".credentials.json"
        return self.account_dir(claude_account) / ".credentials.json"

    def all_active_projects_dirs(
        self,
        account_names: Iterable[str],
        *,
        include_legacy: bool = True,
    ) -> list[Path]:
        """Projects directories for all given accounts plus the legacy one.

        Returned in deterministic order: legacy first (if included), then
        accounts in the order they appear in ``account_names``. Used by
        :class:`JsonlMonitor` (Phase 0.6) to scan the union per tick.
        """
        paths: list[Path] = []
        if include_legacy:
            paths.append(self.legacy_claude_dir() / "projects")
        for name in account_names:
            paths.append(self.account_dir(name) / "projects")
        return paths

    # -- directory creation --------------------------------------------

    def create_account_dir(
        self,
        name: str,
        *,
        capture_current: bool = False,
        source: Path | None = None,
    ) -> Path:
        """Create ``~/.claude-{name}/`` with the ghost-managed marker.

        :param capture_current: if True, run ``rsync -a <source>/ <target>/``
            after creating the marker, copying the entire source directory
            including ``.credentials.json``. Used by the first-account
            migration path (``gits account add <name> --capture-current``).
        :param source: the source directory to rsync from when
            ``capture_current=True``. Defaults to :meth:`legacy_claude_dir`.
        :returns: the created directory path.
        :raises AccountLayoutError: if the target already exists, the source
            is missing, or the rsync subprocess fails. On failure the partial
            directory is removed (including on ``KeyboardInterrupt``) so a
            retry can succeed.
        """
        validate_account_name(name)
        target = self.account_dir(name)

        if target.exists():
            if is_ghost_managed(target):
                raise AccountLayoutError(
                    f"account '{name}' already exists at {target}"
                )
            raise AccountLayoutError(
                f"{target} already exists but is not ghost-managed "
                f"(no {MARKER_FILENAME} marker). Refusing to overwrite. "
                "Delete the directory or pick another account name."
            )

        src_dir = source if source is not None else self.legacy_claude_dir()
        success = False
        try:
            target.mkdir(mode=0o700, parents=False, exist_ok=False)
            marker = target / MARKER_FILENAME
            marker.touch(mode=0o644)

            if capture_current:
                if not src_dir.exists():
                    raise AccountLayoutError(
                        f"capture-current source {src_dir} does not exist"
                    )
                # Trailing slashes ensure rsync copies *contents* of src into
                # target (not src as a subdir). ``-a`` preserves modes,
                # ownership, mtimes, and existing symlinks.
                src_arg = str(src_dir).rstrip("/") + "/"
                tgt_arg = str(target).rstrip("/") + "/"
                cmd = ["rsync", "-a", src_arg, tgt_arg]
                logger.info("capture-current rsync: %s", " ".join(cmd))
                try:
                    subprocess.run(cmd, check=True)
                except FileNotFoundError as e:
                    raise AccountLayoutError(
                        "rsync executable not found on PATH"
                    ) from e
                except subprocess.CalledProcessError as e:
                    raise AccountLayoutError(
                        f"rsync exited with code {e.returncode}"
                    ) from e
            success = True
            return target
        finally:
            if not success and target.exists():
                logger.warning(
                    "create_account_dir failed; removing partial dir at %s",
                    target,
                )
                shutil.rmtree(target, ignore_errors=True)

    # -- account discovery ---------------------------------------------

    def discover_account_dirs(self) -> list[Path]:
        """Return all ``~/.claude-{name}/`` directories that are ghost-managed.

        Useful for sanity scans (e.g. comparing against the manifest at
        startup). Skips ``~/.claude-shared/`` (reserved name) and any
        directory missing the marker.
        """
        results: list[Path] = []
        try:
            for entry in self._home.iterdir():
                if not entry.is_dir():
                    continue
                if not entry.name.startswith(".claude-"):
                    continue
                rest = entry.name[len(".claude-"):]
                if not rest or rest in _RESERVED_NAMES:
                    continue
                if not _NAME_PATTERN.match(rest):
                    continue
                if is_ghost_managed(entry):
                    results.append(entry)
        except OSError:
            return []
        return sorted(results)
