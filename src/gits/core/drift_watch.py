"""Speak up when a ghost deployment has drifted — without latching silent.

Ghost task drftnt / ghost#37
----------------------------
``ghost doctor`` (task whlive) made "which code is live" *answerable*. This
makes it *heard*: a periodic in-process scan that notices drift nobody asked
about. Two failure modes bracket the design, and both end in silence:

* **The latch.** "Report each drift once" inverts urgency — a drift still
  unresolved a week later matters *more* than on day one, and that is exactly
  when a naive latch has stopped speaking.
* **The noise.** An alert on "not on master" fires on every ordinary working
  branch, gets muted, and then does not ring when it should. Same silence,
  reached from the other side.

So the alert predicate is deliberately **not** ``Report.errors``. Doctor grades
``not-on-master`` / ``dirty-worktree`` as errors, which is right for a
diagnostic you *invoke* — it answers "is this exactly master?". It is wrong for
something that interrupts people, because that is the shape of somebody working.

What is alertable
-----------------
A deployment is **tended** when its checkout is dirty or on a non-main branch
*and* shows human activity within :attr:`DriftPolicy.tended_ttl_hours`. Tended
deployments are never pushed to. Past that TTL the tending claim expires and
becomes its own finding — otherwise "someone is working here" is a permanent
excuse and an abandoned dirty tree that the *hooks* run from is immune forever.

For everything else:

======================================  ==========================================
``config-key-missing``                  immediately — it is already broken today
``behind`` ≥ :attr:`behind_threshold`   immediately
``behind`` ≥ 1, seen for ``min_age``    on crossing that age
commits touching the hook surface       drops the age gate
``distance-unmeasured`` past its TTL    immediately — "cannot tell" is not "fine"
no deployments scanned at all           immediately — an empty scan passes silently
======================================  ==========================================

**Suppression hides the notification, never the record.** A tended deployment
that is 18 commits behind is still written to the ledger and still answers
``ghost doctor --outstanding``. Quiet is not the same as forgotten.

The three dedupe questions (ghost#37)
-------------------------------------
1. **Does the key include state?** Coarse state only. The key is
   ``(executable, finding code)`` — not the sha (that re-fires whenever
   *master* moves, the wrong axis) and not the commit count (re-fires on every
   merge). Severity *bands* live in the value, and crossing a band upward
   re-arms immediately: "1 behind" and "18 behind" are one incident, but the
   second is worse, and worsening speaks.
2. **Can the outstanding set be queried?** :class:`Ledger`, persisted to
   ``<state_dir>/drift_incidents.json`` and printed by
   ``ghost doctor --outstanding`` — including suppressed incidents, recently
   resolved ones, when the scanner last ran, and when each repo was last
   fetched (by us, and by anyone else).
3. **What re-arms?** Three things, each observable: a **capped** backoff
   ladder (6h → 24h → 72h, never longer, because week-old drift is more
   urgent); a **severity escalation**, which resets the ladder; and
   **resolution**, which emits one closing notice so you learn the channel is
   alive. A restart is deliberately *not* a re-arm — the ledger is on disk, so
   a bouncing bot neither replays everything nor forgets what it owes.

A send that fails does **not** advance ``last_notified_at``. Delivery failure
must not be indistinguishable from delivery.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import deployments as dep_mod

logger = logging.getLogger(__name__)

STATE_FILENAME = "drift_incidents.json"

#: Kept for the query so "was it fixed, or did the notifier die?" is answerable.
MAX_RESOLVED_KEPT = 20

#: How many dirty paths to stat when dating human activity on a checkout.
_ACTIVITY_SAMPLE = 64

HOUR = 3600.0


# ── policy ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DriftPolicy:
    """Thresholds. Constants rather than config keys, on purpose.

    ``~/.gits/config.env`` is validated with ``extra='forbid'``: every key
    added here is a key that must also be declared in
    :class:`gits.config.Settings` forever, and an undeclared one takes down
    every ``Settings()`` in the bot, the hooks and the CLI (ghost#18). Only
    the two knobs an operator actually turns — on/off and cadence — are
    settings; the judgment lives here where it can be tested.
    """

    behind_threshold: int = 5
    min_age_hours: float = 24.0
    tended_ttl_hours: float = 24.0
    unmeasured_ttl_hours: float = 6.0
    #: Never longer than the last rung: drift outstanding for a week must not
    #: decay into an annual reminder.
    renotify_ladder_hours: tuple[float, ...] = (6.0, 24.0, 72.0)
    main_branches: tuple[str, ...] = ("master", "main")
    #: Roles expected to track master on their own. A hook running from an
    #: editable checkout is somebody's working copy; a CLI snapshot is not.
    following_roles: tuple[str, ...] = ("cli", "bot", "hook")

    def band_for(self, behind: int) -> int:
        """Coarse severity. Crossing a boundary upward re-arms the incident."""
        if behind >= 20:
            return 3
        if behind >= self.behind_threshold:
            return 2
        if behind >= 1:
            return 1
        return 0


# ── what a scan saw ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Observation:
    """One thing this scan noticed about one deployment."""

    key: str
    code: str
    executable: str
    roles: tuple[str, ...] = ()
    band: int = 1
    magnitude: int | None = None
    detail: str = ""
    #: None when it should be pushed; otherwise why it is record-only.
    suppressed_reason: str | None = None
    #: Set when the incident should bypass the age gate. Named for what it
    #: measures: commits touching the hook surface. Not "security fixes".
    hook_surface_commits: int | None = None
    first_seen_gate_hours: float = 0.0

    @property
    def alertable(self) -> bool:
        return self.suppressed_reason is None


def make_key(executable: str, code: str) -> str:
    """Dedupe identity: the deployment and what is wrong with it.

    Excludes the sha and the commit count on purpose — see module docstring.
    """
    return f"{executable}::{code}"


# ── the ledger ───────────────────────────────────────────────────────────


@dataclass
class Incident:
    """An outstanding (or recently resolved) drift, as persisted."""

    key: str
    code: str
    executable: str
    roles: tuple[str, ...] = ()
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_notified_at: float | None = None
    notify_count: int = 0
    ladder_index: int = 0
    band: int = 0
    peak_band: int = 0
    magnitude: int | None = None
    peak_magnitude: int | None = None
    detail: str = ""
    suppressed_reason: str | None = None
    last_notify_error: str | None = None
    resolved_at: float | None = None

    def outstanding_hours(self, now: float) -> float:
        return max(0.0, (now - self.first_seen) / HOUR)


@dataclass
class FetchRecord:
    """Where the ``FETCH_HEAD`` mtime signal went after we started fetching.

    Dating a deployment's last pull by ``.git/FETCH_HEAD`` stops working once
    something fetches on a cadence. Rather than destroying that evidence, the
    watcher carries it: ``last_foreign_fetch`` is the newest ``FETCH_HEAD``
    mtime we ever saw that was not ours.
    """

    last_watcher_fetch: float | None = None
    last_foreign_fetch: str | None = None
    last_error: str | None = None


@dataclass
class Ledger:
    """Everything the watcher remembers between scans."""

    incidents: dict[str, Incident] = field(default_factory=dict)
    recently_resolved: list[Incident] = field(default_factory=list)
    last_scan_at: float | None = None
    scan_count: int = 0
    fetch: dict[str, FetchRecord] = field(default_factory=dict)

    # -- persistence ------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "incidents": {k: asdict(v) for k, v in self.incidents.items()},
            "recently_resolved": [asdict(v) for v in self.recently_resolved],
            "last_scan_at": self.last_scan_at,
            "scan_count": self.scan_count,
            "fetch": {k: asdict(v) for k, v in self.fetch.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Ledger:
        def incident(d: dict[str, Any]) -> Incident:
            known = {f: d[f] for f in Incident.__dataclass_fields__ if f in d}
            known["roles"] = tuple(known.get("roles") or ())
            return Incident(**known)

        def fetch_record(d: dict[str, Any]) -> FetchRecord:
            known = {f: d[f] for f in FetchRecord.__dataclass_fields__ if f in d}
            return FetchRecord(**known)

        return cls(
            incidents={k: incident(v) for k, v in (data.get("incidents") or {}).items()},
            recently_resolved=[incident(v) for v in (data.get("recently_resolved") or [])],
            last_scan_at=data.get("last_scan_at"),
            scan_count=int(data.get("scan_count") or 0),
            fetch={k: fetch_record(v) for k, v in (data.get("fetch") or {}).items()},
        )

    @classmethod
    def load(cls, path: Path) -> Ledger:
        try:
            return cls.from_json(json.loads(Path(path).read_text()))
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path = Path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n")
            tmp.replace(path)
        except OSError as exc:  # a query we cannot persist is still better than a crash
            logger.warning("drift_watch: cannot persist ledger to %s: %s", path, exc)

    # -- delivery bookkeeping ---------------------------------------------

    def mark_notified(self, key: str, now: float) -> None:
        """Record a *delivered* notification and advance the backoff ladder."""
        inc = self.incidents.get(key)
        if inc is None:
            return
        inc.last_notified_at = now
        inc.notify_count += 1
        inc.ladder_index += 1
        inc.last_notify_error = None

    def mark_notify_error(self, key: str, error: str) -> None:
        """Record a *failed* send. ``last_notified_at`` deliberately unmoved.

        Advancing it here would make an undelivered notice indistinguishable
        from a delivered one, and the drift would go quiet for a whole rung.
        """
        inc = self.incidents.get(key)
        if inc is None:
            return
        inc.last_notify_error = error

    def is_stale(self, now: float, interval_s: float) -> bool:
        """True when the scanner itself has not run recently enough."""
        if self.last_scan_at is None:
            return True
        return (now - self.last_scan_at) > 2 * interval_s


# ── observing ────────────────────────────────────────────────────────────


def worktree_activity_at(state: dep_mod.WorktreeState) -> float | None:
    """When a human last touched this checkout, as an epoch time.

    Two signals, both immune to being scanned: the HEAD commit's date, and the
    newest mtime among the dirty paths themselves. Index mtime is deliberately
    *not* used — reading a repo's status can rewrite the index, so a scanner
    keyed on it would keep marking every tree it looked at as freshly active
    and suppress itself into silence.
    """
    stamps: list[float] = []
    rc, out = dep_mod._git_line(["log", "-1", "--format=%ct", "HEAD"], state.path)
    if rc == 0 and out.strip().isdigit():
        stamps.append(float(out.strip()))
    for name in (*state.modified, *state.untracked)[:_ACTIVITY_SAMPLE]:
        try:
            stamps.append((state.path / name).stat().st_mtime)
        except OSError:
            continue
    return max(stamps) if stamps else None


def _tended_reason(
    dep: dep_mod.Deployment, *, now: float, policy: DriftPolicy
) -> tuple[str | None, float | None]:
    """``(reason, idle_hours)`` — reason is None when nobody is on this tree."""
    wt = dep.worktree
    if wt is None:
        return None, None
    looks_worked_on = wt.dirty or (wt.branch not in policy.main_branches)
    if not looks_worked_on:
        return None, None
    activity = worktree_activity_at(wt)
    if activity is None:
        return None, None
    idle_hours = max(0.0, (now - activity) / HOUR)
    if idle_hours > policy.tended_ttl_hours:
        return None, idle_hours
    where = f"branch {wt.branch!r}" if wt.branch else "detached HEAD"
    dirt = f"{len(wt.modified)} modified, {len(wt.untracked)} untracked"
    return (
        f"someone is working here — {where}, {dirt}, last touched "
        f"{idle_hours:.1f}h ago",
        idle_hours,
    )


def classify(
    report: dep_mod.Report,
    *,
    now: float,
    policy: DriftPolicy | None = None,
) -> list[Observation]:
    """Turn a doctor report into the things worth remembering.

    Pure apart from stat-ing the checkouts named in the report; every decision
    is a function of ``(report, now, policy)``, which is what lets the latch
    behaviour be tested against an injected clock instead of a sleep.
    """
    policy = policy or DriftPolicy()
    out: list[Observation] = []

    relevant = [
        d for d in report.deployments if any(r in policy.following_roles for r in d.roles)
    ]
    if not relevant:
        # A scan that found nothing passes silently, which is the same vacuum
        # as a fetch failure reading as 0-behind. Say it out loud.
        return [
            Observation(
                key=make_key("-", "no-deployments-scanned"),
                code="no-deployments-scanned",
                executable="-",
                band=1,
                detail=(
                    "no ghost deployment was found to check — "
                    f"{len(report.deployments)} discovered, none with a "
                    f"{'/'.join(policy.following_roles)} role. This is not a clean "
                    "machine, it is a scan that measured nothing."
                ),
            )
        ]

    for dep in relevant:
        exe = str(dep.executable)
        roles = tuple(dep.roles)
        reason, idle_hours = _tended_reason(dep, now=now, policy=policy)
        codes = {f.code for f in dep.findings}

        if dep.config is not None and dep.config.status == "missing":
            missing = ", ".join(dep.config.missing)
            out.append(
                Observation(
                    key=make_key(exe, "config-key-missing"),
                    code="config-key-missing",
                    executable=exe,
                    roles=roles,
                    band=2,
                    magnitude=len(dep.config.missing),
                    detail=(
                        f"{exe} does not declare {missing} — a key already present "
                        f"in {report.config_env}. Settings() raises there, so this "
                        "deployment is broken now, not drifting toward broken."
                    ),
                )
            )

        dist = dep.distance
        behind = dist.behind if dist is not None else None
        if "distance-unmeasured" in codes:
            out.append(
                Observation(
                    key=make_key(exe, "distance-unmeasured"),
                    code="distance-unmeasured",
                    executable=exe,
                    roles=roles,
                    band=1,
                    detail=(
                        f"{exe}: cannot measure distance to {report.compare_ref} — "
                        f"fetch failed ({report.fetch.error if report.fetch else '?'}). "
                        "Reporting nothing here would look identical to reporting "
                        "'up to date'."
                    ),
                    first_seen_gate_hours=policy.unmeasured_ttl_hours,
                )
            )
        elif behind:
            hook_commits = dep.hook_surface_commits
            band = policy.band_for(behind)
            gate = policy.min_age_hours
            note = ""
            if hook_commits:
                # Escalation only, and named for what it measures.
                gate = 0.0
                band = max(band, 2)
                note = (
                    f" {hook_commits} of them touch the hook surface "
                    f"({', '.join(dep_mod.HOOK_SURFACE_PREFIXES)}) — the code paths "
                    "that refuse operations"
                )
            if behind >= policy.behind_threshold:
                gate = 0.0
            out.append(
                Observation(
                    key=make_key(exe, "behind-master"),
                    code="behind-master",
                    executable=exe,
                    roles=roles,
                    band=band,
                    magnitude=behind,
                    hook_surface_commits=hook_commits,
                    detail=(
                        f"{exe} ({'/'.join(roles)}) is {behind} commit(s) behind "
                        f"{report.compare_ref}.{note}"
                    ),
                    suppressed_reason=reason,
                    first_seen_gate_hours=gate,
                )
            )

        if "local-requirement" in codes:
            out.append(
                Observation(
                    key=make_key(exe, "local-requirement"),
                    code="local-requirement",
                    executable=exe,
                    roles=roles,
                    band=1,
                    detail=(
                        f"{exe}: its uv receipt requires {dep.receipt_requirement} — "
                        "a reinstall would pack whatever that working tree holds "
                        "at the time."
                    ),
                    suppressed_reason=reason,
                )
            )

        if reason is None and idle_hours is not None:
            # Was tended once; the claim has expired. Escalate rather than
            # letting "someone is working here" become permanent immunity.
            wt = dep.worktree
            assert wt is not None
            out.append(
                Observation(
                    key=make_key(exe, "stale-tended"),
                    code="stale-tended",
                    executable=exe,
                    roles=roles,
                    band=1,
                    magnitude=int(idle_hours),
                    detail=(
                        f"{exe} ({'/'.join(roles)}) runs from {wt.path}, which has been "
                        f"dirty or off master and untouched for {idle_hours:.0f}h "
                        f"({len(wt.modified)} modified, {len(wt.untracked)} untracked). "
                        "Nobody appears to be working on it any more."
                    ),
                )
            )

    return out


# ── reconciling ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Notice:
    """One thing to say. The watcher sends it; the ledger is told afterwards."""

    key: str
    kind: str  # "new" | "escalated" | "reminder" | "resolved"
    text: str


def _due_after(policy: DriftPolicy, ladder_index: int) -> float:
    """The gap owed *after* ``ladder_index`` notices: 1st → 6h, 2nd → 24h, …

    ``ladder_index`` counts notices sent, so the first rung is at index 1;
    reading the ladder straight would skip 6h entirely and make the first
    reminder arrive a day late.
    """
    ladder = policy.renotify_ladder_hours or (24.0,)
    return ladder[min(max(ladder_index - 1, 0), len(ladder) - 1)] * HOUR


def _describe(inc: Incident, kind: str, *, now: float, policy: DriftPolicy) -> str:
    head = {
        "new": "⚠️ deployment drift",
        "escalated": "⚠️ deployment drift worsened",
        "reminder": "⚠️ deployment drift still outstanding",
        "resolved": "✅ deployment drift resolved",
    }[kind]
    lines = [f"{head} — {inc.code}", inc.detail]
    if kind == "resolved":
        lines.append(
            f"outstanding {inc.outstanding_hours(now):.0f}h, "
            f"{inc.notify_count} notice(s) sent."
        )
        return "\n".join(lines)
    if kind != "new":
        # Repetition is only pressure if it says how long it has been going on.
        lines.append(
            f"outstanding {inc.outstanding_hours(now):.0f}h; "
            f"this is notice #{inc.notify_count + 1}."
        )
    if kind == "escalated" and inc.peak_magnitude is not None:
        lines.append(f"worst seen: {inc.peak_magnitude}.")
    lines.append("`ghost doctor --outstanding` lists everything still unresolved.")
    return "\n".join(lines)


def reconcile(
    ledger: Ledger,
    observations: Sequence[Observation],
    *,
    now: float,
    policy: DriftPolicy | None = None,
) -> tuple[Ledger, list[Notice]]:
    """Fold this scan into the ledger and say what is due.

    Returns notices *to attempt*; it does not mark anything as notified. The
    caller does that only on a successful send, so a transport failure retries
    on the next scan instead of being swallowed.
    """
    policy = policy or DriftPolicy()
    notices: list[Notice] = []
    seen: set[str] = set()

    for obs in observations:
        seen.add(obs.key)
        inc = ledger.incidents.get(obs.key)
        if inc is None:
            inc = Incident(
                key=obs.key,
                code=obs.code,
                executable=obs.executable,
                roles=obs.roles,
                first_seen=now,
                peak_band=obs.band,
                peak_magnitude=obs.magnitude,
            )
            ledger.incidents[obs.key] = inc
            escalated = False
        else:
            # A previously suppressed incident that becomes alertable counts as
            # an escalation: the tending claim expiring is news.
            escalated = obs.band > inc.peak_band or (
                inc.suppressed_reason is not None and obs.suppressed_reason is None
            )

        inc.last_seen = now
        inc.band = obs.band
        inc.magnitude = obs.magnitude
        inc.detail = obs.detail
        inc.roles = obs.roles or inc.roles
        inc.suppressed_reason = obs.suppressed_reason
        inc.resolved_at = None
        inc.peak_band = max(inc.peak_band, obs.band)
        if obs.magnitude is not None:
            inc.peak_magnitude = max(inc.peak_magnitude or 0, obs.magnitude)

        if obs.suppressed_reason is not None:
            continue  # recorded, queryable, not pushed

        age_hours = (now - inc.first_seen) / HOUR
        if age_hours < obs.first_seen_gate_hours:
            continue  # real, recorded, but too young to interrupt anyone over

        if escalated and inc.last_notified_at is not None:
            inc.ladder_index = 0
            kind = "escalated"
        elif inc.last_notified_at is None:
            kind = "new"
        elif now - inc.last_notified_at >= _due_after(policy, inc.ladder_index):
            kind = "reminder"
        else:
            continue
        notices.append(Notice(obs.key, kind, _describe(inc, kind, now=now, policy=policy)))

    for key in [k for k in ledger.incidents if k not in seen]:
        inc = ledger.incidents.pop(key)
        inc.resolved_at = now
        ledger.recently_resolved.append(inc)
        del ledger.recently_resolved[:-MAX_RESOLVED_KEPT]
        if inc.notify_count:
            # Only close what we opened; announcing the end of something never
            # announced is noise. Closing what we *did* announce is what makes
            # the re-arm observable instead of another silent path.
            notices.append(
                Notice(key, "resolved", _describe(inc, "resolved", now=now, policy=policy))
            )

    ledger.last_scan_at = now
    ledger.scan_count += 1
    return ledger, notices


# ── the scan ─────────────────────────────────────────────────────────────


def scan(
    *,
    now: float,
    policy: DriftPolicy | None = None,
    ledger: Ledger | None = None,
    collect: Callable[..., dep_mod.Report] = dep_mod.collect_report,
    **collect_kwargs: Any,
) -> tuple[dep_mod.Report, list[Observation]]:
    """Fetch, collect a report, annotate it, and classify.

    The fetch is ours (``--no-tags``, no prune, no force) because a watcher
    that never fetches measures 0-behind forever. Its outcome is recorded so a
    failure surfaces as *unmeasured* rather than as *clean*.
    """
    policy = policy or DriftPolicy()
    report = collect(fetch=True, **collect_kwargs)

    if ledger is not None and report.compare_repo is not None:
        record = ledger.fetch.setdefault(str(report.compare_repo), FetchRecord())
        outcome = report.fetch
        if outcome is not None:
            # Preserve the forensic signal our own fetch is about to erase: a
            # FETCH_HEAD we did not write is somebody's pull, and dating those
            # is what the mtime used to be for.
            if outcome.previous_fetch_at and record.last_watcher_fetch is None:
                record.last_foreign_fetch = outcome.previous_fetch_at
            elif outcome.previous_fetch_at and record.last_watcher_fetch is not None:
                prev = outcome.previous_fetch_at
                if _iso_to_epoch(prev) is not None and (
                    _iso_to_epoch(prev) or 0
                ) > record.last_watcher_fetch + 1:
                    record.last_foreign_fetch = prev
            record.last_error = outcome.error
            if outcome.ok:
                record.last_watcher_fetch = now

    for dep in report.deployments:
        dep.hook_surface_commits = dep_mod.count_commits_touching(
            report.compare_repo, dep.sha, report.compare_ref
        )

    return report, classify(report, now=now, policy=policy)


def _iso_to_epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


# ── the loop ─────────────────────────────────────────────────────────────

NotifyFn = Callable[[str], Awaitable[None]]

#: Default cadence. Hourly is far below the ladder's fastest rung, so the scan
#: rate never sets the notification rate.
DEFAULT_INTERVAL_S = 3600.0

#: Let the bot finish coming up before spending a fetch and N subprocess probes.
START_DELAY_S = 300.0


class DriftWatcher:
    """The in-process periodic scan. No new daemon, per ghost#37.

    Shaped like :class:`gits.core.token_refresh.TokenRefreshScheduler`: an
    asyncio task, state on disk so restarts neither replay nor forget, and the
    blocking git/subprocess work pushed to a thread.

    Not reachable from the guard. The guard runs on every tool call and must
    never gain a notification path, a network call, or an opinion here.
    """

    def __init__(
        self,
        state_dir: Path,
        notify: NotifyFn,
        *,
        policy: DriftPolicy | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        start_delay_s: float = START_DELAY_S,
        collect_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._state_path = Path(state_dir) / STATE_FILENAME
        self._notify = notify
        self._policy = policy or DriftPolicy()
        self._interval_s = interval_s
        self._start_delay_s = start_delay_s
        self._collect_kwargs = collect_kwargs or {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Spawn the loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="drift-watch")
        logger.info("DriftWatcher started (interval=%.0fs)", self._interval_s)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def run_once(self, *, now: float | None = None) -> list[Notice]:
        """One scan → reconcile → send cycle. Returns the notices attempted."""
        now = time.time() if now is None else now
        ledger = Ledger.load(self._state_path)
        try:
            _, observations = await asyncio.to_thread(
                scan,
                now=now,
                policy=self._policy,
                ledger=ledger,
                **self._collect_kwargs,
            )
        except Exception:
            logger.exception("drift_watch: scan failed")
            ledger.save(self._state_path)
            return []

        ledger, notices = reconcile(ledger, observations, now=now, policy=self._policy)
        # Persist before sending: if the send crashes the process, the incident
        # is still on record and still queryable.
        ledger.save(self._state_path)

        for notice in notices:
            try:
                await self._notify(notice.text)
            except Exception as exc:
                logger.warning("drift_watch: notification failed: %s", exc)
                ledger.mark_notify_error(notice.key, str(exc))
                continue
            if notice.kind != "resolved":
                ledger.mark_notified(notice.key, now)
        ledger.save(self._state_path)
        return notices

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(self._start_delay_s)
            while True:
                try:
                    await self.run_once()
                except Exception:
                    # A watcher that dies is a silent watcher.
                    logger.exception("drift_watch: cycle failed")
                await asyncio.sleep(self._interval_s)
        except asyncio.CancelledError:
            raise


# ── query ────────────────────────────────────────────────────────────────


def outstanding(state_dir: Path) -> Ledger:
    """The ledger as ``ghost doctor --outstanding`` reads it."""
    return Ledger.load(Path(state_dir) / STATE_FILENAME)


__all__ = [
    "DEFAULT_INTERVAL_S",
    "DriftPolicy",
    "DriftWatcher",
    "FetchRecord",
    "Incident",
    "Ledger",
    "Notice",
    "Observation",
    "STATE_FILENAME",
    "classify",
    "make_key",
    "outstanding",
    "reconcile",
    "scan",
]
