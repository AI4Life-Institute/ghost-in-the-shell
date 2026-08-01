"""Resource + token watchdog — read-only samplers, classifiers, formatters.

The collection + classification half of the dual-face watchdog
(task [[jeyuxq]]). Pure of any scheduling: the cadence lives in
:class:`gits.core.health.HealthMonitor`'s sibling loops, which call the
samplers here on independent slow sub-ticks (never the 5s main tick).

Two faces:

* **Resource** — :func:`sample_resources` does one read-only pass:
  load avg vs core count, memory (free% / wired / compressed / swap-used%),
  process count, **tmux-server fd count vs its 256 ceiling**, and disk-free%
  on the data volume. The tmux-fd count resolves the server holding the
  session *socket* via ``lsof -t <socket_path>`` — NOT ``pgrep -f tmux``,
  which false-matches ``npm exec vite`` and friends.
* **Token** — :func:`sample_tokens` reuses :func:`rank_accounts`
  (local-JSONL, zero-network, no OAuth/Usage API) for per-account 5h/7d
  load, bindings, score, and a cap-% / balance-skew read.

Classification (:func:`classify_resources`, :func:`classify_token`,
:func:`classify_skew`) is **pure** and **hysteretic**: a metric that rises
past its warn band only *clears* once it falls back through a separate
clear band (a dead-band between holds the prior level so alerts don't
flap). :func:`reconcile` turns verdicts into edge-triggered alert payloads
against :class:`gits.core.watchdog_state.WatchdogState`.

Hard guarantees (CEO, non-negotiable): read-only + zero-network. Nothing
here kills/signals a process or mutates tmux; the token face is
local-JSONL only. No HTTP client, no ``oauth``/``usage`` URL.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from statistics import median

from .account import AccountLayout
from .account_load import rank_accounts
from .account_vault import AccountVault
from .health import _available_memory_mb
from .watchdog_config import Thresholds, WatchdogConfig
from .watchdog_state import LEVEL_CRITICAL, LEVEL_OK, LEVEL_WARN, WatchdogState

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 4.0  # seconds; keeps a slow lsof/df from piling up


# ─────────────────────────────────────────────────────────────────────
# Sample structs
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ResourceSample:
    """One read-only pass over host resources. Fields are ``None`` when a
    collector is unavailable (e.g. non-darwin, no tmux server) — callers
    skip the corresponding threshold rather than crashing."""

    cores: int = 0
    load_1m: float | None = None
    load_ratio: float | None = None  # load_1m / cores
    mem_avail_mb: int | None = None
    mem_free_pct: float | None = None
    wired_mb: int | None = None
    compressed_mb: int | None = None
    swap_used_pct: float | None = None
    proc_count: int | None = None
    tmux_fd: int | None = None
    tmux_fd_limit: int = 256
    disk_free_pct: float | None = None
    disk_path: str = ""


@dataclass
class TokenAccount:
    """Per-account token-face row."""

    name: str
    load_5h: float
    load_7d: float
    bindings: int
    score: float
    cap_5h: float | None = None  # None = unconfigured → cap-% inert
    cap_7d: float | None = None
    pct_5h: float | None = None  # load_5h / cap_5h * 100 (None if no cap)
    pct_7d: float | None = None


@dataclass
class TokenSample:
    """Token-face snapshot across all accounts."""

    accounts: list[TokenAccount] = field(default_factory=list)
    skew: bool = False
    skew_reason: str = ""


@dataclass
class Verdict:
    """A single metric's classification result."""

    metric: str  # stable key for state de-dupe, e.g. "swap", "token:foo"
    level: str  # LEVEL_OK | LEVEL_WARN | LEVEL_CRITICAL
    summary: str  # human one-liner: "swap 92% vs 90%"


@dataclass
class Alert:
    """An edge-triggered alert payload ready for the notify callback."""

    metric: str
    level: str
    is_recovery: bool
    text: str


# ─────────────────────────────────────────────────────────────────────
# Resource sampler (read-only)
# ─────────────────────────────────────────────────────────────────────


