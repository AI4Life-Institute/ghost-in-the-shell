"""Tests for QuotaPatternMatcher."""

import time

import pytest

from gits.core.quota import (
    DEFAULT_PATTERNS,
    QuotaCategory,
    QuotaMatch,
    QuotaPatternMatcher,
    parse_reset_timestamp,
)


@pytest.fixture
def matcher_default():
    m = QuotaPatternMatcher()
    m.load()
    return m


@pytest.fixture
def yaml_patterns(tmp_path):
    """Custom patterns file with reset capture."""
    p = tmp_path / "quota_patterns.yaml"
    p.write_text(
        """
hard_limit:
  - regex: "5-hour limit reached. Resumes at (?P<reset>[0-9T:+\\\\-Z]+)"
  - regex: "weekly limit"
soft_warning:
  - regex: "you are nearing the limit"
ignore:
  - regex: rate_limit_tier
"""
    )
    return p


class TestLoading:
    def test_loads_defaults_when_no_file(self):
        m = QuotaPatternMatcher()
        m.load()
        assert m.loaded_from_defaults is True
        assert m.patterns_summary["hard_limit"]  # not empty

    def test_loads_defaults_when_file_missing(self, tmp_path):
        m = QuotaPatternMatcher(tmp_path / "missing.yaml")
        m.load()
        assert m.loaded_from_defaults is True

    def test_loads_custom_yaml(self, yaml_patterns):
        m = QuotaPatternMatcher(yaml_patterns)
        m.load()
        assert m.loaded_from_defaults is False
        assert any("5-hour" in r for r in m.patterns_summary["hard_limit"])

    def test_falls_back_on_yaml_parse_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: valid: yaml: [")
        m = QuotaPatternMatcher(bad)
        m.load()
        # On yaml error we fall back to defaults (loaded_from_defaults toggles)
        assert m.loaded_from_defaults is True

    def test_invalid_regex_is_skipped_not_fatal(self, tmp_path):
        p = tmp_path / "patterns.yaml"
        p.write_text(
            """
hard_limit:
  - regex: "[invalid("
  - regex: "valid pattern"
"""
        )
        m = QuotaPatternMatcher(p)
        m.load()
        assert any("valid pattern" in r for r in m.patterns_summary["hard_limit"])

    def test_hot_reload_on_mtime_change(self, tmp_path):
        p = tmp_path / "patterns.yaml"
        p.write_text(
            """
hard_limit:
  - regex: "v1 pattern"
"""
        )
        m = QuotaPatternMatcher(p)
        m.load()
        assert any("v1" in r for r in m.patterns_summary["hard_limit"])

        time.sleep(1.1)  # ensure mtime changes (1s resolution on some FS)
        p.write_text(
            """
hard_limit:
  - regex: "v2 pattern"
"""
        )
        m.maybe_reload()
        assert any("v2" in r for r in m.patterns_summary["hard_limit"])
        assert not any("v1" in r for r in m.patterns_summary["hard_limit"])


class TestClassify:
    def test_empty_text(self, matcher_default):
        result = matcher_default.classify("")
        assert result.category == QuotaCategory.NONE

    def test_no_match(self, matcher_default):
        result = matcher_default.classify("just normal output")
        assert result.category == QuotaCategory.NONE

    def test_hard_limit_jsonl_format(self, matcher_default):
        text = '{"type": "rate_limit_error", "message": "..."}'
        result = matcher_default.classify(text)
        assert result.category == QuotaCategory.HARD_LIMIT

    def test_ignore_wins_over_hard(self, matcher_default):
        # The default `rateLimitTier` ignore pattern matches; even if a hard
        # pattern would also match, ignore must come first.
        text = '{"rateLimitTier": "standard", "type": "rate_limit_error"}'
        result = matcher_default.classify(text)
        assert result.category == QuotaCategory.IGNORE

    def test_hard_with_reset_capture(self, yaml_patterns):
        m = QuotaPatternMatcher(yaml_patterns)
        m.load()
        text = "5-hour limit reached. Resumes at 2026-04-27T15:00:00+00:00"
        result = m.classify(text)
        assert result.category == QuotaCategory.HARD_LIMIT
        assert result.reset_at is not None
        assert result.has_reset is True

    def test_hard_without_reset_capture(self, yaml_patterns):
        m = QuotaPatternMatcher(yaml_patterns)
        m.load()
        # 'weekly limit' has no reset group
        result = m.classify("weekly limit reached for your plan")
        assert result.category == QuotaCategory.HARD_LIMIT
        assert result.reset_at is None
        assert result.has_reset is False

    def test_soft_warning(self, yaml_patterns):
        m = QuotaPatternMatcher(yaml_patterns)
        m.load()
        result = m.classify("you are nearing the limit, careful")
        assert result.category == QuotaCategory.SOFT_WARNING


class TestSignalDebouncer:
    @pytest.fixture
    def debouncer(self):
        from gits.core.quota import QuotaSignalDebouncer

        m = QuotaPatternMatcher()
        m.load()
        return QuotaSignalDebouncer(m)

    def test_single_match_does_not_fire(self, debouncer):
        ev = debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        assert ev is None

    def test_two_matches_within_window_fire(self, debouncer):
        text = '{"type": "rate_limit_error"}'
        assert debouncer.feed("ch-1", text) is None
        ev = debouncer.feed("ch-1", text)
        assert ev is not None
        assert ev.channel_id == "ch-1"
        assert ev.match.is_hard_limit

    def test_two_matches_outside_window_do_not_fire(self, debouncer):
        text = '{"type": "rate_limit_error"}'
        debouncer.escalate_window = 0.1
        assert debouncer.feed("ch-1", text) is None
        time.sleep(0.2)
        ev = debouncer.feed("ch-1", text)
        # Second match starts a new pending; no escalation.
        assert ev is None

    def test_reverse_signal_clears_pending(self, debouncer):
        debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        debouncer.feed("ch-1", "connection reset by peer")
        # Now a fresh first match should NOT fire (it's not the second of a pair)
        ev = debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        assert ev is None

    def test_reverse_outside_window_does_not_clear(self, debouncer):
        debouncer.reverse_window = 0.05
        debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        time.sleep(0.1)
        # Reverse arrives too late — pending is preserved
        debouncer.feed("ch-1", "connection reset by peer")
        ev = debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        assert ev is not None  # the original pending is still alive

    def test_per_binding_state(self, debouncer):
        debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        # Different channel — single match still doesn't fire
        ev = debouncer.feed("ch-2", '{"type": "rate_limit_error"}')
        assert ev is None
        # Two for ch-1 fire
        ev = debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        assert ev is not None
        assert ev.channel_id == "ch-1"

    def test_ignore_category_does_not_pollute_state(self, debouncer):
        # First a real match
        debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        # Then an ignore-category line — should not consume the pending nor fire
        ev = debouncer.feed("ch-1", '{"rateLimitTier": "standard"}')
        assert ev is None
        # Real second match still fires
        ev = debouncer.feed("ch-1", '{"type": "rate_limit_error"}')
        assert ev is not None


class TestParseResetTimestamp:
    def test_iso_with_z(self):
        v = parse_reset_timestamp("2026-04-27T15:00:00Z")
        assert v is not None
        assert v > 1_700_000_000

    def test_iso_with_offset(self):
        v = parse_reset_timestamp("2026-04-27T15:00:00+00:00")
        assert v is not None

    def test_epoch_string(self):
        v = parse_reset_timestamp("1735689600")
        assert v == 1735689600.0

    def test_unparseable(self):
        assert parse_reset_timestamp("tomorrow") is None
        assert parse_reset_timestamp("") is None
