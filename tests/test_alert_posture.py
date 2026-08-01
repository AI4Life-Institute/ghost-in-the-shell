"""One failure posture, one channel resolution (task [[alrpos]] / ghost#42).

Two subsystems said the same thing — "the notification did not reach
anyone" — and meant opposite things by it. Drift notices retried; watchdog
alerts went permanently silent. These tests pin the settled answer.

**The posture**: an undelivered alert is never recorded as delivered. The
edge state is committed *after* a confirmed send, never before one. And it
is implemented by *not advancing state*, never by raising — a watchdog that
takes down its own loop is a worse failure than the silence it replaced. So
"doesn't crash" and "doesn't forget" are asserted together; either alone is
a regression dressed as a fix.

**The channel**: `1510821666492649503` is *not* an orphan constant — it is
the bound home channel of `vault-weiliu-ghost-efficiency`, whose charter the
watchdog belongs to. ghost#42's premise that two knobs serve one audience is
wrong, so the destination deliberately does **not** move (operator answer
Q1, 2026-08-01). What changes is only that falling back to it stops being
invisible: a "reasonable default" had turned an observable state
(is this configured?) into an unobservable one.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from gits.core import resource_watch as rw
from gits.core.engine import Engine
from gits.core.health import HealthMonitor
from gits.core.watchdog_config import (
    _DEFAULT_ALERT_CHANNEL,
    load_watchdog_config,
)
from gits.core.watchdog_state import LEVEL_CRITICAL, LEVEL_OK

ALERT_CHANNEL_ENV = "GITS_WATCHDOG_ALERT_CHANNEL"


def _cfg(env: dict | None = None):
    return load_watchdog_config(config_env_path=Path("/nonexistent"), env=env or {})


def _monitor(tmp_path, notify, cfg=None):
    return HealthMonitor(
        tmux=None,
        session_mgr=None,
        launcher=None,
        notify=notify,
        watchdog_config=cfg or _cfg(),
        watchdog_state_path=tmp_path / "watchdog_state.json",
    )


class _FakeAdapter:
    """Records every send; optionally fails the first ``fail_first`` calls."""

    def __init__(self, fail_first: int = 0):
        self.sent: list[tuple[str, str]] = []
        self._fail_first = fail_first

    async def send_message(self, channel_id, message):
        self.sent.append((channel_id, getattr(message, "text", str(message))))
        if len(self.sent) <= self._fail_first:
            raise RuntimeError("transport boom")


def _bare_engine(cfg, adapter):
    """An Engine with only what the alert path touches.

    Deliberately not a constructed Engine: ``Settings()`` reads the real
    ``~/.gits/config.env`` on a dev machine, which is how unrelated suites
    have been made to fail before now.
    """
    eng = Engine.__new__(Engine)
    eng._adapter = adapter
    eng.watchdog_config = cfg
    return eng


async def _until(predicate, *, timeout=5.0, what="", describe=None):
    """Await ``predicate()`` becoming true; assert with context on timeout."""
    waited = 0.0
    while waited < timeout:
        if predicate():
            return
        await asyncio.sleep(0.01)
        waited += 0.01
    observed = f" (observed: {describe()})" if describe else ""
    raise AssertionError(f"timed out waiting for {what}{observed}")


async def _run_resource_loop(mon, hot, *, until, timeout=5.0, what="", describe=None):
    """Drive the real ``_resource_watch_loop`` until ``until()`` or timeout."""
    mon._running = True
    with patch.object(HealthMonitor, "RESOURCE_WATCH_INTERVAL", 0.001), \
         patch.object(rw, "sample_resources", lambda cfg: hot):
        task = asyncio.create_task(mon._resource_watch_loop())
        try:
            await _until(
                until,
                timeout=timeout,
                what=what or "the watchdog loop to tick",
                describe=describe,
            )
            # Captured *before* teardown: the caller cannot ask the task
            # whether it was alive once we have cancelled it.
            return not task.done()
        finally:
            mon._running = False
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# ─────────────────────────────────────────────────────────────────────
# 1. Posture — a failed send must not consume the edge
# ─────────────────────────────────────────────────────────────────────


def test_failed_send_does_not_advance_edge_and_is_retried(tmp_path):
    """RED on master: ``reconcile()`` commits the level before any send.

    Drives the real loop rather than the pieces, because the defect lives in
    the seam between them: ``reconcile()`` advances ``WatchdogState`` and
    ``_safe_notify`` then discards the delivery outcome.
    """
    attempts: list[str] = []

    async def failing_notify(text):
        attempts.append(text)
        raise RuntimeError("transport boom")

    mon = _monitor(tmp_path, failing_notify)
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)

    async def run():
        await _run_resource_loop(
            mon,
            hot,
            until=lambda: len(attempts) >= 3,
            what="a failed alert to be retried on the next tick",
            describe=lambda: (
                f"{len(attempts)} send attempt(s) over many ticks, "
                f"edge state={mon._state().level('swap')!r} — the failed send "
                f"was recorded as delivered, so it is never said again"
            ),
        )

    asyncio.run(run())

    # Retried, not consumed: the same alert is attempted again and again.
    assert len(attempts) >= 3, attempts
    swap_attempts = [t for t in attempts if "swap" in t.lower()]
    assert len(swap_attempts) >= 3, attempts
    # And the ledger never claimed it was delivered.
    assert mon._state().level("swap") == LEVEL_OK


def test_edge_advances_once_the_send_actually_succeeds(tmp_path):
    """The retry stops the moment delivery is confirmed — and only then."""
    attempts: list[str] = []
    fail_until = 2

    async def flaky_notify(text):
        attempts.append(text)
        if len(attempts) <= fail_until:
            raise RuntimeError("transport boom")

    mon = _monitor(tmp_path, flaky_notify)
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)

    async def run():
        await _run_resource_loop(
            mon, hot, until=lambda: len(attempts) > fail_until
        )
        # Give the loop room to (wrongly) keep alerting after success.
        await asyncio.sleep(0.05)

    asyncio.run(run())

    assert mon._state().level("swap") == LEVEL_CRITICAL
    # Sustained condition post-delivery → edge de-dupe holds, no spam.
    assert len(attempts) == fail_until + 1, attempts


def test_watchdog_loop_survives_send_failure(tmp_path):
    """"Don't forget" must not be bought with "took the loop down".

    This has to stay green *simultaneously* with the retry test above; on
    its own, either one is satisfiable by a fix that breaks the other.
    """
    attempts: list[str] = []

    async def failing_notify(text):
        attempts.append(text)
        raise RuntimeError("transport boom")

    mon = _monitor(tmp_path, failing_notify)
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)
    still_running: list[bool] = []

    async def run():
        still_running.append(
            await _run_resource_loop(mon, hot, until=lambda: len(attempts) >= 3)
        )

    asyncio.run(run())

    assert still_running == [True], "loop died on a send failure"
    assert len(attempts) >= 3


# ─────────────────────────────────────────────────────────────────────
# 2. Same defect, longer silence — the digest date-gate
# ─────────────────────────────────────────────────────────────────────


class _FakeTime:
    def __init__(self, hour):
        self.tm_hour = hour

    def __call__(self):
        return self


def test_failed_digest_does_not_burn_the_date_gate(tmp_path):
    """RED on master: ``mark_digest_sent`` runs even when the send failed.

    Identical defect to the edge state, with a 24h silence instead of an
    until-it-re-crosses one.
    """
    from gits.core import health as health_mod

    attempts: list[str] = []

    async def failing_notify(text):
        attempts.append(text)
        raise RuntimeError("transport boom")

    cfg = _cfg()
    mon = _monitor(tmp_path, failing_notify, cfg)
    st = mon._state()
    sample = rw.TokenSample(accounts=[rw.TokenAccount("a", 1, 2, 1, 3)])

    async def run():
        with patch.object(health_mod.time, "localtime", _FakeTime(9)), \
             patch.object(health_mod.time, "strftime", lambda *a: "2026-08-01"):
            await mon._maybe_send_digest(sample, cfg, st)
            assert st.last_digest_date() == "", (
                "failed digest recorded as sent → silent for 24h"
            )
            # Next pass retries rather than waiting out the day.
            await mon._maybe_send_digest(sample, cfg, st)

    asyncio.run(run())
    assert len(attempts) == 2, attempts


def test_delivered_digest_does_burn_the_date_gate(tmp_path):
    """The companion: success must still gate, or the digest spams."""
    from gits.core import health as health_mod

    sent: list[str] = []

    async def ok_notify(text):
        sent.append(text)

    cfg = _cfg()
    mon = _monitor(tmp_path, ok_notify, cfg)
    st = mon._state()
    sample = rw.TokenSample(accounts=[rw.TokenAccount("a", 1, 2, 1, 3)])

    async def run():
        with patch.object(health_mod.time, "localtime", _FakeTime(9)), \
             patch.object(health_mod.time, "strftime", lambda *a: "2026-08-01"):
            await mon._maybe_send_digest(sample, cfg, st)
            await mon._maybe_send_digest(sample, cfg, st)

    asyncio.run(run())
    assert len(sent) == 1, sent
    assert st.last_digest_date() == "2026-08-01"


# ─────────────────────────────────────────────────────────────────────
# 3. Channel: zero relocation, zero silence
# ─────────────────────────────────────────────────────────────────────


def test_unset_key_keeps_the_existing_destination(tmp_path):
    """No relocation. This test exists to stop a future tidy-up.

    ghost#42 proposed converging watchdog routing onto the butler home
    channel. That would move another team's dashboard alerts to a channel
    they do not read — reproducing this very ticket's failure mode with a
    different victim. The destination stays put; only its *visibility*
    changes.
    """
    cfg = _cfg(env={})
    assert cfg.alert_channel == _DEFAULT_ALERT_CHANNEL

    adapter = _FakeAdapter()
    eng = _bare_engine(cfg, adapter)
    asyncio.run(eng._send_watchdog_alert("swap critical"))

    # Anti-vacuum: prove the send path really ran, to the unmoved channel.
    assert adapter.sent == [(_DEFAULT_ALERT_CHANNEL, "swap critical")]


def test_explicit_key_still_routes_there(tmp_path):
    """Old key not regressed — #18 deployments keep working untouched."""
    cfg = _cfg(env={ALERT_CHANNEL_ENV: "999000111"})
    assert cfg.alert_channel == "999000111"

    adapter = _FakeAdapter()
    eng = _bare_engine(cfg, adapter)
    asyncio.run(eng._send_watchdog_alert("swap critical"))

    assert adapter.sent == [("999000111", "swap critical")]


