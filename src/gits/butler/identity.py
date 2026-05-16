"""Vault contributor identity + per-worktree binding state.

The hard rule (no OS-user fallback) lives here: on this machine multiple
humans share the ``sharon`` OS user, so ``getpass.getuser()`` would
silently misattribute every dispatch. If nothing in the chain resolves,
``resolve_user`` returns ``(None, None)`` and the caller must refuse.

All git introspection runs against the **caller's cwd**, never the
install dir — so ``ghost butler whoami`` in ``vault-weiliu`` yields
``weiliu`` and in ``vault-kathy`` yields ``kathy``, even though both
invocations run the same installed binary.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

BINDING_FILENAME = ".butler.json"

# The vault's personal-worktree convention encodes the contributor name in
# two redundant places (either signal is sufficient):
#   git worktree add <ai4life-src>/vault-<name> -b <name>/work
# We try branch first (the canonical signal); fall back to dir basename
# (robust if the worktree is on some other branch — e.g. ``main`` for a
# sync, or detached HEAD during a rebase).
_WORKTREE_BRANCH_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*)/work$")
_WORKTREE_DIR_RE = re.compile(r"^vault-([a-z0-9][a-z0-9_-]*)$")


def run_git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git subcommand in ``cwd`` (default: current dir). None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            cwd=cwd,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def detect_worktree_user(cwd: str | None = None) -> tuple[str, str] | None:
    """Infer the contributor name from the git checkout at ``cwd``.

    Returns ``(name, source_label)`` or ``None``. The label is a
    human-readable explanation suitable for ``whoami`` output.
    """
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if branch:
        m = _WORKTREE_BRANCH_RE.match(branch)
        if m:
            return m.group(1), f"worktree branch ({branch})"

    toplevel = run_git("rev-parse", "--show-toplevel", cwd=cwd)
    if toplevel:
        basename = os.path.basename(toplevel)
        m = _WORKTREE_DIR_RE.match(basename)
        if m:
            return m.group(1), f"worktree dir ({basename})"

    return None


def resolve_user(
    explicit: str | None = None,
    cwd: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve outgoing-prefix identity. First hit wins:

      1. ``explicit`` (typically from ``--user``/``--as`` flag)
      2. ``BUTLER_USER`` env var
      3. worktree branch ``<name>/work``
      4. worktree dir ``vault-<name>``

    Returns ``(None, None)`` if nothing resolves. **No OS-user fallback**
    — see module docstring for why.
    """
    if explicit:
        return explicit, "--user flag"
    env_user = os.environ.get("BUTLER_USER")
    if env_user:
        return env_user, "BUTLER_USER env"
    wt = detect_worktree_user(cwd=cwd)
    if wt:
        return wt
    return None, None


# ---------------------------------------------------------------------------
# Per-worktree binding state (<worktree>/.butler.json)
# ---------------------------------------------------------------------------


def binding_path(cwd: str | None = None) -> str | None:
    """Path to the current worktree's .butler.json, or None if not in a repo."""
    top = run_git("rev-parse", "--show-toplevel", cwd=cwd)
    if not top:
        return None
    return os.path.join(top, BINDING_FILENAME)


def load_binding(cwd: str | None = None) -> dict:
    """Return current worktree's binding dict, or {} if absent / not in a repo."""
    path = binding_path(cwd=cwd)
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_binding(data: dict, cwd: str | None = None) -> None:
    path = binding_path(cwd=cwd)
    if not path:
        sys.exit("ghost butler: not in a git worktree; can't persist a binding")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def clear_binding(cwd: str | None = None) -> None:
    path = binding_path(cwd=cwd)
    if path and os.path.exists(path):
        os.remove(path)


def require_home_channel(verb: str, cwd: str | None = None) -> str:
    """Return home channel id, or exit pointing at ``ghost butler bind``."""
    binding = load_binding(cwd=cwd)
    cid = binding.get("channel_id")
    if not cid:
        sys.exit(
            f"ghost butler: no target_id given and no home channel bound "
            f"for this worktree.\n"
            f"  Either: pass an id explicitly to `ghost butler {verb}`,\n"
            f"  or:     `ghost butler bind <channel_id>` to set a home channel."
        )
    return cid
