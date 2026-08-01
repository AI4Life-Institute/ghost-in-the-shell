"""Tell the operator when the *refusing* hooks are running unreviewed code.

Ghost task whlive
-----------------
``gits guard`` is installed by absolute path into ``~/.claude*/settings.json``,
and on this machine that path is an **editable** install pointing at a working
checkout. Editable is worth keeping — hooks are iterated on constantly and a
re-deploy per tweak pushes people toward disabling them — but it means the
behaviour of things that *refuse operations* is decided by whichever branch
that checkout happens to be parked on, and nothing said so out loud.

This prints one line when that is the case. It is the whole concession:
observability, not enforcement.

Hard constraints (all load-bearing)
-----------------------------------
* **Never stdout.** PreToolUse stdout is protocol; the banner goes to stderr.
* **Never the refusal path, never the exit code.** The caller invokes this
  only on the *allow* path and swallows every exception. A cosmetic notice
  must not be able to change a verdict.
* **Rate limited.** This hangs off every Edit/Write/Bash call, so an
  unthrottled banner becomes noise everyone learns to ignore — which is the
  same as not having it. A stamp file gates both the printing *and* the
  ``git status`` behind it to at most once per TTL, so the common path costs
  one ``stat``.
* **Stdlib only, no pydantic**, like the rest of the hook package.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

#: One banner (and one ``git status``) per this many seconds.
DEFAULT_TTL = 3600.0

#: Set ``GHOST_GUARD_DRIFT_TTL=0`` to silence the banner entirely.
TTL_ENV = "GHOST_GUARD_DRIFT_TTL"

MAIN_BRANCHES = ("master", "main")

_STATUS_TIMEOUT = 5.0


def _resolve_ttl(ttl: float | None) -> float:
    if ttl is not None:
        return ttl
    raw = os.environ.get(TTL_ENV)
    if raw is None:
        return DEFAULT_TTL
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TTL


def default_stamp_path() -> Path:
    gits_dir = os.environ.get("GITS_DIR")
    base = Path(gits_dir).expanduser() if gits_dir else Path.home() / ".gits"
    return base / "guard_drift_stamp"


def find_checkout(module_file: Path) -> Path | None:
    """The source checkout this module is being imported from, if any.

    Returns None for a non-editable install (code copied into site-packages),
    where there is no branch to drift.
    """
    for parent in module_file.resolve().parents:
        if not (parent / ".git").exists():
            continue
        if (parent / "src" / "gits" / "hooks" / "drift_banner.py").exists():
            return parent
        return None
    return None


def read_branch(checkout: Path) -> str | None:
    """Current branch from ``.git/HEAD`` — a file read, no subprocess.

    None means detached HEAD or an unreadable HEAD; both are "not master".
    """
    dot_git = checkout / ".git"
    if dot_git.is_file():
        # A linked worktree: ``gitdir: <path>``.
        try:
            line = dot_git.read_text().strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        git_dir = Path(line.split(":", 1)[1].strip())
        if not git_dir.is_absolute():
            git_dir = (checkout / git_dir).resolve()
    else:
        git_dir = dot_git

    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/") :]
    return None


def is_dirty(checkout: Path) -> bool | None:
    """``git status --porcelain``; None if it could not be determined."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain"],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return bool(proc.stdout.strip())


def _read_stamp(stamp: Path) -> float | None:
    """Last consideration time, from the stamp's *contents*.

    The timestamp is stored in the file rather than read off ``st_mtime`` so
    the clock is a single injectable value — mtime would silently mix the
    caller's notion of "now" with the filesystem's.
    """
    try:
        return float(stamp.read_text().strip())
    except (OSError, ValueError):
        return None


def _rate_limited(stamp: Path, now: float, ttl: float) -> bool:
    """True if a banner was already considered within the TTL.

    The stamp is refreshed *before* any further work, so a failure downstream
    costs at most one cycle rather than becoming a per-tool-call retry storm.
    A stamp from the future (clock change) is treated as stale, not as a
    permanent mute.
    """
    last = _read_stamp(stamp)
    if last is not None and 0 <= (now - last) < ttl:
        return True
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
    except OSError:
        pass
    return False


def compose(checkout: Path, branch: str | None, dirty: bool | None) -> str | None:
    """The banner text, or None when there is nothing to say."""
    problems: list[str] = []
    if branch is None:
        problems.append("detached HEAD")
    elif branch not in MAIN_BRANCHES:
        problems.append(f"branch {branch!r} (not master)")
    if dirty:
        problems.append("uncommitted changes")
    if not problems:
        return None
    return (
        f"[ghost guard] running editable code from {checkout}: "
        + ", ".join(problems)
        + ". The hooks that refuse operations are NOT master. "
        "Run `ghost doctor` for the full picture."
    )


def emit(
    *,
    module_file: Path | None = None,
    stamp_path: Path | None = None,
    ttl: float | None = None,
    now: float | None = None,
    stream=None,
) -> str | None:
    """Print the drift banner to stderr at most once per TTL.

    Returns the banner text if one was printed, else None. Every parameter is
    injectable so the rate limiting and both drift states can be constructed
    in tests.
    """
    ttl = _resolve_ttl(ttl)
    if ttl <= 0:
        return None
    now = time.time() if now is None else now
    stamp = stamp_path or default_stamp_path()
    if _rate_limited(stamp, now, ttl):
        return None

    checkout = find_checkout(module_file or Path(__file__))
    if checkout is None:
        return None

    banner = compose(checkout, read_branch(checkout), is_dirty(checkout))
    if banner is None:
        return None
    print(banner, file=stream if stream is not None else sys.stderr)
    return banner