def test_defaulted_channel_complains_and_names_the_env_var(caplog):
    """RED on master: falling back to the baked-in default is silent.

    The complaint must name the symbol a reader has to act on. Pinning the
    literal env var name is the point — a complaint that says "misconfigured"
    without naming the knob makes the reader go source-diving.
    """
    cfg = _cfg(env={})
    adapter = _FakeAdapter()
    eng = _bare_engine(cfg, adapter)

    with caplog.at_level(logging.WARNING, logger="gits.core.engine"):
        asyncio.run(eng._send_watchdog_alert("swap critical"))

    complaints = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and ALERT_CHANNEL_ENV in r.getMessage()
    ]
    assert complaints, (
        "using the hard-coded default channel produced no visible complaint; "
        f"records={[r.getMessage() for r in caplog.records]}"
    )
    # The destination it silently chose must be in the complaint too.
    assert any(_DEFAULT_ALERT_CHANNEL in m for m in complaints), complaints
    # Still delivered — the complaint is additive, never a refusal to send.
    assert adapter.sent == [(_DEFAULT_ALERT_CHANNEL, "swap critical")]


def test_configured_channel_does_not_complain(caplog):
    """The complaint must distinguish configured from defaulted.

    If it fires either way it stops carrying information and gets filtered.
    """
    cfg = _cfg(env={ALERT_CHANNEL_ENV: "999000111"})
    eng = _bare_engine(cfg, _FakeAdapter())

    with caplog.at_level(logging.WARNING, logger="gits.core.engine"):
        asyncio.run(eng._send_watchdog_alert("swap critical"))

    assert not [
        r for r in caplog.records if ALERT_CHANNEL_ENV in r.getMessage()
    ], [r.getMessage() for r in caplog.records]


