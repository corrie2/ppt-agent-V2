"""Style presets for PPT rendering.

Each preset defines colors, typography, accent patterns, and layout preferences.
Users select a style by name (e.g., theme="academic"), and the renderer applies
the corresponding visual system.
"""
from __future__ import annotations

from dataclasses import dataclass
from pptx.dml.color import RGBColor


@dataclass(frozen=True)
class StylePreset:
    name: str

    # Colors
    primary: RGBColor        # headings, title text, hero numbers
    secondary: RGBColor      # accent highlights, badges, step numbers
    tertiary: RGBColor       # secondary accent (used sparingly)
    background: RGBColor     # slide background
    surface: RGBColor        # card/panel fill
    border: RGBColor         # card borders, dividers
    text_body: RGBColor      # body text
    text_muted: RGBColor     # captions, subtitles, footnotes
    text_on_primary: RGBColor  # text drawn on primary-colored shapes

    # Typography
    title_size: int = 24
    heading_size: int = 18
    subheading_size: int = 15
    body_size: int = 14
    caption_size: int = 11
    font_name: str = "Calibri"

    # Accent pattern: "gradient_band" | "side_bar" | "corner_stripe" | "underline"
    accent_pattern: str = "gradient_band"

    # Layout
    margin_scale: float = 1.0   # 1.0 = default 0.8in margins
    card_style: str = "rounded"  # "rounded" or "sharp"


# ── Corporate (current default) ─────────────────────────────

CORPORATE = StylePreset(
    name="corporate",
    primary=RGBColor(16, 37, 66),       # navy
    secondary=RGBColor(32, 91, 172),    # blue
    tertiary=RGBColor(36, 124, 136),    # teal
    background=RGBColor(244, 247, 251), # light blue-grey
    surface=RGBColor(255, 255, 255),    # white
    border=RGBColor(221, 228, 237),     # mid grey-blue
    text_body=RGBColor(47, 58, 74),     # dark slate
    text_muted=RGBColor(110, 122, 138), # grey
    text_on_primary=RGBColor(255, 255, 255),
)

# ── Academic (paper explanations) ───────────────────────────

ACADEMIC = StylePreset(
    name="academic",
    primary=RGBColor(45, 45, 45),       # near-black
    secondary=RGBColor(139, 0, 0),      # dark red
    tertiary=RGBColor(0, 71, 107),      # dark teal
    background=RGBColor(252, 250, 245), # warm paper
    surface=RGBColor(255, 255, 255),
    border=RGBColor(200, 195, 185),     # warm grey
    text_body=RGBColor(33, 33, 33),
    text_muted=RGBColor(100, 100, 100),
    text_on_primary=RGBColor(255, 255, 255),
    title_size=22,
    heading_size=16,
    body_size=13,
    font_name="Georgia",
    accent_pattern="underline",
    card_style="sharp",
)

# ── Modern (clean, spacious) ────────────────────────────────

MODERN = StylePreset(
    name="modern",
    primary=RGBColor(17, 24, 39),       # almost black
    secondary=RGBColor(99, 102, 241),   # indigo
    tertiary=RGBColor(236, 72, 153),    # pink
    background=RGBColor(255, 255, 255), # pure white
    surface=RGBColor(249, 250, 251),    # near-white
    border=RGBColor(229, 231, 235),     # light grey
    text_body=RGBColor(17, 24, 39),
    text_muted=RGBColor(107, 114, 128),
    text_on_primary=RGBColor(255, 255, 255),
    title_size=28,
    heading_size=20,
    body_size=15,
    accent_pattern="corner_stripe",
    margin_scale=1.2,
)

# ── Minimal (ultra-clean) ───────────────────────────────────

MINIMAL = StylePreset(
    name="minimal",
    primary=RGBColor(30, 30, 30),
    secondary=RGBColor(80, 80, 80),
    tertiary=RGBColor(120, 120, 120),
    background=RGBColor(255, 255, 255),
    surface=RGBColor(250, 250, 250),
    border=RGBColor(210, 210, 210),
    text_body=RGBColor(30, 30, 30),
    text_muted=RGBColor(130, 130, 130),
    text_on_primary=RGBColor(255, 255, 255),
    title_size=26,
    heading_size=17,
    body_size=14,
    font_name="Helvetica Neue",
    accent_pattern="side_bar",
    margin_scale=1.3,
    card_style="sharp",
)


# ── Registry ────────────────────────────────────────────────

_PRESETS: dict[str, StylePreset] = {
    "corporate": CORPORATE,
    "executive_blue": CORPORATE,  # backward compat
    "academic": ACADEMIC,
    "modern": MODERN,
    "minimal": MINIMAL,
}


def get_style(name: str) -> StylePreset:
    """Resolve a style name to a StylePreset. Falls back to CORPORATE."""
    return _PRESETS.get(name.lower().strip(), CORPORATE)


def list_styles() -> list[str]:
    """Return available style names."""
    return list(_PRESETS.keys())
