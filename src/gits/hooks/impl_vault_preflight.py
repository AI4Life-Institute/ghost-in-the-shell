"""PreToolUse guard: refuse impl edits inside a vault/PM session.

Governance background (Ghost task j5pn2w)
------------------------------------------
The vault session is butler/PM. Implementation work — editing a source repo,
committing, opening a PR — must run in a *dispatched executor* whose project
root is the source repo, NOT in the vault session.

There is **no settings-inheritance bug**: Claude Code loads project hooks only
from the session-start directory, and ``ghost butler dispatch`` always binds an
executor to the source repo (``work_dir`` = project ``local_path``). So a
source-repo-bound executor structurally cannot load the vault's
``block-source-repo-mutations`` hook, and is free to commit. The failure mode
is a *workflow misroute*: impl work performed **inside the vault session**
(project root = vault), where the source-mutation hook fires correctly.

This guard is the impl-aware companion to that hook. When — and only when — the
current session's project root is a vault, it refuses tool calls that would
mutate a path *outside* the vault, with a message that points at the real fix
(re-dispatch into a source-repo-bound executor) instead of tempting the
operator to disable a correctly-firing safety hook.

Design constraints
------------------
* **Self-gating.** Installed account-wide, so it runs in every session. The
  first thing it does is check the project root: if it is not a vault, it
  exits 0 immediately. Source-repo executors (the common case) pay only a
  basename check plus two ``stat`` calls.
* **Vault hook stays as-is.** This does not replace or weaken
  ``block-source-repo-mutations`` — it adds clearer impl-phase guidance.
* **Stdlib only.** Runs on every Edit/Write/Bash call; no heavy imports.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# A vault checkout is the main ``vault`` repo or a personal worktree
# ``vault-<name>`` (matches gits.butler.identity._WORKTREE_DIR_RE's intent).
_VAULT_DIR_RE = re.compile(r"^vault(-[a-z0-9][a-z0-9_-]*)?$")

# Verbs that *change* a repo. A bare read (status/log/diff) never matches.
_MUTATING_GIT_VERBS = {
    "add", "commit", "tag", "reset", "mv", "rm", "push", "rebase", "merge",
    "checkout", "switch", "stash", "cherry-pick", "revert", "apply", "am",
    "clean", "gc", "init", "clone", "submodule", "restore", "worktree", "notes",
}

_MUTATING_NON_GIT = re.compile(
    r"\b("
    r"npm\s+(install|i|add|remove|uninstall|update|upgrade|ci|publish)|"
    r"yarn\s+(install|add|remove|upgrade|publish)|"
    r"pnpm\s+(install|i|add|remove|update|publish)|"
    r"bun\s+(install|i|add|remove|update|publish)|"
    r"uv\s+(add|remove|sync|pip|publish|build)|"
    r"pip\s+(install|uninstall)|"
    r"poetry\s+(add|remove|install|update|publish)|"
    r"cargo\s+(add|remove|install|publish)|"
    r"go\s+(install|get)|"
    r"make|"
    r"pre-commit\s+install"
    r")\b"
)

_ABS_PATH_RE = re.compile(r"(?<![\w/])(/[\w.@+\-]+(?:/[\w.@+\-]+)*)")

_FILE_TOOLS = ("Edit", "Write", "NotebookEdit")

_MESSAGE = """\
BLOCKED by ghost impl-preflight: this session's project root is the vault
  {project_dir}
but the tool is trying to mutate a path OUTSIDE the vault:
  {target}

Implementation work must NEVER run in the vault/PM session. Re-dispatch this
task into a source-repo-bound executor — that session's project root is the
source repo, where this guard (and the vault source-mutation hook) does not
apply, so it can edit/commit/push freely:

  ghost butler dispatch <task-id> --phase impl

Do NOT disable any hook to push through. The source-mutation guard is firing
correctly; disabling it leaves the PM session's guardrail off. The fix is to
move the work to the right session, not to silence the guard.

