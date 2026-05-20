"""Local-JSONL account usage scanner + load-balanced picker.

Scans ``~/.claude-<name>/projects/**/*.jsonl`` for ``assistant`` events
whose ``timestamp`` falls inside a rolling window and sums cost-weighted
tokens to produce a per-account "load" number. Used by
``ghost butler dispatch`` to spread task dispatches across accounts so a
single one doesn't absorb all load and hit its 5h/7d caps. Also surfaces
the same ranking to ``ghost account list`` / Discord ``/accounts`` so
the operator sees exactly what ``--account=auto`` would pick right now.

Locked operator decisions (PoC 2026-05-19; see task [[gbraq8]],
broadened by [[4h3v7j]] to ban all OAuth Usage API calls from ghost):

* **Zero network.** No programmatic Anthropic Usage API calls from
  anywhere in ghost — ban-risk concern. This module is JSONL-only.
* **JSONL-only blindspot.** Usage from other machines or claude.ai web
  is invisible. Accepted: dispatched CLI sessions run on the
  orchestrator machine, so local JSONL is authoritative for the
  workload that matters here.
* **Capacity normalization.** Accounts on different plans (Max 20x vs
  Team 6x) are not directly comparable; ``utilization = load / weight``.
  Operator maintains ``weight`` via ``gits account set-weight``.
* **Pricing weights are approximate.** ``load = input + 5*output +
  1.25*cache_creation + 0.1*cache_read`` mirrors Sonnet 3:1 input:output
  ratios and cache-discount ratios; Opus-heavy accounts therefore look
  cheaper than they actually are. ``weight`` is the lever the operator
  turns when this matters in practice.
* **Credential gate.** ``pick_account()`` skips any account with no
  resolvable credential. A stale/expired token is fine because the
  claude CLI refreshes on launch; only a fully-absent credential
  disqualifies, so we never route a task to an account claude can't
  launch. For each account we accept any of: a readable
  ``~/.claude-<name>/.credentials.json``; for the manifest default,
  a readable native ``~/.claude/.credentials.json``; or a macOS
  keychain entry under either ``Claude Code-credentials`` (default
  account's no-suffix service) or
  ``Claude Code-credentials-<sha256(config_dir)[:8]>`` (the
  suffix-keyed service used by isolated ``CLAUDE_CONFIG_DIR`` accounts).
  We check entry *existence* only — never read the secret.
* **No mid-task rebalancing.** A binding's account is sticky once
  chosen; ``gits account switch`` is the operator-driven path.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .account import AccountLayout
from .account_vault import AccountEntry, AccountVault

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Constants — operator-locked
# ─────────────────────────────────────────────────────────────────────

DEFAULT_WEIGHT = 1.0
WINDOW_5H = 5 * 3600
WINDOW_7D = 7 * 86400

# Cost-weighted token coefficients. Mirror Claude Sonnet pricing ratios
# (input:output = 1:5) and cache discount/premium ratios (cache_create
# = 1.25× input price; cache_read = 0.1× input price). Approximate for
# Opus — see module docstring.
W_INPUT = 1.0
W_OUTPUT = 5.0
W_CACHE_CREATE = 1.25
W_CACHE_READ = 0.1

_KEYCHAIN_DEFAULT_SERVICE = "Claude Code-credentials"
_USAGE_MARKER = b'"usage"'  # cheap pre-filter before json.loads


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def effective_weight(entry: AccountEntry) -> float:
    """Return ``entry.weight`` if set & positive, else :data:`DEFAULT_WEIGHT`."""
    w = getattr(entry, "weight", None)
    if isinstance(w, (int, float)) and w > 0:
        return float(w)
    return DEFAULT_WEIGHT


def _parse_iso_ts(ts: str) -> float | None:
    """Parse an ISO 8601 timestamp into an epoch float. None on failure.

    Tolerates the trailing ``Z`` form claude emits as well as offset forms.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return _dt.datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return None


def _keychain_service_candidates(
    name: str, *, default: str | None, layout: AccountLayout,
) -> list[str]:
    """Return keychain service names to check for ``name``, in priority order.

    * The **default** account routes through native ``~/.claude/`` → claude
      uses the no-suffix service ``Claude Code-credentials``. We still
      include the suffix-keyed name as a fallback in case the account was
      previously isolated.
    * **Isolated** accounts (``~/.claude-<name>/``) → claude uses
      ``Claude Code-credentials-<sha256(config_dir)[:8]>``. We include the
      no-suffix name as a fallback so we surface a live token wherever it
      lives.

    Every isolated account on a real machine stores its token under the
    suffix-keyed service, and a default-only keychain check would skip
    every isolated account whose credential is keychain-only (no
    ``.credentials.json`` file).
    """
    config_dir = str(layout.account_dir(name))
    suffix = hashlib.sha256(config_dir.encode()).hexdigest()[:8]
    suffix_svc = f"Claude Code-credentials-{suffix}"
    default_svc = _KEYCHAIN_DEFAULT_SERVICE
    if name == default:
        return [default_svc, suffix_svc]
    return [suffix_svc, default_svc]


