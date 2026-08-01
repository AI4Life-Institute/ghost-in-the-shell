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

Precision (Ghost task gdprec, ghost#34)
---------------------------------------
The guard is fail-*closed* by design and stays that way; what follows narrows
*what it looks at*, never *how hard it refuses*.

* **A path belongs to the sub-command it appears in.** The command line is
  split (quote-aware) on ``&&`` / ``||`` / ``;`` / ``|`` / newline, each
  segment is tokenised with :mod:`shlex`, and only segments whose *verb* is
  mutating can contribute a target. A source-repo path quoted inside a
  ``ghost butler send`` message body is an argument of a non-mutating command
  and is therefore invisible to the guard.
* **cwd-shaped git verbs target cwd.** ``git add -A`` / ``git commit`` with no
  path operand act on the working tree, so the target is the segment's cwd
  (tracked across ``cd`` in earlier segments), not "any path mentioned
  nearby". Option arguments (``-m <msg>``, ``-F <file>`` …) are consumed, so a
  message body can never be mistaken for a pathspec.
* **Shell redirections count as mutations.** ``echo x > /repo/f.py`` writes a
  source file without any git verb; with paths no longer harvested globally,
  the redirect target is what makes that case still refuse.
* **The criterion is the *class of activity*, not "inside the vault".** A PM
  session legitimately writes outside the vault to *its own session state* —
  its memory under the Claude config dir, its per-session scratchpad. That is
  bookkeeping, not implementation work, and this guard does not police it.
  Everything else outside the vault is still refused.
"""

from __future__ import annotations

import json
import os
import re
import shlex
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

# Non-git mutations, keyed by the segment's *verb*. ``None`` = any invocation
# of that command mutates. Matched at verb position only: the word "make"
# inside a quoted message body is prose, not a build.
_MUTATING_NON_GIT: dict[str, set[str] | None] = {
    "npm": {"install", "i", "add", "remove", "uninstall", "update", "upgrade", "ci", "publish"},
    "yarn": {"install", "add", "remove", "upgrade", "publish"},
    "pnpm": {"install", "i", "add", "remove", "update", "publish"},
    "bun": {"install", "i", "add", "remove", "update", "publish"},
    "uv": {"add", "remove", "sync", "pip", "publish", "build"},
    "pip": {"install", "uninstall"},
    "pip3": {"install", "uninstall"},
    "poetry": {"add", "remove", "install", "update", "publish"},
    "cargo": {"add", "remove", "install", "publish"},
    "go": {"install", "get"},
    "pre-commit": {"install"},
    "make": None,
}

# Top-level git flags (before the verb) that take a separate argument; the
# three that *relocate the repo* are singled out because they redefine the
# segment's target.
_GIT_REPO_FLAGS = {"-C", "--git-dir", "--work-tree"}
_GIT_TOP_FLAGS_WITH_ARG = _GIT_REPO_FLAGS | {"-c", "--exec-path", "--namespace"}

# Verb-level options that consume the next token. Without these, ``git commit
# -m "see /issues"`` would read its own message as a pathspec.
_GIT_VERB_OPTS_WITH_ARG = {
    "-m", "--message", "-F", "--file", "-c", "--reedit-message", "-C",
    "--reuse-message", "--author", "--date", "-S", "--gpg-sign", "--fixup",
    "--squash", "--pathspec-from-file", "--strategy", "--strategy-option",
    "-X", "-b", "-B", "-t", "--track", "--orphan", "--template", "--separator",
    "-e", "--exclude", "--depth", "--branch", "--origin", "-o",
}
# Short-option clusters whose final letter takes an argument (``-am wip``).
_GIT_SHORT_OPTS_WITH_ARG = set("mFcCSbBtXe")

# Verbs whose bare operands are pathspecs. Elsewhere (``rebase -i main``,
# ``worktree add ../wt``, ``push origin main``) a bare operand is a ref or a
# name, so only *absolute* operands and anything after ``--`` count as paths;
# the target is then the repo itself.
_PATHSPEC_GIT_VERBS = {"add", "rm", "mv", "restore", "clean", "apply", "am", "commit"}

# Command wrappers to look through when finding a segment's verb.
_WRAPPERS = {"sudo", "env", "time", "nohup", "command", "exec", "builtin", "\\"}

_FILE_TOOLS = ("Edit", "Write", "NotebookEdit")

# The PM session's own state lives outside the vault by construction: its
# cross-session memory under the Claude config dir, and its per-session
# scratchpad under a temp root (``.../claude-<uid>/<project>/<session>/...``).
# Writing there is bookkeeping, not implementation work.
_SESSION_SCRATCH_RE = re.compile(r"(?:^|/)claude-\d+/")
_SESSION_STATE_SUBDIRS = ("projects", "todos")

# ``2>``, ``>>``, ``&>``, ``<`` … optionally glued to their target
# (``2>/dev/null``).
_REDIRECT_RE = re.compile(r"^(\d*|&)(>>|>|<)(.*)$")

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

Vault-internal edits (task pages, docs under this repo), this session's own
state (its memory, its scratchpad), and read-only inspection (git
status/log/diff, ls, cat, grep) remain allowed — as does naming any path you
like inside a message body."""


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


def is_session_state(path: str) -> bool:
    """True if ``path`` is the *current session's own* state, not a repo.

    Two locations, both outside any vault by construction:

    * the Claude config dir's per-project state (``projects/`` — transcripts
      and the session's cross-session memory — and ``todos/``);
    * a per-session scratchpad under a temp root, whose path carries a
      ``claude-<uid>`` component.

    A PM session recording what it just learned is not implementation work,
    so this guard has nothing to say about it. Note the scratchpad rule is
    deliberately not "anything under /tmp": a repo cloned to ``/tmp/foo`` and
    committed from the PM session is still impl work and still refused.
    """
    if not path:
        return False
    p = os.path.normpath(path)
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )
    if any(_path_under(p, os.path.join(config_dir, sub)) for sub in _SESSION_STATE_SUBDIRS):
        return True
    if _is_temp(p):
        return bool(_SESSION_SCRATCH_RE.search(p + "/"))
    return False


