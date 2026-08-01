"""Drift notification: it must ring, and it must not latch (Ghost task drftnt).

The interesting assertions are about *silence* — the two ways this feature
ships broken are "never speaks again" and "speaks so often it gets muted".
Both are tested against an injected clock rather than sleeps, which is why
``classify`` and ``reconcile`` are pure functions of ``(state, now, policy)``.

Three of these tests are **reverse-validated** in
``test_reverse_validation.py``: removing the judgement they cover makes them
fail. A test for a branch that never ran is not a test.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from gits.core import deployments as dm
from gits.core import drift_watch as dw

from .conftest import clone_at, editable_direct_url, git_direct_url, make_env, run_git

HOUR = 3600.0
T0 = 1_750_000_000.0


# ── helpers ──────────────────────────────────────────────────────────────


def report_with(
    *deployments: dm.Deployment,
    compare_ref: str = "origin/master",
    fetch: dm.FetchOutcome | None = None,
) -> dm.Report:
    r = dm.Report(compare_ref=compare_ref, config_env=Path("/fake/config.env"))
    r.deployments = list(deployments)
    r.fetch = fetch
    return r


def deployment(
    exe: str = "/opt/ghost/bin/ghost",
    *,
    roles: tuple[str, ...] = ("cli",),
    behind: int | None = None,
    worktree: dm.WorktreeState | None = None,
    hook_commits: int | None = None,
    findings: tuple[dm.Finding, ...] = (),
    config: dm.ConfigCompat | None = None,
) -> dm.Deployment:
    dep = dm.Deployment(executable=Path(exe), roles=list(roles))
    dep.distance = dm.Distance(ahead=0, behind=behind)
    dep.worktree = worktree
    dep.hook_surface_commits = hook_commits
    dep.findings = list(findings)
    dep.config = config
    return dep


def by_code(observations) -> dict[str, dw.Observation]:
    return {o.code: o for o in observations}


def kinds(notices) -> list[str]:
    return [n.kind for n in notices]


# ── red side: it rings, and it carries the magnitude ─────────────────────


def test_behind_master_notifies_and_the_message_carries_the_magnitude():
    obs = dw.classify(report_with(deployment(behind=18)), now=T0)
    assert by_code(obs)["behind-master"].magnitude == 18

    _, notices = dw.reconcile(dw.Ledger(), obs, now=T0)
    assert kinds(notices) == ["new"]
    # Not merely "a message was sent": the number has to survive into the text,
    # because "something drifted" is not actionable and "18 behind" is.
    assert "18 commit(s) behind" in notices[0].text
    assert "origin/master" in notices[0].text


def test_below_threshold_waits_for_the_age_gate_then_fires():
    """1 behind for twenty minutes is a merge window; at 24h it is drift."""
    ledger = dw.Ledger()
    obs = dw.classify(report_with(deployment(behind=1)), now=T0)
    ledger, notices = dw.reconcile(ledger, obs, now=T0)
    assert notices == []
    # ...but it is on record from the first sighting, not invented later.
    assert ledger.incidents[dw.make_key("/opt/ghost/bin/ghost", "behind-master")]

    later = T0 + 25 * HOUR
    ledger, notices = dw.reconcile(
        ledger, dw.classify(report_with(deployment(behind=1)), now=later), now=later
    )
    assert kinds(notices) == ["new"]


def test_hook_surface_commits_drop_the_age_gate():
    obs = dw.classify(
        report_with(deployment(behind=1, hook_commits=1)), now=T0
    )
    _, notices = dw.reconcile(dw.Ledger(), obs, now=T0)
    assert kinds(notices) == ["new"]
    text = notices[0].text
    assert "touch the hook surface" in text
    assert "src/gits/hooks/" in text
    # Named for what it counts. Claiming "security fix" is a claim this
    # mechanism cannot support, and a claim in wording is believed every time
    # it is read.
    assert "security" not in text.lower()


def test_config_key_missing_fires_immediately_with_no_gate():
    dep = deployment(config=dm.ConfigCompat(status="missing", missing=("ghost_new_key",)))
    _, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(dep), now=T0), now=T0
    )
    assert kinds(notices) == ["new"]
    assert "ghost_new_key" in notices[0].text
    assert "broken now" in notices[0].text


# ── anti-noise: somebody is working there ────────────────────────────────


def working_tree(tmp_path: Path, *, age_hours: float, now: float) -> dm.WorktreeState:
    """A checkout on a feature branch with uncommitted work of a given age.

    Both signals are aged together — the commit *and* the dirty file — because
    activity is their maximum: a tree whose HEAD was committed ten minutes ago
    is being worked in no matter how old its stray files are.
    """
    root = tmp_path / "checkout"
    root.mkdir()
    stamp = now - age_hours * HOUR
    run_git(["init", "-b", "feature/x", "."], root)
    (root / "seed.txt").write_text("x")
    run_git(["add", "-A"], root)
    run_git(
        ["commit", "-m", "seed"],
        root,
        extra_env={"GIT_COMMITTER_DATE": f"{int(stamp)} +0000"},
    )
    dirty = root / "wip.py"
    dirty.write_text("half a thought\n")
    os.utime(dirty, (stamp, stamp))
    return dm.WorktreeState(
        path=root,
        head_sha="a" * 40,
        branch="feature/x",
        dirty=True,
        modified=(),
        untracked=("wip.py",),
    )


def test_someone_working_on_the_tree_is_not_alerted(tmp_path):
    now = time.time()
    wt = working_tree(tmp_path, age_hours=0.5, now=now)
    obs = dw.classify(report_with(deployment(behind=18, worktree=wt)), now=now)
    behind = by_code(obs)["behind-master"]
    assert behind.suppressed_reason is not None
    assert "someone is working here" in behind.suppressed_reason

    _, notices = dw.reconcile(dw.Ledger(), obs, now=now)
    assert notices == []


def test_suppressed_drift_is_still_recorded_and_queryable(tmp_path):
    """Suppression hides the interruption, never the fact."""
    now = time.time()
    wt = working_tree(tmp_path, age_hours=0.5, now=now)
    ledger, notices = dw.reconcile(
        dw.Ledger(),
        dw.classify(report_with(deployment(behind=18, worktree=wt)), now=now),
        now=now,
    )
    assert notices == []
    inc = ledger.incidents[dw.make_key("/opt/ghost/bin/ghost", "behind-master")]
    assert inc.magnitude == 18
    assert inc.suppressed_reason is not None


def test_tending_expires_rather_than_granting_permanent_immunity(tmp_path):
    """A tree dirty and untouched for a week is abandoned, not tended."""
    now = time.time()
    wt = working_tree(tmp_path, age_hours=24 * 7, now=now)
    dep = deployment(behind=18, worktree=wt)
    obs = dw.classify(report_with(dep), now=now)
    codes = by_code(obs)
    assert codes["behind-master"].suppressed_reason is None
    assert "stale-tended" in codes
    _, notices = dw.reconcile(dw.Ledger(), obs, now=now)
    assert "new" in kinds(notices)


def test_suppression_lifting_counts_as_an_escalation(tmp_path):
    """When the tending claim expires, that is news — not a quiet unmuting."""
    now = time.time()
    tended = working_tree(tmp_path, age_hours=1, now=now)
    ledger, notices = dw.reconcile(
        dw.Ledger(),
        dw.classify(report_with(deployment(behind=18, worktree=tended)), now=now),
        now=now,
    )
    assert notices == []
    # Same drift, nobody has touched the tree since.
    later = now + 48 * HOUR
    ledger, notices = dw.reconcile(
        ledger,
        dw.classify(report_with(deployment(behind=18, worktree=tended)), now=later),
        now=later,
    )
    assert "new" in kinds(notices)


# ── anti permanent silence: the latch ────────────────────────────────────


def test_persistent_drift_speaks_again_on_every_ladder_rung():
    """The anti-latch test. Without it the feature ships broken as described."""
    ledger = dw.Ledger()
    policy = dw.DriftPolicy()
    key = dw.make_key("/opt/ghost/bin/ghost", "behind-master")

    def tick(now: float) -> list[dw.Notice]:
        nonlocal ledger
        ledger, notices = dw.reconcile(
            ledger,
            dw.classify(report_with(deployment(behind=18)), now=now),
            now=now,
            policy=policy,
        )
        for n in notices:
            ledger.mark_notified(n.key, now)
        return notices

    assert kinds(tick(T0)) == ["new"]
    # Nothing changed and no rung has elapsed: staying quiet here is correct.
    assert tick(T0 + 1 * HOUR) == []
    assert kinds(tick(T0 + 7 * HOUR)) == ["reminder"]      # rung 1: 6h
    assert tick(T0 + 20 * HOUR) == []
    assert kinds(tick(T0 + 32 * HOUR)) == ["reminder"]     # rung 2: 24h
    assert kinds(tick(T0 + 110 * HOUR)) == ["reminder"]    # rung 3: 72h
    # A week in it is still speaking, and the ladder never grows past 72h.
    assert kinds(tick(T0 + 190 * HOUR)) == ["reminder"]
    assert ledger.incidents[key].notify_count == 5


def test_reminders_say_how_long_it_has_been_outstanding():
    """Repetition is only pressure if it carries the duration."""
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)
    later = T0 + 100 * HOUR
    _, notices = dw.reconcile(
        ledger, dw.classify(report_with(deployment(behind=18)), now=later), now=later
    )
    assert "outstanding 100h" in notices[0].text
    assert "notice #2" in notices[0].text


def test_worsening_re_arms_immediately_and_resets_the_ladder():
    """'1 behind' and '18 behind' are one incident — but the second is worse."""
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=6)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)

    soon = T0 + 1 * HOUR  # well inside the first rung
    ledger, notices = dw.reconcile(
        ledger, dw.classify(report_with(deployment(behind=25)), now=soon), now=soon
    )
    assert kinds(notices) == ["escalated"]
    assert "25 commit(s) behind" in notices[0].text
    ledger.mark_notified(notices[0].key, soon)
    # Ladder reset: the next reminder is due after the *first* rung again.
    assert ledger.incidents[notices[0].key].ladder_index == 1


def test_master_moving_does_not_re_fire():
    """The dedupe key excludes the sha: re-firing on master's motion is the
    wrong axis, and it is what turns this into noise."""
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=6)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)
    soon = T0 + 1 * HOUR
    # master moved by one; same band, same incident.
    _, notices = dw.reconcile(
        ledger, dw.classify(report_with(deployment(behind=7)), now=soon), now=soon
    )
    assert notices == []


def test_resolution_closes_the_incident_and_says_so():
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)

    later = T0 + 2 * HOUR
    ledger, notices = dw.reconcile(ledger, [], now=later)
    assert kinds(notices) == ["resolved"]
    # Makes the re-arm observable rather than another silent path: you learn
    # the channel is alive and that the thing is closed.
    assert "resolved" in notices[0].text
    assert ledger.incidents == {}
    assert ledger.recently_resolved[-1].resolved_at == later


def test_a_new_drift_after_resolution_notifies_without_waiting_for_a_timer():
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.mark_notified(notices[0].key, T0)
    ledger, _ = dw.reconcile(ledger, [], now=T0 + 2 * HOUR)

    again = T0 + 3 * HOUR
    _, notices = dw.reconcile(
        ledger, dw.classify(report_with(deployment(behind=18)), now=again), now=again
    )
    assert kinds(notices) == ["new"]


def test_resolution_of_something_never_announced_is_not_announced():
    """Closing what was never opened is noise, and noise is how this dies."""
    ledger, notices = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=1)), now=T0), now=T0
    )
    assert notices == []  # age-gated
    _, notices = dw.reconcile(ledger, [], now=T0 + 2 * HOUR)
    assert notices == []


# ── vacuum: measuring nothing must not read as measuring health ──────────


def test_a_scan_that_found_no_deployments_is_itself_a_finding():
    obs = dw.classify(report_with(), now=T0)
    assert by_code(obs)["no-deployments-scanned"].alertable
    _, notices = dw.reconcile(dw.Ledger(), obs, now=T0)
    assert kinds(notices) == ["new"]
    assert "measured nothing" in notices[0].text


def test_fetch_failure_reads_as_unmeasured_not_as_zero_behind():
    """A network blip must not impersonate a healthy machine."""
    report = report_with(
        deployment(
            behind=0,
            findings=(dm.Finding("unknown", "distance-unmeasured", "fetch failed"),),
        ),
        fetch=dm.FetchOutcome(attempted=True, ok=False, error="Could not resolve host"),
    )
    obs = by_code(dw.classify(report, now=T0))
    assert "behind-master" not in obs
    unmeasured = obs["distance-unmeasured"]
    assert unmeasured.alertable
    assert "Could not resolve host" in unmeasured.detail

    # Not instantly, though — one flaky fetch is not an incident.
    ledger, notices = dw.reconcile(dw.Ledger(), [unmeasured], now=T0)
    assert notices == []
    later = T0 + 7 * HOUR
    _, notices = dw.reconcile(ledger, [unmeasured], now=later)
    assert kinds(notices) == ["new"]


def test_grading_marks_zero_behind_unmeasured_only_when_the_fetch_failed():
    """The rule lives in deployments._grade; both directions asserted."""
    dep = dm.Deployment(executable=Path("/opt/ghost/bin/ghost"), roles=["cli"])
    dep.distance = dm.Distance(ahead=0, behind=0)

    ok = dm.Report(fetch=dm.FetchOutcome(attempted=True, ok=True))
    dm._grade(dep, ok, probe_config=False, probe_timeout=1)
    assert dep.findings == []

    dep.findings = []
    failed = dm.Report(fetch=dm.FetchOutcome(attempted=True, ok=False, error="boom"))
    dm._grade(dep, failed, probe_config=False, probe_timeout=1)
    assert [f.code for f in dep.findings] == ["distance-unmeasured"]
    assert dep.findings[0].level == "unknown"


def test_real_drift_still_reports_even_when_the_fetch_failed():
    """A stale ref still proves a lower bound; don't lose real drift to it."""
    dep = dm.Deployment(executable=Path("/opt/ghost/bin/ghost"), roles=["cli"])
    dep.distance = dm.Distance(ahead=0, behind=4)
    report = dm.Report(fetch=dm.FetchOutcome(attempted=True, ok=False, error="boom"))
    dm._grade(dep, report, probe_config=False, probe_timeout=1)
    assert [f.code for f in dep.findings] == ["behind-master"]


