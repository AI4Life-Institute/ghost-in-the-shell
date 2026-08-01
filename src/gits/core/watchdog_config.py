"""Watchdog configuration — thresholds, caps, routing.

Loads the resource/token watchdog's tunables from ``~/.gits/config.env``
merged over ``os.environ`` (process env wins). The parsing lives here
rather than in :class:`gits.config.Settings` (pydantic) for the same
reason it does in :mod:`gits.hooks.core_os_ticket`: a PreToolUse hook
must not import pydantic. **Every key read here is still declared in
Settings** — that model validates the same file with ``extra='forbid'``,
so an undeclared key makes every ``Settings()`` raise (see ghost#18).

Per-account token caps are therefore **one key per window**, holding a
``name=value`` list (``GITS_ACCOUNT_5H_CAPS="alice=150000000,bob=..."``)
rather than a family of dynamic per-account keys. An
arbitrary-suffix key cannot be declared in a fixed pydantic model, so the
dynamic form bricked ``Settings()`` for exactly the operator who
configured the feature. The collapsed form also moves the blast radius of
a typo: a malformed list degrades *here*, in this parser, where the
watchdog can carry on — instead of in ``Settings()``, where it takes down
the bot, the hooks and the CLI alike.

All values have conservative defaults so the watchdog is safe out of the
box (task [[jeyuxq]], operator answers 2026-06-01):

* Cap-% thresholds are **inert** until a real cap is configured — an
  unconfigured cap reports ``None`` and the token-cap classifier emits
  no warn/critical (kills false-positives against a guessed cap). The
  balance-**skew** check is cap-independent and always active.
* Placeholder caps are documented round numbers — replace when the real
  per-account 5h/7d caps are known; no code change needed.

Read-only + zero-network: this module only parses local config text.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default alert channel = the efficiency owner's home channel where the
# CEO reads (operator answer Q1, 2026-06-01). Override with
# GITS_WATCHDOG_ALERT_CHANNEL.
_DEFAULT_ALERT_CHANNEL = "1510821666492649503"

# Placeholder caps (cost-weighted token units) — documented round
# numbers, NOT real caps. While a cap stays at 0/unset the cap-% check is
# inert (see module docstring). Suggested starting points from the
# operator: 5h≈150M, 7d≈2B. We keep them at 0 (= unconfigured/inert) by
# default so nothing fires until the operator opts in with real numbers.
_PLACEHOLDER_5H_CAP = 0.0
_PLACEHOLDER_7D_CAP = 0.0


def _parse_config_env(path: Path) -> dict[str, str]:
    """Best-effort parse of a ``KEY=value`` dotenv file. Never raises.

    Ignores blank lines and ``#`` comments, strips an optional ``export``
    prefix and surrounding quotes. Missing/unreadable file → ``{}``.
    """
    out: dict[str, str] = {}
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


@dataclass(frozen=True)
class Thresholds:
    """Warn/critical/clear bands for every resource metric.

    Bands encode hysteresis directly: a metric that rises past ``*_warn``
    only *clears* once it falls back below ``*_clear`` (a dead-band in
    between holds the current level so the alert doesn't flap). For
    "lower is worse" metrics (disk-free, mem-avail) the comparison is
    inverted — see :mod:`gits.core.resource_watch`.
    """

    # swap-used %  (higher is worse)
    swap_warn: float = 80.0
    swap_critical: float = 90.0
    swap_clear: float = 70.0
    # tmux-server fd count vs 256 ceiling (higher is worse)
    tmux_fd_limit: int = 256
    tmux_fd_warn: int = 200
    tmux_fd_critical: int = 235
    tmux_fd_clear: int = 180
    # load avg as multiple of core count (higher is worse)
    load_warn_ratio: float = 1.5
    load_critical_ratio: float = 2.5
    load_clear_ratio: float = 1.2
    # disk-free %  (LOWER is worse)
    disk_warn: float = 8.0
    disk_critical: float = 4.0
    disk_clear: float = 12.0
    # mem-avail MB (LOWER is worse) — reuse HealthMonitor's existing bands
    mem_warn_mb: int = 2048
    mem_critical_mb: int = 1024
    mem_clear_mb: int = 4096
    # token cap-% (higher is worse) — % of each account's configured cap
    token_warn_pct: float = 75.0
    token_critical_pct: float = 90.0
    token_clear_pct: float = 65.0
    # balance skew (cap-independent, always active)
    skew_binding_share: float = 0.60  # one acct > 60% of dispatched bindings
    skew_score_median_mult: float = 2.0  # or score > 2× median


@dataclass(frozen=True)
class WatchdogConfig:
    """Resolved watchdog configuration for one process run."""

    alert_channel: str = _DEFAULT_ALERT_CHANNEL
    owner_mention: str = ""  # e.g. "<@123>"; empty → plain "@weiliu-ghost-dev"
    disk_watch_path: Path = field(default_factory=lambda: Path("~/.gits"))
    digest_hour: int = 9  # local hour-of-day for the daily balance digest
    thresholds: Thresholds = field(default_factory=Thresholds)
    # Per-account caps, keyed by lowercased account name. 0.0 / missing =
    # unconfigured → cap-% check inert for that account.
    caps_5h: dict[str, float] = field(default_factory=dict)
    caps_7d: dict[str, float] = field(default_factory=dict)

    def cap_5h(self, account: str) -> float | None:
        """Configured 5h cap for ``account`` or ``None`` if unset/inert."""
        v = self.caps_5h.get(account.lower())
        return v if (v and v > 0) else None

    def cap_7d(self, account: str) -> float | None:
        """Configured 7d cap for ``account`` or ``None`` if unset/inert."""
        v = self.caps_7d.get(account.lower())
        return v if (v and v > 0) else None


def _to_float(raw: str | None, default: float) -> float:
    try:
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _to_int(raw: str | None, default: int) -> int:
    try:
        return int(float(raw)) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _parse_caps(raw: str | None, default: float) -> dict[str, float]:
    """Parse ``"alice=150000000,bob=2e9"`` into ``{"alice": ..., "bob": ...}``.

    Never raises. A malformed entry is dropped with a warning and the rest
    of the list still applies — a dropped cap is *inert* (the cap-% check
    reports ``None`` for that account), never a false alarm. Degrading here
    is deliberate: this is the failure that used to happen inside
    ``Settings()``, where it took the whole program down instead.
    """
    caps: dict[str, float] = {}
    if not raw:
        return caps
    for chunk in raw.replace(";", ",").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        name, sep, val = entry.partition("=")
        name = name.strip().lower()
        if not sep or not name:
            logger.warning(
                "watchdog: ignoring malformed account-cap entry %r "
                "(expected NAME=VALUE)", entry
            )
            continue
        parsed = _to_float(val.strip(), -1.0)
        if parsed < 0:
            logger.warning(
                "watchdog: ignoring non-numeric cap for account %r: %r", name, val
            )
            continue
        caps[name] = parsed or default
    return caps


def load_watchdog_config(
    config_env_path: Path | None = None,
    *,
    env: dict[str, str] | None = None,
) -> WatchdogConfig:
    """Build :class:`WatchdogConfig` from ``~/.gits/config.env`` + ``os.environ``.

    Process environment wins over the file (standard precedence). Pass
    ``env`` to inject a synthetic environment in tests; pass
    ``config_env_path`` to point at a fixture file.
    """
    path = config_env_path or Path("~/.gits/config.env")
    merged: dict[str, str] = {}
    merged.update(_parse_config_env(path))
    merged.update(env if env is not None else os.environ)

    def g(key: str) -> str | None:
        return merged.get(key)

    th = Thresholds(
        swap_warn=_to_float(g("GITS_SWAP_WARN_PCT"), 80.0),
        swap_critical=_to_float(g("GITS_SWAP_CRITICAL_PCT"), 90.0),
        swap_clear=_to_float(g("GITS_SWAP_CLEAR_PCT"), 70.0),
        tmux_fd_limit=_to_int(g("GITS_TMUX_FD_LIMIT"), 256),
        tmux_fd_warn=_to_int(g("GITS_TMUX_FD_WARN"), 200),
        tmux_fd_critical=_to_int(g("GITS_TMUX_FD_CRITICAL"), 235),
        tmux_fd_clear=_to_int(g("GITS_TMUX_FD_CLEAR"), 180),
        load_warn_ratio=_to_float(g("GITS_LOAD_WARN_RATIO"), 1.5),
        load_critical_ratio=_to_float(g("GITS_LOAD_CRITICAL_RATIO"), 2.5),
        load_clear_ratio=_to_float(g("GITS_LOAD_CLEAR_RATIO"), 1.2),
        disk_warn=_to_float(g("GITS_DISK_WARN_PCT"), 8.0),
        disk_critical=_to_float(g("GITS_DISK_CRITICAL_PCT"), 4.0),
        disk_clear=_to_float(g("GITS_DISK_CLEAR_PCT"), 12.0),
        mem_warn_mb=_to_int(g("GITS_MEM_WARN_MB"), 2048),
        mem_critical_mb=_to_int(g("GITS_MEM_CRITICAL_MB"), 1024),
        mem_clear_mb=_to_int(g("GITS_MEM_CLEAR_MB"), 4096),
        token_warn_pct=_to_float(g("GITS_TOKEN_WARN_PCT"), 75.0),
        token_critical_pct=_to_float(g("GITS_TOKEN_CRITICAL_PCT"), 90.0),
        token_clear_pct=_to_float(g("GITS_TOKEN_CLEAR_PCT"), 65.0),
        skew_binding_share=_to_float(g("GITS_SKEW_BINDING_SHARE"), 0.60),
        skew_score_median_mult=_to_float(g("GITS_SKEW_SCORE_MEDIAN_MULT"), 2.0),
    )

    caps_5h = _parse_caps(g("GITS_ACCOUNT_5H_CAPS"), _PLACEHOLDER_5H_CAP)
    caps_7d = _parse_caps(g("GITS_ACCOUNT_7D_CAPS"), _PLACEHOLDER_7D_CAP)

    disk = g("GITS_DISK_WATCH_PATH")
    return WatchdogConfig(
        alert_channel=g("GITS_WATCHDOG_ALERT_CHANNEL") or _DEFAULT_ALERT_CHANNEL,
        owner_mention=g("GITS_WATCHDOG_OWNER_MENTION") or "",
        disk_watch_path=Path(disk) if disk else Path("~/.gits"),
        digest_hour=_to_int(g("GITS_BALANCE_DIGEST_HOUR"), 9),
        thresholds=th,
        caps_5h=caps_5h,
        caps_7d=caps_7d,
    )
