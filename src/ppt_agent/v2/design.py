"""Design system: theme tokens that bound what the model may use on a page.

The model never emits raw styling decisions like exact hexes or font names; it
references tokens (color roles, text roles) defined here. That is what keeps
100 independently generated pages looking like one deck.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ppt_agent.models import HexColor, StrictModel


ColorRole = Literal[
    "background",
    "surface",
    "surface_alt",
    "primary",
    "primary_soft",
    "secondary",
    "accent",
    "text",
    "muted",
    "on_primary",
    "success",
    "warning",
    "danger",
]

TextRole = Literal[
    "display",
    "title",
    "subtitle",
    "section",
    "h3",
    "body",
    "body_small",
    "caption",
    "kicker",
    "stat",
    "stat_label",
    "quote",
]

Motif = Literal["none", "corner_arc", "side_band", "dot_grid", "top_rule", "diagonal"]


class TypeSpec(StrictModel):
    size_pt: float = Field(..., gt=0)
    bold: bool = False
    line_spacing: float = Field(default=1.2, gt=0)
    default_color: ColorRole = "text"
    min_size_pt: float = Field(..., gt=0)


# One shared modular type scale. Themes restyle color/font/motif; the scale
# stays fixed so QA overflow estimation is theme-independent.
TYPE_SCALE: dict[str, TypeSpec] = {
    "display": TypeSpec(size_pt=44, bold=True, line_spacing=1.1, min_size_pt=30),
    "title": TypeSpec(size_pt=30, bold=True, line_spacing=1.15, min_size_pt=22),
    "subtitle": TypeSpec(size_pt=18, line_spacing=1.3, default_color="muted", min_size_pt=13),
    "section": TypeSpec(size_pt=36, bold=True, line_spacing=1.1, min_size_pt=26),
    "h3": TypeSpec(size_pt=17, bold=True, line_spacing=1.2, min_size_pt=13),
    "body": TypeSpec(size_pt=13, line_spacing=1.35, min_size_pt=10),
    "body_small": TypeSpec(size_pt=11, line_spacing=1.3, default_color="muted", min_size_pt=9),
    "caption": TypeSpec(size_pt=10, line_spacing=1.25, default_color="muted", min_size_pt=8),
    "kicker": TypeSpec(size_pt=11, bold=True, line_spacing=1.2, default_color="primary", min_size_pt=9),
    "stat": TypeSpec(size_pt=34, bold=True, line_spacing=1.05, default_color="primary", min_size_pt=22),
    "stat_label": TypeSpec(size_pt=11, line_spacing=1.2, default_color="muted", min_size_pt=9),
    "quote": TypeSpec(size_pt=22, line_spacing=1.35, min_size_pt=16),
}


class ThemePalette(StrictModel):
    background: HexColor
    surface: HexColor
    surface_alt: HexColor
    primary: HexColor
    primary_soft: HexColor
    secondary: HexColor
    accent: HexColor
    text: HexColor
    muted: HexColor
    on_primary: HexColor
    success: HexColor = "#2E9E6B"
    warning: HexColor = "#D9822B"
    danger: HexColor = "#C94F4F"

    def resolve(self, role: str) -> str:
        value = getattr(self, role, None)
        if value is None:
            raise ValueError(f"Unknown color role '{role}'")
        return value


class ThemeFonts(StrictModel):
    heading_latin: str = "Segoe UI"
    heading_east_asian: str = "Microsoft YaHei"
    body_latin: str = "Segoe UI"
    body_east_asian: str = "Microsoft YaHei"


class ThemeSpec(StrictModel):
    """All deck-wide visual decisions, fixed before any page is generated."""

    name: str = Field(..., min_length=1)
    mood: str = Field(default="professional", min_length=1)
    palette: ThemePalette
    fonts: ThemeFonts = Field(default_factory=ThemeFonts)
    motif: Motif = "corner_arc"
    dark_cover: bool = True
    corner_radius: float = Field(default=10, ge=0, description="Card corner radius in canvas units.")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (_hex_to_rgb(hex_color))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(color_a: str, color_b: str) -> float:
    lum_a, lum_b = relative_luminance(color_a), relative_luminance(color_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def best_text_color(palette: ThemePalette, background_hex: str) -> ColorRole:
    """Pick the readable text token for an arbitrary theme background color."""

    if contrast_ratio(palette.text, background_hex) >= contrast_ratio(
        palette.on_primary, background_hex
    ):
        return "text"
    return "on_primary"


BUILTIN_THEMES: dict[str, ThemeSpec] = {
    "aurora": ThemeSpec(
        name="aurora",
        mood="modern tech, confident",
        palette=ThemePalette(
            background="#F7F8FC",
            surface="#FFFFFF",
            surface_alt="#EEF1FA",
            primary="#4B5AE4",
            primary_soft="#DDE2FB",
            secondary="#22B8A6",
            accent="#F2A93B",
            text="#1E2233",
            muted="#6B7186",
            on_primary="#FFFFFF",
        ),
        motif="corner_arc",
    ),
    "ink": ThemeSpec(
        name="ink",
        mood="editorial, high contrast",
        palette=ThemePalette(
            background="#FAFAF7",
            surface="#FFFFFF",
            surface_alt="#F0EFE9",
            primary="#16161A",
            primary_soft="#E4E3DD",
            secondary="#8C1D18",
            accent="#C8A24B",
            text="#16161A",
            muted="#5C5C57",
            on_primary="#FFFFFF",
        ),
        motif="top_rule",
        dark_cover=True,
    ),
    "forest": ThemeSpec(
        name="forest",
        mood="calm, sustainable",
        palette=ThemePalette(
            background="#F5F7F4",
            surface="#FFFFFF",
            surface_alt="#E9EFE7",
            primary="#2F6B4F",
            primary_soft="#D7E6DC",
            secondary="#7FA55C",
            accent="#D9822B",
            text="#22301F",
            muted="#66705F",
            on_primary="#FFFFFF",
        ),
        motif="side_band",
    ),
    "slate": ThemeSpec(
        name="slate",
        mood="executive, restrained",
        palette=ThemePalette(
            background="#F4F6F8",
            surface="#FFFFFF",
            surface_alt="#E8ECF1",
            primary="#23415E",
            primary_soft="#D6E0EA",
            secondary="#4A7BA6",
            accent="#C0574F",
            text="#1B2733",
            muted="#5E6B78",
            on_primary="#FFFFFF",
        ),
        motif="dot_grid",
    ),
    "sunrise": ThemeSpec(
        name="sunrise",
        mood="energetic, consumer",
        palette=ThemePalette(
            background="#FFF9F3",
            surface="#FFFFFF",
            surface_alt="#FDEFE2",
            primary="#E4572E",
            primary_soft="#FADFD2",
            secondary="#29335C",
            accent="#F3A712",
            text="#2B2118",
            muted="#7A6A5C",
            on_primary="#FFFFFF",
        ),
        motif="diagonal",
    ),
}


def normalize_theme(theme: ThemeSpec) -> ThemeSpec:
    """Guarantee readability regardless of what a model proposed for a theme."""

    palette = theme.palette
    fixes: dict[str, str] = {}
    if contrast_ratio(palette.text, palette.background) < 4.5:
        fixes["text"] = "#1B1F2A" if relative_luminance(palette.background) > 0.4 else "#F4F6FA"
    if contrast_ratio(palette.on_primary, palette.primary) < 3.0:
        fixes["on_primary"] = (
            "#FFFFFF" if relative_luminance(palette.primary) < 0.5 else "#16181D"
        )
    if fixes:
        palette = palette.model_copy(update=fixes)
        return theme.model_copy(update={"palette": palette})
    return theme


def get_builtin_theme(name: str) -> ThemeSpec:
    try:
        return BUILTIN_THEMES[name]
    except KeyError:
        available = ", ".join(sorted(BUILTIN_THEMES))
        raise ValueError(f"Unknown theme '{name}'. Available: {available}") from None