Vault-internal edits (task pages, docs under this repo) and read-only
inspection (git status/log/diff, ls, cat, grep) remain allowed."""


def is_vault_root(path: str) -> bool:
    """True if ``path`` is a vault checkout (PM context).

    Two independent signals; either is sufficient:

    * basename matches ``vault`` / ``vault-<name>``; or
    * structural markers — a ``Projects/`` directory and a ``MACHINES.md``
      file (the vault's defining layout), which is machine-agnostic and
      survives a renamed checkout.
    """
    if not path:
        return False
    base = os.path.basename(os.path.normpath(path))
    if _VAULT_DIR_RE.match(base):
        return True
    return os.path.isdir(os.path.join(path, "Projects")) and os.path.isfile(
        os.path.join(path, "MACHINES.md")
    )


def detect_project_dir(payload: dict) -> str | None:
    """Resolve the session's project root.

    Prefers ``CLAUDE_PROJECT_DIR`` (set by Claude Code from the session-start
    dir — the authoritative project root, and free of a subprocess). Falls
    back to the payload ``cwd`` resolved to its git toplevel, then to the raw
    ``cwd``.
    """
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return env_dir
    cwd = payload.get("cwd") or ""
    if not cwd:
        return None
    try:
        top = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        if top:
            return top
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


def _path_under(path: str, root: str) -> bool:
    """True if ``path`` is ``root`` or nested under it (lexical, normalized)."""
    root_n = os.path.normpath(root)
    path_n = os.path.normpath(path)
    return path_n == root_n or path_n.startswith(root_n + os.sep)


def _external_bash_target(cmd: str, vault_root: str) -> str | None:
    """Return the first out-of-vault path a *mutating* bash command targets.

    Requires both a mutating verb (git write verb or a package/build install)
    AND an absolute path that is not under the vault. A plain ``git commit`` in
    the vault (no external path) operates on the vault itself — legitimate PM
    bookkeeping — and is allowed.
    """
    has_mutation = False
    for seg in re.split(r"(?:&&|\|\||;|\|)", cmd):
        m = re.match(r"^\s*git\b(.*)$", seg)
        if m:
            tokens = m.group(1).strip().split()
            flag_with_arg = {"-C", "-c", "--git-dir", "--work-tree", "--exec-path"}
            i = 0
            while i < len(tokens):
                t = tokens[i]
                if t.startswith("-"):
                    i += 2 if t in flag_with_arg else 1
                    continue
                if t in _MUTATING_GIT_VERBS:
                    has_mutation = True
                break
        elif _MUTATING_NON_GIT.search(seg):
            has_mutation = True
    if not has_mutation:
        return None
    for m in _ABS_PATH_RE.finditer(cmd):
        p = m.group(1)
        if not _path_under(p, vault_root):
            return p
    return None


def evaluate(
    tool_name: str | None,
    tool_input: dict,
    project_dir: str | None,
    *,
    vault_check=is_vault_root,
) -> tuple[bool, str]:
    """Core decision. Returns ``(allow, target)``.

    ``allow=True`` ⇒ exit 0. When ``allow=False``, ``target`` is the offending
    out-of-vault path, for the message.
    """
    # Can't determine project root → fail open; the vault source-mutation hook
    # remains the real safety net.
    if not project_dir or not vault_check(project_dir):
        return True, ""

    if tool_name in _FILE_TOOLS:
        fp = (tool_input or {}).get("file_path", "") or ""
        if fp and os.path.isabs(fp) and not _path_under(fp, project_dir):
            return False, fp
        return True, ""

    if tool_name == "Bash":
        target = _external_bash_target((tool_input or {}).get("command", "") or "", project_dir)
        if target:
            return False, target
        return True, ""

    return True, ""


def main() -> None:
    """Entry point for the ``gits guard`` PreToolUse hook.

    Runs two independent checks, in order:

    1. :mod:`gits.hooks.core_os_ticket` — refuse core-OS ticket origination
       without disclosed consent (Ghost task corehk). This one is
       **fail-closed**, so it is evaluated from the raw stdin text and cannot
       be skipped by a malformed payload.
    2. this module's vault impl-preflight (Ghost task j5pn2w).
    """
    from . import core_os_ticket

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        payload = None

    allow, message = core_os_ticket.check(payload, raw=raw)
    if not allow:
        print(message, file=sys.stderr)
        sys.exit(2)

    if not isinstance(payload, dict):
        sys.exit(0)

    project_dir = detect_project_dir(payload)
    allow, target = evaluate(
        payload.get("tool_name"),
        payload.get("tool_input") or {},
        project_dir,
    )
    if allow:
        sys.exit(0)
    print(_MESSAGE.format(project_dir=project_dir, target=target), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
