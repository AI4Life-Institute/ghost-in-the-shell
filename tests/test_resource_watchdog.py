"""Tests for the dual-face resource + token watchdog (task [[jeyuxq]]).

Covers the spec test plan: resource sampler shape, tmux-fd resolution via
``lsof -t`` (NOT pgrep), hysteretic threshold classification, config-driven
token caps (inert when unconfigured), edge-trigger de-dupe + recovery,
read-only / zero-network guarantees, tick isolation, and the AC-7 daily
balance digest (date-gate) + immediate skew alert.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import patch

from gits.core import health as health_mod
from gits.core import resource_watch as rw
from gits.core import watchdog_config as wc_mod
from gits.core.health import HealthMonitor
from gits.core.watchdog_config import Thresholds, load_watchdog_config
from gits.core.watchdog_state import (
    LEVEL_CRITICAL,
    LEVEL_OK,
    LEVEL_WARN,
    WatchdogState,
)


def _state(tmp_path: Path) -> WatchdogState:
    return WatchdogState(tmp_path / "watchdog_state.json")


def _cfg(env: dict | None = None):
    return load_watchdog_config(config_env_path=Path("/nonexistent"), env=env or {})


# ─────────────────────────────────────────────────────────────────────
# 1. Resource sampler returns a populated struct
# ─────────────────────────────────────────────────────────────────────


def test_resource_sampler_populated():
    s = rw.sample_resources(_cfg())
    assert s.cores >= 1
    assert s.tmux_fd_limit == 256
    # load ratio present and sane on any POSIX host
    assert s.load_1m is not None and s.load_1m >= 0
    assert s.load_ratio is not None and s.load_ratio >= 0
    # Optional collectors are either None (unsupported platform) or numeric
    # and in a plausible range.
    for val, lo, hi in (
        (s.swap_used_pct, 0, 100),
        (s.mem_free_pct, 0, 100),
        (s.disk_free_pct, 0, 100),
    ):
        assert val is None or (lo <= val <= hi)
    assert s.proc_count is None or s.proc_count > 0
    assert s.tmux_fd is None or s.tmux_fd >= 0


# ─────────────────────────────────────────────────────────────────────
# 2. tmux-fd resolver uses `lsof -t <socket>`, NOT pgrep
# ─────────────────────────────────────────────────────────────────────


def test_tmux_fd_resolver_uses_lsof_socket_not_pgrep():
    calls: list[list[str]] = []

    class FakeResult:
        returncode = 0

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["lsof", "-t"]:
            return FakeResult("4242\n")  # server PID holding the socket
        if cmd[:2] == ["lsof", "-p"]:
            # header + 3 fd rows
            return FakeResult(
                "COMMAND PID USER FD TYPE\n"
                "tmux 4242 u 0u CHR\n"
                "tmux 4242 u 1u CHR\n"
                "tmux 4242 u 2u CHR\n"
            )
        raise AssertionError(f"unexpected command: {cmd}")

    with patch.object(rw.subprocess, "run", side_effect=fake_run):
        count = rw.tmux_fd_count("/private/tmp/tmux-501/default")

    assert count == 3
    # The socket path was passed to `lsof -t`, and pgrep was never used.
    assert calls[0] == ["lsof", "-t", "/private/tmp/tmux-501/default"]
    assert all("pgrep" not in c[0] for c in calls)


# ─────────────────────────────────────────────────────────────────────
# 3. Threshold logic + hysteresis
# ─────────────────────────────────────────────────────────────────────


def test_hysteresis_high_dead_band_holds(tmp_path):
    th = Thresholds()  # swap warn 80 / crit 90 / clear 70
    st = _state(tmp_path)

    def lvl(swap):
        s = rw.ResourceSample(cores=4, swap_used_pct=swap)
        v = next(v for v in rw.classify_resources(s, th, st) if v.metric == "swap")
        st.set_level("swap", v.level)
        return v.level

    assert lvl(85) == LEVEL_WARN          # crosses warn
    assert lvl(75) == LEVEL_WARN          # dead-band (70<75<80) → HOLD warn
    assert lvl(65) == LEVEL_OK            # below clear band → clears
    assert lvl(95) == LEVEL_CRITICAL      # straight to critical
    assert lvl(82) == LEVEL_WARN          # de-escalate critical→warn at warn band


def test_hysteresis_low_metric_disk(tmp_path):
    th = Thresholds()  # disk warn 8 / crit 4 / clear 12 (lower is worse)
    st = _state(tmp_path)

    def lvl(free):
        s = rw.ResourceSample(cores=4, disk_free_pct=free)
        v = next(v for v in rw.classify_resources(s, th, st) if v.metric == "disk")
        st.set_level("disk", v.level)
        return v.level

    assert lvl(6) == LEVEL_WARN           # <=8
    assert lvl(10) == LEVEL_WARN          # dead-band (8<10<12) → HOLD
    assert lvl(15) == LEVEL_OK            # >=12 clears
    assert lvl(3) == LEVEL_CRITICAL       # <=4


# ─────────────────────────────────────────────────────────────────────
# 4. Token caps from config; missing cap → inert (no crash)
# ─────────────────────────────────────────────────────────────────────


def test_token_caps_from_env_and_inert_when_missing(tmp_path):
    cfg = _cfg({"GITS_ACCOUNT_5H_CAPS": "foo=100", "GITS_ACCOUNT_7D_CAPS": "foo=1000"})
    assert cfg.cap_5h("foo") == 100
    assert cfg.cap_5h("FOO") == 100  # case-insensitive
    assert cfg.cap_5h("bar") is None  # unconfigured → inert

    foo = rw.TokenAccount(
        name="foo", load_5h=90, load_7d=100, bindings=1, score=190,
        cap_5h=cfg.cap_5h("foo"), cap_7d=cfg.cap_7d("foo"),
        pct_5h=90.0, pct_7d=10.0,
    )
    bar = rw.TokenAccount(
        name="bar", load_5h=5_000, load_7d=9_000, bindings=1, score=14_000,
        cap_5h=cfg.cap_5h("bar"), cap_7d=cfg.cap_7d("bar"),
        pct_5h=None, pct_7d=None,
    )
    sample = rw.TokenSample(accounts=[foo, bar])
    verdicts = rw.classify_token(sample, cfg.thresholds, _state(tmp_path))
    metrics = {v.metric: v.level for v in verdicts}
    # foo 5h at 90% → critical; bar has no cap → no verdict at all (inert).
    assert metrics["token5h:foo"] == LEVEL_CRITICAL
    assert not any(m.endswith(":bar") for m in metrics)


def test_malformed_cap_list_degrades_locally_and_never_raises():
    """A typo in the cap list must stay inside this parser.

    This is the whole reason the per-account caps are one declared key
    instead of a dynamic key family: the same operator typo now costs an
    inert cap, where before it made every ``Settings()`` raise — bot,
    hooks and CLI. Malformed entries drop; well-formed neighbours survive.
    """
    cfg = _cfg({"GITS_ACCOUNT_5H_CAPS": "good=500,,garbage,bad=xyz,other=250"})
    assert cfg.cap_5h("good") == 500
    assert cfg.cap_5h("other") == 250  # survives a bad neighbour
    assert cfg.cap_5h("garbage") is None  # no '=' → dropped, inert
    assert cfg.cap_5h("bad") is None  # non-numeric → dropped, inert

    # Entirely unparseable input is still not an exception.
    assert _cfg({"GITS_ACCOUNT_5H_CAPS": "=,=,="}).caps_5h == {}
    assert _cfg({"GITS_ACCOUNT_7D_CAPS": ""}).caps_7d == {}


def test_skew_detection_cap_independent():
    th = Thresholds()
    # One account holds >60% of bindings.
    accts = [
        rw.TokenAccount("a", 10, 10, 8, 20),
        rw.TokenAccount("b", 10, 10, 1, 20),
        rw.TokenAccount("c", 10, 10, 1, 20),
    ]
    skew, reason = rw._detect_skew(accts, th)
    assert skew and "a" in reason


# ─────────────────────────────────────────────────────────────────────
# 5. Edge-trigger de-dupe + recovery
# ─────────────────────────────────────────────────────────────────────


def _sent(alerts, st):
    """One reconcile→deliver pass with a transport that always succeeds.

    Edge de-dupe is a property of reconcile *plus* delivery: since ghost#42
    the state advances only once an alert actually lands, so a test that
    calls ``reconcile`` alone would never de-dupe. Going through
    ``deliver`` is also what production does.
    """

    async def ok(_text):
        return True

    return asyncio.run(rw.deliver(alerts, st, ok))


def test_edge_trigger_dedupe_and_recovery(tmp_path):
    cfg = _cfg()
    st = _state(tmp_path)
    hot = rw.ResourceSample(cores=4, swap_used_pct=95)

    a1 = _sent(rw.reconcile(rw.classify_resources(hot, cfg.thresholds, st), st, cfg), st)
    a2 = _sent(rw.reconcile(rw.classify_resources(hot, cfg.thresholds, st), st, cfg), st)
    swap1 = [a for a in a1 if a.metric == "swap"]
    swap2 = [a for a in a2 if a.metric == "swap"]
    assert len(swap1) == 1 and swap1[0].level == LEVEL_CRITICAL
    assert len(swap2) == 0  # sustained → no second alert

    cool = rw.ResourceSample(cores=4, swap_used_pct=50)
    a3 = _sent(rw.reconcile(rw.classify_resources(cool, cfg.thresholds, st), st, cfg), st)
    swap3 = [a for a in a3 if a.metric == "swap"]
    assert len(swap3) == 1 and swap3[0].is_recovery


def test_skew_immediate_alert_one_then_clear(tmp_path):
    cfg = _cfg()
    st = _state(tmp_path)
    skewed = rw.TokenSample(skew=True, skew_reason="`a` holds 80%")
    balanced = rw.TokenSample(skew=False)

    a1 = _sent(rw.reconcile([rw.classify_skew(skewed, st)], st, cfg), st)
    a2 = _sent(rw.reconcile([rw.classify_skew(skewed, st)], st, cfg), st)
    a3 = _sent(rw.reconcile([rw.classify_skew(balanced, st)], st, cfg), st)
    assert len(a1) == 1 and a1[0].level == LEVEL_WARN
    assert len(a2) == 0
    assert len(a3) == 1 and a3[0].is_recovery


def test_state_persists_across_reload(tmp_path):
    p = tmp_path / "watchdog_state.json"
    s1 = WatchdogState(p)
    s1.set_level("swap", LEVEL_CRITICAL)
    # New instance reading the same file must not re-alert.
    s2 = WatchdogState(p)
    assert s2.level("swap") == LEVEL_CRITICAL


# ─────────────────────────────────────────────────────────────────────
# 6. Read-only / zero-network guarantee
# ─────────────────────────────────────────────────────────────────────


def _code_only(mod) -> str:
    """Module source with comments and string/docstring literals removed,
    so the read-only/zero-network scan checks executable code — not prose
    that *describes* the banned operations."""
    import io
    import tokenize

    src = inspect.getsource(mod)
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out).lower()


def test_no_network_no_kill_in_module_source():
    banned = (
        "oauth", "requests", "httpx", "urllib", "aiohttp",
        "os.kill", "killpg", "sigkill", "sigterm", "pgrep",
    )
    for mod in (rw, health_mod):
        code = _code_only(mod)
        for needle in banned:
            assert needle.lower() not in code, f"{needle!r} found in {mod.__name__} code"


def test_no_mutating_tmux_subcommands():
    # The watchdog only ever asks tmux for the socket path; never mutates.
    src = inspect.getsource(rw)
    assert "display-message" in src
    code = _code_only(rw)
    for mutating in ("kill", "send-keys", "new-window", "new-session", "respawn"):
        assert mutating not in code


# ─────────────────────────────────────────────────────────────────────
# 7. Tick isolation — heavy samplers off the 5s main tick
# ─────────────────────────────────────────────────────────────────────


def test_samplers_not_called_in_main_check_loop():
    for fn in (HealthMonitor._check_loop, HealthMonitor._check_health):
        src = inspect.getsource(fn)
        assert "sample_resources" not in src
        assert "sample_tokens" not in src


def test_heavy_samplers_run_in_to_thread():
    for fn in (
        HealthMonitor._resource_watch_loop,
        HealthMonitor._token_watch_loop,
    ):
        src = inspect.getsource(fn)
        assert "to_thread" in src


# ─────────────────────────────────────────────────────────────────────
# AC-7: daily balance digest (date-gate) folded into the token loop
# ─────────────────────────────────────────────────────────────────────


def _bare_monitor(tmp_path, sent: list):
    cfg = _cfg()

    async def capture(text):
        sent.append(text)

    return HealthMonitor(
        tmux=None,
        session_mgr=None,
        launcher=None,
        notify=capture,
        watchdog_config=cfg,
        watchdog_state_path=tmp_path / "watchdog_state.json",
    ), cfg


class _FakeTime:
    def __init__(self, hour, day):
        self.tm_hour = hour
        self._day = day

    def __call__(self):  # time.localtime()
        return self


def test_daily_digest_fires_once_per_day(tmp_path):
    sent: list[str] = []
    mon, cfg = _bare_monitor(tmp_path, sent)
    st = mon._state()
    sample = rw.TokenSample(accounts=[rw.TokenAccount("a", 1, 2, 1, 3)])

    async def run():
        # Before the digest hour → nothing.
        with patch.object(health_mod.time, "localtime", _FakeTime(8, 1)), \
             patch.object(health_mod.time, "strftime", lambda *a: "2026-06-01"):
            await mon._maybe_send_digest(sample, cfg, st)
        assert sent == []
        # After the hour → exactly one digest.
        with patch.object(health_mod.time, "localtime", _FakeTime(9, 1)), \
             patch.object(health_mod.time, "strftime", lambda *a: "2026-06-01"):
            await mon._maybe_send_digest(sample, cfg, st)
            await mon._maybe_send_digest(sample, cfg, st)  # same day → no dupe
        assert len(sent) == 1 and "digest" in sent[0].lower()
        # Next day → re-arms.
        with patch.object(health_mod.time, "localtime", _FakeTime(9, 2)), \
             patch.object(health_mod.time, "strftime", lambda *a: "2026-06-02"):
            await mon._maybe_send_digest(sample, cfg, st)
        assert len(sent) == 2

    asyncio.run(run())


def test_digest_format_shows_dash_headroom_when_no_cap(tmp_path):
    cfg = _cfg()
    sample = rw.TokenSample(
        accounts=[rw.TokenAccount("a", 100, 200, 2, 300)],
        skew=False,
    )
    text = rw.format_digest(sample, cfg)
    assert "headroom=—" in text
    assert "均" in text  # balanced verdict


# ─────────────────────────────────────────────────────────────────────
# 9. Settings() must survive the keys this module is documented to read
#
# The rest of this file loads config with `config_env_path=/nonexistent`
# and a synthetic `env`, so the watchdog never meets the real file it is
# documented to read from — and `Settings` (pydantic, extra='forbid')
# parses that same file. These two tests close that gap: the feature and
# the model that validates its config file must meet at least once.
# ─────────────────────────────────────────────────────────────────────


def _watchdog_keys_in_source() -> list[str]:
    """Every ``GITS_*`` key literal the watchdog config reader looks up.

    Note the character class includes digits: a ``[A-Z_]+``-only pattern
    silently truncates ``GITS_ACCOUNT_5H_CAPS`` at the ``5``.
    """
    import re

    src = inspect.getsource(wc_mod)
    return sorted(set(re.findall(r"GITS_[A-Z0-9_]+", src)))


def test_settings_accepts_a_real_config_env_with_watchdog_keys(tmp_path):
    """Writing the watchdog keys into a real config.env must not brick Settings.

    This is the operator's only documented way to configure the feature.
    An undeclared key here makes *every* ``Settings()`` raise — bot, hooks
    and CLI — and the failure lands nowhere near the watchdog.
    """
    from gits.config import Settings

    keys = _watchdog_keys_in_source()
    assert keys, "no GITS_* keys found in watchdog_config source"

    env_file = tmp_path / "config.env"
    env_file.write_text(
        "\n".join(f"{k}={_sample_value(k)}" for k in keys) + "\n",
        encoding="utf-8",
    )

    # Must not raise. Before the keys were declared this raised
    # ValidationError: Extra inputs are not permitted [type=extra_forbidden].
    Settings(_env_file=str(env_file))


def _sample_value(key: str) -> str:
    if key.endswith("_CAPS"):
        return "acctname=150000000"
    if key.endswith("_CHANNEL"):
        return "1510821666492649503"
    if key.endswith("_MENTION"):
        return "<@123>"
    if key.endswith("_PATH"):
        return "~/.gits"
    return "1"


def test_every_watchdog_key_is_declared_in_settings():
    """Coverage guard: no watchdog key may exist without a Settings field.

    Deliberately has **no prefix exception**. The per-account caps are
    collapsed into single ``GITS_ACCOUNT_5H_CAPS``/``7D_CAPS`` keys
    precisely so that an arbitrary-suffix family cannot exist here — if
    this test ever needs a wildcard, a dynamic key has come back and
    ``Settings`` cannot cover it.
    """
    from gits.config import Settings

    declared = set(Settings.model_fields)
    missing = [k for k in _watchdog_keys_in_source() if k.lower() not in declared]
    assert not missing, f"watchdog keys not declared in Settings: {missing}"
