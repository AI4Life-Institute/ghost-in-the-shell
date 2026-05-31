"""``ghost org`` — standalone org-tree commands (currently: ``lint``).

``ghost org lint`` is the hard backstop for the org schema: deterministic,
runs standalone, and is also what the vault commit-gate (``.githooks/pre-commit``)
invokes. Same lint as ``ghost butler onboard`` step 5 — one source of truth.

Exit codes: 0 = clean (warnings allowed), 1 = HARD lint error(s).
"""

from __future__ import annotations

import argparse
import sys

from .butler import org


def cmd_lint(args: argparse.Namespace) -> None:
    import os

    cwd = os.getcwd()
    try:
        root = org.org_root(cwd=cwd)
        if args.org:
            org_dirs = [org.resolve_org_dir(org_slug=args.org, cwd=cwd)]
        else:
            org_dirs = org.discover_orgs(root)
    except org.OrgError as e:
        sys.exit(f"ghost org lint: {e}")

    if not org_dirs:
        sys.exit(f"ghost org lint: no orgs found under {root}")

    total_hard = 0
    for od in org_dirs:
        slug = org.org_slug_of(od)
        hard, warns = org.split_errors(org.lint_org_dir(od))
        total_hard += len(hard)
        if not hard and not warns:
            print(f"✓ {slug}: clean")
        for e in hard:
            print(f"✗ {slug}/{e}")
        for w in warns:
            print(f"⚠ {slug}/{w}")

    if total_hard:
        sys.exit(f"\nghost org lint: {total_hard} hard error(s) — reject")


def install_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``org`` subparser group under ``ghost``."""
    p = sub.add_parser(
        "org",
        help="Org-tree schema commands (lint)",
        description=(
            "Org-tree schema authority. `lint` asserts every HARD invariant "
            "(see <ghost-repo>/docs/org-schema.md) across all orgs in the "
            "caller's vault; deterministic and standalone."
        ),
    )
    verbs = p.add_subparsers(dest="org_verb", required=True)

    sp = verbs.add_parser(
        "lint",
        help="Lint the org tree(s); exit 1 on any HARD invariant violation",
    )
    sp.add_argument("--org", default=None, help="Limit to one org slug")
    sp.set_defaults(func=cmd_lint)


def dispatch(args: argparse.Namespace) -> None:
    args.func(args)