def test_config_knows_whether_the_channel_was_chosen_or_defaulted():
    """"Configured" and "defaulted" must be different observable states.

    This is the property the hard-coded default destroyed: with the constant
    as the field default there was no value of ``alert_channel`` that meant
    "nobody set this".
    """
    assert _cfg(env={}).alert_channel_configured is False
    assert _cfg(env={ALERT_CHANNEL_ENV: "999"}).alert_channel_configured is True
    # Explicitly setting it *to* the default is still a choice, not a default.
    explicit = _cfg(env={ALERT_CHANNEL_ENV: _DEFAULT_ALERT_CHANNEL})
    assert explicit.alert_channel_configured is True


# ─────────────────────────────────────────────────────────────────────
# 4. Delivery failure must be audible, and reported to the caller
# ─────────────────────────────────────────────────────────────────────


def test_send_failure_is_reported_as_undelivered_and_logged(caplog):
    """RED on master: returns ``None`` and logs the failure at DEBUG.

    The caller cannot distinguish sent from dropped, which is the mechanism
    behind the permanent silence.
    """
    cfg = _cfg(env={ALERT_CHANNEL_ENV: "999000111"})
    adapter = _FakeAdapter(fail_first=1)
    eng = _bare_engine(cfg, adapter)

    with caplog.at_level(logging.WARNING, logger="gits.core.engine"):
        delivered = asyncio.run(eng._send_watchdog_alert("swap critical"))

    assert delivered is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a dropped operator alert was logged below WARNING"
    )


