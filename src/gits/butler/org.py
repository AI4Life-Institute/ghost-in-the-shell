"""Org tree: discovery, schema lint, resolver, owner-resolution, dispatch guard.

This module is the **pure-logic** half of task or6t4n. Every function here is
side-effect-free (the only I/O is reading ``Org/<org>/*.yaml`` off disk and a
``git rev-parse`` to locate the vault root); nothing in this file creates
channels, worktrees, or commits — that all lives in :mod:`gits.butler.onboard`.

The normative schema this code enforces is documented in
``<ghost-repo>/docs/org-schema.md``. The conformance spec is the POC harness
``2026-05-31-or6t4n-org-schema-poc.py`` in the vault — :func:`lint`,
:func:`render_tree`, :func:`resolve_channel`, :func:`resolve_owner` and
:func:`can_dispatch` must reproduce its behavior exactly.

Layout (one directory per org)::

    Org/<org>/
      _meta.yaml          org-level config + DERIVED tree_snapshot (NOT a node)
      <alias>.yaml        one file per node; filename stem MUST == `alias`

Org discovery = subdirs of ``Org/`` that contain a ``_meta.yaml``.
"""

from __future__ import annotations

import copy
import glob
import os
import re

import yaml

from . import identity

# A node alias: lowercase, starts with a letter. The federation root is the one
# documented exception (it may be CamelCase, e.g. ``Ai4Life``).
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
REQUIRED = ["alias", "channel_id", "human", "reports_to", "scope", "desc"]
RESERVED = ("main", "master", "head")

# Fields the abstract root is allowed to omit (it has no Discord channel, owns
# no folder by default, etc.). Mirrors the POC's REQUIRED-skip set.
_ROOT_OPTIONAL = ("channel_id", "scope", "desc", "human")

# Keys a node file may legitimately carry. Anything else => "unknown key".
_KNOWN_NODE_KEYS = set(REQUIRED) | {"kind", "chair", "__file"}


# ---------------------------------------------------------------------------
# Tree rendering (the DERIVED _meta.tree_snapshot)
# ---------------------------------------------------------------------------


def render_tree(meta: dict, nodes: dict) -> list:
    """Canonical org-tree snapshot as a YAML SEQUENCE.

    leaf = alias (str); parent = ``{alias: [children]}``; children sorted;
    cycle-safe. Identical to the POC's ``render_tree``.
    """
    children: dict = {}
    for a, n in nodes.items():
        children.setdefault(n.get("reports_to"), []).append(a)

    def build(a, seen):
        if a in seen:
            return a
        seen = seen | {a}
        kids = sorted(children.get(a, []))
        return {a: [build(c, seen) for c in kids]} if kids else a

    return [build(meta.get("root"), set())]


def with_snapshot(meta: dict, nodes: dict) -> dict:
    """``meta`` copy whose ``tree_snapshot`` is regenerated — what onboard
    does on write (in memory; the on-disk splice lives in :mod:`onboard`)."""
    m = copy.deepcopy(meta)
    m["tree_snapshot"] = render_tree(meta, nodes)
    return m


def render_snapshot_block(meta: dict, nodes: dict, inline_comment: str = "") -> str:
    """Render the full ``tree_snapshot:`` YAML block as text, matching the
    hand-authored style (sequence items at indent 2, nested +2).

    ``inline_comment`` is re-emitted verbatim after ``tree_snapshot:`` (it
    already includes its leading whitespace + ``#``) so a regen round-trips
    the human comment on that line. Used by the surgical splice in onboard.
    """
    seq = render_tree(meta, nodes)
    lines = [f"tree_snapshot:{inline_comment}"]

    def emit(node, indent: int) -> None:
        pad = " " * indent
        if isinstance(node, dict):
            (alias, kids), = node.items()
            lines.append(f"{pad}- {alias}:")
            for k in kids:
                emit(k, indent + 2)
        else:
            lines.append(f"{pad}- {node}")

    for top in seq:
        emit(top, 2)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Discovery + load
# ---------------------------------------------------------------------------


def discover_orgs(org_root: str) -> list[str]:
    """Org dirs = subdirs of ``Org/`` that contain a ``_meta.yaml``. Sorted."""
    return sorted(
        x for x in glob.glob(os.path.join(org_root, "*/"))
        if os.path.exists(os.path.join(x, "_meta.yaml"))
    )


def load_org(org_dir: str) -> tuple[dict, dict]:
    """Load ``(meta, nodes)`` from an org dir. Each node carries an internal
    ``__file`` key (basename) for ``file:field`` error messages."""
    with open(os.path.join(org_dir, "_meta.yaml")) as f:
        meta = yaml.safe_load(f)
    nodes: dict = {}
    for fp in glob.glob(os.path.join(org_dir, "*.yaml")):
        if os.path.basename(fp) == "_meta.yaml":
            continue
        with open(fp) as f:
            nd = yaml.safe_load(f)
        nd["__file"] = os.path.basename(fp)
        nodes[nd["alias"]] = nd
    return meta, nodes


