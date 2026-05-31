"""``ghost butler onboard`` — create a new agent end-to-end, atomically.

This is the **only** code path allowed to write an ``Org/<org>/<alias>.yaml``
node file (and to regenerate the DERIVED ``_meta.tree_snapshot`` via a surgical
text-splice). All schema enforcement is delegated to the pure
:mod:`gits.butler.org` lint — the same lint the standalone ``ghost org lint``
and the vault commit-gate run, so a broken tree can never be written.

Lifecycle (see docs/org-schema.md and task or6t4n):
  1. validate ``<name>`` (regex + reserved word)
  2. resolve org + parent (``--reports-to`` or caller's node)
  3. guard: no existing node / worktree / branch
  4. create (or reuse) the Discord channel under the org's onboarding_category
  5. write node + regenerate snapshot + lint + commit (atomic: lint BEFORE write)
  6. ``git worktree add ... -b <name>/work`` from caller HEAD (NO push)
  7. bind home channel + send ``/bind`` + await 🔗
  8. verify + 9. summary

``--dry-run`` prints the plan and creates nothing. Re-running for an existing
name is a no-op-or-resume, never a duplicate.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import yaml

from . import identity, onboarding, org
from .http import api

CLIS = ("claude", "codex", "copilot", "opencode")


# ---------------------------------------------------------------------------
# Node file rendering (fresh files only — never a round-trip, so safe_dump is
# fine; node files carry a one-line provenance header like the hand-authored
# ones).
# ---------------------------------------------------------------------------


def build_node(
    name: str,
    channel_id: str | None,
    human: str | None,
    reports_to: str,
    scope: list[str],
    desc: str,
) -> dict:
    """The node mapping, in canonical field order."""
    node = {
        "alias": name,
        "channel_id": channel_id,
        "human": human,
        "reports_to": reports_to,
        "scope": list(scope or []),
        "desc": desc or "",
    }
    return node


def render_node_yaml(org_slug: str, node: dict) -> str:
    """Serialize a node to YAML text with a provenance header comment."""
    header = (
        f"# {node['alias']} — node in the {org_slug} org tree. "
        f"Model + meta: Org/{org_slug}/_meta.yaml (org: {org_slug})\n"
    )
    body = yaml.safe_dump(
        node, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return header + body


def splice_meta_snapshot(meta_text: str, meta: dict, nodes: dict) -> str:
    """Regenerate ``_meta.tree_snapshot`` by replacing from the
    ``tree_snapshot:`` line to EOF — leaving every hand-written comment above
    it untouched. ``tree_snapshot`` is required to be the last top-level key
    (lint enforces this), which is what makes the splice safe.
    """
    import re

    m = re.search(r"(?m)^tree_snapshot:(.*)$", meta_text)
    if not m:
        block = org.render_snapshot_block(meta, nodes, "")
        sep = "" if meta_text.endswith("\n") else "\n"
        return f"{meta_text}{sep}\n{block}"
    inline = m.group(1)  # preserve the inline "# DERIVED ..." comment
    block = org.render_snapshot_block(meta, nodes, inline)
    return meta_text[: m.start()] + block


# ---------------------------------------------------------------------------
# git helpers (mutating ops; longer timeout than identity.run_git's 2s)
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True, text=True, timeout=60, check=check,
    )


def _branch_exists_local(vault: str, branch: str) -> bool:
    r = _git("rev-parse", "--verify", "--quiet", branch, cwd=vault, check=False)
    return r.returncode == 0


def _branch_exists_origin(vault: str, branch: str) -> bool | None:
    """True/False if origin is reachable; None if we couldn't tell (offline)."""
    try:
        r = subprocess.run(
            ["git", "-C", vault, "ls-remote", "--exit-code", "origin", branch],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.SubprocessError:
        return None
    if r.returncode == 0:
        return True
    if r.returncode == 2:  # ls-remote: ref not found
        return False
    return None


def _worktree_exists(vault: str, wt_path: str) -> bool:
    r = _git("worktree", "list", "--porcelain", cwd=vault, check=False)
    return os.path.abspath(wt_path) in (r.stdout or "")


# ---------------------------------------------------------------------------
# Discord channel (create-or-reuse) + reaction await
# ---------------------------------------------------------------------------


def _find_channel(guild_id: str, name: str, category_id: str) -> str | None:
    status, resp = api(f"/guilds/{guild_id}/channels")
    if status != 200 or not isinstance(resp, list):
        return None
    for ch in resp:
        if ch.get("name") == name and str(ch.get("parent_id")) == str(category_id):
            return str(ch["id"])
    return None


def _create_channel(guild_id: str, name: str, category_id: str) -> str:
    status, resp = api(
        f"/guilds/{guild_id}/channels",
        method="POST",
        body={"name": name, "type": 0, "parent_id": category_id},
    )
    if status not in (200, 201):
        raise org.OrgError(f"Discord channel create failed [{status}]: {resp}")
    return str(resp["id"])


def _await_reaction(channel_id: str, message_id: str, emoji: str = "🔗",
                    timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, resp = api(f"/channels/{channel_id}/messages/{message_id}")
        if status == 200 and isinstance(resp, dict):
            for r in resp.get("reactions", []) or []:
                if (r.get("emoji") or {}).get("name") == emoji:
                    return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Plan / pre-flight (shared by --dry-run and the live path)
# ---------------------------------------------------------------------------


def preflight(meta: dict, nodes: dict, name: str, reports_to: str | None,
              scope: list[str], human: str | None, desc: str) -> tuple[dict, list[str], list[str]]:
    """Validate inputs + run the trial lint with the new node added.

    Returns ``(trial_node, problems, lint_errors)``. ``problems`` are the
    pre-checks (bad name, alias taken, parent missing); ``lint_errors`` are
    HARD lint failures of the would-be tree (warnings excluded).
    """
    problems: list[str] = []
    if not org.NAME_RE.match(name) or name in org.RESERVED:
        problems.append(f"invalid/reserved name '{name}'")
    if name in nodes:
        problems.append(f"alias '{name}' already exists")
    if reports_to is None:
        problems.append("no --reports-to and caller has no resolvable node")
    elif reports_to not in nodes:
        problems.append(f"--reports-to '{reports_to}' not in org")

    node = build_node(name, "999_PENDING", human or name, reports_to or "", scope, desc)
    node["__file"] = f"{name}.yaml"
    trial = {**nodes, name: node}
    hard, _ = org.split_errors(org.lint(org.with_snapshot(meta, trial), trial))
    return node, problems, hard


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def cmd_onboard(args: argparse.Namespace) -> None:
    cwd = os.getcwd()
    try:
        _run_onboard(args, cwd)
    except org.OrgError as e:
        sys.exit(f"ghost butler onboard: {e}")


def _run_onboard(args: argparse.Namespace, cwd: str) -> None:
    name = args.name
    vault = org.vault_root(cwd=cwd)
    org_dir = org.resolve_org_dir(org_slug=args.org, cwd=cwd)
    org_slug = org.org_slug_of(org_dir)
    meta, nodes = org.load_org(org_dir)

    # Parent: --reports-to, else caller's own node.
    reports_to = args.reports_to or org.resolve_caller_alias(org_dir, cwd=cwd)

    # ── step 1+2 pre-checks + trial lint ────────────────────────────────
    scope = list(args.scope or [])
    human = args.human or name
    node_path = os.path.join(org_dir, f"{name}.yaml")

    # Idempotency: node already present?
    if os.path.exists(node_path):
        existing = yaml.safe_load(open(node_path))
        if reports_to and existing.get("reports_to") != reports_to:
            raise org.OrgError(
                f"'{name}' already exists with reports_to="
                f"'{existing.get('reports_to')}' (you passed '{reports_to}'); "
                f"refusing to clobber. Edit the node by hand or pick another name."
            )
        print(f"✓ already onboarded: {node_path} (reports_to={existing.get('reports_to')})")
        return

    _trial_node, problems, lint_errs = preflight(
        meta, nodes, name, reports_to, scope, human, args.desc
    )
    if problems:
        raise org.OrgError("; ".join(problems))
    if lint_errs:
        raise org.OrgError("would break the org tree:\n  " + "\n  ".join(lint_errs))

    # worktree parent: dirname(vault) by default (worktrees are siblings),
    # overridable per-machine via GITS_WORKTREE_PARENT.
    wt_parent = os.environ.get("GITS_WORKTREE_PARENT") or os.path.dirname(vault)
    wt_path = os.path.join(wt_parent, f"vault-{name}")
    branch = f"{name}/work"

    # config (authoritative in _meta; legacy global only if no org resolved)
    guild_id = str(meta.get("guild_id") or "")
    category_id = str(meta.get("onboarding_category") or "")
    if not guild_id or not category_id:
        cfg = onboarding.load() or {}
        guild_id = guild_id or str(cfg.get("guild_id") or "")
        category_id = category_id or str(cfg.get("category_id") or "")
    if not guild_id or not category_id:
        raise org.OrgError(
            f"_meta.yaml missing guild_id/onboarding_category for org '{org_slug}' "
            f"(and no legacy ~/.gits/butler-onboarding.json fallback)"
        )

    # ── step 3 guards ───────────────────────────────────────────────────
    if _worktree_exists(vault, wt_path):
        print(f"• worktree already present at {wt_path} — will resume")
    if _branch_exists_local(vault, branch):
        # branch without a node usually means an interrupted run; allow resume
        print(f"• branch {branch} already exists locally — will resume")
    origin = _branch_exists_origin(vault, branch)
    if origin is True:
        raise org.OrgError(f"branch {branch} already exists on origin; refusing")

    plan_lines = [
        f"onboard '{name}' into org '{org_slug}'",
        f"  reports_to : {reports_to}",
        f"  scope      : {scope or '(none)'}",
        f"  human      : {human}",
        f"  cli        : {args.cli}",
        f"  channel    : create/reuse #{name} under category {category_id} (guild {guild_id})",
        f"  node file  : {node_path}",
        f"  worktree   : {wt_path}  (branch {branch}, from caller HEAD, NO push)",
        f"  bind       : home channel + /bind {wt_path} {args.cli}",
    ]
    if args.dry_run:
        print("DRY-RUN — would do:\n" + "\n".join(plan_lines))
        print("\npre-checks: ok | trial lint: would pass")
        return

    print("\n".join(plan_lines))
    created: list[str] = []  # for partial-failure reporting

    # ── step 4 channel (create or reuse) ────────────────────────────────
    channel_id = _find_channel(guild_id, name, category_id)
    if channel_id:
        print(f"✓ reusing existing channel #{name} ({channel_id})")
    else:
        channel_id = _create_channel(guild_id, name, category_id)
        created.append(f"discord channel #{name} ({channel_id})")
        print(f"✓ created channel #{name} ({channel_id})")

    # ── step 5 write node + regen snapshot + lint + commit (atomic) ──────
    try:
        node = build_node(name, channel_id, human, reports_to, scope, args.desc)
        node_for_lint = {**node, "__file": f"{name}.yaml"}
        trial = {**nodes, name: node_for_lint}
        hard, _ = org.split_errors(org.lint(org.with_snapshot(meta, trial), trial))
        if hard:
            raise org.OrgError("post-build lint failed:\n  " + "\n  ".join(hard))

        with open(node_path, "w") as f:
            f.write(render_node_yaml(org_slug, node))
        meta_path = os.path.join(org_dir, "_meta.yaml")
        new_meta_text = splice_meta_snapshot(org.read_meta_text(org_dir), meta, trial)
        with open(meta_path, "w") as f:
            f.write(new_meta_text)

        # re-lint on disk (belt-and-suspenders) before committing
        disk_hard, _ = org.split_errors(org.lint_org_dir(org_dir))
        if disk_hard:
            raise org.OrgError("on-disk lint failed:\n  " + "\n  ".join(disk_hard))

        rel_node = os.path.relpath(node_path, vault)
        rel_meta = os.path.relpath(meta_path, vault)
        _git("add", rel_node, rel_meta, cwd=vault)
        _git("commit", "-m",
             f"org({org_slug}): onboard {name} (reports_to {reports_to})",
             cwd=vault)
        created.append(f"node file + commit ({rel_node})")
        print(f"✓ wrote + committed {rel_node} (+ regenerated _meta.tree_snapshot)")
    except Exception as e:
        # revert the two files so the tree is exactly as before
        _git("checkout", "--", "Org/", cwd=vault, check=False)
        if os.path.exists(node_path):
            os.remove(node_path)
        _report_partial(created)
        raise org.OrgError(f"node write/commit failed ({e}); reverted Org/")

    # ── step 6 worktree (NO push) ───────────────────────────────────────
    if not _worktree_exists(vault, wt_path):
        if _branch_exists_local(vault, branch):
            _git("worktree", "add", wt_path, branch, cwd=vault)
        else:
            _git("worktree", "add", wt_path, "-b", branch, cwd=vault)
        created.append(f"worktree {wt_path} (branch {branch})")
        print(f"✓ worktree {wt_path} on {branch} (not pushed — vault-sync propagates)")

    # ── step 7 bind home channel + /bind + await 🔗 ─────────────────────
    identity.save_binding(
        {"channel_id": channel_id, "channel_name": name, "guild_id": guild_id},
        cwd=wt_path,
    )
    from .butler_cli import send_decorated
    mid = send_decorated(channel_id, f"/bind {wt_path} {args.cli}", cwd=wt_path)
    created.append("home-channel binding + /bind sent")
    if _await_reaction(channel_id, mid, "🔗", timeout=5.0):
        print("✓ ghost acknowledged /bind (🔗)")
    else:
        _report_partial(created)
        raise org.OrgError(
            "no 🔗 within ~5s — ghost may be down or the gateway butler-prefix "
            "handler is missing. Channel/worktree/node/binding are all in place; "
            "re-run onboard to resume the bind."
        )

    # ── step 8+9 verify + summary ───────────────────────────────────────
    disk_hard, warns = org.split_errors(org.lint_org_dir(org_dir))
    lint_state = "clean" if not disk_hard else f"FAILED: {disk_hard}"
    print("\n".join([
        "",
        f"✅ onboarded {name}",
        f"  worktree : {wt_path}  (branch {branch})",
        f"  channel  : #{name} ({channel_id})",
        f"  reports_to: {reports_to}",
        f"  scope    : {scope or '(none)'}",
        f"  node     : {node_path}  (lint {lint_state})",
        f"  next     : cd {wt_path} && ghost butler whoami",
    ]))
    for w in warns:
        print(f"  ⚠ {w}")


def _report_partial(created: list[str]) -> None:
    if created:
        print("\nPartial side-effects created so far (clean up or re-run to resume):",
              file=sys.stderr)
        for c in created:
            print(f"  - {c}", file=sys.stderr)