def test_successful_send_reports_delivered():
    cfg = _cfg(env={ALERT_CHANNEL_ENV: "999000111"})
    eng = _bare_engine(cfg, _FakeAdapter())
    assert asyncio.run(eng._send_watchdog_alert("swap critical")) is True


def test_no_adapter_is_not_delivered():
    """No transport wired is "not delivered", so the edge is kept for later."""
    eng = _bare_engine(_cfg(env={ALERT_CHANNEL_ENV: "999"}), None)
    assert asyncio.run(eng._send_watchdog_alert("swap critical")) is False


# ─────────────────────────────────────────────────────────────────────
# 5. The two subsystems must keep agreeing
# ─────────────────────────────────────────────────────────────────────


def test_reconcile_does_not_commit_state(tmp_path):
    """The root cause, pinned directly.

    ``reconcile()`` is pure with respect to ``WatchdogState``; committing the
    edge belongs to whoever knows whether the send landed. Without this, a
    future refactor can quietly move the commit back upstream and restore the
    silence with every delivery test still green.
    """
    from gits.core.watchdog_state import WatchdogState

    cfg = _cfg()
    st = WatchdogState(tmp_path / "watchdog_state.json")
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)

    first = rw.reconcile(rw.classify_resources(hot, cfg.thresholds, st), st, cfg)
    assert [a for a in first if a.metric == "swap"]
    assert st.level("swap") == LEVEL_OK, "reconcile() committed the edge itself"

    # Nothing was delivered, so the same edge must still be there to find.
    second = rw.reconcile(rw.classify_resources(hot, cfg.thresholds, st), st, cfg)
    assert [a for a in second if a.metric == "swap"], (
        "the un-delivered edge was consumed by reconcile()"
    )


def test_deliver_commits_only_what_landed(tmp_path):
    """``deliver()`` is the single place the edge may advance."""
    from gits.core.watchdog_state import WatchdogState

    cfg = _cfg()
    st = WatchdogState(tmp_path / "watchdog_state.json")
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)
    alerts = [
        a
        for a in rw.reconcile(rw.classify_resources(hot, cfg.thresholds, st), st, cfg)
        if a.metric == "swap"
    ]

    async def refuse(_text):
        return False

    async def accept(_text):
        return True

    assert asyncio.run(rw.deliver(alerts, st, refuse)) == []
    assert st.level("swap") == LEVEL_OK

    assert asyncio.run(rw.deliver(alerts, st, accept)) == alerts
    assert st.level("swap") == LEVEL_CRITICAL