def _is_temp(path: str) -> bool:
    """True if ``path`` lives under a system temp root."""
    roots = [os.environ.get("TMPDIR") or "", "/tmp", "/private/tmp",
             "/var/folders", "/private/var/folders"]
    return any(r and _path_under(path, r) for r in roots)


def _is_discard_sink(path: str) -> bool:
    """True if a redirect to ``path`` writes nothing durable.

    ``/dev/null`` (and the rest of ``/dev``: ``/dev/stderr``, ``/dev/fd/3``,
    ``/dev/tty``) is not a file being edited, and a temp file is scratch
    output. ``uv run pytest 2>/dev/null`` must stay allowed. This applies to
    *redirects only* — a git verb aimed at a repo under ``/tmp`` is still
    implementation work.
    """
    return _path_under(path, "/dev") or _is_temp(path)


def _split_segments(cmd: str) -> list[str]:
    """Split a command line into sub-commands, respecting quotes.

    Splits on unquoted ``&&`` / ``||`` / ``;`` / ``|`` / ``&`` / newline. A
    separator inside a quoted argument (a Discord message body, a doc string
    being written to a task page) does not split — it is data, not shell.
    """
    segments: list[str] = []
    buf: list[str] = []
    quote = ""
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(ch)
                buf.append(cmd[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            buf.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(cmd[i + 1])
            i += 2
            continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        # A lone ``&`` backgrounds a command and does separate one — but the
        # ``&`` of ``2>&1`` / ``&>log`` / ``>&2`` belongs to a redirection.
        if ch == "&" and (cmd[i - 1: i] == ">" or cmd[i + 1: i + 2] == ">"):
            buf.append(ch)
            i += 1
            continue
        if ch in ";|&\n":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]


def _tokenize(segment: str) -> list[str]:
    """Tokenize one segment, dropping shell grouping punctuation.

    Falls back to a whitespace split when the segment is not lexable on its
    own (an unbalanced quote, e.g. a heredoc body line).
    """
    seg = segment.strip().lstrip("({ ").rstrip(") }")
    try:
        return shlex.split(seg, comments=False)
    except ValueError:
        return seg.split()


def _verb(tokens: list[str]) -> tuple[str, list[str]]:
    """Return ``(verb, tokens-from-the-verb)``, skipping env assignments and
    wrappers (``sudo``, ``env``, …). Verb is basename'd so ``/usr/bin/git``
    reads as ``git``."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            continue
        base = os.path.basename(t)
        if base in _WRAPPERS:
            i += 1
            continue
        return base, tokens[i:]
    return "", []


def _resolve(path: str, cwd: str) -> str:
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.normpath(p)


def _git_targets(tokens: list[str], cwd: str) -> list[str] | None:
    """Paths a git segment mutates, or ``None`` if the verb is not mutating.

    Precedence: an explicit repo relocation (``-C`` / ``--git-dir`` /
    ``--work-tree``) sets the base; path operands of the verb (after option
    arguments are consumed) are the targets; with no operands the target is
    the base — i.e. **cwd** for the cwd-shaped verbs (``add``, ``commit``,
    ``stash``, …).
    """
    repo_dir = None
    i = 1
    verb = ""
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-"):
            name, sep, val = t.partition("=")
            if sep and name in _GIT_REPO_FLAGS:
                repo_dir = val
            elif not sep and t in _GIT_TOP_FLAGS_WITH_ARG and i + 1 < len(tokens):
                if t in _GIT_REPO_FLAGS:
                    repo_dir = tokens[i + 1]
                i += 1
            i += 1
            continue
        verb = t
        i += 1
        break
    if verb not in _MUTATING_GIT_VERBS:
        return None

    base = _resolve(repo_dir, cwd) if repo_dir else cwd
    pathspecs = verb in _PATHSPEC_GIT_VERBS
    operands: list[str] = []
    explicit: list[str] = []
    rest = tokens[i:]
    j = 0
    while j < len(rest):
        t = rest[j]
        if t == "--":
            # Everything past ``--`` is a pathspec, whatever the verb.
            explicit.extend(rest[j + 1:])
            break
        if t.startswith("--"):
            if "=" not in t and t in _GIT_VERB_OPTS_WITH_ARG:
                j += 1
            j += 1
            continue
        if t.startswith("-") and len(t) > 1:
            if t in _GIT_VERB_OPTS_WITH_ARG or t[-1] in _GIT_SHORT_OPTS_WITH_ARG:
                j += 1
            j += 1
            continue
        operands.append(t)
        j += 1
    paths = [_resolve(o, base) for o in explicit]
    paths += [
        _resolve(o, base) for o in operands if pathspecs or os.path.isabs(o) or o.startswith("~")
    ]
    return paths or [base]


def _non_git_targets(tokens: list[str], cwd: str) -> list[str] | None:
    """Paths a package/build segment mutates, or ``None`` if not mutating."""
    name = os.path.basename(tokens[0])
    if name not in _MUTATING_NON_GIT:
        return None
    subcommands = _MUTATING_NON_GIT[name]
    if subcommands is not None:
        sub = next((t for t in tokens[1:] if not t.startswith("-")), None)
        if sub not in subcommands:
            return None
    paths = [_resolve(t, cwd) for t in tokens[1:] if t.startswith("/")]
    return paths or [cwd]


def _split_redirects(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Separate redirections from the command's own words.

    Returns ``(tokens without redirections, files written via > / >>)``. Both
    halves matter: the write targets are mutations, and pulling them out is
    what stops ``git add -A > /dev/null`` from reading ``/dev/null`` as a
    pathspec. ``2>&1`` / ``>&2`` duplicate a descriptor and write nothing;
    ``< file`` is an input, not a target.
    """
    clean: list[str] = []
    sinks: list[str] = []
    j = 0
    while j < len(tokens):
        m = _REDIRECT_RE.match(tokens[j])
        if not m:
            clean.append(tokens[j])
            j += 1
            continue
        op, rest = m.group(2), m.group(3)
        if rest.startswith("&"):
            j += 1
            continue
        if not rest and j + 1 < len(tokens):
            j += 1
            rest = tokens[j]
        if rest and op in (">", ">>"):
            sinks.append(rest)
        j += 1
    return clean, sinks


def _external_bash_target(cmd: str, vault_root: str, cwd: str | None = None) -> str | None:
    """Return the first out-of-vault path a *mutating* bash command targets.

    Each sub-command is considered on its own: a path only counts when it is
    an operand (or redirect target) of a segment whose own verb mutates.
    ``cd`` in an earlier segment moves the cwd the later segments resolve
    against. Session-state writes (see :func:`is_session_state`) never count.
    """
    cursor = cwd or vault_root
    for segment in _split_segments(cmd):
        tokens, sinks = _split_redirects(_tokenize(segment))
        verb, tokens = _verb(tokens)
        if not tokens:
            continue
        if verb == "git":
            targets = _git_targets(tokens, cursor) or []
        else:
            targets = _non_git_targets(tokens, cursor) or []
        # A redirect only counts when it writes something durable: ``echo x >
        # /repo/f.py`` does, ``2>/dev/null`` and ``> /tmp/d.txt`` do not.
        targets += [
            p for p in (_resolve(s, cursor) for s in sinks) if not _is_discard_sink(p)
        ]
        for target in targets:
            if not _path_under(target, vault_root) and not is_session_state(target):
                return target
        if verb in ("cd", "pushd"):
            arg = next((t for t in tokens[1:] if not t.startswith("-")), None)
            cursor = _resolve(arg, cursor) if arg else os.path.expanduser("~")
    return None


def evaluate(
    tool_name: str | None,
    tool_input: dict,
    project_dir: str | None,
    *,
    vault_check=is_vault_root,
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Core decision. Returns ``(allow, target)``.

    ``allow=True`` ⇒ exit 0. When ``allow=False``, ``target`` is the offending
    out-of-vault path, for the message. ``cwd`` is the session's working
    directory (payload ``cwd``); it anchors relative paths and the cwd-shaped
    git verbs, and defaults to ``project_dir``.
    """
    # Can't determine project root → fail open; the vault source-mutation hook
    # remains the real safety net.
    if not project_dir or not vault_check(project_dir):
        return True, ""

    if tool_name in _FILE_TOOLS:
        fp = (tool_input or {}).get("file_path", "") or ""
        if fp and os.path.isabs(fp) and not _path_under(fp, project_dir):
            # The PM session's own memory / scratchpad is bookkeeping, not
            # implementation work — outside the vault, but not a repo.
            if is_session_state(fp):
                return True, ""
            return False, fp
        return True, ""

    if tool_name == "Bash":
        target = _external_bash_target(
            (tool_input or {}).get("command", "") or "", project_dir, cwd or project_dir
        )
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
        cwd=payload.get("cwd") or None,
    )
    if allow:
        sys.exit(0)
    print(_MESSAGE.format(project_dir=project_dir, target=target), file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