def _run(cmd: list[str]) -> str | None:
    """Run a read-only command, return stdout or ``None`` on any failure."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("watchdog cmd failed %s: %s", cmd, e)
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _sysctl_int(name: str) -> int | None:
    out = _run(["sysctl", "-n", name])
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _swap_used_pct() -> float | None:
    """Parse ``sysctl -n vm.swapusage`` → used/total %. macOS only."""
    out = _run(["sysctl", "-n", "vm.swapusage"])
    if out is None:
        return None
    total = used = None
    m_total = re.search(r"total\s*=\s*([\d.]+)M", out)
    m_used = re.search(r"used\s*=\s*([\d.]+)M", out)
    if m_total:
        total = float(m_total.group(1))
    if m_used:
        used = float(m_used.group(1))
    if total is None or used is None:
        return None
    if total <= 0:
        return 0.0
    return used / total * 100.0


def _vm_stat_mem() -> tuple[float | None, int | None, int | None]:
    """Return ``(free_pct, wired_mb, compressed_mb)`` from vm_stat. macOS."""
    out = _run(["vm_stat"])
    if out is None:
        return None, None, None
    page_size = 16384
    m = re.search(r"page size of (\d+) bytes", out)
    if m:
        page_size = int(m.group(1))
    free = inactive = wired = compressed = 0
    for line in out.splitlines():
        if "Pages free:" in line:
            free = int(re.sub(r"\D", "", line))
        elif "Pages inactive:" in line:
            inactive = int(re.sub(r"\D", "", line))
        elif "Pages wired down:" in line:
            wired = int(re.sub(r"\D", "", line))
        elif "Pages occupied by compressor:" in line:
            compressed = int(re.sub(r"\D", "", line))
    wired_mb = wired * page_size // 1024 // 1024
    compressed_mb = compressed * page_size // 1024 // 1024
    total_bytes = _sysctl_int("hw.memsize")
    free_pct: float | None = None
    if total_bytes:
        avail_bytes = (free + inactive) * page_size
        free_pct = avail_bytes / total_bytes * 100.0
    return free_pct, wired_mb, compressed_mb


def _proc_count() -> int | None:
    out = _run(["ps", "-A", "-o", "pid="])
    if out is None:
        return None
    return sum(1 for line in out.splitlines() if line.strip())


def _tmux_socket_path(session_name: str = "gits") -> str | None:
    """Resolve the tmux server's socket path via tmux itself."""
    out = _run(["tmux", "display-message", "-p", "#{socket_path}"])
    if out and out.strip():
        return out.strip()
    return None


def tmux_fd_count(socket_path: str) -> int | None:
    """fd count of the tmux server holding ``socket_path``.

    Resolves the server PID via ``lsof -t <socket_path>`` (the process
    with the socket open) — NOT ``pgrep -f tmux``, which false-matches
    unrelated processes carrying "tmux" in their argv (e.g. ``npm exec
    vite``, observed in the 2026-05-31 baseline). Then counts that PID's
    open fds via ``lsof -p <pid>``.
    """
    out = _run(["lsof", "-t", socket_path])
    if out is None:
        return None
    pids = [p for p in out.split() if p.strip().isdigit()]
    if not pids:
        return None
    pid = pids[0]
    fds = _run(["lsof", "-p", pid])
    if fds is None:
        return None
    lines = [ln for ln in fds.splitlines() if ln.strip()]
    # Drop the header line lsof prints (COMMAND PID USER FD ...).
    count = len(lines) - 1 if lines and lines[0].startswith("COMMAND") else len(lines)
    return max(count, 0)


def sample_resources(config: WatchdogConfig) -> ResourceSample:
    """One read-only pass over host resources. Never raises."""
    cores = os.cpu_count() or 1
    s = ResourceSample(cores=cores, tmux_fd_limit=config.thresholds.tmux_fd_limit)

    try:
        s.load_1m = os.getloadavg()[0]
        s.load_ratio = s.load_1m / cores if cores else None
    except (OSError, ValueError):
        pass

    s.mem_avail_mb = _available_memory_mb()
    s.mem_free_pct, s.wired_mb, s.compressed_mb = _vm_stat_mem()
    s.swap_used_pct = _swap_used_pct()
    s.proc_count = _proc_count()

    socket_path = _tmux_socket_path()
    if socket_path:
        s.tmux_fd = tmux_fd_count(socket_path)

    disk_path = config.disk_watch_path.expanduser()
    s.disk_path = str(disk_path)
    try:
        st = os.statvfs(disk_path)
        if st.f_blocks:
            s.disk_free_pct = st.f_bavail / st.f_blocks * 100.0
    except OSError:
        pass

    return s