def read_meta_text(org_dir: str) -> str:
    with open(os.path.join(org_dir, "_meta.yaml")) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Lint — every HARD invariant from the schema. Pure: (meta, nodes) -> [errors]
# ---------------------------------------------------------------------------


def lint(meta: dict, nodes: dict) -> list[str]:
    """Return a list of ``file:field`` error strings; empty == clean.

    Reproduces the POC's ``lint`` exactly (the conformance spec), including
    the structural ``tree_snapshot`` comparison and the explicit alias-format
    check.
    """
    errs: list[str] = []
    root = meta.get("root")

    for a, n in nodes.items():
        is_root = n.get("reports_to") is None
        for f in REQUIRED:
            if f in _ROOT_OPTIONAL and is_root:
                continue
            if f not in n:
                errs.append(f"{n['__file']}:{f} missing required field")
        if not is_root and not isinstance(n.get("scope"), list):
            errs.append(f"{n['__file']}:scope must be a list")
        # alias format — explicit check (root exempt). Do NOT rely on another
        # rule incidentally catching a malformed alias (POC regression).
        if a != root and not NAME_RE.match(str(a)):
            errs.append(f"{n['__file']}:alias '{a}' not ^[a-z][a-z0-9_-]*$")
        if n["__file"] != f"{a}.yaml":  # filename stem == alias
            errs.append(f"{n['__file']}:filename != alias '{a}'")
        # no unknown keys
        for k in n:
            if k not in _KNOWN_NODE_KEYS:
                errs.append(f"{n['__file']}:unknown key '{k}'")

    # channel_id unique across nodes
    cid: dict = {}
    for a, n in nodes.items():
        if n.get("channel_id"):
            cid.setdefault(n["channel_id"], []).append(a)
    for c, al in cid.items():
        if len(al) > 1:
            errs.append(f"channel_id {c} shared by {sorted(al)}")

    # exactly one root, and it == _meta.root
    roots = [a for a, n in nodes.items() if n.get("reports_to") is None]
    if len(roots) != 1:
        errs.append(f"expected exactly 1 root, got {sorted(roots)}")
    elif roots[0] != root:
        errs.append(f"root {roots[0]} != _meta.root {root}")

    # reports_to resolves in-org
    for a, n in nodes.items():
        rt = n.get("reports_to")
        if rt is not None and rt not in nodes:
            errs.append(f"{n['__file']}:reports_to dangling -> {rt}")

    # acyclic
    for a in nodes:
        seen, cur = set(), a
        while cur is not None:
            if cur in seen:
                errs.append(f"{nodes[a]['__file']}: cycle in reports_to at {cur}")
                break
            seen.add(cur)
            cur = nodes.get(cur, {}).get("reports_to")

    # scope folders mutually exclusive (exact duplicate == ambiguous owner)
    owner: dict = {}
    for a, n in nodes.items():
        for f in (n.get("scope") or []):
            if f in owner:
                errs.append(f"scope folder '{f}' claimed by both {owner[f]} and {a}")
            owner[f] = a

    # tree_snapshot must equal the reconstruction (skip if structure broken)
    if not any(("root" in e or "cycle" in e or "dangling" in e) for e in errs):
        if meta.get("tree_snapshot") != render_tree(meta, nodes):
            errs.append("_meta.yaml:tree_snapshot out of sync with reports_to (regenerate)")

    return errs


def meta_top_level_keys(meta_text: str) -> list[str]:
    """Top-level keys of ``_meta.yaml`` in document order (textual scan)."""
    keys = []
    for line in meta_text.splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):", line)
        if m:
            keys.append(m.group(1))
    return keys


def lint_org_dir(org_dir: str) -> list[str]:
    """Disk-level lint: the pure :func:`lint` PLUS textual/filesystem checks
    that need the raw file or the vault tree.

    Extra checks beyond :func:`lint`:
      - ``tree_snapshot`` MUST be the final top-level key in ``_meta.yaml``
        (keeps the onboard surgical splice — "replace from tree_snapshot: to
        EOF" — safe).
      - (warn, not fail) each ``scope`` path exists on disk under the vault
        root. Warnings are prefixed ``warn:`` and never make the org dirty.
    """
    meta, nodes = load_org(org_dir)
    errs = lint(meta, nodes)

    meta_text = read_meta_text(org_dir)
    keys = meta_top_level_keys(meta_text)
    if "tree_snapshot" in keys and keys[-1] != "tree_snapshot":
        errs.append(
            "_meta.yaml:tree_snapshot must be the LAST top-level key "
            f"(found last key '{keys[-1]}'); onboard splices it from EOF"
        )

    # warn-only: scope folder existence under the vault root
    vault = os.path.dirname(os.path.dirname(os.path.abspath(org_dir.rstrip("/"))))
    for a, n in nodes.items():
        for f in (n.get("scope") or []):
            if not os.path.exists(os.path.join(vault, f)):
                errs.append(f"warn: {n['__file']}:scope folder '{f}' not found on disk")
    return errs


