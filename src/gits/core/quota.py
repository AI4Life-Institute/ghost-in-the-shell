"""QuotaPatternMatcher — classify CLI output as quota-exhaustion signals.

.. deprecated:: 0.3
    The primary quota signal is now ``gits.core.account_load`` —
    cost-weighted local-JSONL scanning that powers
    ``ghost butler dispatch --account=auto`` and ``ghost account list``.
    Passive output pattern matching survives as a defensive fallback for
    edge cases the JSONL scanner can't see (CLI emits a rate-limit line
    before any usage record lands). See openspec change
    ``add-multi-account-hotswap``.

Loads regex patterns from ``~/.gits/quota_patterns.yaml`` (with sensible
defaults if the file is missing) and exposes a synchronous ``classify`` method
called by both ``JsonlMonitor`` and ``PaneMonitor``.

A `hard_limit` match without a parseable reset time is reported as
``QuotaMatch(reset_at=None)``; downstream consumers (``QuotaNotifier``)
notify users without persisting a rate-limit deadline. Patterns SHOULD
include a ``(?P<reset>...)`` named capture group emitting an ISO-8601 /
RFC-3339 timestamp or epoch seconds.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class QuotaCategory(str, Enum):
    HARD_LIMIT = "hard_limit"
    SOFT_WARNING = "soft_warning"
    IGNORE = "ignore"
    NONE = "none"


# Built-in defaults shipped with ghost. The user can override by writing to
# ``~/.gits/quota_patterns.yaml`` — these are guesses (real patterns to be
# refined during the P0-2 spike with live samples).
DEFAULT_PATTERNS: dict[str, list[dict[str, str]]] = {
    "hard_limit": [
        # Common rate-limit messages. The reset capture variants try epoch and
        # ISO-8601 forms in sequence — the matcher tries each in order.
        {"regex": r"rate[ _-]?limit (?:reached|exceeded).*?reset(?:s|ting)?\s+at\s+(?P<reset>[\d:T+\-]{16,})"},
        {"regex": r"5[ -]?hour limit.*?(?:resumes|resets?)\s+at\s+(?P<reset>[\d:T+\-]{16,})"},
        {"regex": r"weekly limit.*?(?:resumes|resets?)\s+at\s+(?P<reset>[\d:T+\-]{16,})"},
        {"regex": r'"type"\s*:\s*"rate_limit_error"'},
    ],
    "soft_warning": [
        {"regex": r"approaching (?:rate|usage) limit"},
    ],
    "ignore": [
        # Exact-string field names that contain "rate_limit" but are not signals.
        {"regex": r'"rateLimitTier"'},
        {"regex": r"rate_limit_tier"},
    ],
}


@dataclass(frozen=True)
class QuotaMatch:
    """Result of classifying a single line/JSONL entry."""

    category: QuotaCategory
    matched_text: str = ""
    pattern_index: int = -1
    reset_at: float | None = None  # epoch seconds; None means unparseable

    @property
    def is_hard_limit(self) -> bool:
        return self.category == QuotaCategory.HARD_LIMIT

    @property
    def has_reset(self) -> bool:
        return self.reset_at is not None


@dataclass
class _CompiledPattern:
    raw: str
    regex: re.Pattern
    has_reset_group: bool


@dataclass
class QuotaPatternMatcher:
    """Classify text against the configured quota patterns.

    Construct with a path to a YAML file; call ``load()`` to compile patterns.
    The matcher will reload patterns when the file's mtime changes.
    """

    path: Path | None = None
    _hard: list[_CompiledPattern] = field(default_factory=list)
    _soft: list[_CompiledPattern] = field(default_factory=list)
    _ignore: list[_CompiledPattern] = field(default_factory=list)
    _last_mtime: float = 0.0
    _loaded_defaults: bool = False

    def load(self) -> None:
        """Load and compile patterns. Falls back to defaults if file missing."""
        spec = DEFAULT_PATTERNS
        if self.path is not None and self.path.exists():
            try:
                import yaml

                with open(self.path) as f:
                    raw = yaml.safe_load(f) or {}
                if isinstance(raw, dict):
                    spec = {k: raw.get(k, []) for k in ("hard_limit", "soft_warning", "ignore")}
                    if not any(spec.values()):
                        spec = DEFAULT_PATTERNS
                self._last_mtime = self.path.stat().st_mtime
                self._loaded_defaults = False
            except Exception as e:
                logger.error("Failed to read %s: %s; using defaults", self.path, e)
                spec = DEFAULT_PATTERNS
                self._loaded_defaults = True
        else:
            self._loaded_defaults = True
            if self.path is not None:
                logger.info("%s missing; using built-in default patterns", self.path)

        self._hard = self._compile(spec.get("hard_limit", []))
        self._soft = self._compile(spec.get("soft_warning", []))
        self._ignore = self._compile(spec.get("ignore", []))

    def maybe_reload(self) -> None:
        """Reload patterns if the source file has been modified."""
        if self.path is None or not self.path.exists():
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._last_mtime:
            logger.info("%s changed; reloading patterns", self.path)
            self.load()

    def classify(self, text: str) -> QuotaMatch:
        """Classify *text* into a category. Empty / non-matching → category=NONE.

        Order of checks: ignore wins over everything (returns IGNORE), then
        hard_limit, then soft_warning. Inside each tier the first match wins.
        """
        if not text:
            return QuotaMatch(category=QuotaCategory.NONE)

        for pat in self._ignore:
            if pat.regex.search(text):
                return QuotaMatch(
                    category=QuotaCategory.IGNORE, matched_text=text[:200]
                )

        for i, pat in enumerate(self._hard):
            m = pat.regex.search(text)
            if m:
                reset = self._extract_reset(m)
                return QuotaMatch(
                    category=QuotaCategory.HARD_LIMIT,
                    matched_text=m.group(0)[:200],
                    pattern_index=i,
                    reset_at=reset,
                )

        for i, pat in enumerate(self._soft):
            m = pat.regex.search(text)
            if m:
                return QuotaMatch(
                    category=QuotaCategory.SOFT_WARNING,
                    matched_text=m.group(0)[:200],
                    pattern_index=i,
                )

        return QuotaMatch(category=QuotaCategory.NONE)

    @property
    def loaded_from_defaults(self) -> bool:
        return self._loaded_defaults

    @property
    def patterns_summary(self) -> dict:
        return {
            "hard_limit": [p.raw for p in self._hard],
            "soft_warning": [p.raw for p in self._soft],
            "ignore": [p.raw for p in self._ignore],
        }

    # ── internals ────────────────────────────────────────────────────
    def _compile(self, raw_list: Iterable[dict]) -> list[_CompiledPattern]:
        out: list[_CompiledPattern] = []
        for entry in raw_list or []:
            rgx = entry.get("regex") if isinstance(entry, dict) else None
            if not rgx:
                continue
            try:
                compiled = re.compile(rgx, re.IGNORECASE | re.DOTALL)
            except re.error as e:
                logger.error("Invalid regex %r: %s; skipping", rgx, e)
                continue
            has_reset = "(?P<reset>" in rgx
            out.append(_CompiledPattern(raw=rgx, regex=compiled, has_reset_group=has_reset))
        return out

    def _extract_reset(self, match: re.Match) -> float | None:
        try:
            raw = match.group("reset")
        except (IndexError, KeyError):
            return None
        if not raw:
            return None
        return parse_reset_timestamp(raw)


# ─────────────────────────────────────────────────────────────────────
# Debouncer
# ─────────────────────────────────────────────────────────────────────

# Patterns that indicate a hard_limit hit was a false alarm — the API call
# was cancelled or interrupted, not actually rate-limited. If any of these
# arrives within ``reverse_window`` seconds of a hard_limit match, the match
# is dropped.
REVERSE_SIGNAL_REGEXES = [
    re.compile(r"cancelled by user", re.IGNORECASE),
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"keyboard interrupt", re.IGNORECASE),
    re.compile(r"^\s*\^C\s*$", re.MULTILINE),
]


@dataclass
class QuotaExhaustedEvent:
    channel_id: str
    match: QuotaMatch


class QuotaSignalDebouncer:
    """Per-binding debouncer for ``QuotaPatternMatcher`` results.

    Per the spec:

    * A single ``hard_limit`` match is held as 'pending' for ``escalate_window``
      seconds; if no second match arrives, no event fires.
    * If a reverse signal (``cancelled by user`` / ``connection reset`` / etc.)
      arrives within ``reverse_window`` seconds of a pending match, the match
      is silently dropped.
    * A second ``hard_limit`` match within ``escalate_window`` seconds of the
      first immediately fires a ``QuotaExhaustedEvent``.
    """

    def __init__(
        self,
        matcher: QuotaPatternMatcher,
        *,
        escalate_window: float = 2.0,
        reverse_window: float = 0.2,
    ):
        self.matcher = matcher
        self.escalate_window = escalate_window
        self.reverse_window = reverse_window
        # channel_id -> (timestamp, QuotaMatch)
        self._pending: dict[str, tuple[float, QuotaMatch]] = {}

    def feed(self, channel_id: str, text: str) -> QuotaExhaustedEvent | None:
        """Classify *text* and update debounce state. Returns event on escalation."""
        if not text:
            return None

        # Reverse signal? Drop a recent pending match.
        if self._is_reverse(text):
            pending = self._pending.get(channel_id)
            if pending is not None:
                t0, _ = pending
                if time.time() - t0 <= self.reverse_window:
                    self._pending.pop(channel_id, None)
                    logger.debug(
                        "QuotaSignalDebouncer: reverse signal cleared pending match for %s",
                        channel_id,
                    )
            return None

        result = self.matcher.classify(text)
        if result.category == QuotaCategory.IGNORE:
            return None
        if result.category != QuotaCategory.HARD_LIMIT:
            return None

        now = time.time()
        existing = self._pending.get(channel_id)
        if existing is not None:
            t0, _ = existing
            if now - t0 <= self.escalate_window:
                self._pending.pop(channel_id, None)
                logger.info(
                    "QuotaSignalDebouncer: escalating quota_exhausted for %s",
                    channel_id,
                )
                return QuotaExhaustedEvent(channel_id=channel_id, match=result)
            # else: pending expired; fall through and treat current as new first

        self._pending[channel_id] = (now, result)
        return None

    def _is_reverse(self, text: str) -> bool:
        for r in REVERSE_SIGNAL_REGEXES:
            if r.search(text):
                return True
        return False

    def reset(self, channel_id: str) -> None:
        self._pending.pop(channel_id, None)


def parse_reset_timestamp(raw: str) -> float | None:
    """Parse a reset timestamp string into epoch seconds.

    Accepts:
        * Epoch integer / float (e.g. ``"1735689600"``)
        * ISO-8601 / RFC-3339 (e.g. ``"2026-04-27T15:00:00Z"``, ``"2026-04-27T15:00:00+00:00"``)
        * ISO-8601 without timezone (assumes local time)
    Returns None if all parsers fail.
    """
    raw = raw.strip().rstrip(".,;")
    # Epoch
    try:
        v = float(raw)
        if v > 1_000_000_000:  # plausible epoch (≥ 2001)
            return v
    except ValueError:
        pass
    # ISO-8601 — Python's fromisoformat handles most variants from 3.11+
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    for c in candidates:
        try:
            dt = datetime.fromisoformat(c)
            return dt.timestamp()
        except ValueError:
            continue
    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(raw, fmt))
        except ValueError:
            continue
    return None
