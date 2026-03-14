"""ScreenshotEngine — terminal ANSI text to PNG rendering.

Based on ccbot screenshot.py. Uses Pillow for rendering with
3-tier font fallback: JetBrainsMono -> NotoSansCJK -> Symbola.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..utils.ansi import DEFAULT_BG, StyledSegment, parse_ansi_line

# Font search paths
FONT_DIR = Path(__file__).parent.parent / "fonts"
SYSTEM_FONT_DIRS = [
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
]

# Font file names to search for (ordered by preference)
FONT_CANDIDATES: dict[str, list[str]] = {
    "mono": [
        "JetBrainsMono-Regular.ttf",
        "JetBrainsMonoNL-Regular.ttf",
        "DejaVuSansMono.ttf",
        "FiraCode-Regular.ttf",
    ],
    "cjk": [
        "NotoSansMonoCJKsc-Regular.otf",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansMonoCJKsc-Regular.ttf",
    ],
    "symbol": [
        "Symbola.ttf",
        "Symbola.otf",
    ],
}


def _find_font(candidates: list[str]) -> str | None:
    """Search for a font file in known directories."""
    for name in candidates:
        # Check bundled fonts first
        p = FONT_DIR / name
        if p.exists():
            return str(p)
        # Check system font dirs
        for d in SYSTEM_FONT_DIRS:
            if d.exists():
                for fp in d.rglob(name):
                    return str(fp)
    return None


def _is_wide_char(ch: str) -> bool:
    """Check if a character is full-width (CJK etc)."""
    if len(ch) != 1:
        return False
    eaw = unicodedata.east_asian_width(ch)
    return eaw in ("F", "W")


def _font_tier(ch: str) -> int:
    """Determine which font tier a character belongs to.

    0 = mono (ASCII/Latin), 1 = CJK, 2 = symbol/emoji
    """
    cp = ord(ch)
    # ASCII and common Latin
    if cp < 0x2000:
        return 0
    # CJK ranges
    if (
        0x2E80 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0xFE30 <= cp <= 0xFE4F
        or 0x20000 <= cp <= 0x2FA1F
        or 0x3000 <= cp <= 0x303F
        or 0x3040 <= cp <= 0x30FF  # Hiragana/Katakana
        or 0x31F0 <= cp <= 0x31FF
        or 0xFF00 <= cp <= 0xFFEF
    ):
        return 1
    # Everything else (symbols, emoji, etc)
    if cp >= 0x2000:
        return 2
    return 0


class ScreenshotEngine:
    """Render terminal ANSI content to PNG images."""

    def __init__(self, font_size: int = 28):
        self.font_size = font_size
        self.fonts: list[ImageFont.FreeTypeFont | ImageFont.ImageFont | None] = [
            None,
            None,
            None,
        ]
        self._load_fonts()

    def _load_fonts(self) -> None:
        """Load the 3-tier font stack."""
        for tier, key in enumerate(["mono", "cjk", "symbol"]):
            path = _find_font(FONT_CANDIDATES[key])
            if path:
                with contextlib.suppress(Exception):
                    self.fonts[tier] = ImageFont.truetype(path, self.font_size)

        # Fallback: if no mono font found, use default
        if self.fonts[0] is None:
            try:
                self.fonts[0] = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                    self.font_size,
                )
            except Exception:
                self.fonts[0] = ImageFont.load_default()

    def _get_font(self, ch: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """Get the appropriate font for a character."""
        tier = _font_tier(ch)
        font = self.fonts[tier]
        if font is not None:
            return font
        # Fallback through tiers
        for f in self.fonts:
            if f is not None:
                return f
        return ImageFont.load_default()

    def _char_size(self) -> tuple[int, int]:
        """Get the size of a single character cell."""
        font = self.fonts[0] or ImageFont.load_default()
        # Measure a reference character
        bbox = font.getbbox("M")
        w = bbox[2] - bbox[0]
        h = int(self.font_size * 1.4)
        return w, h

    async def capture(self, ansi_text: str) -> bytes:
        """Render ANSI text to PNG bytes."""
        return await asyncio.to_thread(self._render_sync, ansi_text)

    def _render_sync(self, ansi_text: str) -> bytes:
        """Synchronous rendering (runs in thread pool)."""
        lines = ansi_text.split("\n")
        char_w, char_h = self._char_size()

        # Parse lines and calculate max visible columns
        max_visible_cols = 0
        parsed_lines: list[list[StyledSegment]] = []
        for line in lines:
            segments = parse_ansi_line(line)
            parsed_lines.append(segments)
            visible_len = sum(
                sum(2 if _is_wide_char(ch) else 1 for ch in seg.text)
                for seg in segments
            )
            max_visible_cols = max(max_visible_cols, visible_len)

        if max_visible_cols == 0:
            max_visible_cols = 80

        padding = 16
        img_w = max_visible_cols * char_w + padding * 2
        img_h = len(parsed_lines) * char_h + padding * 2

        img = Image.new("RGB", (img_w, img_h), DEFAULT_BG)
        draw = ImageDraw.Draw(img)

        y = padding
        for segments in parsed_lines:
            x = padding
            for seg in segments:
                fg = seg.style.effective_fg()
                bg = seg.style.effective_bg()

                for ch in seg.text:
                    ch_font = self._get_font(ch)
                    is_wide = _is_wide_char(ch)
                    cw = char_w * (2 if is_wide else 1)

                    # Draw background if not default
                    if bg != DEFAULT_BG:
                        draw.rectangle([x, y, x + cw, y + char_h], fill=bg)

                    # Draw character
                    draw.text((x, y), ch, fill=fg, font=ch_font)
                    x += cw

            y += char_h

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