# ─────────────────────────────────────────────────────────────────────
# Token sampler (local-JSONL, zero-network)
# ─────────────────────────────────────────────────────────────────────


def sample_tokens(
    vault: AccountVault,
    config: WatchdogConfig,
    *,
    layout: AccountLayout | None = None,
    live_binding_counts: dict[str, int] | None = None,
    now: float | None = None,
) -> TokenSample:
    """Per-account token load + cap-% + balance-skew, via :func:`rank_accounts`.

    Strictly local-JSONL (rank_accounts → account_load_dual). No network.
    """
    ranks = rank_accounts(
        vault,
        live_binding_counts=live_binding_counts,
        layout=layout,
        now=now,
    )
    accounts: list[TokenAccount] = []
    for r in ranks:
        cap_5h = config.cap_5h(r.name)
        cap_7d = config.cap_7d(r.name)
        accounts.append(
            TokenAccount(
                name=r.name,
                load_5h=r.load_5h,
                load_7d=r.load_7d,
                bindings=r.bindings,
                score=r.score,
                cap_5h=cap_5h,
                cap_7d=cap_7d,
                pct_5h=(r.load_5h / cap_5h * 100.0) if cap_5h else None,
                pct_7d=(r.load_7d / cap_7d * 100.0) if cap_7d else None,
            )
        )
    skew, reason = _detect_skew(accounts, config.thresholds)
    return TokenSample(accounts=accounts, skew=skew, skew_reason=reason)


def _detect_skew(
    accounts: list[TokenAccount], th: Thresholds
) -> tuple[bool, str]:
    """Balance-skew check — cap-independent, always active.

    Trips when one account holds > ``skew_binding_share`` of all
    dispatched bindings, OR its score > ``skew_score_median_mult`` ×
    median score across accounts.
    """
    if len(accounts) < 2:
        return False, ""
    total_bindings = sum(a.bindings for a in accounts)
    if total_bindings > 0:
        for a in accounts:
            share = a.bindings / total_bindings
            if share > th.skew_binding_share:
                return True, (
                    f"`{a.name}` holds {share*100:.0f}% of {total_bindings} "
                    f"dispatched bindings (>{th.skew_binding_share*100:.0f}%)"
                )
    scores = [a.score for a in accounts if a.score > 0]
    if len(scores) >= 2:
        med = median(scores)
        if med > 0:
            for a in accounts:
                if a.score > th.skew_score_median_mult * med:
                    return True, (
                        f"`{a.name}` score {a.score:.0f} is "
                        f">{th.skew_score_median_mult:g}× median {med:.0f}"
                    )
    return False, ""


# ─────────────────────────────────────────────────────────────────────
# Classification — pure + hysteretic
# ─────────────────────────────────────────────────────────────────────


def _classify_high(
    value: float, warn: float, critical: float, clear: float, prev: str
) -> str:
    """Hysteretic classify for "higher is worse" metrics.

    Above ``critical`` → critical; above ``warn`` → warn; at/below
    ``clear`` → ok. In the dead-band (``clear`` < value < ``warn``) the
    prior level is held so the alert does not flap.
    """
    if value >= critical:
        return LEVEL_CRITICAL
    if value >= warn:
        return LEVEL_WARN
    if value <= clear:
        return LEVEL_OK
    return prev


def _classify_low(
    value: float, warn: float, critical: float, clear: float, prev: str
) -> str:
    """Hysteretic classify for "lower is worse" metrics (disk, mem)."""
    if value <= critical:
        return LEVEL_CRITICAL
    if value <= warn:
        return LEVEL_WARN
    if value >= clear:
        return LEVEL_OK
    return prev