def split_errors(errs: list[str]) -> tuple[list[str], list[str]]:
    """Partition lint output into ``(hard_errors, warnings)``."""
    hard = [e for e in errs if not e.startswith("warn:")]
    warns = [e for e in errs if e.startswith("warn:")]
    return hard, warns


# ---------------------------------------------------------------------------
# Resolver / owner-resolution / dispatch guard (pure; POC-equivalent).
# NOTE (or6t4n scope): resolve_owner / is_descendant / can_dispatch are
# delivered + tested here but are NOT wired into live dispatch — that is
# Phase 2 (a separate task). See docs/org-schema.md.
# ---------------------------------------------------------------------------


def resolve_channel(nodes: dict, alias: str) -> str | None:
    """alias -> channel_id (None if unknown / abstract root)."""
    return nodes.get(alias, {}).get("channel_id")


def resolve_alias_by_channel(nodes: dict, channel_id: str) -> str | None:
    """channel_id -> alias (the inverse; used to resolve the caller's node)."""
    for a, n in nodes.items():
        if n.get("channel_id") == channel_id:
            return a
    return None


def resolve_owner(nodes: dict, path: str) -> str | None:
    """task path -> owner alias. Deepest scope folder that prefixes ``path``
    wins; ``None`` => UNASSIGNED (surface to the chair, never auto-open on the
    abstract root). Identical to the POC."""
    best: tuple[str | None, int] = (None, -1)
    for a, n in nodes.items():
        for f in (n.get("scope") or []):
            fp = f if f.endswith("/") else f + "/"
            if path.startswith(fp) and len(fp) > best[1]:
                best = (a, len(fp))
    return best[0]


def is_descendant(nodes: dict, target: str, caller: str) -> bool:
    """True iff ``target`` is a strict descendant of ``caller`` in reports_to."""
    cur = nodes.get(target, {}).get("reports_to")
    while cur is not None:
        if cur == caller:
            return True
        cur = nodes.get(cur, {}).get("reports_to")
    return False


def can_dispatch(nodes: dict, caller: str | None, target: str) -> bool:
    """Descendant-only dispatch guard. ``caller is None`` == chair / operating
    from main → may dispatch to anyone (treated as root)."""
    return caller is None or is_descendant(nodes, target, caller)


# ---------------------------------------------------------------------------
# Locating the vault + org for a given cwd (applies to every org command)
# ---------------------------------------------------------------------------


class OrgError(Exception):
    """Raised when the vault/org can't be resolved or a guard halts."""


def vault_root(cwd: str | None = None) -> str:
    """Vault root = git toplevel of the caller's cwd. Raises if not in a repo."""
    top = identity.run_git("rev-parse", "--show-toplevel", cwd=cwd)
    if not top:
        raise OrgError("not in a git worktree (cannot locate the vault root)")
    return top


def org_root(cwd: str | None = None) -> str:
    return os.path.join(vault_root(cwd=cwd), "Org")


def resolve_org_dir(
    org_slug: str | None = None,
    cwd: str | None = None,
) -> str:
    """Resolve WHICH ``Org/<org>/`` to operate on, per the schema rules:

      1. ``--org <slug>`` if given;
      2. else if exactly one org dir exists, use it;
      3. else resolve by the caller's own node — match ``.butler.json``
         ``channel_id`` against each org's node files.
      Still ambiguous → :class:`OrgError` asking for ``--org``.
    """
    root = org_root(cwd=cwd)
    orgs = discover_orgs(root)
    if not orgs:
        raise OrgError(f"no orgs found under {root} (need a subdir with _meta.yaml)")

    if org_slug:
        target = os.path.join(root, org_slug, "")
        if target not in orgs:
            avail = ", ".join(os.path.basename(o.rstrip("/")) for o in orgs)
            raise OrgError(f"--org '{org_slug}' not found (available: {avail})")
        return target

    if len(orgs) == 1:
        return orgs[0]

    # resolve by caller's node channel_id
    binding = identity.load_binding(cwd=cwd)
    cid = binding.get("channel_id")
    if cid:
        matches = [o for o in orgs if resolve_alias_by_channel(load_org(o)[1], cid)]
        if len(matches) == 1:
            return matches[0]
    avail = ", ".join(os.path.basename(o.rstrip("/")) for o in orgs)
    raise OrgError(f"ambiguous org (found: {avail}); pass --org <slug>")


def org_slug_of(org_dir: str) -> str:
    return os.path.basename(org_dir.rstrip("/"))


def resolve_caller_alias(org_dir: str, cwd: str | None = None) -> str | None:
    """The caller's own node alias, via ``.butler.json`` channel_id, or None
    (e.g. running from ``main`` or an unregistered channel)."""
    binding = identity.load_binding(cwd=cwd)
    cid = binding.get("channel_id")
    if not cid:
        return None
    _, nodes = load_org(org_dir)
    return resolve_alias_by_channel(nodes, cid)
