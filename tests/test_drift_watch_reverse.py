"""Reverse validation for the drift watcher (Ghost task drftnt).

Each test here **removes one judgement** and asserts the corresponding
guarantee breaks. Without this, a suppression rule that never executed and a
suppression rule that works are indistinguishable — every test passes either
way, and the feature ships with a dead branch.

The task page asks for this explicitly on the anti-noise predicate ("先证它在
没有该判据时会误报"). The same reasoning applies to every other place where the
*absence* of a message is the assertion, so all four are covered:

* tending suppression        → without it, ordinary development alerts
* the re-arm ladder          → without it, persistent drift goes permanently quiet
* the fetch-failure guard    → without it, a failed fetch grades as clean
* not advancing on failure   → without it, an undelivered notice is swallowed
"""

from __future__ import annotations

import time

import pytest

from gits.core import deployments as dm
from gits.core import drift_watch as dw

from .test_drift_watch import (
    HOUR,
    T0,
    FlakyNotifier,
    by_code,
    deployment,
    kinds,
    report_with,
    watcher,
    working_tree,
)


def test_without_the_tending_predicate_ordinary_development_alerts(tmp_path, monkeypatch):
    """The anti-noise proof: the same fixture misfires once the check is gone."""
    now = time.time()
    wt = working_tree(tmp_path, age_hours=0.5, now=now)
    report = report_with(deployment(behind=18, worktree=wt))

    # Baseline (asserted in test_drift_watch too): silent.
    assert dw.reconcile(dw.Ledger(), dw.classify(report, now=now), now=now)[1] == []

    monkeypatch.setattr(dw, "_tended_reason", lambda dep, *, now, policy: (None, None))
    _, notices = dw.reconcile(dw.Ledger(), dw.classify(report, now=now), now=now)
    assert kinds(notices) == ["new"]  # ← the noise this feature would have shipped


def test_without_the_ladder_persistent_drift_goes_permanently_quiet(monkeypatch):
    """The anti-latch proof: re-arming is what produces the later notices."""
    monkeypatch.setattr(dw, "_due_after", lambda policy, index: float("inf"))

    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)

    # A week later, unchanged and unresolved — the latch has stopped speaking.
    for hours in (7, 32, 110, 190):
        later = T0 + hours * HOUR
        ledger, notices = dw.reconcile(
            ledger,
            dw.classify(report_with(deployment(behind=18)), now=later),
            now=later,
        )
        assert notices == []


def test_without_the_fetch_guard_a_failed_fetch_grades_as_clean(monkeypatch):
    """The vacuum proof: 0-behind off stale refs is indistinguishable from health."""
    monkeypatch.setattr(dm, "fetch_failed", lambda report: False)

    dep = dm.Deployment(executable=dm.Path("/opt/ghost/bin/ghost"), roles=["cli"])
    dep.distance = dm.Distance(ahead=0, behind=0)
    report = dm.Report(fetch=dm.FetchOutcome(attempted=True, ok=False, error="no network"))
    dm._grade(dep, report, probe_config=False, probe_timeout=1)

    assert dep.findings == []          # ← a network blip reading as a clean machine
    assert report.verdict == "clean"


def test_without_the_empty_scan_finding_a_scan_of_nothing_is_silent():
    """A scan that measured nothing must not be able to pass quietly."""
    empty = report_with()
    assert by_code(dw.classify(empty, now=T0))["no-deployments-scanned"].alertable

    # Drop that observation and the whole cycle is green with zero coverage.
    _, notices = dw.reconcile(dw.Ledger(), [], now=T0)
    assert notices == []


@pytest.mark.asyncio
async def test_without_the_failure_rule_an_undelivered_notice_is_swallowed(
    tmp_path, monkeypatch
):
    """Proof that *not* advancing last_notified_at is what makes the retry happen."""

    def advance_anyway(self, key, error):  # what a careless implementation does
        inc = self.incidents.get(key)
        if inc is not None:
            inc.last_notified_at = T0
            inc.last_notify_error = error

    monkeypatch.setattr(dw.Ledger, "mark_notify_error", advance_anyway)

    notifier = FlakyNotifier(fail_times=1)
    w = watcher(tmp_path, notifier, report_with(deployment(behind=18)))
    await w.run_once(now=T0)
    await w.run_once(now=T0 + 60)

    # The notice was never delivered, and the next scan says nothing: the drift
    # is now silent until the first ladder rung elapses.
    assert notifier.sent == []
