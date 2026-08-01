"""``ghost doctor`` — which ghost code is actually live (Ghost task whlive).

Thin printer over :mod:`gits.core.deployments`, mirroring the
``org_cli`` / ``butler.org`` split: all logic is in the core module so the
interesting states can be constructed in tests instead of observed on one
machine at one moment.

Exit codes: ``0`` clean *or* unresolved, ``1`` provable drift. ``unresolved``
never prints as clean — a probe we could not run is not evidence of agreement.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .core import deployments as dep_mod


def install_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "doctor",
        help="Report which ghost code is live (CLI, hooks, bot) and how it differs from master",
    )
    p.add_argument(
        "--json", action="store_true", dest="as_json", help="Machine-readable report"
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="git fetch before measuring distance (default: report as of the last fetch)",
    )
    p.add_argument(
        "--compare-ref",
        default=dep_mod.DEFAULT_COMPARE_REF,
        help=f"Ref to measure against (default: {dep_mod.DEFAULT_COMPARE_REF})",
    )
    p.add_argument(
        "--no-config-probe",
        action="store_true",
        help="Skip running each deployment's own interpreter to list Settings fields",
    )
    p.add_argument(
        "--preinstall",
        nargs="?",
        const=".",
        metavar="PATH",
        help=(
            "Instead of the full report: check whether installing from PATH "
            "(default: cwd) would pack uncommitted or untracked source. "
            "This does not prevent anything — it is a question you have to "
            "remember to ask."
        ),
    )


def dispatch(args: argparse.Namespace) -> None:
    if getattr(args, "preinstall", None):
        raise SystemExit(_run_preinstall(Path(args.preinstall).expanduser().resolve(),
                                         as_json=args.as_json))
    report = dep_mod.collect_report(
        compare_ref=args.compare_ref,
        fetch=args.fetch,
        probe_config=not args.no_config_probe,
    )
    if args.as_json:
        print(json.dumps(_to_jsonable(report), indent=2, sort_keys=True))
    else:
        _print_report(report)
    raise SystemExit(report.exit_code)


# ── preinstall ───────────────────────────────────────────────────────────


def _run_preinstall(path: Path, *, as_json: bool = False) -> int:
    check = dep_mod.check_preinstall(path)
    if as_json:
        print(json.dumps(_to_jsonable(check), indent=2, sort_keys=True))
        return check.exit_code

    print(f"ghost doctor --preinstall {path}")
    wt = check.worktree
    if wt is not None:
        branch = wt.branch or f"detached at {wt.head_sha}"
        print(f"  HEAD    {wt.head_sha} on {branch}")
    if not check.findings:
        print("  ✓ clean — an install from here packs exactly this commit")
    for finding in check.findings:
        print(f"  {_mark(finding.level)} {finding.code}: {finding.message}")
    print(
        "  note: this is a check you must remember to run; it cannot stop a "
        "dirty install, only answer whether one would be dirty."
    )
    return check.exit_code


# ── report printing ──────────────────────────────────────────────────────

_MARKS = {"error": "✗", "warn": "⚠", "unknown": "?"}


def _mark(level: str) -> str:
    return _MARKS.get(level, "-")


def _print_report(report: dep_mod.Report) -> None:
    head = f"ghost doctor — deployment provenance (compare: {report.compare_ref}"
    if report.compare_sha:
        head += f" @ {report.compare_sha}"
    head += ")"
    print(head)
    if report.compare_repo:
        print(f"  measured in {report.compare_repo}")
    print(f"  last fetch: {report.fetched_at or 'never / unknown'}")
    if report.config_env:
        print(f"  config.env: {report.config_env} ({len(report.config_keys)} key(s))")
    print()

    if not report.deployments:
        print("  no deployments found — nothing on PATH, in hook settings, or running.")

    for dep in report.deployments:
        _print_deployment(dep, report)
        print()

    _print_findings(report)


def _print_deployment(dep: dep_mod.Deployment, report: dep_mod.Report) -> None:
    print(f"[{'+'.join(dep.roles) or 'unknown'}] {dep.executable}")
    for label in dep.labels:
        print(f"      via {label}")
    if dep.origin is not None:
        print(f"      source: {dep.origin.summary}")
    if dep.receipt_requirement:
        print(f"      uv requirement: {dep.receipt_requirement}")

    wt = dep.worktree
    if wt is not None:
        branch = wt.branch or f"detached at {wt.head_sha}"
        print(f"      HEAD: {wt.head_sha} on {branch}")
        state = "dirty" if wt.dirty else "clean"
        print(
            f"      worktree: {state} "
            f"({len(wt.modified)} modified, {len(wt.untracked)} untracked, "
            f"{len(wt.untracked_sources)} untracked under src/)"
        )

    dist = dep.distance
    if dist is not None:
        if dist.error:
            print(f"      vs {report.compare_ref}: unknown — {dist.error}")
        elif not dist.ahead and not dist.behind:
            print(f"      vs {report.compare_ref}: up to date")
        else:
            print(f"      vs {report.compare_ref}: {dist.ahead} ahead, {dist.behind} behind")

    if dep.sha and not dep.sha_is_complete:
        print("      ⚠ uncommitted changes: this sha does NOT describe what runs")

    if dep.config is not None:
        if dep.config.status == "ok":
            print(f"      config: ok — declares all {len(report.config_keys)} key(s)")
        elif dep.config.status == "missing":
            print(f"      config: MISSING {', '.join(dep.config.missing)}")
        else:
            print(f"      config: unknown — {dep.config.reason}")


def _print_findings(report: dep_mod.Report) -> None:
    errors = report.errors
    warnings = report.warnings
    unknowns = report.unknowns

    if errors:
        print("drift:")
        for _, finding in errors:
            print(f"  ✗ {finding.code}: {finding.message}")
    if warnings:
        print("notes:")
        for _, finding in warnings:
            print(f"  ⚠ {finding.code}: {finding.message}")
    if unknowns:
        print("unresolved (NOT the same as fine):")
        for _, finding in unknowns:
            print(f"  ? {finding.code}: {finding.message}")

    print()
    if report.verdict == "clean":
        print("verdict: clean — every deployment matches "
              f"{report.compare_ref} and declares every config key.")
    elif report.verdict == "drift":
        print(f"verdict: drift — {len(errors)} problem(s)"
              + (f", {len(unknowns)} unresolved" if unknowns else "")
              + ".")
    else:
        print(f"verdict: unresolved — {len(unknowns)} question(s) could not be answered.")


# ── json ─────────────────────────────────────────────────────────────────


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def main(argv: list[str] | None = None) -> int:
    """Standalone entry (used by tests): returns the exit code."""
    parser = argparse.ArgumentParser(prog="ghost doctor")
    sub = parser.add_subparsers(dest="command")
    install_parser(sub)
    args = parser.parse_args(["doctor", *(argv or [])])
    try:
        dispatch(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
