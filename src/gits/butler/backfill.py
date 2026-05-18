"""``ghost butler backfill-owners`` — one-shot repair for legacy task pages.

Scans every task page under the caller's vault (``<git-toplevel>/Projects/``)
for tasks whose status is past ``draft`` but whose frontmatter has no
``owner:`` field. For each match, fetches the thread's first
``[butler:USER]``-decorated message via the Discord REST API and writes
``USER`` into the task page using the same atomic writeback machinery as
:mod:`gits.butler.dispatch_task`.

Idempotent — re-runs skip tasks that already have an owner. Default mode
is **dry-run** (prints the planned writes); pass ``--apply`` to actually
write.

Scope is intentionally **the caller's vault only**, matching how
``ghost butler dispatch`` resolves ``_vault_root()``. To repair a sibling
worktree, ``cd`` into it first.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from . import dispatch_task
from .http import api
from .prefix import VAULT_DISPATCH_RE


_NON_DRAFT_STATUSES = {
    "dispatched (plan-phase)",
    "dispatched",
    "in-progress",
    "review",
    "done",
    "cancelled",
}

_THREAD_ID_RE = re.compile(r"/(\d{15,})\)")
# Capture the optional `:USER` group from a butler-decorated header. Mirrors
# the shape produced by :func:`gits.butler.prefix.decorate`.
_BUTLER_USER_RE = re.compile(
    r"\[(?:butler|管家|vault):([^\]]+)\]"
)


def _iter_task_files(vault_root: str):
    """Yield every ``.md`` path under ``Projects/*/{tasks,archive}/``."""
    projects_dir = os.path.join(vault_root, "Projects")
    if not os.path.isdir(projects_dir):
        return
    for project in sorted(os.listdir(projects_dir)):
        for kind in ("tasks", "archive"):
            base = os.path.join(projects_dir, project, kind)
            if not os.path.isdir(base):
                continue
            for root, _, files in os.walk(base):
                for fn in files:
                    if fn.endswith(".md") and fn != "README.md":
                        yield os.path.join(root, fn)


def _extract_thread_id(thread_field: str) -> str | None:
    """Pull the 15+-digit snowflake out of a markdown link frontmatter value."""
    m = _THREAD_ID_RE.search(thread_field or "")
    return m.group(1) if m else None


def _first_butler_user(thread_id: str) -> str | None:
    """Return the USER from the first ``[butler:USER]``-decorated message in
    the thread, or ``None`` if no such message is found / the thread can't
    be read.

    Discord returns messages newest-first by default; we walk *backwards*
    by sliding a ``before=<oldest-so-far>`` cursor until we either find the
    earliest decorated message or run out of history. The earliest in time
    is the dispatch pointer in practice. Capped at 10 pages × 100 to avoid
    hammering the API on pathologically long threads."""
    found: str | None = None
    before: str | None = None
    pages = 0
    while pages < 10:
        query: dict[str, str | int] = {"limit": 100}
        if before is not None:
            query["before"] = before
        status, body = api(f"/channels/{thread_id}/messages", query=query)
        if status != 200 or not isinstance(body, list) or not body:
            break
        for msg in body:
            content = msg.get("content", "") or ""
            if not VAULT_DISPATCH_RE.match(content):
                continue
            m = _BUTLER_USER_RE.search(content)
            if m:
                # Body is newest-first; keep overwriting so we end with the
                # OLDEST decorated message in this page.
                found = m.group(1).strip()
        before = str(min(int(m.get("id", "0")) for m in body))
        pages += 1
        if len(body) < 100:
            break
    return found


def backfill_owners(vault_root: str, *, apply: bool) -> int:
    """Walk vault, write ``owner:`` where missing. Return process exit code.

    Prints one line per task examined; emits a summary at the end. In
    dry-run mode, prints the *would-be* writes but does not modify files.
    """
    examined = 0
    skipped_has_owner = 0
    skipped_draft = 0
    resolved = 0
    unresolved: list[tuple[str, str]] = []  # (path, reason)
    written: list[tuple[str, str]] = []     # (path, owner)

    for path in _iter_task_files(vault_root):
        examined += 1
        try:
            fm = dispatch_task.parse_frontmatter(path)
        except SystemExit:
            unresolved.append((path, "no/invalid frontmatter"))
            continue

        status = (fm.get("status") or "").strip()
        if status not in _NON_DRAFT_STATUSES:
            skipped_draft += 1
            continue
        if fm.get("owner"):
            skipped_has_owner += 1
            continue

        tid = _extract_thread_id(fm.get("thread", ""))
        if not tid:
            unresolved.append((path, "no thread id in frontmatter"))
            continue

        owner = _first_butler_user(tid)
        if not owner:
            unresolved.append((path, f"no [butler:USER] message in thread {tid}"))
            continue

        resolved += 1
        if apply:
            dispatch_task.writeback_frontmatter_atomic(path, {"owner": owner})
            written.append((path, owner))
            print(f"WROTE  owner={owner:<24}  {os.path.relpath(path, vault_root)}")
        else:
            print(f"would: owner={owner:<24}  {os.path.relpath(path, vault_root)}")

    print()
    print("─" * 60)
    print(f"examined:       {examined}")
    print(f"skipped (draft):     {skipped_draft}")
    print(f"skipped (owner set): {skipped_has_owner}")
    print(f"resolved:       {resolved}")
    if apply:
        print(f"WROTE:          {len(written)}")
    else:
        print(f"would write:    {resolved}  (dry-run — pass --apply to commit)")
    if unresolved:
        print(f"unresolved:     {len(unresolved)}  (skipped — not a failure)")
        for path, reason in unresolved:
            print(f"  ! {reason}: {os.path.relpath(path, vault_root)}")
    return 0


def cmd_backfill_owners(args: argparse.Namespace) -> None:
    cwd = os.getcwd()
    vault_root = dispatch_task._vault_root(cwd=cwd)
    sys.exit(backfill_owners(vault_root, apply=args.apply))