def _macos_keychain_entry_exists(service: str) -> bool:
    """Best-effort: does ``security find-generic-password -s <service>`` find one?

    Existence check only — we deliberately omit ``-w`` so the secret is
    never dumped to stdout. Non-darwin or any subprocess failure returns
    False; caller falls back to file checks.
    """
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service],
            capture_output=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("keychain existence check failed: %s", e)
        return False
    return result.returncode == 0


def _has_credential(
    name: str, *, default: str | None, layout: AccountLayout,
) -> bool:
    """Return True iff ``name``'s claude credentials are resolvable.

    A stale token is fine (claude CLI refreshes on launch); only a
    fully-absent credential disqualifies. Per-account checks, in order:

    1. Isolated ``~/.claude-<name>/.credentials.json`` readable.
    2. If ``name == default``: native ``~/.claude/.credentials.json``
       readable.
    3. macOS keychain entry exists under any candidate service for this
       account (default-routed → no-suffix ``Claude Code-credentials``
       preferred; isolated → suffix-keyed
       ``Claude Code-credentials-<sha256(config_dir)[:8]>`` preferred).

    The keychain check runs for **every** account, not just the default
    — every isolated account on a real machine stores its token under
    the suffix-keyed service, and on hosts where claude never wrote a
    ``.credentials.json`` (default install path on darwin) the keychain
    is the only credential.
    """
    try:
        iso = layout.credentials_file(name)
        if iso.is_file() and os.access(iso, os.R_OK):
            return True
    except OSError:
        pass
    if name == default:
        try:
            native = layout.legacy_claude_dir() / ".credentials.json"
            if native.is_file() and os.access(native, os.R_OK):
                return True
        except OSError:
            pass
    for service in _keychain_service_candidates(
        name, default=default, layout=layout,
    ):
        if _macos_keychain_entry_exists(service):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────
# Usage scan
# ─────────────────────────────────────────────────────────────────────


def _iter_jsonl_files(
    name: str, *, cutoff_epoch: float, layout: AccountLayout,
) -> list[Path]:
    """Mtime-prefiltered list of transcript files for ``name``.

    The mtime check is the dominant cost saver per the PoC: 2415 files
    drop to ~635 in a 7-day window, and only those are opened.
    """
    projects = layout.projects_dir(name)
    try:
        if not projects.exists():
            return []
    except OSError:
        return []
    survivors: list[Path] = []
    try:
        for sub in projects.iterdir():
            if not sub.is_dir():
                continue
            try:
                for f in sub.iterdir():
                    if f.suffix != ".jsonl":
                        continue
                    try:
                        if f.stat().st_mtime >= cutoff_epoch:
                            survivors.append(f)
                    except OSError:
                        continue
            except OSError:
                continue
    except OSError:
        return survivors
    return survivors


def _scan_loads(
    name: str,
    *,
    windows: tuple[float, ...],
    now: float,
    layout: AccountLayout,
) -> tuple[float, ...]:
    """Sum cost-weighted load for each window (each is a cutoff epoch).

    A single pass — line-by-line, cheap-reject lines without ``"usage"``
    before ``json.loads``. Every parse error skipped silently. ``windows``
    is a tuple of cutoff epochs (oldest acceptable). The widest window
    is used for file-level mtime prefiltering.
    """
    if not windows:
        return ()
    cutoff_widest = min(windows)
    totals = [0.0] * len(windows)
    for path in _iter_jsonl_files(name, cutoff_epoch=cutoff_widest, layout=layout):
        try:
            f = open(path, "rb")
        except OSError:
            continue
        with f:
            for raw in f:
                if _USAGE_MARKER not in raw:
                    continue
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(event, dict) or event.get("type") != "assistant":
                    continue
                ts_epoch = _parse_iso_ts(event.get("timestamp"))
                if ts_epoch is None or ts_epoch > now:
                    continue
                msg = event.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                try:
                    delta = (
                        W_INPUT * float(usage.get("input_tokens") or 0)
                        + W_OUTPUT * float(usage.get("output_tokens") or 0)
                        + W_CACHE_CREATE
                        * float(usage.get("cache_creation_input_tokens") or 0)
                        + W_CACHE_READ
                        * float(usage.get("cache_read_input_tokens") or 0)
                    )
                except (TypeError, ValueError):
                    continue
                for i, cutoff in enumerate(windows):
                    if ts_epoch >= cutoff:
                        totals[i] += delta
    return tuple(totals)


def account_load(
    account_name: str,
    window_seconds: int,
    *,
    now: float | None = None,
    layout: AccountLayout | None = None,
) -> float:
    """Cost-weighted token load for ``account_name`` over the trailing window.

    Returns ``0.0`` for a missing/empty transcript dir — an account with
    no recorded activity is idle, and should be a top candidate.
    """
    layout = layout or AccountLayout()
    now = now if now is not None else _dt.datetime.now(_dt.UTC).timestamp()
    cutoff = now - max(window_seconds, 0)
    (total,) = _scan_loads(
        account_name, windows=(cutoff,), now=now, layout=layout,
    )
    return total