# ── the fetch itself ─────────────────────────────────────────────────────


def test_fetch_origin_reports_failure_as_failure(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init", "-b", "master", "."], repo)
    run_git(["remote", "add", "origin", str(tmp_path / "does-not-exist")], repo)
    outcome = dm.fetch_origin(repo, timeout=30)
    assert outcome.attempted and not outcome.ok
    assert outcome.error


def test_fetch_origin_succeeds_and_carries_the_previous_fetch_head(upstream, tmp_path):
    origin, _ = upstream
    clone = clone_at(origin, tmp_path / "clone")
    first = dm.fetch_origin(clone, timeout=30)
    assert first.ok
    second = dm.fetch_origin(clone, timeout=30)
    assert second.ok
    # The FETCH_HEAD mtime our own fetch is about to overwrite comes back out,
    # so the "when did this deployment pull" signal is relocated, not erased.
    assert second.previous_fetch_at is not None


def test_watcher_records_a_pre_existing_fetch_as_somebody_elses(upstream, tmp_path):
    origin, shas = upstream
    clone = clone_at(origin, tmp_path / "clone")
    dm.fetch_origin(clone, timeout=30)  # stand in for a human's pull

    ledger = dw.Ledger()

    def collect(**kwargs):
        report = dm.Report(compare_repo=clone, compare_ref="origin/master")
        report.fetch = dm.fetch_origin(clone, timeout=30)
        return report

    dw.scan(now=T0, ledger=ledger, collect=collect)
    record = ledger.fetch[str(clone)]
    assert record.last_watcher_fetch == T0
    assert record.last_foreign_fetch is not None  # the clone's own fetch, kept


# ── the loop ─────────────────────────────────────────────────────────────


class FlakyNotifier:
    def __init__(self, fail_times: int = 0):
        self.sent: list[str] = []
        self.fail_times = fail_times

    async def __call__(self, text: str) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transport down")
        self.sent.append(text)


def watcher(tmp_path: Path, notify, report: dm.Report) -> dw.DriftWatcher:
    w = dw.DriftWatcher(state_dir=tmp_path, notify=notify, start_delay_s=0)
    w._collect_kwargs = {"collect": lambda **kw: report}
    return w


@pytest.mark.asyncio
async def test_run_once_sends_and_persists(tmp_path):
    notifier = FlakyNotifier()
    w = watcher(tmp_path, notifier, report_with(deployment(behind=18)))
    await w.run_once(now=T0)
    assert len(notifier.sent) == 1
    ledger = dw.outstanding(tmp_path)
    assert ledger.last_scan_at == T0
    key = dw.make_key("/opt/ghost/bin/ghost", "behind-master")
    assert ledger.incidents[key].last_notified_at == T0


@pytest.mark.asyncio
async def test_failed_send_leaves_the_incident_unnotified_and_retries(tmp_path):
    """A delivery failure must not be indistinguishable from a delivery."""
    notifier = FlakyNotifier(fail_times=1)
    w = watcher(tmp_path, notifier, report_with(deployment(behind=18)))
    await w.run_once(now=T0)

    assert notifier.sent == []
    ledger = dw.outstanding(tmp_path)
    key = dw.make_key("/opt/ghost/bin/ghost", "behind-master")
    inc = ledger.incidents[key]
    assert inc.last_notified_at is None      # not advanced
    assert inc.notify_count == 0
    assert "transport down" in inc.last_notify_error
    # ...and the loop survived to say it on the very next scan, not a rung later.
    await w.run_once(now=T0 + 60)
    assert len(notifier.sent) == 1
    assert dw.outstanding(tmp_path).incidents[key].last_notified_at == T0 + 60


@pytest.mark.asyncio
async def test_a_scan_that_raises_does_not_kill_the_ledger(tmp_path):
    def explode(**kwargs):
        raise RuntimeError("git is having a day")

    w = dw.DriftWatcher(state_dir=tmp_path, notify=FlakyNotifier(), start_delay_s=0)
    w._collect_kwargs = {"collect": explode}
    assert await w.run_once(now=T0) == []
    assert (tmp_path / dw.STATE_FILENAME).exists()


@pytest.mark.asyncio
async def test_restart_neither_replays_nor_forgets(tmp_path):
    """State is on disk: a bouncing bot must not re-announce everything."""
    notifier = FlakyNotifier()
    report = report_with(deployment(behind=18))
    await watcher(tmp_path, notifier, report).run_once(now=T0)
    assert len(notifier.sent) == 1

    fresh = watcher(tmp_path, notifier, report)  # "restart"
    await fresh.run_once(now=T0 + 60)
    assert len(notifier.sent) == 1  # not replayed
    key = dw.make_key("/opt/ghost/bin/ghost", "behind-master")
    assert dw.outstanding(tmp_path).incidents[key].first_seen == T0  # not forgotten


# ── persistence ──────────────────────────────────────────────────────────


def test_ledger_survives_a_round_trip(tmp_path):
    ledger, _ = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.fetch["/repo"] = dw.FetchRecord(last_watcher_fetch=T0, last_foreign_fetch="x")
    path = tmp_path / dw.STATE_FILENAME
    ledger.save(path)
    back = dw.Ledger.load(path)
    assert back.incidents.keys() == ledger.incidents.keys()
    assert back.last_scan_at == T0
    assert back.fetch["/repo"].last_foreign_fetch == "x"


def test_a_corrupt_ledger_reads_as_empty_rather_than_crashing(tmp_path):
    path = tmp_path / dw.STATE_FILENAME
    path.write_text("{ not json")
    assert dw.Ledger.load(path).incidents == {}


def test_stale_scanner_is_detectable():
    ledger = dw.Ledger()
    assert ledger.is_stale(T0, 3600)          # never ran
    ledger.last_scan_at = T0
    assert not ledger.is_stale(T0 + 100, 3600)
    assert ledger.is_stale(T0 + 3 * HOUR, 3600)


# ── end to end over real deployments ─────────────────────────────────────


def test_classify_over_a_real_behind_deployment(upstream, tmp_path):
    """Constructed, not observed: a real repo genuinely behind a real origin."""
    origin, shas = upstream
    clone = clone_at(origin, tmp_path / "clone")
    env = tmp_path / "env"
    exe = make_env(env, git_direct_url(shas[0]))

    report = dm.build_report(
        [dm.DeploymentRef(role="cli", label="PATH:ghost", executable=exe)],
        compare_repo=clone,
        probe_config=False,
    )
    for dep in report.deployments:
        dep.hook_surface_commits = dm.count_commits_touching(
            report.compare_repo, dep.sha, report.compare_ref
        )
    obs = by_code(dw.classify(report, now=T0))
    assert obs["behind-master"].magnitude == 2
    assert obs["behind-master"].alertable


def test_editable_checkout_being_worked_in_is_not_alerted(upstream, tmp_path):
    origin, shas = upstream
    clone = clone_at(origin, tmp_path / "clone")
    work = clone_at(origin, tmp_path / "work")
    run_git(["checkout", "-q", "-b", "task/something", shas[0]], work)
    (work / "src" / "gits" / "wip.py").write_text("in progress\n")
    env = tmp_path / "env"
    exe = make_env(env, editable_direct_url(work))

    report = dm.build_report(
        [dm.DeploymentRef(role="hook", label="settings (guard)", executable=exe)],
        compare_repo=clone,
        probe_config=False,
    )
    obs = by_code(dw.classify(report, now=time.time()))
    assert obs["behind-master"].suppressed_reason is not None
    # ...and doctor still calls it drift, which is the point of the split: the
    # diagnostic answers "is this master?", the notifier answers "interrupt?".
    assert report.verdict == "drift"


def test_scanning_does_not_dirty_the_tree_it_looks_at(upstream, tmp_path):
    """The scanner must be able to read a checkout without touching it."""
    origin, _ = upstream
    work = clone_at(origin, tmp_path / "work")
    index = work / ".git" / "index"
    before = index.stat().st_mtime_ns
    time.sleep(0.01)
    assert dm.inspect_worktree(work) is not None
    assert index.stat().st_mtime_ns == before


def test_count_commits_touching_counts_only_the_named_prefix(upstream, tmp_path):
    origin, shas = upstream
    clone = clone_at(origin, tmp_path / "clone")
    assert dm.count_commits_touching(clone, shas[0], "origin/master") == 0
    assert (
        dm.count_commits_touching(clone, shas[0], "origin/master", prefixes=("src/",)) == 2
    )


# ── the CLI query ────────────────────────────────────────────────────────


def test_outstanding_on_a_machine_that_never_scanned_says_unknown(tmp_path, capsys):
    from gits import cli_doctor

    rc = cli_doctor._run_outstanding(state_dir=tmp_path)
    out = capsys.readouterr().out
    assert rc == 0
    # Not "✓ nothing outstanding" — that would be the same lie as behind=0.
    assert "never run" in out
    assert "nothing outstanding" not in out


def test_outstanding_lists_alerting_and_suppressed_separately(tmp_path, capsys):
    from gits import cli_doctor

    now = time.time()
    wt = working_tree(tmp_path, age_hours=0.5, now=now)
    obs = dw.classify(
        report_with(
            deployment("/opt/ghost/bin/ghost", behind=18),
            deployment("/src/x/.venv/bin/gits", roles=("hook",), behind=9, worktree=wt),
        ),
        now=now,
    )
    ledger, notices = dw.reconcile(dw.Ledger(), obs, now=now)
    for n in notices:
        ledger.mark_notified(n.key, now)
    ledger.save(tmp_path / dw.STATE_FILENAME)

    rc = cli_doctor._run_outstanding(state_dir=tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "18 commit(s) behind" in out
    assert "9 commit(s) behind" in out          # suppressed, still listed
    assert "not notified: someone is working here" in out


def test_outstanding_json_is_machine_readable(tmp_path, capsys):
    from gits import cli_doctor

    ledger, _ = dw.reconcile(
        dw.Ledger(), dw.classify(report_with(deployment(behind=18)), now=T0), now=T0
    )
    ledger.save(tmp_path / dw.STATE_FILENAME)
    rc = cli_doctor._run_outstanding(as_json=True, state_dir=tmp_path)
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["last_scan_at"] == T0
    assert any(i["magnitude"] == 18 for i in data["incidents"].values())
