"""ANSI escape sequence parser for terminal output.

Parses SGR (Select Graphic Rendition) sequences and produces
styled text segments for rendering by ScreenshotEngine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ANSI SGR pattern
ANSI_ESCAPE_RE = re.compile(r"\x1b\[([0-9;]*)m")

# Standard 16 colours (0-7 normal, 8-15 bright)
COLORS_16: list[tuple[int, int, int]] = [
    (0, 0, 0),        # 0: black
    (187, 0, 0),      # 1: red
    (0, 187, 0),      # 2: green
    (187, 187, 0),    # 3: yellow
    (0, 0, 187),      # 4: blue
    (187, 0, 187),    # 5: magenta
    (0, 187, 187),    # 6: cyan
    (187, 187, 187),  # 7: white
    (85, 85, 85),     # 8: bright black
    (255, 85, 85),    # 9: bright red
    (85, 255, 85),    # 10: bright green
    (255, 255, 85),   # 11: bright yellow
    (85, 85, 255),    # 12: bright blue
    (255, 85, 255),   # 13: bright magenta
    (85, 255, 255),   # 14: bright cyan
    (255, 255, 255),  # 15: bright white
]

DEFAULT_FG = (212, 212, 212)
DEFAULT_BG = (30, 30, 30)


def color_256(n: int) -> tuple[int, int, int]:
    """Convert 256-colour index to RGB."""
    if n < 16:
        return COLORS_16[n]
    if n < 232:
        # 6×6×6 RGB cube
        n -= 16
        b = (n % 6) * 51
        n //= 6
        g = (n % 6) * 51
        r = (n // 6) * 51
        return (r, g, b)
    # Greyscale: 232-255
    v = 8 + (n - 232) * 10
    return (v, v, v)


@dataclass
class TextStyle:
    """Current text rendering style."""

    fg: tuple[int, int, int] = field(default_factory=lambda: DEFAULT_FG)
    bg: tuple[int, int, int] = field(default_factory=lambda: DEFAULT_BG)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    reverse: bool = False

    def effective_fg(self) -> tuple[int, int, int]:
        return self.bg if self.reverse else self.fg

    def effective_bg(self) -> tuple[int, int, int]:
        return self.fg if self.reverse else self.bg


@dataclass
class StyledSegment:
    """A segment of text with a specific style."""

    text: str
    style: TextStyle


def _copy_style(s: TextStyle) -> TextStyle:
    return TextStyle(
        fg=s.fg, bg=s.bg, bold=s.bold,
        italic=s.italic, underline=s.underline, reverse=s.reverse,
    )


def parse_ansi_line(line: str) -> list[StyledSegment]:
    """Parse a line containing ANSI escape codes into styled segments.

    Handles SGR codes 0-107, 256-colour (38;5;N / 48;5;N),
    and 24-bit true colour (38;2;R;G;B / 48;2;R;G;B).
    """
    segments: list[StyledSegment] = []
    style = TextStyle()
    pos = 0

    for match in ANSI_ESCAPE_RE.finditer(line):
        start = match.start()
        if start > pos:
            text = line[pos:start]
            if text:
                segments.append(StyledSegment(text=text, style=_copy_style(style)))
        pos = match.end()

        params_str = match.group(1)
        if not params_str:
            style = TextStyle()
            continue

        params = [int(p) if p else 0 for p in params_str.split(";")]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                style = TextStyle()
            elif p == 1:
                style.bold = True
            elif p == 3:
                style.italic = True
            elif p == 4:
                style.underline = True
            elif p == 7:
                style.reverse = True
            elif p == 22:
                style.bold = False
            elif p == 23:
                style.italic = False
            elif p == 24:
                style.underline = False
            elif p == 27:
                style.reverse = False
            elif 30 <= p <= 37:
                style.fg = COLORS_16[p - 30]
            elif p == 38:
                # Extended foreground
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        style.fg = color_256(params[i + 2])
                        i += 2
                elif (
                    i + 1 < len(params) and params[i + 1] == 2 and i + 4 < len(params)
                ):
                    style.fg = (params[i + 2], params[i + 3], params[i + 4])
                    i += 4
            elif p == 39:
                style.fg = DEFAULT_FG
            elif 40 <= p <= 47:
                style.bg = COLORS_16[p - 40]
            elif p == 48:
                # Extended background
                if i + 1 < len(params) and params[i + 1] == 5:
                    if i + 2 < len(params):
                        style.bg = color_256(params[i + 2])
                        i += 2
                elif (
                    i + 1 < len(params) and params[i + 1] == 2 and i + 4 < len(params)
                ):
                    style.bg = (params[i + 2], params[i + 3], params[i + 4])
                    i += 4
            elif p == 49:
                style.bg = DEFAULT_BG
            elif 90 <= p <= 97:
                style.fg = COLORS_16[p - 90 + 8]
            elif 100 <= p <= 107:
                style.bg = COLORS_16[p - 100 + 8]
            i += 1

    # Remaining text after last escape
    if pos < len(line):
        text = line[pos:]
        if text:
            segments.append(StyledSegment(text=text, style=_copy_style(style)))

    return segments