def account_load_dual(
    account_name: str,
    short_seconds: int,
    long_seconds: int,
    *,
    now: float | None = None,
    layout: AccountLayout | None = None,
) -> tuple[float, float]:
    """One-pass variant of :func:`account_load`. Returns ``(short, long)``."""
    layout = layout or AccountLayout()
    now = now if now is not None else _dt.datetime.now(_dt.UTC).timestamp()
    cutoff_short = now - max(short_seconds, 0)
    cutoff_long = now - max(long_seconds, 0)
    short, long = _scan_loads(
        account_name,
        windows=(cutoff_short, cutoff_long),
        now=now,
        layout=layout,
    )
    return short, long


# ─────────────────────────────────────────────────────────────────────
# Ranking + picker
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AccountRank:
    """One row in the dispatcher's ranked view of available accounts.

    ``rank`` is ``None`` when the account fails the credential gate —
    such rows are still emitted (so the UI can show them as ``—``) but
    are excluded from the picker. Eligible rows carry ``rank=1, 2, …``
    in score-asc order (same tiebreak rules as :func:`pick_account`).
    """

    name: str
    tier: str | None
    weight: float
    load_5h: float
    load_7d: float
    score: float
    rank: int | None
    bindings: int
    last_used: str


def rank_accounts(
    vault: AccountVault,
    *,
    live_binding_counts: Mapping[str, int] | None = None,
    layout: AccountLayout | None = None,
    now: float | None = None,
) -> list[AccountRank]:
    """Return the full ranked account table the dispatcher would consult.

    Every account in the manifest appears in the result, including ones
    the credential gate excludes (those have ``rank=None``). Eligible
    rows are ranked ``1, 2, …`` by score ascending, tiebreaking on live
    bindings, then ``last_used``, then name — identical to
    :func:`pick_account`. Result ordering: eligible rows first (by
    rank), then credential-gated rows by name.

    Returns ``[]`` if the vault isn't initialized or the manifest can't
    be loaded. Single-account manifests return a 1-row table (whereas
    :func:`pick_account` returns ``None`` — single-account installs
    fall through to ``manifest.default`` at the dispatch layer).
    """
    if not vault.is_initialized():
        return []
    try:
        manifest = vault.load()
    except Exception:
        return []
    accounts = list(manifest.accounts)
    if not accounts:
        return []

    layout = layout or AccountLayout()
    default = manifest.default
    binding_counts: Mapping[str, int] = live_binding_counts or {}

    eligible: list[tuple[float, int, str, str, AccountEntry, float, float]] = []
    gated: list[AccountRank] = []
    for entry in accounts:
        load_5h, load_7d = account_load_dual(
            entry.name, WINDOW_5H, WINDOW_7D, now=now, layout=layout,
        )
        weight = effective_weight(entry)
        score = (load_5h + load_7d) / weight
        bindings = binding_counts.get(entry.name, 0)
        last_used = entry.last_used or ""
        if _has_credential(entry.name, default=default, layout=layout):
            eligible.append((score, bindings, last_used, entry.name, entry, load_5h, load_7d))
        else:
            gated.append(AccountRank(
                name=entry.name,
                tier=entry.subscription_type,
                weight=weight,
                load_5h=load_5h,
                load_7d=load_7d,
                score=score,
                rank=None,
                bindings=bindings,
                last_used=last_used,
            ))

    eligible.sort()
    ranked: list[AccountRank] = []
    for i, (score, bindings, last_used, _name, entry, l5, l7) in enumerate(eligible, start=1):
        ranked.append(AccountRank(
            name=entry.name,
            tier=entry.subscription_type,
            weight=effective_weight(entry),
            load_5h=l5,
            load_7d=l7,
            score=score,
            rank=i,
            bindings=bindings,
            last_used=last_used,
        ))
    gated.sort(key=lambda r: r.name)
    return ranked + gated


def pick_account(
    vault: AccountVault,
    *,
    live_binding_counts: Mapping[str, int] | None = None,
    layout: AccountLayout | None = None,
    now: float | None = None,
) -> str | None:
    """Pick the lowest-utilization account with launchable credentials.

    Thin wrapper over :func:`rank_accounts`. Returns ``None`` when:

    * the multi-account vault isn't initialized,
    * the manifest has 0 or 1 account (single-account install — caller
      keeps legacy behavior),
    * or every account fails the credential gate.
    """
    if not vault.is_initialized():
        return None
    try:
        manifest = vault.load()
    except Exception:
        return None
    if len(manifest.accounts) <= 1:
        return None
    ranked = rank_accounts(
        vault,
        live_binding_counts=live_binding_counts,
        layout=layout,
        now=now,
    )
    for row in ranked:
        if row.rank == 1:
            return row.name
    return None
