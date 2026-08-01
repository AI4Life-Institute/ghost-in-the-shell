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
import time
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
        "--outstanding",
        action="store_true",
        help=(
            "Instead of scanning: list the drift the watcher currently has on "
            "record and has not seen resolved, including drift it deliberately "
            "did not notify about"
        ),
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
    if getattr(args, "outstanding", False):
        raise SystemExit(_run_outstanding(as_json=args.as_json))
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


# ── outstanding ──────────────────────────────────────────────────────────


def _age(seconds: float) -> str:
    hours = seconds / 3600.0
    return f"{hours:.0f}h" if hours < 48 else f"{hours / 24:.1f}d"


def _run_outstanding(*, as_json: bool = False, state_dir: Path | None = None) -> int:
    """Print what the drift watcher has on record (Ghost task drftnt).

    Answers ghost#37's second question — "can an outstanding unresolved state
    be queried?" — and covers the two ways this could itself go quiet: drift
    the watcher chose not to notify about is listed anyway, and a watcher that
    has stopped scanning says so instead of showing an empty, reassuring list.
    """
    from .core import drift_watch as dw

    if state_dir is None:
        from .config import Settings

        state_dir = Settings().state_dir
    ledger = dw.outstanding(state_dir)
    now = time.time()

    if as_json:
        print(json.dumps(ledger.to_json(), indent=2, sort_keys=True))
        return 1 if any(i.suppressed_reason is None for i in ledger.incidents.values()) else 0

    if ledger.last_scan_at is None:
        print("ghost doctor --outstanding")
        print(
            "  ? the drift watcher has never run on this machine. That is "
            "'unknown', not a clean bill of health."
        )
        print(f"    (expected state at {state_dir / dw.STATE_FILENAME})")
        return 0

    print(
        f"ghost doctor --outstanding   (last scan {_age(now - ledger.last_scan_at)} ago, "
        f"{ledger.scan_count} scan(s))"
    )
    if ledger.is_stale(now, dw.DEFAULT_INTERVAL_S):
        print(
            "  ⚠ the watcher has not scanned recently — what follows may be "
            "stale, and new drift would not be here at all."
        )

    alerting = [i for i in ledger.incidents.values() if i.suppressed_reason is None]
    quiet = [i for i in ledger.incidents.values() if i.suppressed_reason is not None]

    if not ledger.incidents:
        print("  ✓ nothing outstanding")
    for inc in sorted(alerting, key=lambda i: i.first_seen):
        notified = (
            f"{inc.notify_count} notice(s), last {_age(now - inc.last_notified_at)} ago"
            if inc.last_notified_at
            else "not yet notified"
        )
        print(f"  ✗ {inc.code}  {inc.executable}")
        print(f"      {inc.detail}")
        print(f"      outstanding {_age(now - inc.first_seen)} · {notified}")
        if inc.last_notify_error:
            print(f"      ⚠ last send failed: {inc.last_notify_error} (will retry)")
    for inc in sorted(quiet, key=lambda i: i.first_seen):
        # Recorded but not pushed. Suppression hides the interruption, never
        # the fact — otherwise "we decided not to say" becomes "nobody knows".
        print(f"  · {inc.code}  {inc.executable}  (not notified: {inc.suppressed_reason})")
        print(f"      {inc.detail}")
        print(f"      outstanding {_age(now - inc.first_seen)}")

    for name, record in sorted(ledger.fetch.items()):
        # The FETCH_HEAD mtime this watcher overwrites, kept rather than lost.
        parts = []
        if record.last_watcher_fetch:
            parts.append(f"watcher fetched {_age(now - record.last_watcher_fetch)} ago")
        if record.last_foreign_fetch:
            parts.append(f"last non-watcher fetch {record.last_foreign_fetch}")
        if record.last_error:
            parts.append(f"last error: {record.last_error}")
        if parts:
            print(f"  fetch {name}: {'; '.join(parts)}")

    if ledger.recently_resolved:
        print("  recently resolved:")
        for inc in ledger.recently_resolved[-5:]:
            when = _age(now - inc.resolved_at) if inc.resolved_at else "?"
            print(f"    ✓ {inc.code}  {inc.executable}  ({when} ago)")

    return 1 if alerting else 0


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