def classify_resources(
    s: ResourceSample, th: Thresholds, state: WatchdogState
) -> list[Verdict]:
    """Classify every populated resource metric against its bands."""
    out: list[Verdict] = []

    if s.swap_used_pct is not None:
        lvl = _classify_high(
            s.swap_used_pct, th.swap_warn, th.swap_critical, th.swap_clear,
            state.level("swap"),
        )
        out.append(Verdict("swap", lvl, f"swap {s.swap_used_pct:.0f}% used"))

    if s.tmux_fd is not None:
        lvl = _classify_high(
            s.tmux_fd, th.tmux_fd_warn, th.tmux_fd_critical, th.tmux_fd_clear,
            state.level("tmux_fd"),
        )
        out.append(
            Verdict("tmux_fd", lvl, f"tmux-fd {s.tmux_fd}/{s.tmux_fd_limit}")
        )

    if s.load_ratio is not None:
        lvl = _classify_high(
            s.load_ratio, th.load_warn_ratio, th.load_critical_ratio,
            th.load_clear_ratio, state.level("load"),
        )
        out.append(
            Verdict(
                "load", lvl,
                f"load {s.load_1m:.1f} = {s.load_ratio:.1f}×{s.cores} cores",
            )
        )

    if s.disk_free_pct is not None:
        lvl = _classify_low(
            s.disk_free_pct, th.disk_warn, th.disk_critical, th.disk_clear,
            state.level("disk"),
        )
        out.append(
            Verdict("disk", lvl, f"disk-free {s.disk_free_pct:.0f}% on {s.disk_path}")
        )

    if s.mem_avail_mb is not None:
        lvl = _classify_low(
            s.mem_avail_mb, th.mem_warn_mb, th.mem_critical_mb, th.mem_clear_mb,
            state.level("mem"),
        )
        out.append(Verdict("mem", lvl, f"mem-avail {s.mem_avail_mb} MB"))

    return out


def classify_token(
    sample: TokenSample, th: Thresholds, state: WatchdogState
) -> list[Verdict]:
    """Per-account cap-% classification. Accounts with no configured cap
    are **inert** (skipped) — see operator answer Q3, 2026-06-01."""
    out: list[Verdict] = []
    for a in sample.accounts:
        # 5h is the tighter, more actionable window; classify it. 7d cap
        # also classified when present.
        if a.pct_5h is not None:
            metric = f"token5h:{a.name}"
            lvl = _classify_high(
                a.pct_5h, th.token_warn_pct, th.token_critical_pct,
                th.token_clear_pct, state.level(metric),
            )
            out.append(
                Verdict(metric, lvl, f"`{a.name}` 5h at {a.pct_5h:.0f}% of cap")
            )
        if a.pct_7d is not None:
            metric = f"token7d:{a.name}"
            lvl = _classify_high(
                a.pct_7d, th.token_warn_pct, th.token_critical_pct,
                th.token_clear_pct, state.level(metric),
            )
            out.append(
                Verdict(metric, lvl, f"`{a.name}` 7d at {a.pct_7d:.0f}% of cap")
            )
    return out


def classify_skew(sample: TokenSample, state: WatchdogState) -> Verdict:
    """Balance-skew verdict (warn-only; cap-independent)."""
    lvl = LEVEL_WARN if sample.skew else LEVEL_OK
    summary = sample.skew_reason if sample.skew else "balance ok"
    return Verdict("skew", lvl, summary)


# ─────────────────────────────────────────────────────────────────────
# Edge-trigger reconciliation
# ─────────────────────────────────────────────────────────────────────


def reconcile(
    verdicts: list[Verdict], state: WatchdogState, config: WatchdogConfig
) -> list[Alert]:
    """Turn verdicts into edge-triggered alerts. **Does not touch ``state``.**

    An alert is emitted only when a metric's level *changes* (rising edge
    into warn/critical, escalation, or recovery to ok). A sustained level
    emits nothing.

    Reads ``state`` to find the edge; committing it is :func:`deliver`'s job,
    because only the sender knows whether the alert actually landed. This
    function used to call ``state.set_level`` here, which consumed the edge
    before anything was sent — a send that then failed was indistinguishable
    from one that succeeded, and the alert was never said again (ghost#42).
    Pinned by ``test_reconcile_does_not_commit_state``.
    """
    alerts: list[Alert] = []
    for v in verdicts:
        prev = state.level(v.metric)
        if v.level == prev:
            continue
        is_recovery = v.level == LEVEL_OK
        alerts.append(
            Alert(
                metric=v.metric,
                level=v.level,
                is_recovery=is_recovery,
                text=format_alert(v, is_recovery, config),
            )
        )
    return alerts


