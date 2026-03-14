"""Tests for ScreenshotEngine."""

import asyncio

import pytest

from gits.core.screenshot import ScreenshotEngine, _font_tier, _is_wide_char


class TestFontTier:
    def test_ascii(self):
        assert _font_tier("A") == 0
        assert _font_tier("z") == 0
        assert _font_tier("0") == 0
        assert _font_tier(" ") == 0

    def test_cjk(self):
        assert _font_tier("你") == 1
        assert _font_tier("好") == 1
        assert _font_tier("世") == 1
        # Katakana
        assert _font_tier("ア") == 1
        # Fullwidth Latin
        assert _font_tier("Ａ") == 1

    def test_symbol(self):
        assert _font_tier("→") == 2
        assert _font_tier("•") == 2
        assert _font_tier("─") == 2


class TestIsWideChar:
    def test_ascii_narrow(self):
        assert _is_wide_char("A") is False
        assert _is_wide_char(" ") is False

    def test_cjk_wide(self):
        assert _is_wide_char("你") is True
        assert _is_wide_char("好") is True

    def test_multi_char_string(self):
        assert _is_wide_char("AB") is False

    def test_empty(self):
        assert _is_wide_char("") is False


class TestScreenshotEngine:
    @pytest.fixture
    def engine(self):
        return ScreenshotEngine(font_size=14)  # small for fast tests

    def test_init(self, engine):
        assert engine.font_size == 14
        # At least the mono font should be loaded (or default fallback)
        assert engine.fonts[0] is not None

    def test_char_size(self, engine):
        w, h = engine._char_size()
        assert w > 0
        assert h > 0

    def test_render_plain_text(self, engine):
        png = asyncio.run(engine.capture("Hello World"))
        assert isinstance(png, bytes)
        assert len(png) > 0
        # Check PNG magic bytes
        assert png[:4] == b"\x89PNG"

    def test_render_ansi_colored(self, engine):
        ansi = "\x1b[31mRed\x1b[0m \x1b[32mGreen\x1b[0m"
        png = asyncio.run(engine.capture(ansi))
        assert png[:4] == b"\x89PNG"

    def test_render_multiline(self, engine):
        text = "line1\nline2\nline3"
        png = asyncio.run(engine.capture(text))
        assert png[:4] == b"\x89PNG"

    def test_render_empty(self, engine):
        png = asyncio.run(engine.capture(""))
        assert png[:4] == b"\x89PNG"

    def test_render_cjk(self, engine):
        png = asyncio.run(engine.capture("你好世界 Hello"))
        assert png[:4] == b"\x89PNG"

    def test_render_256_color(self, engine):
        ansi = "\x1b[38;5;196mBright Red\x1b[0m"
        png = asyncio.run(engine.capture(ansi))
        assert png[:4] == b"\x89PNG"

    def test_different_renders_different_sizes(self, engine):
        short = asyncio.run(engine.capture("Hi"))
        long = asyncio.run(engine.capture("A" * 200))
        # Longer text should produce larger image
        assert len(long) > len(short)
