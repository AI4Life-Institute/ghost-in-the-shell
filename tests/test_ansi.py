"""Tests for ANSI escape sequence parser."""

from gits.utils.ansi import (
    COLORS_16,
    DEFAULT_BG,
    DEFAULT_FG,
    TextStyle,
    color_256,
    parse_ansi_line,
)


class TestColor256:
    def test_standard_16(self):
        for i in range(16):
            assert color_256(i) == COLORS_16[i]

    def test_rgb_cube(self):
        # color 16 = (0,0,0), color 21 = (0,0,255)
        assert color_256(16) == (0, 0, 0)
        assert color_256(21) == (0, 0, 255)
        # color 196 = (255,0,0)
        assert color_256(196) == (255, 0, 0)
        # color 46 = (0,255,0)
        assert color_256(46) == (0, 255, 0)

    def test_greyscale(self):
        assert color_256(232) == (8, 8, 8)
        assert color_256(255) == (238, 238, 238)

    def test_mid_grey(self):
        # 244 = 8 + (244-232)*10 = 128
        assert color_256(244) == (128, 128, 128)


class TestTextStyle:
    def test_defaults(self):
        s = TextStyle()
        assert s.fg == DEFAULT_FG
        assert s.bg == DEFAULT_BG
        assert not s.bold
        assert not s.reverse

    def test_effective_normal(self):
        s = TextStyle(fg=(255, 0, 0), bg=(0, 0, 255))
        assert s.effective_fg() == (255, 0, 0)
        assert s.effective_bg() == (0, 0, 255)

    def test_effective_reverse(self):
        s = TextStyle(fg=(255, 0, 0), bg=(0, 0, 255), reverse=True)
        assert s.effective_fg() == (0, 0, 255)
        assert s.effective_bg() == (255, 0, 0)


class TestParseAnsiLine:
    def test_plain_text(self):
        segments = parse_ansi_line("hello world")
        assert len(segments) == 1
        assert segments[0].text == "hello world"
        assert segments[0].style.fg == DEFAULT_FG

    def test_empty_string(self):
        segments = parse_ansi_line("")
        assert segments == []

    def test_single_color(self):
        segments = parse_ansi_line("\x1b[31mred text\x1b[0m")
        assert len(segments) == 1
        assert segments[0].text == "red text"
        assert segments[0].style.fg == COLORS_16[1]  # red

    def test_color_then_plain(self):
        segments = parse_ansi_line("\x1b[32mgreen\x1b[0m plain")
        assert len(segments) == 2
        assert segments[0].text == "green"
        assert segments[0].style.fg == COLORS_16[2]  # green
        assert segments[1].text == " plain"
        assert segments[1].style.fg == DEFAULT_FG

    def test_bold(self):
        segments = parse_ansi_line("\x1b[1mbold\x1b[0m")
        assert segments[0].style.bold is True

    def test_multiple_sgr_params(self):
        # bold + red in one sequence
        segments = parse_ansi_line("\x1b[1;31mbold red\x1b[0m")
        assert segments[0].style.bold is True
        assert segments[0].style.fg == COLORS_16[1]

    def test_reset(self):
        segments = parse_ansi_line("\x1b[31mred\x1b[0mnormal")
        assert len(segments) == 2
        assert segments[0].style.fg == COLORS_16[1]
        assert segments[1].style.fg == DEFAULT_FG

    def test_bright_colors(self):
        # \x1b[91m = bright red (index 9)
        segments = parse_ansi_line("\x1b[91mbright red\x1b[0m")
        assert segments[0].style.fg == COLORS_16[9]

    def test_background_color(self):
        segments = parse_ansi_line("\x1b[44mblue bg\x1b[0m")
        assert segments[0].style.bg == COLORS_16[4]

    def test_256_color_foreground(self):
        # \x1b[38;5;196m = 256-color red
        segments = parse_ansi_line("\x1b[38;5;196mred256\x1b[0m")
        assert segments[0].style.fg == (255, 0, 0)

    def test_256_color_background(self):
        segments = parse_ansi_line("\x1b[48;5;21mblue bg\x1b[0m")
        assert segments[0].style.bg == (0, 0, 255)

    def test_24bit_truecolor_fg(self):
        segments = parse_ansi_line("\x1b[38;2;100;200;50mtrue\x1b[0m")
        assert segments[0].style.fg == (100, 200, 50)

    def test_24bit_truecolor_bg(self):
        segments = parse_ansi_line("\x1b[48;2;10;20;30mbg\x1b[0m")
        assert segments[0].style.bg == (10, 20, 30)

    def test_reverse_video(self):
        segments = parse_ansi_line("\x1b[7mreversed\x1b[0m")
        assert segments[0].style.reverse is True

    def test_italic_underline(self):
        segments = parse_ansi_line("\x1b[3;4mitalic underline\x1b[0m")
        assert segments[0].style.italic is True
        assert segments[0].style.underline is True

    def test_default_fg_reset(self):
        # \x1b[39m resets foreground to default
        segments = parse_ansi_line("\x1b[31mred\x1b[39mdefault")
        assert segments[0].style.fg == COLORS_16[1]
        assert segments[1].style.fg == DEFAULT_FG

    def test_default_bg_reset(self):
        segments = parse_ansi_line("\x1b[41mred bg\x1b[49mdefault bg")
        assert segments[0].style.bg == COLORS_16[1]
        assert segments[1].style.bg == DEFAULT_BG

    def test_text_before_first_escape(self):
        segments = parse_ansi_line("prefix\x1b[31mred")
        assert len(segments) == 2
        assert segments[0].text == "prefix"
        assert segments[1].text == "red"
        assert segments[1].style.fg == COLORS_16[1]

    def test_empty_sgr_resets(self):
        # \x1b[m is same as \x1b[0m
        segments = parse_ansi_line("\x1b[31mred\x1b[mnormal")
        assert segments[1].style.fg == DEFAULT_FG

    def test_style_independence(self):
        """Segments should have independent style objects."""
        segments = parse_ansi_line("\x1b[31mred\x1b[0mplain")
        assert len(segments) == 2
        segments[0].style.fg = (0, 0, 0)
        # Mutating segment 0 should not affect segment 1
        assert segments[1].style.fg == DEFAULT_FG