async def deliver(
    alerts: list[Alert],
    state: WatchdogState,
    send: Callable[[str], Awaitable[bool]],
) -> list[Alert]:
    """Send ``alerts``, advancing ``state`` only for those actually delivered.

    The single place the watchdog's failure posture lives, matching the one
    :mod:`gits.core.drift_watch` already implements for drift notices: **an
    undelivered alert is never recorded as delivered**, because the de-dupe
    ledger is the only thing deciding whether it is ever said again. Trading
    one network blip for permanent silence is the bad half of that deal.

    Note the posture is *don't advance the edge*, *not* *raise*. ``send`` is
    expected to swallow its own transport errors and answer ``False``; a
    watchdog that propagates an exception can take down the loop it runs in,
    which is a worse failure than the silence it replaced. "Doesn't crash"
    and "doesn't forget" are separate properties and this gets both.

    An undelivered alert leaves the metric's stored level untouched, so the
    next sub-tick re-derives the same edge and retries by construction.
    """
    delivered: list[Alert] = []
    for a in alerts:
        if await send(a.text):
            state.set_level(a.metric, a.level)
            delivered.append(a)
    return delivered


# ─────────────────────────────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────────────────────────────


def _mention_prefix(config: WatchdogConfig) -> str:
    return config.owner_mention or "@weiliu-ghost-dev"


def format_alert(v: Verdict, is_recovery: bool, config: WatchdogConfig) -> str:
    """Render an edge alert. Critical alerts are short and direct."""
    prefix = _mention_prefix(config)
    if is_recovery:
        return f"{prefix} ✅ WATCHDOG CLEAR: {v.summary} (recovered)"
    icon = "🚨" if v.level == LEVEL_CRITICAL else "⚠️"
    return f"{prefix} {icon} WATCHDOG {v.level.upper()}: {v.summary}"


def _fmt(val: float | None, suffix: str = "") -> str:
    return f"{val:.0f}{suffix}" if val is not None else "—"


def format_snapshot(
    res: ResourceSample, tok: TokenSample, th: Thresholds
) -> str:
    """Human-readable red/green snapshot for the ``gits resource`` CLI."""

    def dot(level: str) -> str:
        return {"ok": "🟢", "warn": "🟡", "critical": "🔴"}.get(level, "⚪")

    # Reuse the pure classifiers against a fresh (empty) state so the CLI
    # shows the raw level of each metric right now.
    blank = _BlankState()
    lines = ["**Resource watchdog — snapshot**", ""]
    for v in classify_resources(res, th, blank):
        lines.append(f"{dot(v.level)} {v.summary}")
    lines.append("")
    lines.append("**Token balance**")
    if not tok.accounts:
        lines.append("  (no accounts)")
    for a in tok.accounts:
        pct5 = f"{a.pct_5h:.0f}%" if a.pct_5h is not None else "—"
        lines.append(
            f"  `{a.name}`: 5h={a.load_5h:.0f} ({pct5} cap) "
            f"7d={a.load_7d:.0f}  bindings={a.bindings}"
        )
    lines.append(f"{dot('warn' if tok.skew else 'ok')} "
                 f"skew: {tok.skew_reason or '均 (balanced)'}")
    return "\n".join(lines)


def format_digest(tok: TokenSample, config: WatchdogConfig) -> str:
    """Daily per-account balance digest (AC-7). Pushed to the owner once
    per day; independent of edge state."""
    prefix = _mention_prefix(config)
    total_b = sum(a.bindings for a in tok.accounts)
    lines = [f"{prefix} 📊 **Daily token-balance digest**", ""]
    if not tok.accounts:
        lines.append("(no accounts configured)")
        return "\n".join(lines)
    for a in tok.accounts:
        share = (a.bindings / total_b * 100.0) if total_b else 0.0
        if a.cap_5h:
            headroom = f"{max(a.cap_5h - a.load_5h, 0):.0f} ({100 - (a.pct_5h or 0):.0f}% free)"
        else:
            headroom = "—"
        pct5 = f"{a.pct_5h:.0f}%" if a.pct_5h is not None else "—"
        lines.append(
            f"• `{a.name}`: 5h={a.load_5h:.0f} ({pct5} cap) · 7d={a.load_7d:.0f} · "
            f"bindings={a.bindings} ({share:.0f}%) · headroom={headroom}"
        )
    lines.append("")
    verdict = f"⚠️ 不均 — {tok.skew_reason}" if tok.skew else "✅ 均 (balanced)"
    lines.append(f"**Verdict:** {verdict}")
    return "\n".join(lines)


class _BlankState:
    """Stand-in WatchdogState that always reports ok — for snapshot
    rendering where we want each metric's instantaneous level, not the
    persisted alert level."""

    def level(self, metric: str) -> str:  # noqa: D401
        return LEVEL_OK

    def set_level(self, metric: str, level: str) -> None:
        pass
